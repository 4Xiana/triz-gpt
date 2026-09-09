# -*- coding: utf-8 -*-
"""语义向量与设计空间监控工具。

对应论文中的：
- RAG 检索：查询向量与知识库向量的余弦相似度检索；
- 创意发散监控：将方案关键词嵌入语义空间，PCA 降维到三维后计算词袋
  凸包体积，通过凸包体积变化判断设计空间是否扩展（Algorithm 1）。

当未配置在线 embedding 模型时，使用确定性的本地哈希向量作为兜底，
保证系统在无 API Key 时仍可完整运行。
"""

import hashlib
import math
import re
from typing import Callable, Dict, List, Optional

import numpy as np

LOCAL_DIM = 256

_CJK_STOPWORDS = {
    "的", "了", "和", "与", "及", "或", "是", "在", "我们", "他们", "可以",
    "需要", "问题", "通过", "进行", "实现", "一种", "这个", "那个", "以及",
    "但是", "然而", "因此", "所以", "如果", "由于", "对于", "根据", "其中",
    "为了", "能够", "可能", "应该", "必须", "之间", "进行", "使得", "从而",
    "同时", "并且", "或者", "没有", "一个", "一种", "这些", "那些", "它们",
    "它", "他", "她", "其", "之", "且", "并", "而", "则", "又", "再", "很",
    "更", "最", "较", "把", "被", "让", "使", "向", "从", "到", "于", "上",
    "下", "中", "内", "外", "时", "后", "前", "着", "过", "地", "得",
}

_LATIN_WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9\-]+")
_CJK_RUN_RE = re.compile(r"[一-鿿]+")


def keyword_bag(text: str) -> List[str]:
    """将方案文本拆分为关键词词袋：英文整词 + 中文二字/三字短语，去停用词。

    对应论文「将创意方案文本拆分成关键词，并去除其中的停止词，构成词袋模型」。
    """
    keywords: List[str] = []
    for w in _LATIN_WORD_RE.findall(text):
        if len(w) >= 2 and w.lower() not in _CJK_STOPWORDS:
            keywords.append(w.lower())
    for run in _CJK_RUN_RE.findall(text):
        if len(run) <= 4 and run not in _CJK_STOPWORDS:
            keywords.append(run)
        # 二字滑动窗口，捕获主要表意短语
        for i in range(len(run) - 1):
            bg = run[i:i + 2]
            if bg not in _CJK_STOPWORDS:
                keywords.append(bg)
        # 三字滑动窗口，保留部分专有短语
        for i in range(len(run) - 2):
            tg = run[i:i + 3]
            keywords.append(tg)
    # 去重保序
    seen = set()
    uniq = []
    for kw in keywords:
        if kw not in seen:
            seen.add(kw)
            uniq.append(kw)
    return uniq


def _hash_token(token: str) -> int:
    h = hashlib.md5(token.encode("utf-8")).hexdigest()
    return int(h[:8], 16)


def local_embed(texts: List[str], dim: int = LOCAL_DIM) -> np.ndarray:
    """本地确定性向量：字符/词 n-gram 特征哈希 + L2 归一化。

    不依赖外部模型，语义能力弱但行为稳定，用于离线演示与在线接口失败兜底。
    """
    vectors = np.zeros((len(texts), dim), dtype=np.float64)
    for row, text in enumerate(texts):
        tokens = list(text.lower())
        tokens += [text[i:i + 2] for i in range(len(text) - 1)]
        tokens += keyword_bag(text)
        for tok in tokens:
            idx = _hash_token(tok) % dim
            sign = 1.0 if (_hash_token(tok + "#sign") % 2 == 0) else -1.0
            vectors[row, idx] += sign
        norm = np.linalg.norm(vectors[row])
        if norm > 0:
            vectors[row] /= norm
    return vectors


def cosine_similarity(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """a: (n, d) 或 (d,)；b: (m, d)，返回 (n, m) 余弦相似度。"""
    a = np.atleast_2d(a)
    b = np.atleast_2d(b)
    a_norm = a / (np.linalg.norm(a, axis=1, keepdims=True) + 1e-12)
    b_norm = b / (np.linalg.norm(b, axis=1, keepdims=True) + 1e-12)
    with np.errstate(divide="ignore", over="ignore", invalid="ignore"):
        sims = a_norm @ b_norm.T
    return np.nan_to_num(sims, nan=0.0, posinf=0.0, neginf=0.0)


class VectorStore:
    """简易向量库（对应论文中 Weaviate 的角色）：存知识块 + 余弦相似检索。"""

    def __init__(self, embed_fn: Optional[Callable[[List[str]], np.ndarray]] = None):
        self.chunks: List[Dict] = []
        self.vectors: Optional[np.ndarray] = None
        self._embed_fn = embed_fn

    def set_embed_fn(self, embed_fn: Callable[[List[str]], np.ndarray]) -> None:
        self._embed_fn = embed_fn

    def _embed(self, texts: List[str]) -> np.ndarray:
        if self._embed_fn is not None:
            try:
                vecs = np.asarray(self._embed_fn(texts), dtype=np.float64)
                if vecs.shape[0] == len(texts):
                    return vecs
            except Exception:
                pass
        return local_embed(texts)

    def add(self, chunks: List[Dict]) -> None:
        if not chunks:
            return
        vecs = self._embed([c["text"] for c in chunks])
        self.chunks.extend(chunks)
        self.vectors = vecs if self.vectors is None else np.vstack([self.vectors, vecs])

    def query(self, text: str, top_k: int = 5,
              types: Optional[tuple] = None) -> List[Dict]:
        """余弦相似检索；types 给定时仅在指定知识块类型（parameter/principle）内检索。"""
        if self.vectors is None or len(self.chunks) == 0:
            return []
        qv = self._embed([text])
        sims = cosine_similarity(qv, self.vectors)[0]
        if types:
            wanted = set(types)
            cand = np.asarray(
                [i for i, c in enumerate(self.chunks) if c.get("type") in wanted],
                dtype=int,
            )
            if cand.size == 0:
                return []
            order = cand[np.argsort(-sims[cand])[:top_k]]
        else:
            order = np.argsort(-sims)[:top_k]
        results = []
        for idx in order:
            item = dict(self.chunks[idx])
            item["score"] = float(sims[idx])
            results.append(item)
        return results


def pca_project(vectors: np.ndarray, dim: int = 3,
                comps: Optional[np.ndarray] = None) -> np.ndarray:
    """主成分分析降维（论文统一采用 PCA 将语义向量投影到三维空间）。

    可传入外部拟合好的主成分基 comps，使不同点集投影到同一坐标系
    （保证凸包体积跨步骤可比）。
    """
    x = np.asarray(vectors, dtype=np.float64)
    if comps is None:
        if x.shape[0] <= dim:
            out = np.zeros((x.shape[0], dim))
            out[:, : min(x.shape[1], dim)] = x[:, : min(x.shape[1], dim)]
            return out
        x_fit = x - x.mean(axis=0, keepdims=True)
        with np.errstate(divide="ignore", over="ignore", invalid="ignore"):
            _, _, vt = np.linalg.svd(x_fit, full_matrices=False)
        comps = vt[:dim]
    with np.errstate(divide="ignore", over="ignore", invalid="ignore"):
        proj = x @ comps.T
    return np.nan_to_num(proj, nan=0.0, posinf=0.0, neginf=0.0)


def compare_design_space_expansion(old_keywords: List[str], new_keywords: List[str],
                                   embed_fn: Optional[Callable] = None):
    """以共享 PCA 基比较词袋扩张前后的凸包体积（Algorithm 1 第 5-8 行）。

    在合并后词袋上拟合一次 PCA，将旧词袋与合并词袋投影到同一三维坐标系，
    从而保证「凸包体积随点集单调」成立。返回 (旧体积, 新体积, 是否扩展)。
    """
    merged = list(dict.fromkeys(old_keywords + new_keywords))
    embed = embed_fn or (lambda texts: local_embed(texts))
    if len(merged) < 4:
        return 0.0, 0.0, False

    def safe_embed(keywords):
        try:
            vecs = np.asarray(embed(keywords), dtype=np.float64)
        except Exception:
            vecs = local_embed(keywords)
        if len(vecs.shape) != 2:
            vecs = local_embed(keywords)
        return vecs

    merged_vecs = safe_embed(merged)
    # 在合并词袋上拟合共享主成分基
    centered = merged_vecs - merged_vecs.mean(axis=0, keepdims=True)
    with np.errstate(divide="ignore", over="ignore", invalid="ignore"):
        _, _, vt = np.linalg.svd(centered, full_matrices=False)
    comps = vt[:3]
    new_proj = pca_project(merged_vecs, 3, comps=comps)
    new_vol = convex_hull_volume(new_proj)
    if len(old_keywords) < 4:
        old_vol = 0.0
    else:
        old_vecs = safe_embed(old_keywords)
        old_proj = pca_project(old_vecs, 3, comps=comps)
        old_vol = convex_hull_volume(old_proj)
    expanded = new_vol > old_vol + 1e-9
    return old_vol, new_vol, expanded


def series_volumes(snapshots: List[List[str]], embed_fn: Optional[Callable] = None) -> List[float]:
    """用统一 PCA 基重算各历史词袋快照的凸包体积（跨步骤可比，供绘制曲线图）。

    在最新（最大）词袋上拟合主成分基，再将每个历史快照投影到同一坐标系。
    """
    embed = embed_fn or (lambda texts: local_embed(texts))
    if not snapshots:
        return []

    def safe_embed(keywords):
        try:
            vecs = np.asarray(embed(keywords), dtype=np.float64)
        except Exception:
            vecs = local_embed(keywords)
        if len(vecs.shape) != 2:
            vecs = local_embed(keywords)
        return vecs

    latest = snapshots[-1]
    latest_vecs = safe_embed(latest)
    centered = latest_vecs - latest_vecs.mean(axis=0, keepdims=True)
    with np.errstate(divide="ignore", over="ignore", invalid="ignore"):
        _, _, vt = np.linalg.svd(centered, full_matrices=False)
    comps = vt[:3]
    volumes = []
    for snap in snapshots:
        if len(set(snap)) < 4:
            volumes.append(0.0)
            continue
        vecs = safe_embed(snap)
        proj = pca_project(vecs, 3, comps=comps)
        volumes.append(convex_hull_volume(proj))
    return volumes


def convex_hull_volume(points: np.ndarray) -> float:
    """三维点集凸包体积；点数不足（<4）或共面时体积为 0。"""
    pts = np.asarray(points, dtype=np.float64)
    if pts.shape[0] < 4:
        return 0.0
    try:
        from scipy.spatial import ConvexHull

        try:
            hull = ConvexHull(pts)
            return float(hull.volume)
        except Exception:
            return 0.0
    except ImportError:
        return _signed_tetra_volume_fallback(pts)


def _signed_tetra_volume_fallback(pts: np.ndarray) -> float:
    """无 scipy 时的兜底：以质心为公共顶点的四面体体积之和（近似）。"""
    center = pts.mean(axis=0)
    total = 0.0
    n = pts.shape[0]
    for i in range(n):
        for j in range(i + 1, n):
            for k in range(j + 1, n):
                tetra = np.vstack([pts[i] - center, pts[j] - center, pts[k] - center])
                total += abs(np.linalg.det(tetra)) / 6.0
    return total


def design_space_volume(keywords: List[str], embed_fn: Optional[Callable] = None) -> float:
    """词袋 -> 嵌入 -> PCA 三维 -> 凸包体积。"""
    if len(keywords) < 4:
        return 0.0
    if embed_fn is not None:
        try:
            vecs = np.asarray(embed_fn(keywords), dtype=np.float64)
        except Exception:
            vecs = local_embed(keywords)
    else:
        vecs = local_embed(keywords)
    projected = pca_project(vecs, 3)
    return convex_hull_volume(projected)
