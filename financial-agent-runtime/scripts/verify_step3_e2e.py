"""第三步端到端验证：真实 workflow 工具调用落 CONFIRMED + checkpoint 不回归。"""
from __future__ import annotations

import asyncio
from datetime import timedelta
from uuid import uuid4

from sqlalchemy import func, select
from temporalio.client import Client

from agent_runtime.contracts import WorkflowInput
from agent_runtime.db import CheckpointRecord, RunRecord, SessionLocal, ToolInvocation
from agent_runtime.rls import set_tenant_context
from agent_runtime.workflow import AgentRunWorkflow

TENANT = "demo-bank"
USER = "analyst-01"


async def main():
    rid = str(uuid4())
    async with SessionLocal() as s:
        await set_tenant_context(s, TENANT)
        s.add(RunRecord(run_id=rid, tenant_id=TENANT, user_id=USER,
                        session_id="e2e", agent_id="a", prompt="金融监测演示", status="RUNNING", metadata_json={}))
        await s.commit()

    client = await Client.connect("temporal:7233")
    inp = WorkflowInput(run_id=rid, tenant_id=TENANT, user_id=USER, session_id="e2e",
                        agent_id="a", prompt="金融监测演示：请对账户 ACC-DEMO-001 运行交易筛查。",
                        scopes=["agent:execute", "financial:transactions:read", "financial:monitoring:run", "agent:tools:invoke"], max_steps=10, max_duration_seconds=300,
                        model_name="", harness_version="e2e").model_dump(mode="json")
    handle = await client.start_workflow(AgentRunWorkflow.run, inp,
                                         id=f"agent-run/{TENANT}/{rid}", task_queue="agent-runtime",
                                         execution_timeout=timedelta(minutes=5))
    result = await asyncio.wait_for(handle.result(), timeout=60)
    print("workflow_result", result.get("status") if isinstance(result, dict) else result)

    async with SessionLocal() as s:
        await set_tenant_context(s, TENANT)
        run = await s.get(RunRecord, rid)
        print("db_run_status", run.status)

        cp = (await s.execute(select(func.count()).select_from(CheckpointRecord)
                              .where(CheckpointRecord.run_id == rid))).scalar()
        print("checkpoint_count", cp)

        invocations = (await s.execute(select(ToolInvocation)
                                       .where(ToolInvocation.run_id == rid))).scalars().all()
        statuses = [(i.status, i.fencing_token, i.attempt_count) for i in invocations]
        print("invocations", statuses)
        print("all_confirmed", all(st == "CONFIRMED" for st, _, _ in statuses) if statuses else "no_tool_calls")


asyncio.run(main())
