"""第四步端到端：Outbox 派发 PAUSE/RESUME signal 到真实 Workflow + 去重。
在 api 容器内运行。
"""
from __future__ import annotations

import asyncio
from datetime import timedelta
from uuid import uuid4

from sqlalchemy import select
from temporalio.client import Client

from agent_runtime.contracts import WorkflowInput
from agent_runtime.db import RunRecord, SessionLocal, WorkflowOutbox
from agent_runtime.outbox.commands import dispatch_commands_once, enqueue_command
from agent_runtime.rls import set_tenant_context
from agent_runtime.workflow import AgentRunWorkflow

TENANT = "demo-bank"
USER = "analyst-01"


async def main():
    rid = str(uuid4())
    async with SessionLocal() as s:
        await set_tenant_context(s, TENANT)
        s.add(RunRecord(run_id=rid, tenant_id=TENANT, user_id=USER, session_id="s4e2e",
                        agent_id="a", prompt="金融监测演示", status="QUEUED", version=0,
                        pause_state="NONE", metadata_json={}))
        await s.commit()

    client = await Client.connect("temporal:7233")
    inp = WorkflowInput(run_id=rid, tenant_id=TENANT, user_id=USER, session_id="s4e2e", agent_id="a",
        prompt="金融监测演示：交易筛查", scopes=["agent:execute", "financial:monitoring:run"],
        max_steps=2, max_duration_seconds=300, model_name="", harness_version="s4").model_dump(mode="json")
    wid = f"agent-run/{TENANT}/{rid}"
    await client.start_workflow(AgentRunWorkflow.run, inp, id=wid, task_queue="agent-runtime",
                                execution_timeout=timedelta(minutes=5))

    # 等待 workflow 进入 RUNNING 并处理第一步
    await asyncio.sleep(6)

    # 入队 PAUSE 命令（模拟 API），重复入队同一 command_id 应幂等
    async with SessionLocal() as s:
        await set_tenant_context(s, TENANT)
        a = await enqueue_command(s, run_id=__import__("uuid").UUID(rid), tenant_id=TENANT,
            command_type="PAUSE_WORKFLOW", command_id=f"pause:{rid}:0",
            payload={"requested_by": USER, "signal": "pause"})
        b = await enqueue_command(s, run_id=__import__("uuid").UUID(rid), tenant_id=TENANT,
            command_type="PAUSE_WORKFLOW", command_id=f"pause:{rid}:0", payload={"signal": "pause"})
        await s.commit()
    print("pause_enqueue_idempotent", a and not b)

    # 触发 dispatcher 派发 PAUSE signal
    n = await dispatch_commands_once(client, SessionLocal)
    print("dispatched_commands", n)

    await asyncio.sleep(6)
    handle = client.get_workflow_handle(wid)
    state = await handle.query("get_state")
    print("workflow_state_after_pause", state)

    async with SessionLocal() as s:
        await set_tenant_context(s, TENANT)
        run = await s.get(RunRecord, __import__("uuid").UUID(rid))
        print("db_status", run.status, "pause_state", run.pause_state)

    # 检查 outbox 命令状态
    async with SessionLocal() as s:
        await set_tenant_context(s, TENANT)
        rows = (await s.execute(select(WorkflowOutbox.command_type, WorkflowOutbox.status)
            .where(WorkflowOutbox.run_id == __import__("uuid").UUID(rid)))).all()
    print("outbox_state", [(r[0], r[1]) for r in rows])

    # 重复派发：command 已 DISPATCHED，不应重新派发
    n2 = await dispatch_commands_once(client, SessionLocal)
    print("redispatch_count", n2)


asyncio.run(main())
