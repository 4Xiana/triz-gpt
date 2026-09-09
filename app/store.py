# -*- coding: utf-8 -*-
"""SQLite 持久化（对应论文中 MySQL 的角色）：保存运行会话与事件流。

事件流完整入库，前端断线重连或刷新后可回放整个多智能体协作过程。
"""

import json
import os
import sqlite3
import threading
import time
import uuid
from typing import Any, Dict, List

_DB_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data", "triz_gpt.db")

_lock = threading.Lock()


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(_DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    os.makedirs(os.path.dirname(_DB_PATH), exist_ok=True)
    with _lock, _conn() as conn:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS runs (
                run_id TEXT PRIMARY KEY,
                problem TEXT,
                auto_mode INTEGER,
                status TEXT,
                created_at REAL,
                finished_at REAL,
                parent_run_id TEXT,
                fork_stage TEXT,
                label TEXT
            )""")
        conn.execute(
            """CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT,
                seq INTEGER,
                payload TEXT
            )""")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_events_run ON events(run_id, seq)")
        # 旧库迁移：补充分叉相关列
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(runs)").fetchall()}
        for col, decl in (("parent_run_id", "TEXT"), ("fork_stage", "TEXT"),
                          ("label", "TEXT")):
            if col not in cols:
                conn.execute(f"ALTER TABLE runs ADD COLUMN {col} {decl}")


def create_run(problem: str, auto_mode: bool,
               parent_run_id: str = None, fork_stage: str = None,
               label: str = None) -> str:
    run_id = uuid.uuid4().hex[:12]
    with _lock, _conn() as conn:
        conn.execute(
            "INSERT INTO runs(run_id, problem, auto_mode, status, created_at, "
            "parent_run_id, fork_stage, label) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (run_id, problem, 1 if auto_mode else 0, "running", time.time(),
             parent_run_id, fork_stage, label))
    return run_id


def get_run(run_id: str) -> Dict[str, Any]:
    with _lock, _conn() as conn:
        row = conn.execute(
            "SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
    return dict(row) if row else {}


def copy_events(src_run_id: str, dst_run_id: str, up_to_seq: int,
                mark_superseded_stage: str = None) -> int:
    """分叉时把父 run 的前缀事件复制到子 run（seq 保持一致），返回复制条数。

    checkpoint 在每个阶段 pause 之前发射，因此前缀（截至 checkpoint.seq）
    不含分叉阶段本身的 pause，但包含更早阶段已决策的 pause（如 candidate
    分叉的前缀里有 pair pause）。这些都是父分支的历史决策，统一标记
    superseded——前端渲染为历史记录条，子 run 通过 live 事件在分叉阶段
    重新暂停等待决策。
    """
    events = get_events(src_run_id)
    n = 0
    with _lock, _conn() as conn:
        for ev in events:
            if ev.get("seq", -1) > up_to_seq:
                continue
            if ev.get("type") == "pause" and not ev.get("superseded"):
                ev = dict(ev)
                ev["superseded"] = True
            conn.execute(
                "INSERT INTO events(run_id, seq, payload) VALUES (?, ?, ?)",
                (dst_run_id, ev["seq"], json.dumps(ev, ensure_ascii=False)))
            n += 1
    return n


def set_status(run_id: str, status: str) -> None:
    with _lock, _conn() as conn:
        conn.execute(
            "UPDATE runs SET status = ?, finished_at = ? WHERE run_id = ?",
            (status, time.time() if status in ("done", "error") else None, run_id))


def add_event(run_id: str, seq: int, payload: Dict[str, Any]) -> None:
    with _lock, _conn() as conn:
        conn.execute(
            "INSERT INTO events(run_id, seq, payload) VALUES (?, ?, ?)",
            (run_id, seq, json.dumps(payload, ensure_ascii=False)))


def get_events(run_id: str) -> List[Dict[str, Any]]:
    with _lock, _conn() as conn:
        rows = conn.execute(
            "SELECT payload FROM events WHERE run_id = ? ORDER BY seq",
            (run_id,)).fetchall()
    return [json.loads(r["payload"]) for r in rows]


def list_runs(limit: int = 50) -> List[Dict[str, Any]]:
    with _lock, _conn() as conn:
        rows = conn.execute(
            "SELECT run_id, problem, status, auto_mode, created_at, "
            "parent_run_id, fork_stage, label FROM runs "
            "ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        runs = [dict(r) for r in rows]
        # 进度推导：沿 TRIZ 认知链路（具体问题→抽象参数→抽象解法→具体解法）
        # 与发散/收敛过程判定里程碑，供分支树进度条展示
        if runs:
            ids = [r["run_id"] for r in runs]
            qmarks = ",".join("?" * len(ids))
            ev_rows = conn.execute(
                f"SELECT run_id, payload FROM events WHERE run_id IN ({qmarks})",
                ids).fetchall()
    # 里程碑等级：0 starting → 1 pair(矛盾断点) → 2 principles(发明原则)
    # → 3 diverged(候选发散完成) → 4 candidate(收敛断点) → 5 iterate(优化迭代) → 6 done
    cp_rank = {"pair": 1, "candidate": 4, "iterate": 5}
    card_rank = {"principles": 2, "candidate": 3, "hull_summary": 3}
    prog: Dict[str, int] = {}
    iter_round: Dict[str, int] = {}
    for r in ev_rows:
        rid = r["run_id"]
        try:
            ev = json.loads(r["payload"])
        except (ValueError, TypeError):
            continue
        cur = prog.get(rid, 0)
        etype = ev.get("type")
        if etype == "checkpoint":
            cur = max(cur, cp_rank.get(ev.get("stage"), 0))
            if ev.get("stage") == "iterate":
                iters = (ev.get("state") or {}).get("iterations") or 0
                iter_round[rid] = max(iter_round.get(rid, 0), iters)
        elif etype == "card":
            if ev.get("card") == "final":
                cur = 6
            else:
                cur = max(cur, card_rank.get(ev.get("card"), 0))
        elif etype == "done":
            cur = 6
        prog[rid] = cur
    rank_stage = {0: "starting", 1: "pair", 2: "principles", 3: "diverged",
                  4: "candidate", 5: "iterate", 6: "done"}
    for r in runs:
        r["progress"] = rank_stage.get(prog.get(r["run_id"], 0), "starting")
        r["iter_round"] = iter_round.get(r["run_id"], 0)
    return runs
