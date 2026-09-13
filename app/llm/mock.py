# -*- coding: utf-8 -*-
"""内置演示 LLM。

无需 API Key 即可跑通 TRIZ-GPT 全流程：依据任务类型（meta.task）
以确定性规则生成结构化中文输出，语义能力有限但流程完整、结果稳定，
用于离线演示、开发调试与接口兜底。配置真实模型后本类不再被使用。
"""

import hashlib
import json
import re
from typing import Any, Dict, Iterator, List, Optional

import numpy as np

from ..triz import knowledge as K
from ..triz.vectors import local_embed
from .base import BaseLLM

# ---------------------------------------------------------------------------
# 问题分析：线索词 -> 具体参数 / TRIZ 工程参数
# ---------------------------------------------------------------------------
_IMPROVE_CUES = [
    (r"强度|坚固|结实|牢固|抗冲击|承重|刚性|抗风", "结构强度与抗冲击能力需要提升", 14),
    (r"速度|更快|快速|提速|响应时间|效率低", "运行速度与响应效率需要提高", 9),
    (r"可靠|稳定|故障|失效|寿命|耐久|耐用|老化|疲劳", "可靠性与使用寿命需要提升", 27),
    (r"精度|精确|准确|误差", "测量与控制精度需要提高", 28),
    (r"效率|生产率|产能|产量", "生产效率需要提高", 39),
    (r"自动化|无人值守|人工", "自动化程度需要提高以减少人工", 38),
    (r"散热|降温|过热|发热|温度高|高温", "需要改善散热并控制工作温度", 17),
    (r"操作|便携|方便|易用|携带", "操作与使用的方便性需要改善", 33),
    (r"通用|多功能|适应|兼容", "适应性与通用性需要增强", 35),
    (r"安全|防护|外部环境|腐蚀", "抵御外部有害因素的能力需要增强", 30),
]

_WORSEN_CUES = [
    (r"重量|很重|偏重|笨重|沉", "改进会导致物体重量增加", 1),
    (r"能耗|耗电|耗能|能量|功率|燃料|耗电", "能量消耗随之增加", 19),
    (r"成本|贵|昂贵|费用|材料多|耗材|造价", "成本与材料消耗上升", 26),
    (r"复杂|零件多|结构繁复", "装置的复杂性增加", 36),
    (r"体积|庞大|占.*空间|尺寸|空间", "体积与占用空间增大", 7),
    (r"噪音|噪声|污染|振动|副作用|辐射|有害", "会产生新的有害副作用", 31),
    (r"耗时|等待|时间长|慢", "造成额外的时间损失", 25),
    (r"维修|维护", "维护与维修变得困难", 34),
    (r"信息|数据|信号丢失", "存在信息损失风险", 24),
]

_TOPIC_RE = re.compile(r"[一-鿿]{0,6}(?:系统|装置|设备|结构|部件|机构|伞|杯|盘|架|车|机|器|泵|阀|电池|电机|门窗|门|窗|机器人|起落架|刹车)")


def _extract_topic(problem: str) -> str:
    m = _TOPIC_RE.search(problem)
    if m:
        return m.group(0)
    return "该装置"


def _scan_cues(problem: str):
    found = []
    for pattern, phrase, pid in _IMPROVE_CUES:
        if re.search(pattern, problem):
            found.append(("improve", phrase, pid))
    for pattern, phrase, pid in _WORSEN_CUES:
        if re.search(pattern, problem):
            found.append(("worsen", phrase, pid))
    return found


# ---------------------------------------------------------------------------
# 方案生成：40 个发明原则各自的「应用动作」模板
# ---------------------------------------------------------------------------
_PRINCIPLE_MOVES = {
    1: "将{topic}分解为若干可独立拆装的功能模块，各模块独立承载与损坏更换，按需增减单元",
    2: "把造成有害影响的部分从{topic}中抽取隔离，仅保留必要功能部件，消除干扰源",
    3: "改变{topic}的均匀结构，让不同部位具有不同属性、分别承担最适合的功能",
    4: "打破{topic}的对称布局，采用非对称外形改善受力与工作状态",
    5: "在空间和时间上合并{topic}中相同或相近的功能单元，协同完成动作",
    6: "让{topic}的一个部件同时承担多种功能，从而省去冗余部件",
    7: "采用嵌套式结构，将部件层层套叠，空闲时收缩收纳、工作时展开",
    8: "引入轻质材料或气动/流体升力结构补偿{topic}的重量",
    9: "预先施加反作用，在有害效应出现之前就布置抵消措施",
    10: "提前完成所需的改变或预先布置好相关部件，使{topic}在需要时即时发挥作用",
    11: "增设应急/备份手段，预先补偿{topic}可靠性不足的问题",
    12: "改变工作条件，使{topic}在作业过程中无需反复升降或移位，保持等势",
    13: "反向思考：让原本固定的部分可动、可动的部分固定，或倒置工作方式",
    14: "用曲面、滚轮、球体或旋转运动替代{topic}中的直线/平面结构",
    15: "使{topic}的关键特性可随工作阶段动态调节，把刚性连接改为可活动连接",
    16: "若难以一次达到理想效果，则先实现略低或略高的程度以大幅简化问题",
    17: "把单层结构改为多层堆叠，或倾斜布置、利用表面背面，向更高维空间发展",
    18: "让{topic}的工作部件产生受控振动，必要时提高到超声频率并利用共振",
    19: "用周期性脉冲动作替代连续动作，并在脉冲间隙安排其他有用动作",
    20: "消除空闲与间歇，让{topic}各部件持续满负荷地执行有用工作",
    21: "高速跃过容易产生有害效应的阶段，缩短有害作用时间",
    22: "把有害因素转化为有用效果，例如利用废热、废振实现辅助功能",
    23: "引入传感反馈回路，实时监测输出并自动调节{topic}的工作参数",
    24: "引入可移除的中间载体或中介过程来传递动作、隔离有害接触",
    25: "让{topic}利用自身废弃的能量与物质完成辅助维护，实现自服务",
    26: "用廉价、简单的光学/虚拟副本替代昂贵易碎的原件进行测量与观察",
    27: "用大量廉价的短寿命部件替代昂贵长寿命部件，用完即弃",
    28: "用光、声、磁、电等场作用替代{topic}中的机械接触与机械传动",
    29: "用充气、液压等气液结构替代固体部件实现支撑与传动",
    30: "用柔性壳体或薄膜替代三维刚性结构，并隔离外部环境",
    31: "采用多孔材料或在孔隙中预置功能物质，实现减重与功能增强",
    32: "改变{topic}关键部位的颜色、透明度等可视属性以改善状态识别",
    33: "让相互作用的部件采用相同或特性相近的材料，减少界面副反应",
    34: "让完成使命的部分自动溶解/蒸发消失，并在工作中自动恢复耗材",
    35: "改变关键部件的物理状态、浓度、柔性或温度等参数以获得所需特性",
    36: "利用相变过程中的体积变化与吸放热现象实现驱动或控温",
    37: "利用材料热膨胀/收缩动作，并组合不同膨胀系数的材料形成差动",
    38: "用富氧甚至臭氧环境替代普通空气强化氧化相关过程",
    39: "在{topic}中加入惰性介质或在惰性环境中工作，抑制有害反应",
    40: "由单一材料改为纤维增强等复合材料，兼顾轻质与高强度",
}

_ANGLES = [
    "从结构布局入手，重新组织各模块的空间关系与连接方式",
    "从材料选择入手，引入轻质高强或具备功能特性的新材料",
    "从控制策略入手，增加传感器与闭环反馈，实现自适应调节",
    "从工作时序入手，以周期性脉冲动作替代连续动作并利用间隙",
    "从资源利用入手，就地复用废热、废料与现有环境资源",
    "从人机交互入手，简化操作步骤并提供清晰的工作状态反馈",
    "从系统边界入手，将部分功能转移到相邻系统或环境中协同完成",
]


def _stable_int(text: str, mod: int) -> int:
    h = hashlib.md5(text.encode("utf-8")).hexdigest()
    return int(h[:10], 16) % mod


class MockLLM(BaseLLM):
    name = "内置演示模型"

    def embed(self, texts: List[str]) -> np.ndarray:
        return local_embed(texts)

    def chat(self, messages: List[Dict[str, str]], temperature: float = 0.7,
             meta: Optional[Dict[str, Any]] = None, json_format: bool = False) -> str:
        task = (meta or {}).get("task", "echo")
        handler = {
            "extract_params": self._extract_params,
            "match_params": self._match_params,
            "choose_pair": lambda m: json.dumps({"index": 0}, ensure_ascii=False),
            "select_principles": lambda m: json.dumps(
                {"principles": [15, 1, 28, 35], "rationale": "矩阵该单元为空，依据矛盾特点从40个发明原则中推断常用原则。"},
                ensure_ascii=False),
            "generate_solution": self._generate_solution,
            "score_solution": self._score_solution,
            "refine_solution": self._refine_solution,
        }.get(task, lambda m: json.dumps({"text": "演示模型未定义该任务。"}, ensure_ascii=False))
        return handler(meta or {})

    def chat_stream(self, messages: List[Dict[str, str]], temperature: float = 0.7,
                    meta: Optional[Dict[str, Any]] = None,
                    json_format: bool = False) -> Iterator[str]:
        full = self.chat(messages, temperature=temperature, meta=meta,
                         json_format=json_format)
        step = 20
        for i in range(0, len(full), step):
            yield full[i:i + step]

    # -- 任务处理 -----------------------------------------------------------
    def _extract_params(self, meta: Dict) -> str:
        problem = meta["problem"]
        cues = _scan_cues(problem)
        phrases = []
        for _, phrase, _ in cues:
            if phrase not in phrases:
                phrases.append(phrase)
        if len(phrases) < 2:
            phrases += ["需要在提升核心性能的同时控制附加代价", "方案应兼顾可行性与使用体验"]
        return json.dumps(
            {"parameters": phrases[:6],
             "summary": "从问题描述中识别出的关键工程评估参数如下。"},
            ensure_ascii=False)

    def _match_params(self, meta: Dict) -> str:
        problem = meta["problem"]
        cues = _scan_cues(problem)
        improves = [c for c in cues if c[0] == "improve"]
        worsens = [c for c in cues if c[0] == "worsen"]
        if not improves:
            improves = [("improve", "核心性能需要提升", 14)]
        if not worsens:
            worsens = [("worsen", "系统复杂性与成本可能增加", 36)]
        pairs = []
        for _, ip, iid in improves[:3]:
            for _, wp, wid in worsens[:3]:
                pairs.append({
                    "improving_concrete": ip,
                    "improving_param_id": iid,
                    "improving_param_name": K.ENGINEERING_PARAMETERS[iid]["name"],
                    "worsening_concrete": wp,
                    "worsening_param_id": wid,
                    "worsening_param_name": K.ENGINEERING_PARAMETERS[wid]["name"],
                    "rationale": f"「{ip}」对应改善参数 K{iid}，而「{wp}」对应恶化参数 K{wid}，二者构成技术矛盾。",
                })
                if len(pairs) >= 4:
                    break
            if len(pairs) >= 4:
                break
        return json.dumps(
            {"pairs": pairs, "key_pair_index": 0,
             "note": "已将具体参数匹配到39个TRIZ工程参数并组成矛盾对。"},
            ensure_ascii=False)

    def _generate_solution(self, meta: Dict) -> str:
        problem = meta["problem"]
        principle = meta["principle"]
        index = meta.get("index", 0)
        topic = _extract_topic(problem)
        move = _PRINCIPLE_MOVES.get(
            principle["id"],
            "依据「{name}」的启发对{topic}进行改造，把抽象原则类比迁移到具体结构中").format(
            topic=topic, name=principle["name"])
        angle = _ANGLES[index % len(_ANGLES)]
        text = (f"方案{index + 1}（运用发明原则「{principle['name']}」）：{move}。"
                f"具体而言，{angle}，形成可落地的设计：{topic}在保持所需性能提升的同时，"
                f"通过该设计抑制矛盾参数的恶化，预计可在不显著增加代价的前提下改善目标性能。")
        return json.dumps({"title": f"基于{principle['name']}的创新方案", "text": text},
                          ensure_ascii=False)

    def _score_solution(self, meta: Dict) -> str:
        solution = meta["solution"]
        dimension = meta["dimension"]
        refined = bool(meta.get("refined", False))
        seed = _stable_int(solution + dimension, 1000)
        if dimension == "benefit":
            score = 6 + seed % 4
            if refined:
                score = min(10, score + 1)
            level = "显著" if score >= 8 else "较好"
            reason = f"方案针对矛盾给出了明确的改进路径，核心结构直接作用于目标功能，预期收益{level}。"
            suggestion = "可进一步放大核心结构的作用范围，并在后续优化中优先保持这一收益点不被附加结构稀释。"
        elif dimension == "cost":
            score = 3 + seed % 4
            if refined:
                score = max(1, score - 1)
            level = "较高" if score >= 6 else "中等可控"
            reason = f"方案引入了附加结构与材料，实现所需的制造与维护成本{level}。"
            suggestion = "可将附加结构与原有部件一体化成型或改用更常见的材料/工艺，以降低制造与装配成本。"
        else:
            score = 2 + seed % 4
            if refined:
                score = max(1, score - 1)
            level = "较多" if score >= 5 else "较少且可控"
            reason = f"新增结构在运行中可能带来振动、噪音等次生效应，有害副作用{level}。"
            suggestion = "在连接环节增加缓冲/隔振结构或对称布置抵消附加载荷，以抑制主要副作用。"
        return json.dumps(
            {"score": int(score), "reason": reason, "suggestion": suggestion},
            ensure_ascii=False)

    def _refine_solution(self, meta: Dict) -> str:
        solution = meta["solution"]
        # 去除历史迭代中累积的优化标记与建议段落，避免文本无限堆叠
        base = re.sub(r"^【优化版】+", "", solution.strip())
        base = re.split(r"\s*针对评审意见", base)[0].strip()
        suggestions = meta.get("suggestions", {})
        bits = [v for v in suggestions.values() if isinstance(v, str)]
        suggestion_text = "；".join(bits[:2]) if bits else "进一步压缩成本并抑制副作用"
        angle = _ANGLES[meta.get("index", 0) % len(_ANGLES)]
        text = (f"【优化版】{base} "
                f"针对评审意见（{suggestion_text}）作出改进：{angle}；"
                f"同时简化非关键结构、复用现有资源，在保持收益的前提下降低代价与副作用，"
                f"使方案整体理想性进一步提升。")
        return json.dumps({"title": "优化后的设计方案", "text": text}, ensure_ascii=False)
