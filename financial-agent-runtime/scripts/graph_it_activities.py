"""Integration-test-only Temporal activities for the Task Graph Engine.

These activities are registered only on an isolated task queue
(``agent-runtime-graph-it``) and must never be used in production.
"""
from __future__ import annotations

import asyncio
import os
from typing import Any

import asyncpg
from temporalio import activity

PROBE_TABLE = "graph_it_probe"


def _dsn() -> str:
    dsn = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL") or ""
    return dsn.replace("postgresql+asyncpg://", "postgresql://")


def _sleep_seconds() -> float:
    return float(os.environ.get("GRAPH_IT_SLEEP_SECONDS", "0.6"))


def _fail_nodes() -> set[str]:
    raw = os.environ.get("GRAPH_IT_FAIL_NODES", "")
    return {item.strip() for item in raw.split(",") if item.strip()}


def _fail_runs() -> set[str]:
    raw = os.environ.get("GRAPH_IT_FAIL_RUNS", "")
    return {item.strip() for item in raw.split(",") if item.strip()}


def _node_sleep(node_id: str, run_id: str = "") -> float:
    """Per-node/per-run sleep override for cancel/timeout integration tests.

    Resolution order: GRAPH_IT_NODE_SLEEP_<node_id>, GRAPH_IT_NODE_SLEEP_<run_id>,
    GRAPH_IT_SLEEP_SECONDS. Children in linear mode report node_id='unknown', so
    tests key their slow/fast behavior on the deterministic child run_id.
    """
    for key in (node_id, run_id):
        if not key:
            continue
        raw = os.environ.get(f"GRAPH_IT_NODE_SLEEP_{key}")
        if raw is not None:
            try:
                return max(0.0, float(raw))
            except ValueError:
                pass
    return _sleep_seconds()


async def _probe(run_id: str, node_id: str, phase: str) -> None:
    dsn = _dsn()
    if not dsn:
        return
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            f"INSERT INTO {PROBE_TABLE} (run_id, node_id, phase) VALUES ($1, $2, $3)",
            run_id, node_id, phase,
        )
    finally:
        await conn.close()


@activity.defn(name="agent_decide_activity")
async def graph_it_slow_decide(payload: dict[str, Any]) -> dict[str, Any]:
    """Deterministic slow decision used to observe real node parallelism."""
    node_id = str((payload.get("graph_node") or {}).get("node_id") or "unknown")
    run_id = str(payload.get("run_id") or "unknown")
    await _probe(run_id, node_id, "START")
    try:
        remaining = _node_sleep(node_id, run_id)
        # 分片 sleep + heartbeat，使 Temporal activity 取消能被 promptly 观测到。
        step = min(0.5, max(0.05, remaining))
        while remaining > 0:
            await asyncio.sleep(min(step, remaining))
            activity.heartbeat(node_id)
            remaining -= step
        if node_id in _fail_nodes() or run_id in _fail_runs():
            raise RuntimeError(f"validation_error: injected failure for {node_id or run_id}")
        return {
            "action": "finish",
            "result": {"node_id": node_id, "value": "ok"},
            "message": f"graph-it node {node_id} finished",
        }
    finally:
        await _probe(run_id, node_id, "END")


@activity.defn(name="agent_decide_activity")
async def graph_it_stubborn_decide(payload: dict[str, Any]) -> dict[str, Any]:
    """故意不响应取消：吞掉 CancelledError 直到固定时长结束。

    仅用于验证 ParentClosePolicy.TERMINATE 在 Child Activity 不响应取消时
    仍能终止 Child。通过 GRAPH_IT_STUBBORN_DECIDES 环境开关在测试 worker
    中替换 agent_decide_activity。
    """
    node_id = str((payload.get("graph_node") or {}).get("node_id") or "unknown")
    run_id = str(payload.get("run_id") or "unknown")
    await _probe(run_id, node_id, "START")
    seconds = _node_sleep(node_id, run_id)
    deadline = asyncio.get_event_loop().time() + seconds
    while True:
        try:
            remaining = deadline - asyncio.get_event_loop().time()
            if remaining <= 0:
                break
            await asyncio.sleep(min(0.5, remaining))
        except asyncio.CancelledError:
            # 故意忽略取消：继续睡到 deadline。
            continue
    await _probe(run_id, node_id, "END")
    return {"action": "finish", "result": {"node_id": node_id, "value": "ok"}, "message": "stubborn done"}
