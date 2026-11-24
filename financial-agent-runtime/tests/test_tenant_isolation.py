from __future__ import annotations

import asyncpg
import pytest

from agent_runtime.authorization.policies import AuthorizationRequest
from agent_runtime.authorization.service import evaluate
from agent_runtime.contracts import Principal


async def _seed_identity(conn, tenant_id: str, user_id: str, role_id: str):
    await conn.execute(
        "INSERT INTO tenants (tenant_id, display_name, status) VALUES ($1, $2, 'active') ON CONFLICT DO NOTHING",
        tenant_id,
        tenant_id,
    )
    await conn.execute(
        "INSERT INTO users (user_id, display_name, status) VALUES ($1, $2, 'active') ON CONFLICT DO NOTHING",
        user_id,
        user_id,
    )
    await conn.execute(
        """INSERT INTO tenant_memberships (membership_id, tenant_id, user_id, status)
           VALUES (gen_random_uuid(), $1, $2, 'active')
           ON CONFLICT (tenant_id, user_id) DO UPDATE SET status = 'active'""",
        tenant_id,
        user_id,
    )
    await conn.execute(
        """INSERT INTO user_roles (user_role_id, tenant_id, user_id, role_id, granted_by)
           VALUES (gen_random_uuid(), $1, $2, $3, 'seed')
           ON CONFLICT (tenant_id, user_id, role_id) DO NOTHING""",
        tenant_id,
        user_id,
        role_id,
    )


async def _load_principal(conn, user_id: str, requested_tenant: str) -> Principal | None:
    row = await conn.fetchrow(
        """SELECT m.tenant_id, m.organization_id, u.user_id, ur.role_id
           FROM tenant_memberships m
           JOIN users u ON u.user_id = m.user_id
           JOIN user_roles ur ON ur.tenant_id = m.tenant_id AND ur.user_id = m.user_id
           WHERE u.user_id = $1 AND m.tenant_id = $2 AND m.status = 'active' AND u.status = 'active'""",
        user_id,
        requested_tenant,
    )
    if row is None:
        return None
    permissions = {
        item["permission_id"]
        for item in await conn.fetch("SELECT permission_id FROM role_permissions WHERE role_id = $1", row["role_id"])
    }
    return Principal(
        tenant_id=row["tenant_id"],
        user_id=row["user_id"],
        scopes=set(permissions),
        permissions=set(permissions),
        roles={row["role_id"]},
        organization_id=row["organization_id"],
        authentication_method="oidc",
    )


@pytest.mark.asyncio
async def test_user_can_access_only_own_tenant(real_postgres_dsn: str):
    conn = await asyncpg.connect(real_postgres_dsn)
    try:
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.system_context', 'identity_resolver', true)")
            await _seed_identity(conn, "tenant-a", "user-a", "analyst")
            await _seed_identity(conn, "tenant-b", "user-b", "analyst")

        async with conn.transaction():
            await conn.execute("SELECT set_config('app.tenant_id', 'tenant-a', true)")
            principal_a = await _load_principal(conn, "user-a", "tenant-a")
        assert principal_a is not None
        assert "run:create" in principal_a.permissions

        allowed = evaluate(AuthorizationRequest(
            principal=principal_a,
            action="read",
            resource_type="agent_run",
            resource_id="run-a",
            tenant_id="tenant-a",
            permission="run:read",
        ))
        assert allowed.allowed is True

        denied = evaluate(AuthorizationRequest(
            principal=principal_a,
            action="read",
            resource_type="agent_run",
            resource_id="run-b",
            tenant_id="tenant-b",
            permission="run:read",
        ))
        assert denied.allowed is False

        # 模拟前端把 X-Tenant-ID 改成 tenant-b：用户不属于该租户，身份解析失败。
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.tenant_id', 'tenant-b', true)")
            header_switch = await _load_principal(conn, "user-a", "tenant-b")
        assert header_switch is None

        async with conn.transaction():
            await conn.execute("SELECT set_config('app.tenant_id', 'tenant-b', true)")
            principal_b = await _load_principal(conn, "user-b", "tenant-b")
        assert principal_b is not None
        denied_b = evaluate(AuthorizationRequest(
            principal=principal_b,
            action="read",
            resource_type="agent_run",
            resource_id="run-a",
            tenant_id="tenant-a",
            permission="run:read",
        ))
        assert denied_b.allowed is False
    finally:
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.system_context', 'identity_resolver', true)")
            await conn.execute("DELETE FROM user_roles WHERE user_id IN ('user-a', 'user-b')")
            await conn.execute("DELETE FROM tenant_memberships WHERE user_id IN ('user-a', 'user-b')")
            await conn.execute("DELETE FROM users WHERE user_id IN ('user-a', 'user-b')")
            await conn.execute("DELETE FROM tenants WHERE tenant_id IN ('tenant-a', 'tenant-b')")
        await conn.close()
