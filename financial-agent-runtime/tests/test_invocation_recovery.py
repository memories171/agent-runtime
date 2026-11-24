"""B3: UNKNOWN human reconciliation closed loop (real PostgreSQL).

Covers discovery -> require (idempotent) -> human finalise -> stale fencing
protection -> repeat/concurrency idempotency -> permission isolation.
No automatic terminal transition; no new DB fields; no migration.
"""
from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import asyncpg
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from agent_runtime import activities
from agent_runtime.auth import get_principal
from agent_runtime.contracts import Principal
from agent_runtime.db import RunEvent, ToolInvocation, get_session
from agent_runtime.platform_api import ReconcileRequest, reconcile_invocation
from agent_runtime.recovery.invocations import (
    HUMAN_RECON_FENCING_BASE,
    InvocationConflictError,
    activity_fencing_token,
    InvocationUnknownError,
    ToolInvocationService,
    human_reconciliation_fencing_token,
    make_tool_idempotency_key,
)
from agent_runtime.rls import set_tenant_context


def _async_dsn(dsn: str) -> str:
    return dsn if dsn.startswith("postgresql+asyncpg://") else dsn.replace("postgresql://", "postgresql+asyncpg://", 1)


_ENGINES = []


@pytest.fixture(autouse=True)
async def _dispose_test_engines():
    yield
    while _ENGINES:
        await _ENGINES.pop().dispose()


@pytest.fixture
def tenant_ids():
    return f"b3-a-{uuid4().hex[:10]}", f"b3-b-{uuid4().hex[:10]}"


async def _setup(real_postgres_dsn: str, tenant: str):
    run_id = uuid4()
    conn = await asyncpg.connect(real_postgres_dsn)
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.system_context', 'outbox_dispatcher', true)")
        await conn.execute(
            "INSERT INTO agent_runs(run_id,tenant_id,user_id,session_id,agent_id,prompt,status,metadata) "
            "VALUES($1,$2,'u','s','a','p','RUNNING','{}'::jsonb)",
            run_id, tenant,
        )
    engine = create_async_engine(_async_dsn(real_postgres_dsn))
    _ENGINES.append(engine)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def new_session():
        s = factory()
        await set_tenant_context(s, tenant)
        return s

    return conn, run_id, new_session, factory


def _svc():
    return ToolInvocationService()


class _FakeReconcileSession:
    def __init__(self, run, record=None):
        self._run = run
        self._record = record
        self.committed = False

    async def get(self, model, key):
        return self._run

    async def execute(self, statement):
        return SimpleNamespace(scalar_one_or_none=lambda: self._record)

    async def commit(self):
        self.committed = True


def _reconcile_client(principal, session) -> AsyncClient:
    app = FastAPI()
    app.add_api_route("/runs/{run_id}/reconcile", reconcile_invocation, methods=["POST"])

    async def fake_principal():
        return principal

    async def fake_session():
        yield session

    app.dependency_overrides[get_principal] = fake_principal
    app.dependency_overrides[get_session] = fake_session
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def _principal(tenant, user, scopes=()):
    return Principal(
        tenant_id=tenant,
        user_id=user,
        scopes=set(scopes),
        permissions=set(scopes),
        authentication_method="dev",
    )


async def _begin_unknown(new_session, tenant, run_id, call_id):
    svc = _svc()
    key = make_tool_idempotency_key(tenant, str(run_id), call_id)
    s = await new_session()
    try:
        await svc.begin(s, run_id=run_id, tenant_id=tenant, tool_call_id=call_id,
                        tool_name="demo.echo", arguments={"x": 1}, idempotency_key=key,
                        fencing_token=1, lease_owner="w1", idempotent_provider=False)
        await s.commit()
    finally:
        await s.close()

    s = await new_session()
    try:
        await svc.mark_unknown(s, tenant_id=tenant, idempotency_key=key,
                               fencing_token=1, last_error="TimeoutError: response lost")
        await s.commit()
    finally:
        await s.close()
    return key


@pytest.mark.asyncio
async def test_activity_unknown_is_discoverable_and_reconciliation_required(
    real_postgres_dsn: str, tenant_ids, monkeypatch
):
    """Activity exception path records UNKNOWN and immediately requires human reconciliation."""
    tenant = tenant_ids[0]
    conn, run_id, new_session, _ = await _setup(real_postgres_dsn, tenant)

    async def boom(**kwargs):
        raise TimeoutError("provider timeout")

    monkeypatch.setattr(activities.activity, "info", lambda: SimpleNamespace(attempt=1))
    monkeypatch.setattr(activities, "execute_tool", boom)

    try:
        key = make_tool_idempotency_key(tenant, str(run_id), "call-activity-unknown")
        payload = {
            "tool_name": "demo.echo",
            "arguments": {"text": "lost"},
            "principal": _principal(tenant, "u", ["agent:execute"]).model_dump(mode="json"),
            "tenant_id": tenant,
            "run_id": str(run_id),
            "idempotency_key": key,
        }
        with pytest.raises(TimeoutError):
            await activities.execute_tool_activity(payload)

        s = await new_session()
        try:
            rec = (await s.execute(select(ToolInvocation).where(
                ToolInvocation.tenant_id == tenant, ToolInvocation.idempotency_key == key))).scalar_one()
            assert rec.status == "RECONCILIATION_REQUIRED"
            assert rec.reconciliation_status == "PENDING"
            assert "TimeoutError" in (rec.last_error or "")
            events = (await s.execute(select(RunEvent).where(
                RunEvent.run_id == run_id,
                RunEvent.tenant_id == tenant,
                RunEvent.event_type == "TOOL_RECONCILIATION_REQUIRED",
            ))).scalars().all()
            assert len(events) == 1
        finally:
            await s.close()
    finally:
        await conn.execute("DELETE FROM agent_runs WHERE run_id=$1", run_id)
        await conn.close()


@pytest.mark.asyncio
async def test_unknown_discovery_and_require_reconciliation(real_postgres_dsn: str, tenant_ids):
    """UNKNOWN normal discovery + require -> RECONCILIATION_REQUIRED/PENDING."""
    tenant = tenant_ids[0]
    conn, run_id, new_session, _ = await _setup(real_postgres_dsn, tenant)
    try:
        key = await _begin_unknown(new_session, tenant, run_id, "call-discover")
        s = await new_session()
        try:
            rec = (await s.execute(select(ToolInvocation).where(
                ToolInvocation.tenant_id == tenant, ToolInvocation.idempotency_key == key))).scalar_one()
            assert rec.status == "UNKNOWN"
            assert rec.reconciliation_status == "NONE"
        finally:
            await s.close()

        s = await new_session()
        try:
            await _svc().require_reconciliation(s, tenant_id=tenant, idempotency_key=key,
                                                 fencing_token=1, reason="worker lost response")
            await s.commit()
        finally:
            await s.close()

        s = await new_session()
        try:
            rec = (await s.execute(select(ToolInvocation).where(
                ToolInvocation.tenant_id == tenant, ToolInvocation.idempotency_key == key))).scalar_one()
            assert rec.status == "RECONCILIATION_REQUIRED"
            assert rec.reconciliation_status == "PENDING"
        finally:
            await s.close()
    finally:
        await conn.execute("DELETE FROM agent_runs WHERE run_id=$1", run_id)
        await conn.close()


@pytest.mark.asyncio
async def test_require_reconciliation_is_idempotent(real_postgres_dsn: str, tenant_ids):
    """Repeated require calls produce no error and no duplicate state churn."""
    tenant = tenant_ids[0]
    conn, run_id, new_session, _ = await _setup(real_postgres_dsn, tenant)
    try:
        key = await _begin_unknown(new_session, tenant, run_id, "call-require-idem")
        for _ in range(3):
            s = await new_session()
            try:
                await _svc().require_reconciliation(s, tenant_id=tenant, idempotency_key=key,
                                                     fencing_token=1, reason="repeat")
                await s.commit()
            finally:
                await s.close()
        s = await new_session()
        try:
            rec = (await s.execute(select(ToolInvocation).where(
                ToolInvocation.tenant_id == tenant, ToolInvocation.idempotency_key == key))).scalar_one()
            assert rec.status == "RECONCILIATION_REQUIRED"
        finally:
            await s.close()
    finally:
        await conn.execute("DELETE FROM agent_runs WHERE run_id=$1", run_id)
        await conn.close()


@pytest.mark.asyncio
async def test_unknown_not_auto_finalised(real_postgres_dsn: str, tenant_ids):
    """UNKNOWN alone never becomes SUCCESS/FAILED without human confirmation."""
    tenant = tenant_ids[0]
    conn, run_id, new_session, _ = await _setup(real_postgres_dsn, tenant)
    try:
        key = await _begin_unknown(new_session, tenant, run_id, "call-noauto")
        s = await new_session()
        try:
            # begin() on UNKNOWN is rejected, not silently retried.
            with pytest.raises(InvocationUnknownError):
                await _svc().begin(s, run_id=run_id, tenant_id=tenant, tool_call_id="call-noauto",
                                   tool_name="demo.echo", arguments={"x": 1}, idempotency_key=key,
                                   fencing_token=2, lease_owner="w2", idempotent_provider=False)
            await s.rollback()
        finally:
            await s.close()

        s = await new_session()
        try:
            rec = (await s.execute(select(ToolInvocation).where(
                ToolInvocation.tenant_id == tenant, ToolInvocation.idempotency_key == key))).scalar_one()
            assert rec.status == "UNKNOWN"
        finally:
            await s.close()
    finally:
        await conn.execute("DELETE FROM agent_runs WHERE run_id=$1", run_id)
        await conn.close()


@pytest.mark.asyncio
async def test_human_reconcile_and_repeat_idempotent(real_postgres_dsn: str, tenant_ids):
    """Human finalisation via high fencing; repeated request reuses, no duplicate side effect."""
    tenant = tenant_ids[0]
    conn, run_id, new_session, _ = await _setup(real_postgres_dsn, tenant)
    try:
        key = await _begin_unknown(new_session, tenant, run_id, "call-human")
        s = await new_session()
        try:
            await _svc().require_reconciliation(s, tenant_id=tenant, idempotency_key=key,
                                                 fencing_token=1, reason="lost")
            await s.commit()
        finally:
            await s.close()

        s = await new_session()
        try:
            rec = (await s.execute(select(ToolInvocation).where(
                ToolInvocation.tenant_id == tenant, ToolInvocation.idempotency_key == key))).scalar_one()
            htok = human_reconciliation_fencing_token(rec)
            assert htok >= HUMAN_RECON_FENCING_BASE
            out = await _svc().reconcile(s, tenant_id=tenant, idempotency_key=key,
                                          fencing_token=htok, resolved_status="FAILED",
                                          resolved_result={"error": "human judged"},
                                          actor="admin", note="confirmed failed")
            await s.commit()
            assert out["status"] == "FAILED"
        finally:
            await s.close()

        # Repeated request with same decision -> idempotent reuse.
        s = await new_session()
        try:
            again = await _svc().reconcile(s, tenant_id=tenant, idempotency_key=key,
                                            fencing_token=human_reconciliation_fencing_token(
                                                (await s.execute(select(ToolInvocation).where(
                                                    ToolInvocation.tenant_id == tenant,
                                                    ToolInvocation.idempotency_key == key))).scalar_one()),
                                            resolved_status="FAILED",
                                            resolved_result={"error": "human judged"},
                                            actor="admin", note="confirmed failed")
            await s.commit()
            assert again.get("reused") is True
        finally:
            await s.close()

        s = await new_session()
        try:
            events = (await s.execute(select(RunEvent).where(
                RunEvent.run_id == run_id,
                RunEvent.tenant_id == tenant,
                RunEvent.event_type == "TOOL_RECONCILED",
            ))).scalars().all()
            assert len(events) == 1
        finally:
            await s.close()
    finally:
        await conn.execute("DELETE FROM agent_runs WHERE run_id=$1", run_id)
        await conn.close()


@pytest.mark.asyncio
async def test_stale_worker_cannot_override_human_final_state(real_postgres_dsn: str, tenant_ids):
    """After human finalises, a next Worker attempt (small token) cannot overwrite."""
    tenant = tenant_ids[0]
    conn, run_id, new_session, _ = await _setup(real_postgres_dsn, tenant)
    try:
        key = await _begin_unknown(new_session, tenant, run_id, "call-fence")
        s = await new_session()
        try:
            rec = (await s.execute(select(ToolInvocation).where(
                ToolInvocation.tenant_id == tenant, ToolInvocation.idempotency_key == key))).scalar_one()
            htok = human_reconciliation_fencing_token(rec)
            await _svc().reconcile(s, tenant_id=tenant, idempotency_key=key, fencing_token=htok,
                                    resolved_status="FAILED", resolved_result={"e": 1},
                                    actor="admin", note="final")
            await s.commit()
        finally:
            await s.close()

        # attempt=2/3 tokens are far below HUMAN_RECON_FENCING_BASE -> rejected.
        for stale in (2, 3):
            s = await new_session()
            try:
                with pytest.raises(InvocationConflictError):
                    await _svc().confirm(s, tenant_id=tenant, idempotency_key=key,
                                          fencing_token=stale, result={"v": "overwrite"})
                await s.rollback()
            finally:
                await s.close()
        s = await new_session()
        try:
            rec = (await s.execute(select(ToolInvocation).where(
                ToolInvocation.tenant_id == tenant, ToolInvocation.idempotency_key == key))).scalar_one()
            assert rec.status == "FAILED"
            assert rec.result == {"e": 1}
        finally:
            await s.close()
    finally:
        await conn.execute("DELETE FROM agent_runs WHERE run_id=$1", run_id)
        await conn.close()


@pytest.mark.asyncio
async def test_human_failed_then_new_worker_can_retry_and_complete(real_postgres_dsn: str, tenant_ids):
    """Human FAILED decision permits an explicit new Worker attempt and clean confirm."""
    tenant = tenant_ids[0]
    conn, run_id, new_session, _ = await _setup(real_postgres_dsn, tenant)
    try:
        key = await _begin_unknown(new_session, tenant, run_id, "call-safe-retry")
        s = await new_session()
        try:
            rec = (await s.execute(select(ToolInvocation).where(
                ToolInvocation.tenant_id == tenant, ToolInvocation.idempotency_key == key))).scalar_one()
            await _svc().reconcile(s, tenant_id=tenant, idempotency_key=key,
                                    fencing_token=human_reconciliation_fencing_token(rec),
                                    resolved_status="FAILED", resolved_result={"error": "human judged"},
                                    actor="admin", note="safe to retry")
            await s.commit()
        finally:
            await s.close()

        s = await new_session()
        try:
            rec = (await s.execute(select(ToolInvocation).where(
                ToolInvocation.tenant_id == tenant, ToolInvocation.idempotency_key == key))).scalar_one()
            retry_token = activity_fencing_token(rec, 2)
            assert retry_token == HUMAN_RECON_FENCING_BASE + 2
            decision = await _svc().begin(s, run_id=run_id, tenant_id=tenant,
                                           tool_call_id="call-safe-retry", tool_name="demo.echo",
                                           arguments={"x": 1}, idempotency_key=key,
                                           fencing_token=retry_token, lease_owner="w2", idempotent_provider=False)
            assert decision["decision"] == "execute"
            await s.commit()
        finally:
            await s.close()

        s = await new_session()
        try:
            await _svc().confirm(s, tenant_id=tenant, idempotency_key=key,
                                  fencing_token=retry_token, result={"echo": "ok"})
            await s.commit()
        finally:
            await s.close()

        s = await new_session()
        try:
            rec = (await s.execute(select(ToolInvocation).where(
                ToolInvocation.tenant_id == tenant, ToolInvocation.idempotency_key == key))).scalar_one()
            assert rec.status == "CONFIRMED"
            assert rec.reconciliation_status == "RESOLVED"
            assert rec.result == {"echo": "ok"}
        finally:
            await s.close()
    finally:
        await conn.execute("DELETE FROM agent_runs WHERE run_id=$1", run_id)
        await conn.close()


@pytest.mark.asyncio
async def test_concurrent_reconcile_single_winner(real_postgres_dsn: str, tenant_ids):
    """Two concurrent human reconciles: exactly one terminal write; loser gets idempotent/conflict."""
    import asyncio

    tenant = tenant_ids[0]
    conn, run_id, new_session, _ = await _setup(real_postgres_dsn, tenant)
    try:
        key = await _begin_unknown(new_session, tenant, run_id, "call-concurrent")
        s = await new_session()
        try:
            rec = (await s.execute(select(ToolInvocation).where(
                ToolInvocation.tenant_id == tenant, ToolInvocation.idempotency_key == key))).scalar_one()
            htok = human_reconciliation_fencing_token(rec)
        finally:
            await s.close()

        async def reconcile_one(status):
            sess = await new_session()
            try:
                try:
                    out = await _svc().reconcile(sess, tenant_id=tenant, idempotency_key=key,
                                                  fencing_token=htok, resolved_status=status,
                                                  resolved_result={"s": status}, actor="a", note="c")
                    await sess.commit()
                    return out
                except InvocationConflictError:
                    await sess.rollback()
                    return {"conflict": True}
            finally:
                await sess.close()

        outcomes = await asyncio.gather(reconcile_one("FAILED"), reconcile_one("CONFIRMED"))
        assert any(o.get("status") in {"FAILED", "CONFIRMED"} for o in outcomes)
        s = await new_session()
        try:
            rec = (await s.execute(select(ToolInvocation).where(
                ToolInvocation.tenant_id == tenant, ToolInvocation.idempotency_key == key))).scalar_one()
            assert rec.status in {"FAILED", "CONFIRMED"}
            assert rec.reconciliation_status == "RESOLVED"
        finally:
            await s.close()
    finally:
        await conn.execute("DELETE FROM agent_runs WHERE run_id=$1", run_id)
        await conn.close()


@pytest.mark.asyncio
async def test_cross_tenant_reconcile_isolated(real_postgres_dsn: str, tenant_ids):
    """A tenant cannot reconcile another tenant's invocation."""
    ta, tb = tenant_ids
    conn_a, run_a, sess_a, _ = await _setup(real_postgres_dsn, ta)
    conn_b, _, sess_b, _ = await _setup(real_postgres_dsn, tb)
    try:
        key = await _begin_unknown(sess_a, ta, run_a, "call-tenant")
        s = await sess_b()
        try:
            with pytest.raises(InvocationConflictError):
                await _svc().reconcile(s, tenant_id=tb, idempotency_key=key,
                                        fencing_token=HUMAN_RECON_FENCING_BASE + 99,
                                        resolved_status="FAILED", resolved_result=None,
                                        actor="intruder", note="x")
            await s.rollback()
        finally:
            await s.close()
        s = await sess_a()
        try:
            rec = (await s.execute(select(ToolInvocation).where(
                ToolInvocation.tenant_id == ta, ToolInvocation.idempotency_key == key))).scalar_one()
            assert rec.status == "UNKNOWN"
        finally:
            await s.close()
    finally:
        await conn_a.execute("DELETE FROM agent_runs WHERE run_id=$1", run_a)
        await conn_a.close()
        await conn_b.close()


@pytest.mark.asyncio
async def test_reconcile_api_cross_user_and_cross_tenant_denied():
    """Same-tenant peer and cross-tenant caller cannot reach another run's reconciliation."""
    run_id = uuid4()
    run = SimpleNamespace(tenant_id="tenant-a", user_id="owner")
    body = ReconcileRequest(idempotency_key="k", resolved_status="FAILED")

    peer = _principal("tenant-a", "peer", ["approval:approve"])
    async with _reconcile_client(peer, _FakeReconcileSession(run)) as client:
        resp = await client.post(f"/runs/{run_id}/reconcile", json=body.model_dump())
    assert resp.status_code == 404

    intruder = _principal("tenant-b", "owner", ["approval:approve"])
    async with _reconcile_client(intruder, _FakeReconcileSession(run)) as client:
        resp = await client.post(f"/runs/{run_id}/reconcile", json=body.model_dump())
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_reconcile_api_requires_manual_approval_scope():
    """Owner without approval/manage scope cannot finalise an UNKNOWN invocation."""
    run_id = uuid4()
    run = SimpleNamespace(tenant_id="tenant-a", user_id="owner")
    principal = _principal("tenant-a", "owner", ["agent:execute"])
    body = ReconcileRequest(idempotency_key="k", resolved_status="FAILED")

    async with _reconcile_client(principal, _FakeReconcileSession(run)) as client:
        resp = await client.post(f"/runs/{run_id}/reconcile", json=body.model_dump())
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_reconcile_api_uses_human_fencing_namespace(monkeypatch):
    """Scoped owner path derives the fencing token from the human namespace."""
    run_id = uuid4()
    run = SimpleNamespace(tenant_id="tenant-a", user_id="owner")
    record = SimpleNamespace(attempt_count=2)
    principal = _principal("tenant-a", "owner", ["approval:approve"])
    body = ReconcileRequest(idempotency_key="k", resolved_status="FAILED", note="manual")
    captured = {}

    async def fake_reconcile(self, session, **kwargs):
        captured.update(kwargs)
        return {"idempotency_key": kwargs["idempotency_key"], "status": "FAILED", "reused": False}

    monkeypatch.setattr(ToolInvocationService, "reconcile", fake_reconcile)
    async with _reconcile_client(principal, _FakeReconcileSession(run, record)) as client:
        resp = await client.post(f"/runs/{run_id}/reconcile", json=body.model_dump())
    assert resp.status_code == 200
    assert captured["fencing_token"] == HUMAN_RECON_FENCING_BASE + 3
    assert captured["actor"] == "owner"


def test_fencing_namespace_disjoint():
    assert HUMAN_RECON_FENCING_BASE > 3  # Temporal maximum_attempts=3
    pre_human = SimpleNamespace(fencing_token=1, attempt_count=1)
    post_human = SimpleNamespace(fencing_token=HUMAN_RECON_FENCING_BASE + 2, attempt_count=1)
    assert activity_fencing_token(pre_human, 2) == 2
    assert activity_fencing_token(post_human, 1) == HUMAN_RECON_FENCING_BASE + 1
    assert activity_fencing_token(post_human, 2) == HUMAN_RECON_FENCING_BASE + 2


def test_replay_ast_guard_still_clean():
    import ast
    from pathlib import Path
    root = Path(__file__).resolve().parents[1] / "src" / "agent_runtime"
    wf = ast.parse((root / "workflow.py").read_text(encoding="utf-8"))
    banned = [n.func.id for n in ast.walk(wf)
              if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
              and n.func.id in {"uuid4", "time", "monotonic", "random"}]
    assert banned == []
