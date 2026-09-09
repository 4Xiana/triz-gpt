# -*- coding: utf-8 -*-
"""LLM 抽象层。

论文指出：TRIZ-GPT 是通用框架，后端可采用任意开源/闭源模型，仅需在
LLM 类中替换调用代码，无需改变系统框架和提示词。本模块定义该统一接口：
- chat：多轮对话补全；
- embed：文本向量化；
- chat_json：在 chat 基础上约束/解析 JSON 输出。
"""

import json
import os
import re
from typing import Any, Dict, Iterator, List, Optional

import numpy as np


class BaseLLM:
    name = "base"

    def chat(self, messages: List[Dict[str, str]], temperature: float = 0.7,
             meta: Optional[Dict[str, Any]] = None, json_format: bool = False) -> str:
        raise NotImplementedError

    def chat_stream(self, messages: List[Dict[str, str]], temperature: float = 0.7,
                    meta: Optional[Dict[str, Any]] = None,
                    json_format: bool = False) -> Iterator[str]:
        """流式对话：逐块产出模型输出文本。默认实现退化为一次性返回。"""
        yield self.chat(messages, temperature=temperature, meta=meta,
                        json_format=json_format)

    def embed(self, texts: List[str]) -> np.ndarray:
        raise NotImplementedError

    def chat_json(self, messages: List[Dict[str, str]], temperature: float = 0.4,
                  meta: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """要求模型输出 JSON，并做容错解析。"""
        raw = self.chat(messages, temperature=temperature, meta=meta, json_format=True)
        return parse_json_lenient(raw)


_THINK_CLOSED_RE = re.compile(r"<think>.*?</think>", re.S)
_THINK_OPEN_RE = re.compile(r"<think>.*$", re.S)


def strip_thinking(text: str) -> str:
    """剥离模型思考块（<think>...</think> 及未闭合的残留），避免污染 JSON 解析。"""
    text = _THINK_CLOSED_RE.sub("", text)
    text = _THINK_OPEN_RE.sub("", text)
    return text


def parse_json_lenient(raw: str) -> Dict[str, Any]:
    """从模型输出中容错提取 JSON（兼容思考块、```json 代码块与前后说明文字）。"""
    if not raw:
        raise ValueError("空响应")
    text = strip_thinking(raw).strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, flags=re.S)
    if fence:
        text = fence.group(1).strip()
    try:
        return json.loads(text)
    except Exception:
        pass
    # 退而求其次：截取第一个平衡花括号块
    start = text.find("{")
    if start == -1:
        raise ValueError(f"响应中未找到 JSON：{raw[:200]}")
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                candidate = text[start:i + 1]
                try:
                    return json.loads(candidate)
                except Exception:
                    break
    raise ValueError(f"JSON 解析失败：{raw[:200]}")


def _unescape_json_str(s: str) -> str:
    """对可能不完整的 JSON 字符串片段做最小反转义。"""
    out, i = [], 0
    while i < len(s):
        ch = s[i]
        if ch == "\\" and i + 1 < len(s):
            nxt = s[i + 1]
            out.append({"n": "\n", "t": "\t", "r": "\r", '"': '"',
                        "\\": "\\", "/": "/"}.get(nxt, nxt))
            i += 2
        else:
            out.append(ch)
            i += 1
    return "".join(out)


def _extract_json_objects(frag: str) -> List[Dict[str, Any]]:
    """从片段中扫描所有已平衡闭合的 {...} 对象并逐个解析（字符串感知）。"""
    objs: List[Dict[str, Any]] = []
    depth = 0
    start = -1
    in_str = False
    escaped = False
    for i, ch in enumerate(frag):
        if in_str:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            if depth > 0:
                depth -= 1
                if depth == 0 and start >= 0:
                    try:
                        obj = json.loads(frag[start:i + 1])
                        if isinstance(obj, dict):
                            objs.append(obj)
                    except Exception:
                        pass
                    start = -1
    return objs


def parse_json_partial(raw: str) -> Dict[str, Any]:
    """从流式到达、可能尚未闭合的 JSON 缓冲区中尽力提取已成型字段。

    仅用于流式期间的人读化预览；最终结果仍以 parse_json_lenient 为准。
    """
    if not raw:
        return {}
    raw = strip_thinking(raw)
    try:
        return parse_json_lenient(raw)
    except Exception:
        pass
    out: Dict[str, Any] = {}
    for key in ("title", "text", "summary", "rationale", "comment"):
        m = re.search(rf'"{key}"\s*:\s*"((?:[^"\\]|\\.)*?)(?:"\s*[,}}\]]|$)',
                      raw, flags=re.S)
        if m and m.group(1).strip():
            out[key] = _unescape_json_str(m.group(1))
    m = re.search(r'"parameters"\s*:\s*\[(.*?)(?:\]|$)', raw, flags=re.S)
    if m:
        frag = m.group(1)
        items = []
        for sm in re.finditer(
                r'"((?:[^"\\]|\\.)*)"(?=\s*[,}\]\n]|\s*$)|"((?:[^"\\]|\\.)*)$',
                frag, flags=re.S):
            val = sm.group(1) if sm.group(1) is not None else sm.group(2)
            if val and val.strip():
                items.append(_unescape_json_str(val))
        if items:
            out["parameters"] = items
    m = re.search(r'"principles"\s*:\s*\[(.*?)(?:\]|$)', raw, flags=re.S)
    if m:
        nums = re.findall(r"\d+", m.group(1))
        if nums:
            out["principles"] = [int(n) for n in nums[:4]]
    # 矛盾对：逐组提取已闭合的对象，流式期间矛盾对可逐条浮现
    m = re.search(r'"pairs"\s*:\s*\[(.*)', raw, flags=re.S)
    if m:
        pairs = [obj for obj in _extract_json_objects(m.group(1))
                 if "improving_param_id" in obj or "improving_concrete" in obj]
        if pairs:
            out["pairs"] = pairs
    return out


def _tail_prefix_len(text: str, tag: str) -> int:
    """text 末尾与 tag 前缀匹配的最大长度（用于跨分片边界时暂存不完整标签）。"""
    upper = min(len(text), len(tag) - 1)
    for k in range(upper, 0, -1):
        if tag.startswith(text[-k:]):
            return k
    return 0


class ThinkStreamSplitter:
    """把含 <think>...</think> 的流式增量拆分为「思考流」与「回答流」。

    - 状态机：pre（未见开标签）→ thinking（思考中）→ answer（回答中）；
    - 标签可能被网络分片切开，用末尾前缀匹配暂存不完整片段；
    - 模型不输出思考块时（如已关闭思考/非 MiniMax 模型），全部内容按回答处理。
    """

    OPEN = "<think>"
    CLOSE = "</think>"

    def __init__(self):
        self.buffer = ""
        self.state = "pre"
        self._think_emitted = 0
        self._answer_start = 0
        self._answer_prefix = ""
        self.think_text = ""

    def feed(self, piece: str) -> Dict[str, Any]:
        self.buffer += piece
        ev: Dict[str, Any] = {"think_started": False, "think_delta": None,
                              "think_done": False, "answer": None}
        if self.state == "pre":
            idx = self.buffer.find(self.OPEN)
            if idx == -1:
                hold = _tail_prefix_len(self.buffer, self.OPEN)
                answer = self.buffer[:len(self.buffer) - hold]
                if answer:
                    ev["answer"] = answer
                return ev
            self._answer_prefix = self.buffer[:idx]
            ev["think_started"] = True
            self.state = "thinking"
        if self.state == "thinking":
            close_idx = self.buffer.find(self.CLOSE, len(self.OPEN))
            if close_idx == -1:
                hold = _tail_prefix_len(self.buffer, self.CLOSE)
                think_end = len(self.buffer) - hold
                think_content = self.buffer[len(self.OPEN):think_end]
                if len(think_content) > self._think_emitted:
                    ev["think_delta"] = think_content[self._think_emitted:]
                    self._think_emitted = len(think_content)
                return ev
            think_content = self.buffer[len(self.OPEN):close_idx]
            if len(think_content) > self._think_emitted:
                ev["think_delta"] = think_content[self._think_emitted:]
                self._think_emitted = len(think_content)
            self.think_text = think_content
            self._answer_start = close_idx + len(self.CLOSE)
            self.state = "answer"
            ev["think_done"] = True
        if self.state == "answer":
            ev["answer"] = self._answer_prefix + self.buffer[self._answer_start:]
        return ev


def get_llm():
    """工厂方法：依据环境变量选择真实 API 模型或内置演示模型。"""
    from .mock import MockLLM
    from .provider import OpenAICompatLLM

    if os.environ.get("LLM_FORCE_MOCK", "").strip() == "1":
        return MockLLM()
    api_key = (
        os.environ.get("LLM_API_KEY")
        or os.environ.get("MINIMAX_API_KEY")
        or os.environ.get("OPENAI_API_KEY")
        or ""
    ).strip()
    if not api_key:
        return MockLLM()
    base_url = os.environ.get("LLM_BASE_URL", "https://api.minimaxi.com/v1").strip()
    model = os.environ.get("LLM_MODEL", "MiniMax-M3").strip()
    embedding_model = os.environ.get("LLM_EMBEDDING_MODEL", "").strip() or None
    try:
        llm = OpenAICompatLLM(
            api_key=api_key,
            base_url=base_url,
            model=model,
            embedding_model=embedding_model,
        )
        llm.heartbeat()
        return llm
    except Exception as exc:  # 接口不可用时回退演示模型，保证系统可用
        print(f"[LLM] 真实模型连接失败，回退内置演示模型：{exc}")
        return MockLLM()
