# -*- coding: utf-8 -*-
"""多智能体框架核心对象：Environment（环境黑板）与 Agent（智能体）。

对应论文第二章第三节的多 Agent 系统设计：
- Agent 包含三个模块：
  * 大脑模块——基于 LLM 的理解推理能力，以及身份信息与历史记忆；
  * 感知模块——观察函数通过 RAG 从环境中筛选关联信息加入记忆；
  * 行动模块——行动函数将 Agent 的行为信息（发言/结论）写入环境；
- Environment 承载公共信息，Agent 之间以环境为中介交流：每个 Agent
  从环境获取信息，又把新信息输入环境，告知其他 Agent。
"""

import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from ..llm.base import BaseLLM, parse_json_lenient, strip_thinking
from ..triz import knowledge as K
from ..triz.vectors import VectorStore

# 静态知识库（39 工程参数 + 40 发明原则）向量全局复用：
# 嵌入一次后所有会话共享，避免每次运行重复向量化 79 个知识块
_KNOWLEDGE_STORE: Optional[VectorStore] = None
_KNOWLEDGE_LOCK = threading.Lock()


def get_knowledge_store(llm: BaseLLM) -> VectorStore:
    global _KNOWLEDGE_STORE
    if _KNOWLEDGE_STORE is None:
        with _KNOWLEDGE_LOCK:
            if _KNOWLEDGE_STORE is None:
                store = VectorStore(embed_fn=llm.embed)
                store.add(K.all_parameter_chunks())
                store.add(K.all_principle_chunks())
                _KNOWLEDGE_STORE = store
    return _KNOWLEDGE_STORE


@dataclass
class Environment:
    """环境对象：公共黑板 + 向量知识库（RAG）+ 对话历史。"""

    llm: BaseLLM
    blackboard: Dict[str, Any] = field(default_factory=dict)
    messages: List[Dict[str, Any]] = field(default_factory=list)
    store: Optional[VectorStore] = None

    def __post_init__(self):
        # 内置 TRIZ 知识库：39 工程参数 + 40 发明原则（RAG 静态知识源），
        # 向量在全局单例中构建并复用（含磁盘缓存），会话间零重复向量化
        self.store = get_knowledge_store(self.llm)

    def observe(self, query: str, top_k: int = 6,
                types: Optional[tuple] = None) -> List[Dict[str, Any]]:
        """观察函数：以 RAG 方式从环境中检索与任务关联的知识。

        types 限定检索的知识块类型（"parameter"=39工程参数 / "principle"=40发明原则）；
        按 TRIZ 方法论，参数仅在参数匹配阶段检索，发明原则由矛盾矩阵查表确定，
        不应通过相似度检索混入。
        """
        return self.store.query(query, top_k=top_k, types=types)

    def write(self, key: str, value: Any) -> None:
        """行动函数：把结论写入公共黑板，供后续 Agent 使用。"""
        self.blackboard[key] = value

    def record_message(self, agent_name: str, agent_title: str, role: str,
                       content: str, refs: Optional[List[Dict]] = None) -> Dict:
        msg = {
            "agent": agent_name,
            "agent_title": agent_title,
            "role": role,
            "content": content,
            "refs": refs or [],
        }
        self.messages.append(msg)
        return msg


class Agent:
    """智能体对象：身份 + 记忆 + 感知（observe）+ 行动（act）。"""

    def __init__(self, name: str, title: str, role_prompt: str,
                 env: Environment, llm: BaseLLM):
        self.name = name
        self.title = title
        self.role_prompt = role_prompt
        self.env = env
        self.llm = llm
        # 记忆：身份信息 + 历史对话
        self.memory: List[Dict[str, str]] = [
            {"role": "system", "content": role_prompt}
        ]

    def observe(self, query: str, top_k: int = 6,
                types: Optional[tuple] = None) -> List[Dict[str, Any]]:
        """感知模块：RAG 检索关联知识，结果加入记忆。"""
        refs = self.env.observe(query, top_k=top_k, types=types)
        if refs:
            context = "\n".join(f"- {r['text']}" for r in refs)
            self.memory.append({
                "role": "system",
                "content": f"【检索到的相关TRIZ知识】\n{context}",
            })
        return refs

    def observe_and_act(self, observe_query: str, instruction: str,
                        meta: Optional[Dict] = None, json_mode: bool = True,
                        temperature: float = 0.5, top_k: int = 6,
                        scope: Optional[str] = None,
                        on_delta: Optional[Callable[[str], None]] = None) -> Dict[str, Any]:
        """先感知（RAG 检索关联知识）再行动（推理并写入环境）。

        scope 控制知识检索范围（TRIZ 方法论约束）：
        - None：不检索。所需知识已由固定程序/指令提供——发明原则来自矛盾矩阵
          查表（原则全文随指令注入），40 原则清单已内置于方法学家指令，
          评审与优化不直接引用知识库；
        - "parameter"：仅检索 39 个工程参数块（参数匹配专家使用）；
        - "principle"：仅检索 40 个发明原则块。
        """
        if scope is None:
            refs: List[Dict[str, Any]] = []
        else:
            refs = self.observe(observe_query, top_k=top_k, types=(scope,))
        return self.act(instruction, meta=meta, json_mode=json_mode,
                        temperature=temperature, refs=refs, on_delta=on_delta)

    def act(self, instruction: str, meta: Optional[Dict] = None,
            json_mode: bool = True, temperature: float = 0.5,
            refs: Optional[List[Dict]] = None,
            on_delta: Optional[Callable[[str], None]] = None) -> Dict[str, Any]:
        """行动模块：依据记忆与指令进行推理，并把发言写入环境。

        返回 {"parsed": 结构化结果, "raw": 原始文本, "refs": 引用知识}。
        on_delta 给定时，模型原始输出以流式块回调，用于前端流式展示。
        """
        self.memory.append({"role": "user", "content": instruction})
        if on_delta is not None:
            chunks: List[str] = []
            for piece in self.llm.chat_stream(self.memory, temperature=temperature,
                                              meta=meta, json_format=json_mode):
                chunks.append(piece)
                on_delta(piece)
            raw = "".join(chunks)
        else:
            raw = self.llm.chat(self.memory, temperature=temperature, meta=meta,
                                json_format=json_mode)
        # 思考链仅用于实时展示，不写入记忆/黑板：避免多轮调用时 prompt 随推理文本膨胀
        clean_raw = strip_thinking(raw).strip()
        self.memory.append({"role": "assistant", "content": clean_raw or raw})

        parsed: Any = None
        if json_mode:
            try:
                parsed = parse_json_lenient(raw)
            except Exception:
                parsed = None

        self.env.record_message(self.name, self.title, "assistant",
                                clean_raw or raw, refs=refs)
        return {"parsed": parsed, "raw": raw, "refs": refs or []}
