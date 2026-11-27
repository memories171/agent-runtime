"""新增租户账号：同时写入 Keycloak realm 配置与 Finora 数据库。

用法示例：
  python scripts/seed_tenant_user.py \
    --tenant-id tenant-c --tenant-name "Tenant C" \
    --org-id org-c --org-name "Org C" \
    --user-id user-c --password 'UserC@2026' --role read_only_user
"""

from __future__ import annotations

import argparse
import json
import subprocess
import tempfile
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
REALM_FILE = ROOT / "docker" / "keycloak" / "finora-realm.json"


def upsert_realm_user(args: argparse.Namespace) -> None:
    realm = json.loads(REALM_FILE.read_text(encoding="utf-8"))

    roles = realm.setdefault("roles", {}).setdefault("realm", [])
    if not any(role.get("name") == args.role for role in roles):
        roles.append({"name": args.role, "description": f"Seeded {args.role} role"})

    users = realm.setdefault("users", [])
    user = next((item for item in users if item.get("username") == args.user_id), None)
    if user is None:
        user = {"username": args.user_id, "enabled": True}
        users.append(user)
    user.update({
        "enabled": True,
        "email": args.email or f"{args.user_id}@example.com",
        "emailVerified": True,
        "firstName": args.user_id.split("-")[0].title(),
        "lastName": "User",
        "credentials": [{"type": "password", "value": args.password, "temporary": False}],
        "attributes": {
            "tenant_id": [args.tenant_id],
            "organization_id": [args.org_id],
        },
        "realmRoles": [args.role],
    })
    REALM_FILE.write_text(json.dumps(realm, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def reimport_realm(args: argparse.Namespace) -> None:
    base = args.keycloak_url.rstrip("/")
    token = httpx.post(
        base + "/realms/master/protocol/openid-connect/token",
        data={
            "client_id": "admin-cli",
            "username": args.admin_user,
            "password": args.admin_password,
            "grant_type": "password",
        },
        timeout=10,
    ).json()["access_token"]
    headers = {"Authorization": "Bearer " + token, "Content-Type": "application/json"}
    deleted = httpx.delete(base + "/admin/realms/finora", headers=headers, timeout=10)
    created = httpx.post(
        base + "/admin/realms",
        headers=headers,
        content=REALM_FILE.read_bytes(),
        timeout=20,
    )
    print(f"keycloak_delete={deleted.status_code} create={created.status_code}")
    if created.status_code >= 300:
        raise SystemExit(created.text)


def seed_database(args: argparse.Namespace) -> None:
    # Bind CLI values as psql variables; never interpolate them into SQL text.
    sql = """
INSERT INTO tenants (tenant_id, display_name, status)
VALUES (:'tenant_id', :'tenant_name', 'active')
ON CONFLICT DO NOTHING;

INSERT INTO organizations (organization_id, tenant_id, name, status)
VALUES (:'org_id', :'tenant_id', :'org_name', 'active')
ON CONFLICT DO NOTHING;

INSERT INTO users (user_id, display_name, email, status)
VALUES (:'user_id', :'user_id', :'email', 'active')
ON CONFLICT DO NOTHING;

INSERT INTO tenant_memberships (membership_id, tenant_id, user_id, organization_id, status)
VALUES (gen_random_uuid(), :'tenant_id', :'user_id', :'org_id', 'active')
ON CONFLICT (tenant_id, user_id)
DO UPDATE SET status = 'active', organization_id = :'org_id';

INSERT INTO user_roles (user_role_id, tenant_id, user_id, role_id, granted_by)
VALUES (gen_random_uuid(), :'tenant_id', :'user_id', :'role', 'seed')
ON CONFLICT (tenant_id, user_id, role_id) DO NOTHING;
"""
    with tempfile.NamedTemporaryFile("w", suffix=".sql", delete=False, encoding="utf-8") as handle:
        handle.write(sql)
        local_path = Path(handle.name)
    try:
        subprocess.run(["docker", "compose", "cp", str(local_path), "postgres:/tmp/finora_seed_user.sql"], cwd=ROOT, check=True)
        subprocess.run(
            ["docker", "compose", "exec", "-T", "postgres", "psql", "-U", "agent", "-d", "agent_runtime",
             "-v", f"tenant_id={args.tenant_id}",
             "-v", f"tenant_name={args.tenant_name}",
             "-v", f"org_id={args.org_id}",
             "-v", f"org_name={args.org_name}",
             "-v", f"user_id={args.user_id}",
             "-v", f"email={args.email or args.user_id + '@example.com'}",
             "-v", f"role={args.role}",
             "-f", "/tmp/finora_seed_user.sql"],
            cwd=ROOT,
            check=True,
        )
        print("database_seed=ok")
    finally:
        local_path.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tenant-id", required=True)
    parser.add_argument("--tenant-name", default=None)
    parser.add_argument("--org-id", default=None)
    parser.add_argument("--org-name", default=None)
    parser.add_argument("--user-id", required=True)
    parser.add_argument("--password", required=True)
    parser.add_argument("--role", default="read_only_user")
    parser.add_argument("--email", default=None)
    parser.add_argument("--keycloak-url", default="http://localhost:8090")
    parser.add_argument("--admin-user", default="admin")
    parser.add_argument("--admin-password", default="admin")
    args = parser.parse_args()
    args.tenant_name = args.tenant_name or args.tenant_id
    args.org_id = args.org_id or f"org-{args.tenant_id}"
    args.org_name = args.org_name or args.org_id

    upsert_realm_user(args)
    reimport_realm(args)
    seed_database(args)
    print(f"created user={args.user_id} tenant={args.tenant_id} role={args.role}")


if __name__ == "__main__":
    main()
