"""真实 Worker Kill/Restart 故障注入验证（在 api 容器内运行）。

流程：
  1) 通过 Temporal Client 启动多个 AgentRunWorkflow（同一 tenant / run id）
  2) 等待 checkpoint 写入
  3) 外部（PowerShell）SIGKILL worker 容器并重启
  4) 再次运行本脚本 --check 模式，验证 workflow 完成、无重复、恢复事件存在

用法：
  python verify_worker_recovery.py start
  python verify_worker_recovery.py check
"""
from __future__ import annotations

import asyncio
import json
import sys
from datetime import timedelta
from uuid import uuid4

from temporalio.client import Client

from agent_runtime.contracts import WorkflowInput
from agent_runtime.db import RunRecord, session_for_tenant
from agent_runtime.workflow import AgentRunWorkflow

TENANT = "demo-bank"
USER = "analyst-01"
STATE_FILE = "/tmp/worker_recovery_state.json"


def workflow_id(run_id: str) -> str:
    return f"agent-run/{TENANT}/{run_id}"


def build_input(run_id: str) -> dict:
    return WorkflowInput(
        run_id=run_id, tenant_id=TENANT, user_id=USER,
        session_id=f"recovery-{run_id}", agent_id="default-agent",
        prompt="金融监测演示：请对账户 ACC-DEMO-001 运行交易筛查。",
        scopes=["agent:execute"], max_steps=10, max_duration_seconds=600,
        model_name="", harness_version="recovery-test",
    ).model_dump(mode="json")


async def start_runs(count: int = 3):
    client = await Client.connect("temporal:7233")
    run_ids = [str(uuid4()) for _ in range(count)]
    for rid in run_ids:
        async with session_for_tenant(TENANT) as session:
            session.add(RunRecord(
                run_id=rid, tenant_id=TENANT, user_id=USER,
                session_id=f"recovery-{rid}", agent_id="default-agent",
                prompt="金融监测演示", status="RUNNING", metadata_json={},
            ))
            await session.commit()
        handle = await client.start_workflow(
            AgentRunWorkflow.run, build_input(rid), id=workflow_id(rid),
            task_queue="agent-runtime", execution_timeout=timedelta(minutes=10),
        )
        print("started", rid, handle.id)
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump({"run_ids": run_ids}, f)
    print("started_all", len(run_ids))


async def check_runs():
    with open(STATE_FILE, encoding="utf-8") as f:
        run_ids = json.load(f)["run_ids"]
    client = await Client.connect("temporal:7233")
    from agent_runtime.db import RunEvent, RunRecord
    from sqlalchemy import select
    for rid in run_ids:
        handle = client.get_workflow_handle(workflow_id(rid))
        desc = await handle.describe()
        result = None
        try:
            result = await asyncio.wait_for(handle.result(), timeout=20)
        except Exception:
            pass
        async with session_for_tenant(TENANT) as session:
            run = await session.get(RunRecord, rid)
            events = (await session.execute(
                select(RunEvent.event_type).where(RunEvent.run_id == __import__("uuid").UUID(rid))
            )).scalars().all()
        events = list(events)
        print(json.dumps({
            "run_id": rid,
            "db_status": run.status if run else "MISSING",
            "workflow_status": str(desc.status),
            "result": result,
            "worker_failed_event": "WORKER_FAILED" in events,
            "history_replay_event": "TEMPORAL_HISTORY_REPLAY" in events,
            "worker_reconnected_event": "WORKER_RECONNECTED" in events,
            "workflow_resumed_event": "WORKFLOW_RESUMED" in events,
        }, ensure_ascii=False))


mode = sys.argv[1] if len(sys.argv) > 1 else "start"
if mode == "start":
    asyncio.run(start_runs())
else:
    asyncio.run(check_runs())
