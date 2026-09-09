# -*- coding: utf-8 -*-
"""TRIZ-GPT Web 后端。

- REST：创建运行会话、查询历史、查询知识库；
- WebSocket：实时推送多智能体协作事件流（对应论文「Agent 对话记录实时
  展示给用户」），并接收用户在人机交互节点的决策（矛盾对选择）；
- SQLite：会话与事件持久化，支持刷新/断线回放；
- 静态托管：Vue 单页前端。
"""

import asyncio
import os
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Set

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import store
from .agents.framework import get_knowledge_store
from .llm.base import get_llm
from .pipeline.sop import PipelineConfig, RunControl, TRIZPipeline
from .triz import knowledge as K

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_STATIC_DIR = os.path.join(_BASE_DIR, "static")


def _load_dotenv() -> None:
    """轻量加载 .env（无需第三方依赖）。"""
    path = os.path.join(_BASE_DIR, ".env")
    if not os.path.exists(path):
        return
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key, val = key.strip(), val.strip().strip('"').strip("'")
            os.environ.setdefault(key, val)


_load_dotenv()
store.init_db()

app = FastAPI(title="TRIZ-GPT")


@dataclass
class RunState:
    run_id: str
    control: RunControl = field(default_factory=RunControl)
    queues: Set[asyncio.Queue] = field(default_factory=set)
    seq: int = 0
    status: str = "running"


RUNS: Dict[str, RunState] = {}


class RunRequest(BaseModel):
    problem: str
    auto_mode: bool = True
    candidates_k: int = 6
    gen_fail_t: int = 3
    opt_iter_n: int = 4
    opt_fail_t: int = 2


class ForkRequest(BaseModel):
    stage: str           # pair | candidate | iterate
    label: str = ""


@app.on_event("startup")
async def _startup():
    llm = get_llm()  # 预热：确定使用真实模型还是演示模型
    # 在线程中预热 TRIZ 知识库向量（含磁盘缓存），避免首个会话内同步阻塞事件循环
    await asyncio.to_thread(get_knowledge_store, llm)


@app.get("/")
async def index():
    # HTML 入口禁止缓存：页面结构变更后刷新即生效（/static 资源带 ?v= 版本号，可长期缓存）
    return FileResponse(
        os.path.join(_STATIC_DIR, "index.html"),
        headers={"Cache-Control": "no-cache"},
    )


@app.get("/api/llm/info")
async def llm_info():
    llm = get_llm()
    return {"name": llm.name, "mock": llm.name == "内置演示模型"}


@app.get("/api/triz/knowledge")
async def triz_knowledge():
    return {
        "parameters": [{"id": pid, **info} for pid, info in K.ENGINEERING_PARAMETERS.items()],
        "principles": [{"id": pid, **info} for pid, info in K.INVENTIVE_PRINCIPLES.items()],
    }


@app.get("/api/runs")
async def runs_list():
    return store.list_runs()


@app.post("/api/runs")
async def create_run(req: RunRequest):
    run_id = store.create_run(req.problem, req.auto_mode)
    state = RunState(run_id=run_id)
    RUNS[run_id] = state

    config = PipelineConfig(
        candidates_k=max(2, min(req.candidates_k, 10)),
        gen_fail_t=req.gen_fail_t,
        opt_iter_n=max(1, min(req.opt_iter_n, 8)),
        opt_fail_t=req.opt_fail_t,
        auto_mode=req.auto_mode,
    )
    asyncio.create_task(_run_pipeline(run_id, req.problem, config))
    return {"run_id": run_id}


@app.post("/api/runs/{run_id}/fork")
async def fork_run(run_id: str, req: ForkRequest):
    """从检查点分叉：复制父 run 前缀事件，子 run 跳过已完成阶段并在分叉阶段重新暂停。"""
    parent = store.get_run(run_id)
    if not parent:
        raise HTTPException(status_code=404, detail="父会话不存在")
    if req.stage not in ("pair", "candidate", "iterate"):
        raise HTTPException(status_code=400, detail="分叉阶段无效")

    events = store.get_events(run_id)
    checkpoint = next((e for e in reversed(events)
                       if e.get("type") == "checkpoint" and e.get("stage") == req.stage), None)
    if checkpoint is None:
        raise HTTPException(status_code=404, detail=f"该会话暂无「{req.stage}」检查点")

    child_id = store.create_run(
        parent["problem"], auto_mode=False,
        parent_run_id=run_id, fork_stage=req.stage,
        label=(req.label or "").strip() or None)
    # 复制检查点（含）之前的全部事件作为子 run 前缀；分叉阶段的旧 pause 标记为历史
    copied = store.copy_events(run_id, child_id, checkpoint["seq"],
                               mark_superseded_stage=req.stage)

    state = RunState(run_id=child_id)
    state.seq = checkpoint["seq"] + 1
    RUNS[child_id] = state

    config = PipelineConfig(auto_mode=False)
    resume = {"stage": req.stage, "state": checkpoint.get("state", {})}
    asyncio.create_task(_run_pipeline(child_id, parent["problem"], config,
                                      resume=resume))
    return {"run_id": child_id, "copied_events": copied}


async def _run_pipeline(run_id: str, problem: str, config: PipelineConfig,
                        resume: dict = None):
    state = RUNS[run_id]

    async def emit(ev: Dict[str, Any]):
        ev = dict(ev)
        ev["seq"] = state.seq
        state.seq += 1
        ev["ts"] = time.time()
        # 流式增量与思考流仅实时推送，不入库；刷新/回放以最终 message 事件为准
        if ev["type"] not in ("message_delta", "thinking_start",
                              "thinking_delta", "thinking_done"):
            store.add_event(run_id, ev["seq"], ev)
        for q in list(state.queues):
            await q.put(ev)

    try:
        llm = get_llm()
        pipeline = TRIZPipeline(llm, config)
        await pipeline.run(problem, emit, state.control, resume=resume)
        state.status = "done"
        store.set_status(run_id, "done")
    except Exception as exc:  # noqa: BLE001
        state.status = "error"
        store.set_status(run_id, "error")
        await emit({"type": "error", "message": str(exc)})


@app.websocket("/ws/runs/{run_id}")
async def ws_run(websocket: WebSocket, run_id: str):
    await websocket.accept()

    state = RUNS.get(run_id)
    queue: asyncio.Queue = asyncio.Queue()
    # 先注册实时队列，再重放历史，避免两者切换缝隙丢事件
    if state is not None:
        state.queues.add(queue)

    # 回放历史事件，并记录已重放的最大序号用于去重
    history = store.get_events(run_id)
    for ev in history:
        await websocket.send_json(ev)
    max_seq = max((e.get("seq", -1) for e in history), default=-1)

    async def receiver():
        try:
            while True:
                data = await websocket.receive_json()
                if state is not None and data.get("action"):
                    await state.control.queue.put(data)
        except WebSocketDisconnect:
            pass

    recv_task = asyncio.create_task(receiver())
    try:
        # 连接时会话已结束：历史重放后直接关闭
        if state is not None and state.status in ("done", "error"):
            return
        while True:
            if state is not None:
                ev = await queue.get()
                if ev.get("seq", -1) > max_seq:  # 跳过重放期间重复投递的事件
                    await websocket.send_json(ev)
                    max_seq = ev.get("seq", max_seq)
                    if ev.get("type") in ("done", "error"):
                        break
            else:
                # 历史会话：历史已回放完毕，无实时流
                await websocket.send_json({"type": "system",
                                          "content": "该会话已结束且为历史记录，仅供回放查看。"})
                break
    except WebSocketDisconnect:
        pass
    finally:
        recv_task.cancel()
        if state is not None:
            state.queues.discard(queue)


app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")
