"""E2E: create a run via DB+outbox (bypassing OIDC), wait for the Temporal worker
to build ContextPacks, and verify persistence + tenant scoping. Run in a container
on the compose network with the new image.
"""

from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime, timezone, timedelta
from uuid import uuid4

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from sqlalchemy import select

from agent_runtime.db import (
    RunRecord, WorkflowOutbox, ContextPackRecord, SessionLocal,
    session_for_tenant,
)
from agent_runtime.rls import system_session


async def main() -> None:
    run_id = uuid4()
    tenant = "ctx-e2e"
    print(f"E2E run_id={run_id}")

    workflow_input = {
        "run_id": str(run_id),
        "tenant_id": tenant,
        "user_id": "u1",
        "session_id": "s1",
        "agent_id": "default-agent",
        "prompt": "金融监测演示 ACC-E2E-001 请运行演示交易复核规则",
        "scopes": ["agent:execute", "memory:read"],
        "max_steps": 4,
        "max_duration_seconds": 120,
        "skill_ids": [],
        "model_name": "gpt-4.1-mini",
        "harness_version": "e2e",
        "context_config": {
            "immutable_constraints": ["不得自动批准自己的高风险操作", "账户必须脱敏"],
            "acceptance_criteria": ["引用来源事件"],
            "budget_config": {"context_window_override": 8192},
        },
    }

    # Insert run + START command under system context.
    async with system_session(SessionLocal, "outbox_dispatcher") as s:
        s.add(RunRecord(
            run_id=run_id, tenant_id=tenant, user_id="u1", session_id="s1",
            agent_id="default-agent", prompt=workflow_input["prompt"], status="CREATED",
        ))
        s.add(WorkflowOutbox(
            run_id=run_id, tenant_id=tenant,
            command_id=f"start:{run_id}", command_type="START_WORKFLOW",
            payload=workflow_input, status="PENDING",
            next_attempt_at=datetime.now(timezone.utc),
        ))
        await s.commit()

    # Poll for ContextPacks with a timeout. Open a FRESH session each poll so the
    # transaction snapshot refreshes and newly committed packs become visible.
    found = 0
    deadline = datetime.now(timezone.utc) + timedelta(seconds=90)
    while datetime.now(timezone.utc) < deadline:
        async with session_for_tenant(tenant) as s:
            rows = (await s.execute(
                select(ContextPackRecord).where(ContextPackRecord.run_id == run_id)
            )).scalars().all()
        if rows:
            found = len(rows)
            break
        await asyncio.sleep(2)

    print(f"E2E context_packs_found={found}")
    if not found:
        print("E2E_RESULT FAIL no context packs persisted")
        sys.exit(1)

    # Verify the newest pack's constraints + provenance via tenant session.
    async with session_for_tenant(tenant) as s:
        rows = (await s.execute(
            select(ContextPackRecord).where(ContextPackRecord.run_id == run_id)
            .order_by(ContextPackRecord.created_at.desc())
        )).scalars().all()
        pack = rows[0].pack_json
        constraints = pack["goal"]["immutable_constraints"]
        goal = pack["goal"]["original_user_goal"]
        budget = pack["budget"]

    ok = (
        len(constraints) == 2
        and constraints[0]["provenance"]["trust_level"] == "system"
        and goal["provenance"]["source_type"] == "user_goal"
        and budget["model_context_limit"] == 8192
        and budget["reserved_output_tokens"] > 0
    )
    print(f"E2E constraints={len(constraints)} trust={constraints[0]['provenance']['trust_level']} "
          f"goal_source={goal['provenance']['source_type']} limit={budget['model_context_limit']} "
          f"reserved={budget['reserved_output_tokens']}")
    print("E2E_RESULT", "PASS" if ok else "FAIL")
    sys.exit(0 if ok else 1)


asyncio.run(main())
