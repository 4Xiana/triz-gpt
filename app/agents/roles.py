# -*- coding: utf-8 -*-
"""各 Agent 角色定义与提示词构建。

角色设置对应论文 SOP 中四个模块的任务分工：
- 识别矛盾：问题参数提取师（直觉式信息抽取）+ TRIZ参数匹配专家（抽象匹配）；
- 方案生成：创意设计师（直觉过程主导的发散，双钻模型「先发散」）；
- 方案评估：收益/成本/副作用三组评审专家（理性过程主导的收拢，理想性评分）；
- 优化迭代：优化设计师 + 评审专家（元认知驱动的 LLM-as-optimizer）。

所有结构化任务均要求模型以 JSON 输出，便于系统筛选关键信息单独展示。
"""

import json
from typing import Any, Dict, List, Optional

from ..triz import knowledge as K
from ..triz import cases as CS

JSON_RULE = "请严格按要求输出 JSON，不要输出 JSON 以外的解释文字。"

# ---------------------------------------------------------------------------
# 角色系统提示词
# ---------------------------------------------------------------------------
ROLES = {
    "analyst": (
        "你是TRIZ-GPT系统中的【问题参数提取师】，服务于TRIZ发明问题解决理论的问题分析阶段。"
        "你的任务是阅读用户对具体工程/设计问题的描述，识别其中隐含的、相互制约的关键工程评估参数，"
        "即「希望改善的属性」与「改善时可能恶化的属性」。"
        "你需要像资深工程师一样敏锐地从描述性文字中抽取参数短语，每条参数用简短的中文短语表述。"
    ),
    "matcher": (
        "你是TRIZ-GPT系统中的【TRIZ参数匹配专家】，精通TRIZ理论的39个通用工程参数。"
        "你的任务是把问题中具体的工程参数抽象匹配到TRIZ限定的39个工程参数上，"
        "并识别出一组组技术矛盾：一个参数的改善会导致另一个参数的恶化。"
        "匹配时应抓住参数的物理本质，而非字面相似；每条矛盾需给出判断理由。"
    ),
    "engineer": (
        "你是TRIZ-GPT系统中的【创意设计师】，扮演经验丰富的工程师与工业设计师。"
        "你将依据TRIZ发明原则的启发，运用类比思维把抽象原则迁移到具体问题上，"
        "生成新颖、具体、可落地的设计方案。方案必须与已有方案显著不同，"
        "应具体到结构、材料、工作方式或控制策略，避免空泛的口号。"
    ),
    "expert_benefit": (
        "你是TRIZ-GPT系统中的【收益评审专家】，负责依据TRIZ的「理想性」标准评估设计方案的收益。"
        "你只考察方案能带来的有用功能与收益大小，以十分制（1-10整数）打分，分数越高收益越大。"
        "评分必须给出具体依据（指出方案中带来收益的具体结构/做法），并指出进一步放大收益、"
        "或在后续修改中必须注意保持的收益点，禁止只给笼统结论。"
    ),
    "expert_cost": (
        "你是TRIZ-GPT系统中的【成本评审专家】，负责依据TRIZ的「理想性」标准评估设计方案的成本。"
        "你只考察方案实现所需付出的成本（结构复杂度、材料、制造、能耗等代价），"
        "以十分制（1-10整数）打分，分数越高表示成本越高昂。"
        "评分必须给出具体依据（指出方案中导致成本的具体环节），并给出一条可操作的降低成本建议，"
        "明确到结构、材料、工艺或使用方式的调整，禁止只给笼统结论。"
    ),
    "expert_harm": (
        "你是TRIZ-GPT系统中的【副作用评审专家】，负责依据TRIZ的「理想性」标准评估设计方案的有害副作用。"
        "你只考察方案可能引入的有害效应（污染、振动、噪音、安全隐患、次生问题等），"
        "以十分制（1-10整数）打分，分数越高表示副作用越严重。"
        "评分必须给出具体依据（指出方案中可能引发副作用的具体环节与机理），"
        "并给出一条可操作的抑制/防护建议，明确到结构或控制措施的调整，禁止只给笼统结论。"
    ),
    "refiner": (
        "你是TRIZ-GPT系统中的【优化设计师】，负责依据评审专家的评分与改进建议对方案进行微调优化。"
        "你要在保留原方案核心创意的前提下，针对成本与副作用进行改进，"
        "输出完整、可落地的优化后方案，而不是简单重复原方案。"
    ),
    "methodologist": (
        "你是TRIZ-GPT系统中的【TRIZ方法学家】，精通全部40个发明原则。"
        "当矛盾矩阵未给出推荐原则时，你依据矛盾的具体特点，从40个发明原则中推断最适用的若干原则。"
    ),
}


# ---------------------------------------------------------------------------
# 提示词构建
# ---------------------------------------------------------------------------
def build_extract_params(problem: str):
    instruction = (
        f"【问题描述】\n{problem}\n\n"
        "请从上述问题描述中提取出关键的工程评估参数（4-6条），"
        "涵盖希望改善的方面与可能恶化的方面，每条用简短中文短语表述。\n"
        f"{JSON_RULE}\n输出格式：\n"
        '{"parameters": ["参数短语1", "参数短语2", ...], "summary": "一句话概括"}'
    )
    meta = {"task": "extract_params", "problem": problem}
    return instruction, meta, problem


def build_match_params(problem: str, parameters: List[str]):
    param_text = "；".join(parameters)
    catalog = "\n".join(
        f"{pid}. {p['name']}" for pid, p in K.ENGINEERING_PARAMETERS.items()
    )
    instruction = (
        f"【问题描述】\n{problem}\n\n"
        f"【已提取的具体参数】\n{param_text}\n\n"
        f"【TRIZ 39个通用工程参数清单（编号不得编造，只能从此清单选择）】\n{catalog}\n\n"
        "请将这些具体参数分别匹配到上述39个工程参数，"
        "并组成2-3组技术矛盾（improving=希望改善的参数，worsening=随之恶化的参数）。"
        "匹配时应抓住参数的物理本质，而非字面相似（系统另行检索到的参数详解可供参考）；"
        "每组矛盾的判断理由 rationale 用一句话简述（30字以内）。\n"
        f"{JSON_RULE}\n输出格式：\n"
        '{"pairs": [{"improving_concrete": "具体改善参数", "improving_param_id": 编号, '
        '"improving_param_name": "TRIZ参数名", "worsening_concrete": "具体恶化参数", '
        '"worsening_param_id": 编号, "worsening_param_name": "TRIZ参数名", '
        '"rationale": "矛盾判断理由"}], "key_pair_index": 最关键一组的序号(从0开始)}'
    )
    meta = {"task": "match_params", "problem": problem, "parameters": parameters}
    observe = f"{problem}\n{param_text}"
    return instruction, meta, observe


def _principle_knowledge_block(principle: Dict[str, Any], index: int = 0) -> str:
    """构造发明原则的三层启发知识块：释义/详解 → 可操作子方法 → 真实案例 few-shot。

    index 为候选序号：同一原则被多轮发散复用时，用它轮换真实案例集合，
    避免每个候选拿到完全相同的范例而同质化。
    """
    pid = principle["id"]
    lines = [
        f"原则{pid}：{principle['name']}",
        f"一句话释义：{principle['desc']}",
    ]
    detail = CS.principle_detail(pid)
    if detail and detail != principle["desc"]:
        lines.append(f"详解：{detail}")
    methods = CS.principle_methods(pid)
    if methods:
        lines.append("可操作子方法（括号内为应用示例）：")
        for m in methods[:5]:
            examples = "；".join(m.get("e", [])[:3])
            lines.append(f"- {m['t']}" + (f"（{examples}）" if examples else ""))

    real_cases = CS.cases_for_principle(pid, limit=2, offset=index)
    if real_cases:
        lines.append(
            "该原则的真实工程案例（英文原文；请体会其中原则如何落到具体结构与工作方式，"
            "只可跨领域类比其思路，禁止照抄案例的领域与具体结构）：")
        for i, c in enumerate(real_cases, 1):
            problem = c["problem"][:CS.PROBLEM_MAX]
            solution = c["solution"][:CS.SOLUTION_MAX]
            lines.append(
                f"案例{i}（来源：{c['lib_label']}）\n"
                f"· 问题情境：{problem}\n· 具体解法：{solution}")
    else:
        # 无真实案例的原则：用内置中文教科书案例兜底（40 原则全覆盖）
        textbook = CS.textbook_examples(pid)
        if textbook:
            lines.append("该原则的常见应用示例（仅作联想引子，你的方案必须针对当前问题原创、"
                         "不得直接套用这些示例）：" + "；".join(textbook))
    return "\n".join(lines)


def build_generate_solution(problem: str, principle: Dict[str, Any],
                            candidates: List[Dict[str, Any]], index: int):
    if candidates:
        existing = "\n".join(
            f"{i + 1}. {c.get('text', '')[:120]}" for i, c in enumerate(candidates)
        )
        existing_block = f"【已有候选方案（新方案必须与它们均显著不同）】\n{existing}\n\n"
    else:
        existing_block = "【目前尚无候选方案】\n\n"
    principle_block = _principle_knowledge_block(principle, index)
    instruction = (
        f"【问题描述】\n{problem}\n\n"
        f"【本轮使用的TRIZ发明原则及其启发材料】\n{principle_block}\n\n"
        f"{existing_block}"
        "请先体会上述子方法与真实案例中该原则的运用机理，再通过跨领域类比，"
        "针对当前问题生成一条全新的、具体可落地的设计方案，"
        "要明确结构/材料/工作方式/控制策略上的具体做法，200字以内。\n"
        f"{JSON_RULE}\n输出格式：\n"
        '{"title": "方案标题", "text": "方案完整描述"}'
    )
    meta = {"task": "generate_solution", "problem": problem,
            "principle": principle, "index": index,
            "candidates": [c.get("text", "") for c in candidates]}
    observe = f"{problem}\n发明原则：{principle['name']} {principle['desc']}"
    return instruction, meta, observe


def build_score(problem: str, solution_text: str, dimension: str, refined: bool = False):
    dim_meta = {
        "benefit": ("收益", "分数越高表示收益越大",
                    "进一步放大收益、或在后续修改中必须注意保持该收益的具体做法"),
        "cost": ("成本", "分数越高表示成本越高（越差）",
                 "从结构、材料、工艺或使用方式中选择切入点，给出降低该成本的具体调整方向"),
        "harm": ("副作用", "分数越高表示有害副作用越严重（越差）",
                 "针对该副作用的产生环节，给出具体的抑制或防护措施"),
    }[dimension]
    instruction = (
        f"【问题描述】\n{problem}\n\n"
        f"【待评估方案】\n{solution_text}\n\n"
        f"请仅从【{dim_meta[0]}】维度评估该方案（{dim_meta[1]}）：\n"
        "1) score：十分制整数评分（1-10）；\n"
        "2) reason：评分依据，必须引用方案中的具体设计/环节说明给分理由，"
        "不能只写笼统的好坏判断（60字以内）；\n"
        f"3) suggestion：针对性建议——{dim_meta[2]}（60字以内，要具体可操作）。\n"
        f"{JSON_RULE}\n输出格式：\n"
        '{"score": 数字, "reason": "评分依据", "suggestion": "针对性建议"}'
    )
    meta = {"task": "score_solution", "problem": problem,
            "solution": solution_text, "dimension": dimension, "refined": refined}
    observe = solution_text
    return instruction, meta, observe


def build_refine(problem: str, solution_text: str, suggestions: Dict[str, str],
                 index: int, user_hint: str = ""):
    sug_text = "\n".join(f"- {k}：{v}" for k, v in suggestions.items() if v)
    hint_block = (f"\n【用户特别提出的优化方向】\n{user_hint}\n"
                  "请优先响应用户提出的方向，再兼顾评审专家的建议。\n"
                  if user_hint else "")
    instruction = (
        f"【问题描述】\n{problem}\n\n"
        f"【待优化方案】\n{solution_text}\n\n"
        f"【评审专家的改进建议】\n{sug_text}\n"
        f"{hint_block}\n"
        "请在保留原方案核心创意的基础上，针对上述建议对方案进行微调优化，"
        "输出完整的优化后方案（240字以内），要体现具体的改进措施。\n"
        f"{JSON_RULE}\n输出格式：\n"
        '{"title": "优化方案标题", "text": "优化后方案完整描述"}'
    )
    meta = {"task": "refine_solution", "problem": problem,
            "solution": solution_text, "suggestions": suggestions, "index": index,
            "user_hint": user_hint}
    observe = f"{problem}\n{solution_text}"
    return instruction, meta, observe


def build_select_principles(problem: str, improving: Dict[str, Any], worsening: Dict[str, Any]):
    names = "\n".join(f"{pid}. {p['name']}：{p['desc']}" for pid, p in K.INVENTIVE_PRINCIPLES.items())
    instruction = (
        f"【问题描述】\n{problem}\n\n"
        f"【技术矛盾】改善参数：{improving['name']}；恶化参数：{worsening['name']}。\n"
        "经典矛盾矩阵对该矛盾未给出推荐原则，请从以下40个发明原则中选出最适合解决该矛盾的4个原则，"
        f"按优先级给出编号。\n【40个发明原则】\n{names}\n\n"
        f"{JSON_RULE}\n输出格式：\n"
        '{"principles": [编号, 编号, 编号, 编号], "rationale": "选择理由"}'
    )
    meta = {"task": "select_principles"}
    observe = f"{problem}\n改善{improving['name']} 恶化{worsening['name']}"
    return instruction, meta, observe
