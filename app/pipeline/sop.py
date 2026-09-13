# -*- coding: utf-8 -*-
"""SOP（标准化操作程序）：多智能体流水线编排。

对应论文第三章第一节「系统集成与SOP定义」：整体任务分解为四个模块，
模块间以前一模块输出作为后一模块输入，状态按有限状态机方式转移：

    识别矛盾  ->  方案生成(发散)  ->  方案评估(收拢)  ->  优化迭代

- 识别矛盾：参数提取师抽取具体参数 -> TRIZ匹配专家抽象为39工程参数并
  组成矛盾对 -> （人工干预点：用户可选定矛盾对，或交由系统自动选择）
  -> 固定程序查询矛盾矩阵得到发明原则；
- 方案生成：创意设计师依据原则发散生成 k 条差异化方案，词袋嵌入 +
  PCA + 凸包体积监控设计空间扩展（双钻模型「先发散」，Algorithm 1）；
- 方案评估：收益/成本/副作用三组专家十分制打分，按理想性
  Ideality = Σ收益 / (Σ成本 + Σ副作用) 收拢选优（Algorithm 1 收尾）；
- 优化迭代：评审专家给分与改进建议 -> 优化设计师微调方案，LLM 作为
  优化器迭代至收敛（元认知理论，Algorithm 2）。
"""

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional

from ..agents.framework import Agent, Environment
from ..agents import roles
from ..llm.base import (BaseLLM, ThinkStreamSplitter, parse_json_partial)
from ..triz import knowledge as K
from ..triz.vectors import (compare_design_space_expansion, keyword_bag,
                           series_volumes)

Emit = Callable[[Dict[str, Any]], Awaitable[None]]

PHASES = [
    {"index": 0, "name": "识别矛盾", "desc": "提取工程参数，匹配TRIZ矛盾对，查询发明原则"},
    {"index": 1, "name": "方案生成", "desc": "依据发明原则发散生成多样化候选方案（凸包监控设计空间）"},
    {"index": 2, "name": "方案评估", "desc": "收益/成本/副作用三组专家按理想性评分收拢"},
    {"index": 3, "name": "优化迭代", "desc": "专家建议驱动方案微调，迭代至评分收敛"},
]


@dataclass
class PipelineConfig:
    candidates_k: int = 6          # 发散阶段目标方案数
    gen_fail_t: int = 3            # 发散阶段连续未扩展容忍次数
    opt_iter_n: int = 4            # 优化阶段最大迭代次数
    opt_fail_t: int = 2            # 优化阶段连续无提升容忍次数
    auto_mode: bool = True         # 是否全自动（False 时在矛盾选择处暂停等人机交互）


@dataclass
class RunControl:
    """人机交互控制通道：SOP 在关键节点暂停，等待用户决策。"""
    queue: "asyncio.Queue[Dict[str, Any]]" = field(default_factory=asyncio.Queue)

    async def wait_choice(self) -> Dict[str, Any]:
        return await self.queue.get()


class TRIZPipeline:
    def __init__(self, llm: BaseLLM, config: Optional[PipelineConfig] = None):
        self.llm = llm
        self.config = config or PipelineConfig()
        self.env = Environment(llm=llm)
        self._agents: Dict[str, Agent] = {}

    def agent(self, role_key: str) -> Agent:
        if role_key not in self._agents:
            title_map = {
                "analyst": "问题参数提取师",
                "matcher": "TRIZ参数匹配专家",
                "engineer": "创意设计师",
                "expert_benefit": "收益评审专家",
                "expert_cost": "成本评审专家",
                "expert_harm": "副作用评审专家",
                "refiner": "优化设计师",
                "methodologist": "TRIZ方法学家",
            }
            self._agents[role_key] = Agent(
                name=role_key,
                title=title_map[role_key],
                role_prompt=roles.ROLES[role_key],
                env=self.env,
                llm=self.llm,
            )
        return self._agents[role_key]

    # ------------------------------------------------------------------
    async def _call(self, role_key: str, builder, emit: Emit,
                    render: Callable[[Dict], str], scope: Optional[str] = None,
                    resolve_refs: Optional[Callable[[Dict[str, Any]], List[Dict[str, Any]]]] = None,
                    **builder_kwargs) -> Dict[str, Any]:
        """执行一次 Agent 行动（在线程中调用 LLM），并广播对话消息。

        scope 限定该角色的 RAG 知识检索域（None=不检索，"parameter"=仅工程参数）。
        resolve_refs 给定时，气泡展示的知识引用由最终结论反查（与结论同源），
        而非 RAG 检索的 top-k——例如参数匹配专家的 refs 即其最终选定的矛盾参数。
        """
        instruction, meta, observe_query = builder(**builder_kwargs)
        agent = self.agent(role_key)
        await emit({"type": "agent_start", "agent": role_key,
                    "agent_title": agent.title})

        queue: "asyncio.Queue[tuple]" = asyncio.Queue()
        splitter = ThinkStreamSplitter()
        last_preview = None
        t_start = time.perf_counter()

        def _on_delta(piece: str) -> None:
            ev = splitter.feed(piece)
            if ev["think_started"]:
                queue.put_nowait(("think_start", None))
            if ev["think_delta"]:
                queue.put_nowait(("think_delta", ev["think_delta"]))
            if ev["think_done"]:
                queue.put_nowait(("think_done", None))
            if ev["answer"] is not None:
                try:
                    preview = render(parse_json_partial(ev["answer"]))
                except Exception:
                    preview = None
                if preview and preview.strip():
                    queue.put_nowait(("delta", preview))

        async def _drain(thread_task) -> Dict[str, Any]:
            nonlocal last_preview
            while True:
                kind, payload = await queue.get()
                if kind == "think_start":
                    await emit({"type": "thinking_start", "agent": role_key,
                                "agent_title": agent.title})
                elif kind == "think_delta":
                    await emit({"type": "thinking_delta", "agent": role_key,
                                "agent_title": agent.title, "text": payload})
                elif kind == "think_done":
                    await emit({"type": "thinking_done", "agent": role_key,
                                "agent_title": agent.title})
                elif kind == "delta":
                    if payload != last_preview:
                        last_preview = payload
                        await emit({"type": "message_delta", "agent": role_key,
                                    "agent_title": agent.title, "text": payload})
                elif kind == "done":
                    await thread_task
                    return payload
                else:
                    await thread_task
                    raise payload

        def _work():
            try:
                res = agent.observe_and_act(
                    observe_query, instruction, meta=meta,
                    json_mode=True, temperature=0.6, scope=scope,
                    on_delta=_on_delta)
                queue.put_nowait(("done", res))
            except Exception as exc:  # noqa: BLE001
                queue.put_nowait(("error", exc))

        result = await _drain(asyncio.create_task(asyncio.to_thread(_work)))
        parsed = result["parsed"]
        retried = False
        if parsed is None:
            # 容错：要求只输出 JSON 重试一次（同样走流式，过程对用户可见）
            retried = True
            await emit({"type": "message", "agent": role_key,
                        "agent_title": agent.title,
                        "content": "（首轮输出未通过格式校验，正在重新整理…）",
                        "refs": []})
            splitter = ThinkStreamSplitter()
            last_preview = None
            retry_instruction = instruction + "\n（上次输出无法解析为JSON，请本次只输出JSON。）"

            def _retry():
                try:
                    res = agent.act(retry_instruction, meta=meta, json_mode=True,
                                    temperature=0.3, refs=result["refs"],
                                    on_delta=_on_delta)
                    queue.put_nowait(("done", res))
                except Exception as exc:  # noqa: BLE001
                    queue.put_nowait(("error", exc))

            result = await _drain(asyncio.create_task(asyncio.to_thread(_retry)))
            parsed = result["parsed"]
        elapsed = time.perf_counter() - t_start
        print(f"[SOP] {agent.title}（{role_key}）耗时 {elapsed:.1f}s"
              f"{'（含一次格式重试）' if retried else ''}")
        if parsed is None:
            parsed = {}
            await emit({"type": "message", "agent": role_key,
                        "agent_title": agent.title,
                        "content": "（该步模型输出解析失败，已按兜底策略继续。）",
                        "refs": []})
        else:
            refs_source = resolve_refs(parsed) if resolve_refs is not None else result.get("refs", [])
            await emit({"type": "message", "agent": role_key,
                        "agent_title": agent.title,
                        "content": render(parsed),
                        "thinking_chars": len(splitter.think_text or ""),
                        "refs": [{"type": r.get("type"), "ref_id": r.get("ref_id"),
                                  "name": r.get("name", ""),
                                  "text": r.get("text", "")[:80]}
                                 for r in (refs_source or [])[:6]]})
        return parsed

    # ------------------------------------------------------------------
    async def run(self, problem: str, emit: Emit, control: RunControl,
                  resume: Optional[Dict[str, Any]] = None) -> None:
        cfg = self.config
        try:
            if resume is not None:
                await self._run_resumed(problem, emit, control, resume)
                return
            await emit({"type": "system", "content": "系统已启动，多智能体协作流水线开始运行。"})

            # 阶段一：识别矛盾 -----------------------------------------
            await emit({"type": "phase", **PHASES[0]})
            pair = await self._stage_contradiction(problem, emit, control)

            # 阶段二：方案生成（发散） ---------------------------------
            await emit({"type": "phase", **PHASES[1]})
            candidates = await self._stage_generation(problem, pair, emit)

            # 阶段三：方案评估（收拢） ---------------------------------
            await emit({"type": "phase", **PHASES[2]})
            scored, best_idx = await self._stage_evaluation(problem, candidates, emit)

            # 人工干预点：候选方案定夺（含编辑后重评、直接采纳）
            outcome = await self._decide_candidate(
                problem, scored, best_idx, emit, control, auto_mode=cfg.auto_mode)
            if outcome["action"] == "accept_final":
                await emit({"type": "card", "card": "final",
                            **self._final_from_candidate(outcome["chosen"], 0)})
                await emit({"type": "done"})
                return

            # 阶段四：优化迭代 -----------------------------------------
            await emit({"type": "phase", **PHASES[3]})
            final = await self._stage_optimization(
                problem, outcome["scored"], outcome["best_idx"], emit, control,
                auto_rest=outcome["auto_rest"])

            await emit({"type": "card", "card": "final", **final})
            await emit({"type": "done"})
        except Exception as exc:  # noqa: BLE001
            import traceback
            traceback.print_exc()
            await emit({"type": "error", "message": str(exc)})

    # ------------------------------------------------------------------
    # 分叉恢复：从检查点跳过已完成阶段，在分叉阶段重新暂停等待决策
    # ------------------------------------------------------------------
    async def _run_resumed(self, problem: str, emit: Emit, control: RunControl,
                           resume: Dict[str, Any]) -> None:
        stage = resume["stage"]
        st = resume["state"]
        stage_names = {"pair": "矛盾选择", "candidate": "方案定夺", "iterate": "优化迭代"}
        await emit({"type": "system",
                    "content": f"🌿 新分支已从「{stage_names.get(stage, stage)}」检查点恢复，"
                               f"分叉前的过程沿用原分支记录，请在此分支上做出你的决策。"})

        if stage == "pair":
            pair = await self._decide_pair(
                st.get("pairs", []), emit, control,
                auto_mode=False, key_idx=0)
            pair = await self._finalize_pair(problem, pair, emit)

            await emit({"type": "phase", **PHASES[1]})
            candidates = await self._stage_generation(problem, pair, emit)
            await emit({"type": "phase", **PHASES[2]})
            scored, best_idx = await self._stage_evaluation(problem, candidates, emit)
            outcome = await self._decide_candidate(
                problem, scored, best_idx, emit, control, auto_mode=False)
            if outcome["action"] == "accept_final":
                await emit({"type": "card", "card": "final",
                            **self._final_from_candidate(outcome["chosen"], 0)})
                await emit({"type": "done"})
                return
            await emit({"type": "phase", **PHASES[3]})
            final = await self._stage_optimization(
                problem, outcome["scored"], outcome["best_idx"], emit, control,
                auto_rest=False)

        elif stage == "candidate":
            scored = st.get("scored", [])
            best_idx = st.get("best_idx", 0)
            outcome = await self._decide_candidate(
                problem, scored, best_idx, emit, control, auto_mode=False)
            if outcome["action"] == "accept_final":
                await emit({"type": "card", "card": "final",
                            **self._final_from_candidate(outcome["chosen"], 0)})
                await emit({"type": "done"})
                return
            await emit({"type": "phase", **PHASES[3]})
            final = await self._stage_optimization(
                problem, outcome["scored"], outcome["best_idx"], emit, control,
                auto_rest=False)

        else:  # iterate：携带优化中途状态热启动
            warm = {
                "best": st["best"], "series": st.get("series", []),
                "iterations": st.get("iterations", 0),
                "failures": st.get("failures", 0),
                "last_refined": st.get("last_refined"),
                "last_round": st.get("last_round"),
            }
            await emit({"type": "phase", **PHASES[3]})
            final = await self._stage_optimization(
                problem, [warm["best"]], 0, emit, control,
                auto_rest=False, warm=warm)

        await emit({"type": "card", "card": "final", **final})
        await emit({"type": "done"})

    # ------------------------------------------------------------------
    # 阶段一：识别矛盾
    # ------------------------------------------------------------------
    async def _stage_contradiction(self, problem: str, emit: Emit,
                                   control: RunControl) -> Dict[str, Any]:
        extracted = await self._call(
            "analyst", roles.build_extract_params, emit,
            render=lambda p: "我从问题描述中识别出以下关键工程评估参数：\n" +
                            "\n".join(f"· {x}" for x in p.get("parameters", [])),
            problem=problem)
        parameters = extracted.get("parameters", []) or ["核心性能提升", "附加代价控制"]
        await emit({"type": "card", "card": "params",
                    "parameters": parameters, "summary": extracted.get("summary", "")})

        matched = await self._call(
            "matcher", roles.build_match_params, emit,
            render=lambda p: self._render_pairs(p.get("pairs", [])),
            scope="parameter", resolve_refs=self._matcher_refs,
            problem=problem, parameters=parameters)
        pairs = matched.get("pairs", [])
        if not pairs:  # 兜底
            pairs = [{
                "improving_concrete": "核心性能需要提升", "improving_param_id": 14,
                "improving_param_name": K.ENGINEERING_PARAMETERS[14]["name"],
                "worsening_concrete": "装置复杂性与成本增加", "worsening_param_id": 36,
                "worsening_param_name": K.ENGINEERING_PARAMETERS[36]["name"],
                "rationale": "兜底矛盾对。",
            }]
        await emit({"type": "card", "card": "contradictions",
                    "pairs": pairs, "key_pair_index": matched.get("key_pair_index", 0)})

        # 检查点：矛盾选择（供分叉恢复；自动/手动模式均持久化）
        await emit({"type": "checkpoint", "stage": "pair",
                    "state": {"problem": problem, "parameters": parameters, "pairs": pairs}})

        pair = await self._decide_pair(
            pairs, emit, control,
            auto_mode=self.config.auto_mode,
            key_idx=int(matched.get("key_pair_index", 0) or 0))
        pair = await self._finalize_pair(problem, pair, emit)
        return pair

    async def _decide_pair(self, pairs: List[Dict[str, Any]], emit: Emit,
                           control: RunControl, auto_mode: bool,
                           key_idx: int) -> Dict[str, Any]:
        """人工干预点：矛盾对选择/编辑/自定义。返回最终选定的矛盾对 dict。"""
        if auto_mode:
            idx = min(max(key_idx, 0), len(pairs) - 1)
            return pairs[idx]
        await emit({"type": "pause", "stage": "pair", "pairs": pairs})
        choice = await control.wait_choice()
        pair = self._pair_from_choice(pairs, choice, key_idx)
        await emit({"type": "system",
                    "content": f"用户已选定矛盾对：改善「{pair['improving_param_name']}」"
                               f"⇄ 恶化「{pair['worsening_param_name']}」。"})
        return pair

    @staticmethod
    def _pair_from_choice(pairs: List[Dict[str, Any]], choice: Dict[str, Any],
                          key_idx: int) -> Dict[str, Any]:
        """解析矛盾决策：auto / choose(index) / 编辑既有对 / 自定义新对。

        编辑与自定义的 pair 载荷形如：
        {"improving_param_id": 15, "worsening_param_id": 1,
         "improving_concrete": "...", "worsening_concrete": "..."}
        """
        if choice.get("action") == "auto":
            return pairs[min(max(key_idx, 0), len(pairs) - 1)]
        edited = choice.get("pair")
        if edited:
            pair = TRIZPipeline._build_editor_pair(edited)
            idx = choice.get("index")
            if idx is not None:
                pairs[min(max(int(idx), 0), len(pairs) - 1)] = pair
            else:
                pairs.append(pair)
            return pair
        idx = min(max(int(choice.get("index", 0)), 0), len(pairs) - 1)
        return pairs[idx]

    @staticmethod
    def _build_editor_pair(d: Dict[str, Any]) -> Dict[str, Any]:
        """把用户编辑/自定义的矛盾载荷校验并补全为标准矛盾对（名称取自39参数目录）。"""
        try:
            imp = int(d.get("improving_param_id"))
            wor = int(d.get("worsening_param_id"))
        except (TypeError, ValueError):
            raise ValueError("矛盾对参数编号无效，请从39个工程参数目录中选择。")
        if imp not in K.ENGINEERING_PARAMETERS or wor not in K.ENGINEERING_PARAMETERS:
            raise ValueError("矛盾对参数编号超出39个工程参数范围。")
        if imp == wor:
            raise ValueError("改善参数与恶化参数不能相同。")
        return {
            "improving_concrete": (d.get("improving_concrete") or "").strip()
                                   or f"希望改善：{K.parameter_name(imp)}",
            "improving_param_id": imp,
            "improving_param_name": K.parameter_name(imp),
            "worsening_concrete": (d.get("worsening_concrete") or "").strip()
                                   or f"可能恶化：{K.parameter_name(wor)}",
            "worsening_param_id": wor,
            "worsening_param_name": K.parameter_name(wor),
            "rationale": (d.get("rationale") or "用户编辑/自定义的矛盾对。").strip(),
            "edited": True,
        }

    async def _finalize_pair(self, problem: str, pair: Dict[str, Any],
                             emit: Emit) -> Dict[str, Any]:
        """矛盾对确定后的固定程序：矩阵查表（空单元则方法学家推断）并下发原则卡片。"""
        await emit({"type": "card", "card": "selected_pair", "pair": pair})
        principle_ids = K.lookup_principles(
            pair["improving_param_id"], pair["worsening_param_id"])
        source = "matrix"
        if not principle_ids:
            method = await self._call(
                "methodologist", roles.build_select_principles, emit,
                render=lambda p: f"矩阵对该矛盾未给出原则，我推断最适用的原则为："
                                 f"编号 {p.get('principles', [])}。{p.get('rationale', '')}",
                problem=problem,
                improving={"name": pair.get("improving_param_name", "")},
                worsening={"name": pair.get("worsening_param_name", "")})
            principle_ids = method.get("principles", [15, 1, 28, 35])[:4]
            source = "inferred"
        principles = [K.principle_info(pid) for pid in principle_ids]
        pair["principles"] = principles
        await emit({"type": "card", "card": "principles",
                    "principles": principles, "source": source,
                    "pair": {"improving": pair.get("improving_param_name"),
                             "worsening": pair.get("worsening_param_name")}})
        return pair

    @staticmethod
    def _render_pairs(pairs: List[Dict]) -> str:
        lines = ["我将具体参数抽象为TRIZ工程参数，识别出以下技术矛盾："]
        for i, p in enumerate(pairs):
            lines.append(
                f"{i + 1}. 改善「{p.get('improving_concrete')}」→ "
                f"K{p.get('improving_param_id')} {p.get('improving_param_name')}；"
                f"恶化「{p.get('worsening_concrete')}」→ "
                f"K{p.get('worsening_param_id')} {p.get('worsening_param_name')}。"
                f"理由：{p.get('rationale', '')}")
        return "\n".join(lines)

    @staticmethod
    def _matcher_refs(parsed: Dict[str, Any]) -> List[Dict[str, Any]]:
        """参数匹配专家气泡的知识引用：即其最终选定的矛盾参数（与结论同源）。

        RAG 检索仅用于辅助模型理解参数物理本质；展示给用户的引用必须与
        pairs 中的改善/恶化参数严格一致，去重后按矛盾对顺序返回。
        """
        ids: List[int] = []
        for pair in parsed.get("pairs", []) or []:
            for key in ("improving_param_id", "worsening_param_id"):
                try:
                    pid = int(pair.get(key))
                except (TypeError, ValueError):
                    continue
                if pid not in ids:
                    ids.append(pid)
        refs = []
        for pid in ids:
            ref = K.parameter_ref(pid)
            if ref:
                refs.append(ref)
        return refs

    # ------------------------------------------------------------------
    # 阶段二：方案生成（发散 + 设计空间监控）
    # ------------------------------------------------------------------
    async def _stage_generation(self, problem: str, pair: Dict,
                                emit: Emit) -> List[Dict[str, Any]]:
        cfg = self.config
        principles = pair["principles"]
        candidates: List[Dict[str, Any]] = []
        bag: List[str] = []
        snapshots: List[List[str]] = []
        volume_series: List[float] = []
        failures = 0
        round_idx = 0

        while len(candidates) < cfg.candidates_k and failures < cfg.gen_fail_t:
            principle = principles[round_idx % len(principles)]
            parsed = await self._call(
                "engineer", roles.build_generate_solution, emit,
                render=lambda p: f"【{p.get('title', '候选方案')}】\n{p.get('text', '')}",
                problem=problem, principle=principle,
                candidates=candidates, index=len(candidates))
            text = parsed.get("text", "")
            title = parsed.get("title", f"候选方案{len(candidates) + 1}")
            if not text:
                failures += 1
                round_idx += 1
                continue

            # 词袋 + 凸包体积监控（Algorithm 1 第 4-8 行）
            # 共享 PCA 基判断本步设计空间是否扩展
            new_keywords = keyword_bag(text)
            _, _, expanded = compare_design_space_expansion(
                bag, new_keywords, embed_fn=self.llm.embed)
            if expanded:
                failures = 0
                bag = list(dict.fromkeys(bag + new_keywords))
            else:
                failures += 1
            # 记录累积词袋快照，并用统一 PCA 基重算全过程凸包体积（跨步骤可比）
            snapshots.append(list(bag))
            volume_series = series_volumes(snapshots, embed_fn=self.llm.embed)
            volume = volume_series[-1] if volume_series else 0.0
            base_volume = volume_series[0] if volume_series and volume_series[0] > 0 else 1.0

            candidate = {
                "index": len(candidates),
                "title": title,
                "text": text,
                "principle_id": principle["id"],
                "principle_name": principle["name"],
                "hull_volume": volume,
                "expanded": expanded,
                "bag_size": len(bag),
            }
            candidates.append(candidate)
            await emit({"type": "card", "card": "candidate", **candidate,
                        "volume_series": volume_series,
                        "volume_ratio": (volume / base_volume) if base_volume else 1.0,
                        "failures": failures})
            round_idx += 1

        ratio = (volume_series[-1] / base_volume) if volume_series else 1.0
        await emit({"type": "card", "card": "hull_summary",
                    "series": volume_series, "ratio": round(ratio, 3)})
        await emit({"type": "system",
                    "content": f"发散结束：共生成 {len(candidates)} 条候选方案，"
                               f"词袋设计空间相对初始扩大至 {ratio:.2f} 倍。"})
        return candidates

    # ------------------------------------------------------------------
    # 阶段三：方案评估（理想性评分收拢）
    # ------------------------------------------------------------------
    async def _score_one(self, problem: str, text: str, emit: Emit,
                         refined: bool = False,
                         announce: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """组织三位评审专家并行打分。

        announce 给定时，在打分完成后以「专家评审组」聚合气泡广播一条消息，
        气泡携带三位专家各自的评分依据与针对性建议（reviews），供前端展开查看：
        - {"kind": "candidate", "index", "title"}：初评候选方案；
        - {"kind": "pre_refine", "title"}：优化前评审（建议驱动本轮精化）；
        - {"kind": "rescore_edit", "title"}：用户手改方案后的重新评分。
        """
        dims = [
            ("expert_benefit", "benefit", "收益"),
            ("expert_cost", "cost", "成本"),
            ("expert_harm", "harm", "副作用"),
        ]
        await emit({"type": "agent_start", "agent": "panel",
                    "agent_title": "专家评审组（收益 · 成本 · 副作用）"})

        async def _one(role, dim, label):
            parsed = await self._call(
                role, roles.build_score, self._silent_emit,
                render=lambda p: f"{label}评分：{p.get('score', '?')}/10。"
                                 f"{p.get('reason', p.get('comment', ''))}",
                problem=problem, solution_text=text, dimension=dim, refined=refined)
            return dim, parsed

        results = await asyncio.gather(*[_one(r, d, l) for r, d, l in dims])
        scores = {d: p for d, p in results}

        def _num(dim: str, default: float) -> float:
            try:
                return float(scores.get(dim, {}).get("score", default) or default)
            except (TypeError, ValueError):
                return default

        benefit = _num("benefit", 7)
        cost = _num("cost", 5)
        harm = _num("harm", 4)
        ideality = benefit / max(cost + harm, 1.0)

        # 结构化评审明细：reason 兜底旧字段 comment（兼容历史模型输出）
        label_map = {"benefit": "收益评审专家", "cost": "成本评审专家",
                     "harm": "副作用评审专家"}
        score_map = {"benefit": benefit, "cost": cost, "harm": harm}
        reviews: List[Dict[str, Any]] = []
        comments: Dict[str, str] = {}
        for dim in ("benefit", "cost", "harm"):
            p = scores.get(dim, {}) or {}
            reason = str(p.get("reason") or p.get("comment") or "").strip()
            suggestion = str(p.get("suggestion") or "").strip()
            reviews.append({"dim": dim, "label": label_map[dim],
                            "score": score_map[dim],
                            "reason": reason, "suggestion": suggestion})
            # 交给优化设计师的改进建议：依据 + 可操作建议（键用中文维度名）
            comments[("收益" if dim == "benefit"
                      else "成本" if dim == "cost" else "副作用")] = (
                reason + (f" 改进建议：{suggestion}" if suggestion else ""))

        if announce is not None:
            tail = f"（收益{benefit:.0f}/成本{cost:.0f}/副作用{harm:.0f}）"
            kind = announce.get("kind", "candidate")
            if kind == "candidate":
                content = (f"方案{announce['index'] + 1}《{announce['title']}》"
                           f"理想性得分 {ideality:.2f}{tail}。")
            elif kind == "pre_refine":
                content = (f"优化前评审：当前最优方案《{announce.get('title', '')}》"
                           f"理想性 {ideality:.2f}{tail}，三位专家的具体意见与改进建议如下，"
                           f"优化设计师将据此精化方案。")
            else:  # rescore_edit
                content = (f"修改版《{announce.get('title', '')}》重新评分："
                           f"理想性 {ideality:.2f}{tail}。")
            await emit({"type": "message", "agent": "panel",
                        "agent_title": "专家评审组", "content": content,
                        "reviews": reviews})

        return {
            "benefit": benefit, "cost": cost, "harm": harm, "ideality": ideality,
            "comments": comments, "reviews": reviews,
        }

    async def _silent_emit(self, _event: Dict[str, Any]) -> None:
        pass

    async def _stage_evaluation(self, problem: str, candidates: List[Dict],
                                emit: Emit):
        scored = []
        for cand in candidates:
            result = await self._score_one(
                problem, cand["text"], emit,
                announce={"kind": "candidate", "index": cand["index"],
                          "title": cand["title"]})
            item = dict(cand)
            item.update(result)
            scored.append(item)
            await emit({"type": "card", "card": "score_item", **{
                "index": item["index"], "title": item["title"],
                "benefit": result["benefit"], "cost": result["cost"],
                "harm": result["harm"], "ideality": round(result["ideality"], 3),
                "reviews": result["reviews"]}})
        best_idx = max(range(len(scored)), key=lambda i: scored[i]["ideality"])
        await emit({"type": "card", "card": "scores",
                    "items": [{
                        "index": s["index"], "title": s["title"],
                        "benefit": s["benefit"], "cost": s["cost"], "harm": s["harm"],
                        "ideality": round(s["ideality"], 3)} for s in scored],
                    "best_index": best_idx})
        return scored, best_idx

    # ------------------------------------------------------------------
    # 人工干预点：候选方案定夺（选择基线 / 直接采纳 / 编辑后优化 / 交自动）
    # ------------------------------------------------------------------
    async def _decide_candidate(self, problem: str, scored: List[Dict[str, Any]],
                                best_idx: int, emit: Emit, control: RunControl,
                                auto_mode: bool) -> Dict[str, Any]:
        """返回 {"action": "continue", "scored", "best_idx", "auto_rest"}
        或 {"action": "accept_final", "chosen"}。"""
        # 检查点：方案定夺（scored 含方案全文与评分，供分叉恢复）
        await emit({"type": "checkpoint", "stage": "candidate",
                    "state": {"problem": problem,
                              "scored": [{k: s.get(k) for k in
                                          ("index", "title", "text", "principle_id",
                                           "principle_name", "benefit", "cost",
                                           "harm", "ideality")} for s in scored],
                              "best_idx": best_idx}})
        if auto_mode:
            return {"action": "continue", "scored": scored,
                    "best_idx": best_idx, "auto_rest": True}

        await emit({"type": "pause", "stage": "candidate",
                    "best_index": best_idx,
                    "candidates": [{
                        "index": s["index"], "title": s.get("title", ""),
                        "principle_id": s.get("principle_id"),
                        "principle_name": s.get("principle_name"),
                        "ideality": round(s["ideality"], 3),
                        "benefit": s["benefit"], "cost": s["cost"], "harm": s["harm"],
                    } for s in scored]})
        decision = await control.wait_choice()
        act = decision.get("action")

        if act == "auto":
            return {"action": "continue", "scored": scored,
                    "best_idx": best_idx, "auto_rest": True}

        if act == "accept":
            idx = min(max(int(decision.get("index", best_idx)), 0), len(scored) - 1)
            chosen = scored[idx]
            await emit({"type": "system",
                        "content": f"用户直接采纳方案 {idx + 1}《{chosen.get('title', '')}》"
                                   f"为最终方案，跳过优化迭代。"})
            return {"action": "accept_final", "chosen": chosen}

        if act == "choose_edit":
            # 人工修改方案正文：以修改版重新评分作为优化基线
            idx = min(max(int(decision.get("index", best_idx)), 0), len(scored) - 1)
            new_text = (decision.get("text") or "").strip()
            new_title = (decision.get("title") or "").strip()
            if not new_text:
                raise ValueError("编辑后的方案内容不能为空。")
            await emit({"type": "system",
                        "content": f"用户手动编辑了方案 {idx + 1}《{scored[idx].get('title', '')}》，"
                                   f"评审组正在对修改版重新评分…"})
            new_eval = await self._score_one(
                problem, new_text, emit, refined=True,
                announce={"kind": "rescore_edit",
                          "title": new_title or scored[idx].get("title", "")})
            item = dict(scored[idx])
            item["text"] = new_text
            if new_title:
                item["title"] = new_title
            item.update(new_eval)
            scored[idx] = item
            await emit({"type": "card", "card": "score_item",
                        "index": item["index"], "title": item["title"],
                        "benefit": new_eval["benefit"], "cost": new_eval["cost"],
                        "harm": new_eval["harm"],
                        "ideality": round(new_eval["ideality"], 3),
                        "reviews": new_eval["reviews"]})
            await emit({"type": "system",
                        "content": f"修改版重新评分完成：理想性 {new_eval['ideality']:.2f}"
                                   f"（收益{new_eval['benefit']:.0f}/成本{new_eval['cost']:.0f}/"
                                   f"副作用{new_eval['harm']:.0f}），以此作为优化基线。"})
            return {"action": "continue", "scored": scored,
                    "best_idx": idx, "auto_rest": False}

        # choose：以用户选定方案作为优化基线
        idx = min(max(int(decision.get("index", best_idx)), 0), len(scored) - 1)
        await emit({"type": "system",
                    "content": f"用户选定方案 {idx + 1}《{scored[idx].get('title', '')}》"
                               f"作为优化基线。"})
        return {"action": "continue", "scored": scored,
                "best_idx": idx, "auto_rest": False}

    @staticmethod
    def _final_from_candidate(chosen: Dict[str, Any], iterations: int) -> Dict[str, Any]:
        return {
            "title": chosen.get("title", "最终设计方案"),
            "text": chosen.get("text", ""),
            "ideality": round(chosen.get("ideality", 0), 3),
            "benefit": chosen.get("benefit"), "cost": chosen.get("cost"),
            "harm": chosen.get("harm"),
            "opt_iterations": iterations,
        }

    # ------------------------------------------------------------------
    # 阶段四：优化迭代（LLM-as-optimizer，支持分叉热启动与人工改稿）
    # ------------------------------------------------------------------
    async def _stage_optimization(self, problem: str, scored: List[Dict],
                                  best_idx: int, emit: Emit,
                                  control: Optional[RunControl] = None,
                                  auto_rest: bool = True,
                                  warm: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        cfg = self.config
        if warm is not None:
            # 分叉恢复：沿用检查点的优化中途状态
            best = dict(warm["best"])
            series = list(warm.get("series", []))
            failures = int(warm.get("failures", 0))
            iterations = int(warm.get("iterations", 0))
        else:
            best = dict(scored[best_idx])
            series = [{"iteration": 0, "ideality": round(best["ideality"], 3),
                       "label": "初始最优"}]
            failures = 0
            iterations = 0
        user_hint = ""

        # 热启动时先在分叉点重新暂停，由用户决定本轮成果的去留
        if warm is not None and not auto_rest and control is not None:
            last_refined = warm.get("last_refined") or best
            last_round = warm.get("last_round") or {
                "ideality": round(last_refined["ideality"], 3),
                "improved": last_refined["ideality"] > best["ideality"] + 1e-6}
            decision = await self._pause_iterate(
                emit, control, iteration=iterations,
                round_ideality=last_round["ideality"],
                best_ideality=round(best["ideality"], 3),
                improved=last_round["improved"],
                remaining=cfg.opt_iter_n - iterations,
                round_title=last_refined.get("title", ""),
                round_text=last_refined.get("text", ""))
            best, failures, user_hint, auto_rest, stop = await self._resolve_iter_decision(
                decision, problem, emit, prev_best=best, round_best=dict(last_refined),
                improved=last_round["improved"], iteration=iterations,
                series=series, failures=failures)
            if stop:
                return self._opt_final(best, iterations)

        for i in range(iterations, cfg.opt_iter_n):
            if failures >= cfg.opt_fail_t:
                break
            iterations += 1
            # 评审专家对当前最优方案给出评分依据与改进建议（气泡可展开查看明细）
            current_eval = await self._score_one(
                problem, best["text"], emit,
                announce={"kind": "pre_refine", "title": best.get("title", "")})
            suggestions = current_eval["comments"]
            round_reviews = current_eval["reviews"]
            refined = await self._call(
                "refiner", roles.build_refine, emit,
                render=lambda p: f"【{p.get('title', '优化方案')}】\n{p.get('text', '')}",
                problem=problem, solution_text=best["text"],
                suggestions=suggestions, index=i, user_hint=user_hint)
            user_hint = ""
            new_text = refined.get("text", "")
            if not new_text:
                failures += 1
                continue
            new_eval = await self._score_one(problem, new_text, emit, refined=True)
            improved = new_eval["ideality"] > best["ideality"] + 1e-6
            prev_best = dict(best)
            round_best = {"index": best["index"],
                          "title": refined.get("title", "优化方案"),
                          "text": new_text, **new_eval}
            series.append({"iteration": i + 1,
                           "ideality": round(new_eval["ideality"], 3),
                           "label": f"第{i + 1}轮"})
            await emit({"type": "card", "card": "optimization",
                        "iteration": i + 1,
                        "text": new_text,
                        "title": refined.get("title", f"第{i + 1}轮优化方案"),
                        "ideality": round(new_eval["ideality"], 3),
                        "best_ideality": round(prev_best["ideality"], 3),
                        "improved": improved,
                        "reviews": round_reviews,
                        "series": series})
            if auto_rest:
                tail = "已采纳。" if improved else "保留原方案。"
            else:
                tail = "等待用户定夺去留。"
            opt_msg = (f"第{i + 1}轮优化后理想性 {new_eval['ideality']:.2f}，"
                       + ("高于当前最优，" if improved else "未超过当前最优，") + tail)
            await emit({"type": "message", "agent": "panel",
                        "agent_title": "专家评审组", "content": opt_msg,
                        "reviews": new_eval["reviews"]})

            # 检查点：每轮优化后（供分叉恢复，自动/手动均持久化）
            await emit({"type": "checkpoint", "stage": "iterate",
                        "state": {"problem": problem, "best": prev_best,
                                  "series": series, "iterations": i + 1,
                                  "failures": failures,
                                  "last_refined": round_best,
                                  "last_round": {"ideality": round(new_eval["ideality"], 3),
                                                 "improved": improved}}})

            more_rounds = (i + 1 < cfg.opt_iter_n) and (failures < cfg.opt_fail_t)
            if not auto_rest and control is not None and more_rounds:
                decision = await self._pause_iterate(
                    emit, control, iteration=i + 1,
                    round_ideality=round(new_eval["ideality"], 3),
                    best_ideality=round(prev_best["ideality"], 3),
                    improved=improved,
                    remaining=cfg.opt_iter_n - i - 1,
                    round_title=refined.get("title", ""),
                    round_text=new_text)
            else:
                decision = {"action": "continue"}
            best, failures, user_hint, auto_rest, stop = await self._resolve_iter_decision(
                decision, problem, emit, prev_best=prev_best, round_best=round_best,
                improved=improved, iteration=i + 1, series=series, failures=failures)
            iterations = i + 1
            if stop:
                break

        return self._opt_final(best, iterations)

    async def _pause_iterate(self, emit: Emit, control: RunControl, *,
                             iteration: int, round_ideality: float,
                             best_ideality: float, improved: bool,
                             remaining: int, round_title: str,
                             round_text: str) -> Dict[str, Any]:
        await emit({"type": "pause", "stage": "iterate",
                    "iteration": iteration,
                    "ideality": round_ideality,
                    "best_ideality": best_ideality,
                    "improved": improved,
                    "remaining": remaining,
                    "round_title": round_title,
                    "round_text": round_text})
        return await control.wait_choice()

    async def _resolve_iter_decision(self, decision: Dict[str, Any], problem: str,
                                     emit: Emit, *, prev_best: Dict[str, Any],
                                     round_best: Dict[str, Any], improved: bool,
                                     iteration: int, series: List[Dict[str, Any]],
                                     failures: int):
        """处理优化轮次人工决策，返回 (best, failures, user_hint, auto_rest, stop)。

        动作：continue/feedback（按理想性自动去留）、adopt_round（强制采用本轮）、
        discard_round（保留原版舍弃本轮）、edit_round（手动改稿后重评采用）、
        stop（结束）、auto（剩余轮次自动）。
        """
        act = decision.get("action")
        best = prev_best
        user_hint = ""
        auto_rest = False
        stop = False

        if act == "stop":
            await emit({"type": "system",
                        "content": "用户决定采纳当前方案，结束优化迭代。"})
            stop = True
        elif act == "auto":
            auto_rest = True
            if improved:
                best, failures = round_best, 0
            else:
                failures += 1
        elif act == "adopt_round":
            best, failures = round_best, 0
            await emit({"type": "system",
                        "content": f"用户决定强制采用第 {iteration} 轮优化方案"
                                   f"（理想性 {round_best['ideality']:.2f}）。"})
        elif act == "discard_round":
            failures += 1
            await emit({"type": "system",
                        "content": f"用户决定保留原方案，舍弃第 {iteration} 轮优化结果。"})
        elif act == "edit_round":
            text = (decision.get("text") or "").strip()
            title = (decision.get("title") or "").strip()
            if not text:
                raise ValueError("手动修改的方案内容不能为空。")
            await emit({"type": "system",
                        "content": "用户手动修改了本轮方案，评审组正在重新评分…"})
            new_eval = await self._score_one(
                problem, text, emit, refined=True,
                announce={"kind": "rescore_edit",
                          "title": title or round_best.get("title", "手动修改方案")})
            best = {"index": prev_best.get("index"),
                    "title": title or round_best.get("title", "手动修改方案"),
                    "text": text, **new_eval}
            failures = 0
            series.append({"iteration": iteration,
                           "ideality": round(new_eval["ideality"], 3),
                           "label": f"第{iteration}轮·手改"})
            await emit({"type": "system",
                        "content": f"手动修改版评分完成：理想性 {new_eval['ideality']:.2f}"
                                   f"（收益{new_eval['benefit']:.0f}/成本{new_eval['cost']:.0f}/"
                                   f"副作用{new_eval['harm']:.0f}），已采用为当前最优。"})
        elif act == "feedback":
            hint = (decision.get("text") or "").strip()
            if improved:
                best, failures = round_best, 0
            else:
                failures += 1
            if hint:
                user_hint = hint
                await emit({"type": "system",
                            "content": f"用户补充优化方向：{hint}"})
        else:  # continue / 默认：按理想性决定去留
            if improved:
                best, failures = round_best, 0
            else:
                failures += 1
        return best, failures, user_hint, auto_rest, stop

    @staticmethod
    def _opt_final(best: Dict[str, Any], iterations: int) -> Dict[str, Any]:
        return {
            "title": best.get("title", "最终设计方案"),
            "text": best.get("text", ""),
            "ideality": round(best.get("ideality", 0), 3),
            "benefit": best.get("benefit"), "cost": best.get("cost"),
            "harm": best.get("harm"),
            "opt_iterations": iterations,
        }
