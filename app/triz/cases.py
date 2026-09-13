# -*- coding: utf-8 -*-
"""TRIZ 真实工程案例库（方案生成阶段的 few-shot 素材）。

数据来自用户整理的两个外部案例库（英文原文，ETL 产物 data/triz_cases.json）：
- classic：TRIZ 经典案例（28 例）；
- modern：GPT-3.5 之后的新案例（12 例）。
每个案例含问题场景、39 参数矛盾、按发明原则编号标注的具体解法。
一个案例可能同时使用多个发明原则，因此按「解法行 × 原则」建倒排索引，
同一条案例可作为多个原则的 few-shot。
"""

import json
import os
from typing import Any, Dict, List, Optional

_DATA_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "data")

LIB_LABEL = {"classic": "TRIZ 经典案例库", "modern": "2022 年后新案例库"}

# 提示词中各字段的长度上限（英文按字符截断，控制 token 预算）
PROBLEM_MAX = 700
SOLUTION_MAX = 600

_CASES: Optional[List[Dict[str, Any]]] = None
_INDEX: Optional[Dict[int, List[Dict[str, Any]]]] = None


def _load() -> List[Dict[str, Any]]:
    global _CASES
    if _CASES is None:
        with open(os.path.join(_DATA_DIR, "triz_cases.json"), "r",
                  encoding="utf-8") as f:
            _CASES = json.load(f)
    return _CASES


def _principle_index() -> Dict[int, List[Dict[str, Any]]]:
    """倒排索引：原则编号 → 命中的案例条目（保持经典→现代、原序稳定）。"""
    global _INDEX
    if _INDEX is None:
        idx: Dict[int, List[Dict[str, Any]]] = {pid: [] for pid in range(1, 41)}
        for case in _load():
            for line in case.get("lines", []):
                entry = {
                    "case_id": case["id"],
                    "lib": case["lib"],
                    "lib_label": LIB_LABEL.get(case["lib"], case["lib"]),
                    "problem": case.get("problem", ""),
                    "solution": line.get("solution", ""),
                    "principles": list(line.get("principles", [])),
                }
                for pid in line.get("principles", []):
                    if 1 <= pid <= 40 and entry not in idx[pid]:
                        idx[pid].append(entry)
        _INDEX = idx
    return _INDEX


def cases_for_principle(principle_id: int, limit: int = 2,
                        offset: int = 0) -> List[Dict[str, Any]]:
    """按发明原则取 few-shot 案例（浅拷贝防外部改写）。

    同一原则被多轮候选方案复用时，用 offset（候选序号）轮换案例集合，
    避免每次拿到相同范例导致方案同质化。
    """
    pool = _principle_index().get(principle_id, [])
    if not pool:
        return []
    n = len(pool)
    start = offset % n
    ordered = [pool[(start + i) % n] for i in range(min(limit, n))]
    return [dict(e) for e in ordered]


def textbook_examples(principle_id: int, limit: int = 3) -> List[str]:
    """无真实案例的原则：返回内置中文教科书案例作为兜底 few-shot。"""
    from . import knowledge as K
    info = K.INVENTIVE_PRINCIPLES.get(principle_id, {})
    return list(info.get("examples", []))[:limit]


def principle_methods(principle_id: int) -> List[Dict[str, Any]]:
    """返回原则的可操作子方法（triz40 整理，40 原则全覆盖；可能为空列表）。"""
    from . import knowledge as K
    info = K.INVENTIVE_PRINCIPLES.get(principle_id, {})
    return [dict(m) for m in info.get("methods", [])]


def principle_detail(principle_id: int) -> str:
    """返回原则详解（比一句话 desc 更完整的操作化阐释）。"""
    from . import knowledge as K
    info = K.INVENTIVE_PRINCIPLES.get(principle_id, {})
    return info.get("detail", "")
