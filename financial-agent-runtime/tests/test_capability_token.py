"""Capability Token 安全机制的真实 PostgreSQL 测试。"""
from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4

import asyncpg
import pytest

from agent_runtime import activities
from agent_runtime.contracts import Principal
from agent_runtime.orchestration.models import CapabilityGrant
from agent_runtime.orchestration.token_service import CapabilityDenied, CapabilityTokenService
from agent_runtime.tools.gateway import ApprovalRequired, authorize_tool_with_capability
from agent_runtime.tools.registry import registry

TENANT_A, TENANT_B = "cap-token-a", "cap-token-b"
ALL_CAPS = {"agent:execute", "external:send", "financial:customer:read", "data:sensitive"}
BASE_SCOPE = {"tenant_id": TENANT_A, "resources": "*", "classifications": "*"}


def _principal(tenant: str, caps: set[str]) -> Principal:
    return Principal(
        tenant_id=tenant, user_id="analyst-01", scopes=set(caps), permissions=set(caps),
        authentication_method="dev", session_id="cap-token-session",
    )


async def _seed_runs(dsn: str):
    ids = {
        "root_a": uuid4(), "child_a": uuid4(),
        "root_b": uuid4(), "child_b": uuid4(),
    }
    conn = await asyncpg.connect(dsn)
    try:
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.system_context', 'outbox_dispatcher', true)")
            await conn.execute("SELECT set_config('app.tenant_id', $1, true)", TENANT_A)
            for key, tenant, kind in (
                ("root_a", TENANT_A, "ROOT"), ("child_a", TENANT_A, "SUB_AGENT"),
                ("root_b", TENANT_B, "ROOT"), ("child_b", TENANT_B, "SUB_AGENT"),
            ):
                await conn.execute(
                    """INSERT INTO agent_runs
                       (run_id, tenant_id, user_id, session_id, agent_id, prompt, status, run_kind, metadata)
                       VALUES ($1::uuid, $2, 'analyst-01', 'cap-token-session', 'agent', 'cap token test',
                               'RUNNING', $3, '{}'::json)
                       ON CONFLICT (run_id) DO NOTHING""",
                    ids[key], tenant, kind,
                )
    finally:
        await conn.close()
    return ids


async def _cleanup(dsn: str, ids: dict) -> None:
    conn = await asyncpg.connect(dsn)
    try:
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.system_context', 'outbox_dispatcher', true)")
            await conn.execute("SELECT set_config('app.tenant_id', $1, true)", TENANT_A)
            runs = list(ids.values())
            await conn.execute("DELETE FROM tool_invocations WHERE tenant_id = ANY($1::text[])", [TENANT_A, TENANT_B])
            await conn.execute("DELETE FROM auth_audit_logs WHERE tenant_id = ANY($1::text[])", [TENANT_A, TENANT_B])
            await conn.execute("DELETE FROM run_events WHERE run_id = ANY($1::uuid[])", runs)
            await conn.execute("DELETE FROM capability_tokens WHERE tenant_id = ANY($1::text[])", [TENANT_A, TENANT_B])
            await conn.execute("DELETE FROM agent_runs WHERE run_id = ANY($1::uuid[])", runs)
    finally:
        await conn.close()


def _grant(caps: set[str], scope: dict | None = None, *, max_depth: int = 2, timeout: int = 600) -> CapabilityGrant:
    return CapabilityGrant(
        capabilities=set(caps),
        resource_scope=scope or BASE_SCOPE,
        max_depth=max_depth,
        timeout_seconds=timeout,
    )


@pytest.mark.asyncio
async def test_root_issue_and_child_attenuation(real_postgres_dsn: str):
    ids = await _seed_runs(real_postgres_dsn)
    try:
        principal = _principal(TENANT_A, ALL_CAPS)
        root = await CapabilityTokenService.issue_root(
            principal, ids["root_a"], ttl_seconds=3600, max_depth=3,
        )
        assert root.depth == 0 and root.max_depth == 3
        assert root.capabilities == ALL_CAPS
        assert root.status == "ACTIVE"
        assert root.subject_run_id == ids["root_a"]

        child = await CapabilityTokenService.attenuate(
            root.token_id,
            _grant({"agent:execute"}, {"tenant_id": TENANT_A, "resources": ["customer:c1"], "classifications": "*"}),
            child_run_id=ids["child_a"], subject_agent_id="researcher",
            tenant_id=TENANT_A, actor=principal,
        )
        assert child.parent_token_id == root.token_id
        assert child.depth == 1
        assert child.capabilities == {"agent:execute"}
        assert child.expires_at <= root.expires_at
        validated = await CapabilityTokenService.validate(
            child.token_id, TENANT_A,
            required_capabilities={"agent:execute"},
            resource_scope={"resources": ["customer:c1"]},
            subject_run_id=ids["child_a"],
        )
        assert validated.token_id == child.token_id
    finally:
        await _cleanup(real_postgres_dsn, ids)


@pytest.mark.asyncio
async def test_capability_escalation_is_rejected(real_postgres_dsn: str):
    ids = await _seed_runs(real_postgres_dsn)
    try:
        limited = _principal(TENANT_A, {"agent:execute"})
        root = await CapabilityTokenService.issue_root(limited, ids["root_a"], ttl_seconds=3600)
        with pytest.raises(CapabilityDenied) as raised:
            await CapabilityTokenService.attenuate(
                root.token_id, _grant({"external:send"}),
                child_run_id=ids["child_a"], subject_agent_id="researcher",
                tenant_id=TENANT_A, actor=_principal(TENANT_A, ALL_CAPS),
            )
        assert raised.value.code == "capability_escalation"
    finally:
        await _cleanup(real_postgres_dsn, ids)


@pytest.mark.asyncio
async def test_resource_scope_escalation_is_rejected(real_postgres_dsn: str):
    ids = await _seed_runs(real_postgres_dsn)
    try:
        principal = _principal(TENANT_A, ALL_CAPS)
        root = await CapabilityTokenService.issue_root(principal, ids["root_a"], ttl_seconds=3600, max_depth=3)
        child = await CapabilityTokenService.attenuate(
            root.token_id,
            _grant({"agent:execute"}, {"tenant_id": TENANT_A, "resources": ["customer:c1"], "classifications": "*"}),
            child_run_id=ids["child_a"], subject_agent_id="researcher",
            tenant_id=TENANT_A, actor=principal,
        )
        with pytest.raises(CapabilityDenied) as raised:
            await CapabilityTokenService.attenuate(
                child.token_id,
                _grant({"agent:execute"}, {"tenant_id": TENANT_A, "resources": ["customer:c2"], "classifications": "*"}),
                child_run_id=ids["child_a"], subject_agent_id="sub",
                tenant_id=TENANT_A, actor=principal,
            )
        assert raised.value.code == "resource_scope_exceeded"

        with pytest.raises(CapabilityDenied) as validated:
            await CapabilityTokenService.validate(
                child.token_id, TENANT_A, resource_scope={"resources": ["customer:c2"]},
            )
        assert validated.value.code == "resource_scope_exceeded"
    finally:
        await _cleanup(real_postgres_dsn, ids)


@pytest.mark.asyncio
async def test_expired_and_revoked_tokens_are_rejected(real_postgres_dsn: str):
    ids = await _seed_runs(real_postgres_dsn)
    try:
        principal = _principal(TENANT_A, ALL_CAPS)
        expired = await CapabilityTokenService.issue_root(principal, ids["root_a"], ttl_seconds=3600)
        revoked = await CapabilityTokenService.issue_root(
            principal, ids["child_a"], ttl_seconds=3600, token_id=uuid4(),
        )

        conn = await asyncpg.connect(real_postgres_dsn)
        try:
            async with conn.transaction():
                await conn.execute("SELECT set_config('app.system_context', 'outbox_dispatcher', true)")
                await conn.execute("SELECT set_config('app.tenant_id', $1, true)", TENANT_A)
                await conn.execute(
                    "UPDATE capability_tokens SET expires_at = now() - interval '1 minute' WHERE token_id = $1::uuid",
                    expired.token_id,
                )
        finally:
            await conn.close()

        with pytest.raises(CapabilityDenied) as expired_error:
            await CapabilityTokenService.validate(expired.token_id, TENANT_A)
        assert expired_error.value.code == "token_expired"

        await CapabilityTokenService.revoke(revoked.token_id, TENANT_A, actor=principal)
        with pytest.raises(CapabilityDenied) as revoked_error:
            await authorize_tool_with_capability(
                principal.model_dump(mode="json"), TENANT_A,
                registry.get("demo.echo"), str(revoked.token_id),
            )
        assert revoked_error.value.code == "token_revoked"
    finally:
        await _cleanup(real_postgres_dsn, ids)


@pytest.mark.asyncio
async def test_revoked_parent_cannot_delegate_or_authorize_descendant(real_postgres_dsn: str):
    ids = await _seed_runs(real_postgres_dsn)
    try:
        principal = _principal(TENANT_A, ALL_CAPS)
        root = await CapabilityTokenService.issue_root(principal, ids["root_a"], ttl_seconds=3600, max_depth=3)
        child = await CapabilityTokenService.attenuate(
            root.token_id, _grant({"agent:execute"}),
            child_run_id=ids["child_a"], subject_agent_id="researcher",
            tenant_id=TENANT_A, actor=principal,
        )
        await CapabilityTokenService.revoke(root.token_id, TENANT_A, actor=principal)

        with pytest.raises(CapabilityDenied):
            await CapabilityTokenService.attenuate(
                root.token_id, _grant({"agent:execute"}),
                child_run_id=ids["child_a"], subject_agent_id="researcher2",
                tenant_id=TENANT_A, actor=principal,
            )
        with pytest.raises(CapabilityDenied) as descendant:
            await CapabilityTokenService.validate(child.token_id, TENANT_A)
        assert descendant.value.code == "token_revoked"
    finally:
        await _cleanup(real_postgres_dsn, ids)


@pytest.mark.asyncio
async def test_cross_tenant_token_is_rejected(real_postgres_dsn: str):
    ids = await _seed_runs(real_postgres_dsn)
    try:
        principal_a = _principal(TENANT_A, ALL_CAPS)
        root = await CapabilityTokenService.issue_root(principal_a, ids["root_a"], ttl_seconds=3600)
        with pytest.raises(CapabilityDenied):
            await CapabilityTokenService.validate(root.token_id, TENANT_B)
        with pytest.raises(CapabilityDenied):
            await authorize_tool_with_capability(
                _principal(TENANT_B, ALL_CAPS).model_dump(mode="json"), TENANT_B,
                registry.get("demo.echo"), str(root.token_id),
            )
    finally:
        await _cleanup(real_postgres_dsn, ids)


@pytest.mark.asyncio
async def test_max_delegation_depth_is_enforced(real_postgres_dsn: str):
    ids = await _seed_runs(real_postgres_dsn)
    try:
        principal = _principal(TENANT_A, ALL_CAPS)
        root = await CapabilityTokenService.issue_root(principal, ids["root_a"], ttl_seconds=3600, max_depth=1)
        child = await CapabilityTokenService.attenuate(
            root.token_id, _grant({"agent:execute"}, max_depth=1),
            child_run_id=ids["child_a"], subject_agent_id="researcher",
            tenant_id=TENANT_A, actor=principal,
        )
        with pytest.raises(CapabilityDenied) as raised:
            await CapabilityTokenService.attenuate(
                child.token_id, _grant({"agent:execute"}, max_depth=1),
                child_run_id=ids["child_a"], subject_agent_id="sub",
                tenant_id=TENANT_A, actor=principal,
            )
        assert raised.value.code == "max_depth_exceeded"
    finally:
        await _cleanup(real_postgres_dsn, ids)


@pytest.mark.asyncio
async def test_high_risk_tool_approval_cannot_be_bypassed(real_postgres_dsn: str):
    ids = await _seed_runs(real_postgres_dsn)
    try:
        principal = _principal(TENANT_A, ALL_CAPS)
        root = await CapabilityTokenService.issue_root(principal, ids["root_a"], ttl_seconds=3600)
        child = await CapabilityTokenService.attenuate(
            root.token_id, _grant({"agent:execute", "external:send"}),
            child_run_id=ids["child_a"], subject_agent_id="researcher",
            tenant_id=TENANT_A, actor=principal,
        )
        with pytest.raises(ApprovalRequired):
            await authorize_tool_with_capability(
                principal.model_dump(mode="json"), TENANT_A,
                registry.get("demo.notify"), str(child.token_id),
                approval_granted=False,
            )
        approved = await authorize_tool_with_capability(
            principal.model_dump(mode="json"), TENANT_A,
            registry.get("demo.notify"), str(child.token_id),
            approval_granted=True,
        )
        assert approved.token_id == child.token_id
    finally:
        await _cleanup(real_postgres_dsn, ids)


@pytest.mark.asyncio
async def test_legacy_without_token_remains_compatible(real_postgres_dsn: str, monkeypatch):
    ids = await _seed_runs(real_postgres_dsn)
    try:
        principal = _principal(TENANT_A, ALL_CAPS).model_dump(mode="json")
        decision = await activities.authorize_tool_activity({
            "tool_name": "demo.echo", "principal": principal, "tenant_id": TENANT_A,
        })
        assert decision == {
            "allowed": True, "requires_approval": False,
            "risk_level": "low", "requires_distinct_approver": False,
        }

        monkeypatch.setattr(activities.activity, "info", lambda: SimpleNamespace(attempt=1))
        result = await activities.execute_tool_activity({
            "tool_name": "demo.echo", "arguments": {"text": "legacy"},
            "principal": principal, "tenant_id": TENANT_A,
            "run_id": str(ids["root_a"]), "idempotency_key": "legacy-token-test",
        })
        assert result == {"echo": "legacy"}
    finally:
        await _cleanup(real_postgres_dsn, ids)


@pytest.mark.asyncio
async def test_revocation_between_authorize_and_execute_is_rechecked(real_postgres_dsn: str, monkeypatch):
    ids = await _seed_runs(real_postgres_dsn)
    try:
        principal = _principal(TENANT_A, ALL_CAPS)
        payload = principal.model_dump(mode="json")
        token = await CapabilityTokenService.issue_root(principal, ids["root_a"], ttl_seconds=3600)
        await authorize_tool_with_capability(
            payload, TENANT_A, registry.get("demo.echo"), str(token.token_id),
        )
        await CapabilityTokenService.revoke(token.token_id, TENANT_A, actor=principal)

        monkeypatch.setattr(activities.activity, "info", lambda: SimpleNamespace(attempt=1))
        with pytest.raises(CapabilityDenied) as raised:
            await activities.execute_tool_activity({
                "tool_name": "demo.echo", "arguments": {"text": "must not execute"},
                "principal": payload, "tenant_id": TENANT_A,
                "run_id": str(ids["root_a"]), "idempotency_key": "revoked-token-test",
                "capability_token_id": str(token.token_id),
            })
        assert raised.value.code == "token_revoked"

        conn = await asyncpg.connect(real_postgres_dsn)
        try:
            async with conn.transaction():
                await conn.execute("SELECT set_config('app.system_context', 'outbox_dispatcher', true)")
                count = await conn.fetchval(
                    "SELECT count(*) FROM tool_invocations WHERE idempotency_key = 'revoked-token-test'"
                )
            assert count == 0
        finally:
            await conn.close()
    finally:
        await _cleanup(real_postgres_dsn, ids)


@pytest.mark.asyncio
async def test_subject_run_binding_is_enforced(real_postgres_dsn: str):
    ids = await _seed_runs(real_postgres_dsn)
    try:
        principal = _principal(TENANT_A, ALL_CAPS)
        root = await CapabilityTokenService.issue_root(principal, ids["root_a"], ttl_seconds=3600)
        with pytest.raises(CapabilityDenied) as raised:
            await CapabilityTokenService.validate(
                root.token_id, TENANT_A, subject_run_id=ids["child_b"],
            )
        assert raised.value.code == "subject_run_mismatch"
    finally:
        await _cleanup(real_postgres_dsn, ids)
