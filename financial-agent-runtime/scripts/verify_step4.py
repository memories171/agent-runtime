"""第四步验证：Outbox 多命令、乐观锁、审批绑定状态机、幂等。
在 api 容器内运行（真实 PostgreSQL + Temporal）。
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy import func, select, update

from agent_runtime.approval.envelope import ApprovalEnvelope, canonical_arguments_hash
from agent_runtime.approval.service import ApprovalError, ApprovalService, InvalidApprovalError
from agent_runtime.db import (
    ApprovalBinding, RunRecord, SessionLocal, WorkflowOutbox,
    append_event, session_for_tenant,
)
from agent_runtime.outbox.commands import enqueue_command
from agent_runtime.rls import set_tenant_context

TENANT = "demo-bank"
USER = "analyst-01"
APPROVER = "auditor-01"


@asynccontextmanager
async def scoped():
    s = SessionLocal()
    await set_tenant_context(s, TENANT)
    try:
        yield s
    finally:
        await s.close()


async def seed_run(status="RUNNING"):
    rid = uuid4()
    async with scoped() as s:
        s.add(RunRecord(run_id=rid, tenant_id=TENANT, user_id=USER, session_id="s4",
                        agent_id="a", prompt="p", status=status, version=0, pause_state="NONE", metadata_json={}))
        await s.commit()
    return rid


async def main():
    # --- Outbox 多命令 + 幂等 ---
    rid = await seed_run()
    async with scoped() as s:
        c1 = await enqueue_command(s, run_id=rid, tenant_id=TENANT, command_type="PAUSE_WORKFLOW",
                                   command_id=f"pause:{rid}:0", payload={"requested_by": USER})
        c2 = await enqueue_command(s, run_id=rid, tenant_id=TENANT, command_type="PAUSE_WORKFLOW",
                                   command_id=f"pause:{rid}:0", payload={"requested_by": USER})
        c3 = await enqueue_command(s, run_id=rid, tenant_id=TENANT, command_type="RESUME_WORKFLOW",
                                   command_id=f"resume:{rid}:1", payload={"requested_by": USER})
        await s.commit()
    print("outbox_new_two", c1 and not c2 and c3)
    async with scoped() as s:
        rows = (await s.execute(select(WorkflowOutbox.command_type).where(WorkflowOutbox.run_id == rid))).scalars().all()
    print("outbox_command_types", sorted(set(rows)))

    # --- 乐观锁条件更新 ---
    async with scoped() as s:
        run = await s.get(RunRecord, rid)
        v = run.version
        ok = await s.execute(update(RunRecord).where(RunRecord.run_id == rid, RunRecord.version == v)
                             .values(pause_state="REQUESTED", version=RunRecord.version + 1))
        await s.commit()
        stale = await s.execute(update(RunRecord).where(RunRecord.run_id == rid, RunRecord.version == v)
                                .values(pause_state="RESUME_REQUESTED"))
    print("optimistic_lock_first_ok", ok.rowcount == 1, "stale_rejected", stale.rowcount == 0)

    # --- 审批状态机 ---
    aid = str(uuid4())
    nonce = str(uuid4())
    args = {"account": "ACC-1", "amount": 100}
    env = ApprovalEnvelope(approval_id=aid, tenant_id=TENANT, run_id=str(rid),
                           tool_call_id="call-1", tool_name="financial.screen_transactions",
                           target_resource_id="ACC-1", operation_type="screen",
                           canonical_arguments=args, requested_by=USER, policy_version="v1",
                           risk_level="HIGH", nonce=nonce,
                           expires_at=(datetime.now(timezone.utc) + timedelta(hours=1)).isoformat())
    async with scoped() as s:
        svc = ApprovalService()
        await svc.create(s, tenant_id=TENANT, envelope=env)
        await s.commit()
    print("action_hash_len", len(env.action_hash()), "args_hash_len", len(canonical_arguments_hash(args)))

    async with scoped() as s:
        await ApprovalService().decide(s, tenant_id=TENANT, approval_id=aid, approved=True, approver_id=APPROVER)
        await s.commit()
    # 幂等：重复 approve 不报错
    async with scoped() as s:
        await ApprovalService().decide(s, tenant_id=TENANT, approval_id=aid, approved=True, approver_id=APPROVER)
        print("duplicate_decision_idempotent", True)
        await s.commit()

    # 参数变化 -> 绑定失效
    async with scoped() as s:
        try:
            await ApprovalService().validate_binding(s, tenant_id=TENANT, run_id=str(rid), tool_call_id="call-1",
                tool_name="financial.screen_transactions", target_resource_id="ACC-1",
                arguments={"account": "ACC-1", "amount": 999}, policy_version="v1", approver_id=APPROVER)
            print("param_change_rejected", False)
        except InvalidApprovalError:
            print("param_change_rejected", True)
            await s.rollback()

    # 正确参数 -> 通过并消费
    async with scoped() as s:
        rec = await ApprovalService().validate_binding(s, tenant_id=TENANT, run_id=str(rid), tool_call_id="call-1",
            tool_name="financial.screen_transactions", target_resource_id="ACC-1",
            arguments=args, policy_version="v1", approver_id=APPROVER)
        ver = rec.version
    async with scoped() as s:
        await ApprovalService().consume(s, tenant_id=TENANT, approval_id=aid, version=ver)
        await s.commit()
    async with scoped() as s:
        try:
            await ApprovalService().consume(s, tenant_id=TENANT, approval_id=aid, version=ver)
            print("double_consume_rejected", False)
        except InvalidApprovalError:
            print("double_consume_rejected", True)
            await s.rollback()

    # --- 过期审批 ---
    aid2, nonce2 = str(uuid4()), str(uuid4())
    env2 = ApprovalEnvelope(approval_id=aid2, tenant_id=TENANT, run_id=str(rid), tool_call_id="call-2",
        tool_name="financial.screen_transactions", canonical_arguments={"x": 1},
        requested_by=USER, nonce=nonce2, risk_level="HIGH",
        expires_at=(datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat())
    async with scoped() as s:
        await ApprovalService().create(s, tenant_id=TENANT, envelope=env2)
        await s.commit()
    async with scoped() as s:
        try:
            await ApprovalService().decide(s, tenant_id=TENANT, approval_id=aid2, approved=True, approver_id=APPROVER)
            print("expired_rejected", False)
        except InvalidApprovalError:
            print("expired_rejected", True)
            await s.rollback()


asyncio.run(main())
