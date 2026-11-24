"""Verify Context API endpoints return real persisted data (dev auth)."""
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from agent_runtime.context_api import (
    get_run_context, get_run_context_history, get_run_context_summaries, get_run_context_metrics,
)
from agent_runtime.contracts import Principal


async def main():
    principal = Principal(tenant_id="ctx-e2e", user_id="u1", scopes={"runs:read:tenant", "run:context:read"})
    run_id = "41485f01-7599-4b94-9f41-6ac30a5c0373"
    from uuid import UUID
    from agent_runtime.db import get_session

    # Set RLS tenant context as the auth dependency would.
    from agent_runtime.rls import set_tenant_context
    gen = get_session()
    session = await gen.__anext__()
    await set_tenant_context(session, principal.tenant_id)
    try:
        ctx = await get_run_context(UUID(run_id), principal, session)
        hist = await get_run_context_history(UUID(run_id), principal, session)
        met = await get_run_context_metrics(UUID(run_id), principal, session)
        ok = (
            ctx["schema_version"] == "1.0"
            and len(ctx["goal"]["immutable_constraints"]) == 2
            and ctx["budget"]["model_context_limit"] == 8192
            and len(hist["items"]) >= 1
            and met["context_builds"] >= 1
        )
        print("API context schema", ctx["schema_version"], "constraints", len(ctx["goal"]["immutable_constraints"]))
        print("API history items", len(hist["items"]), "builds", met["context_builds"])
        print("API_RESULT", "PASS" if ok else "FAIL")
        sys.exit(0 if ok else 1)
    finally:
        await gen.aclose()


asyncio.run(main())
