"""Regression checks for the three-job role split migration."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

from agent_runtime.auth import can_cancel_run
from agent_runtime.contracts import Principal
from agent_runtime.knowledge_api import _require_knowledge_scope
from fastapi import HTTPException

MIGRATION = (
    Path(__file__).resolve().parents[1]
    / "migrations" / "versions" / "0027_job_role_split.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location("job_role_split_migration", MIGRATION)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_general_manager_is_governance_only():
    module = _load_migration()
    grants = set(module.ROLE_GRANTS["general_manager"])
    assert {"platform:manage", "user:manage", "security:read", "audit:read"} <= grants
    assert not grants & {
        "agent:execute",
        "run:create",
        "financial:case:create",
        "financial:case:review",
        "financial:case:approve",
        "approval:approve",
    }


def test_analyst_is_execution_only():
    module = _load_migration()
    grants = set(module.ROLE_GRANTS["analyst"])
    assert {
        "agent:execute", "run:create", "financial:case:create", "knowledge:read"
    } <= grants
    assert not grants & set(module.REVOKE_FROM_ANALYST)
    assert not grants & {"approval:approve", "financial:case:approve"}


def test_risk_controller_is_review_only():
    module = _load_migration()
    grants = set(module.ROLE_GRANTS["risk_controller"])
    assert {
        "runs:read:tenant", "runs:cancel:tenant", "financial:case:review",
        "financial:case:approve", "approval:approve", "financial:audit:read",
    } <= grants
    assert not grants & {
        "agent:execute", "run:create", "financial:case:create",
        "memory:write", "platform:manage", "user:manage",
        "skills:manage", "knowledge:write",
    }


def _principal(scopes: set[str]) -> Principal:
    return Principal(
        tenant_id="demo-bank",
        user_id="test-user",
        scopes=scopes,
        permissions=set(scopes),
        authentication_method="dev",
    )


def test_knowledge_scope_allows_all_three_jobs_to_read():
    module = _load_migration()
    for role in ("general_manager", "analyst", "risk_controller"):
        _require_knowledge_scope(_principal(set(module.ROLE_GRANTS[role])))


def test_knowledge_scope_keeps_write_governance_only():
    module = _load_migration()
    _require_knowledge_scope(_principal(set(module.ROLE_GRANTS["general_manager"])), write=True)
    for role in ("analyst", "risk_controller"):
        try:
            _require_knowledge_scope(_principal(set(module.ROLE_GRANTS[role])), write=True)
        except HTTPException:
            pass
        else:
            raise AssertionError(f"{role} must not write knowledge documents")


def test_cancel_scope_is_risk_only():
    module = _load_migration()
    risk = set(module.ROLE_GRANTS["risk_controller"])
    analyst = set(module.ROLE_GRANTS["analyst"])
    manager = set(module.ROLE_GRANTS["general_manager"])
    assert can_cancel_run(_principal(risk), "someone-else") is True
    assert can_cancel_run(_principal(analyst), "someone-else") is False
    assert can_cancel_run(_principal(manager), "someone-else") is False


def test_normalized_scope_migration_matches_role_matrix():
    module = _load_migration()
    normalize_path = MIGRATION.with_name("0029_normalize_job_role_scopes.py")
    spec = importlib.util.spec_from_file_location("normalize_scope_migration", normalize_path)
    assert spec and spec.loader
    normalize = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(normalize)
    for role, expected in normalize.ROLE_PERMISSIONS.items():
        assert expected == set(module.ROLE_GRANTS[role])


def test_keycloak_realm_has_job_roles_and_users():
    realm_path = (
        Path(__file__).resolve().parents[1]
        / "docker" / "keycloak" / "finora-realm.json"
    )
    realm = json.loads(realm_path.read_text(encoding="utf-8"))
    roles = {item["name"] for item in realm["roles"]["realm"]}
    users = {item["username"]: set(item.get("realmRoles", [])) for item in realm["users"]}
    assert {"general_manager", "analyst", "risk_controller"} <= roles
    assert users["manager-01"] == {"general_manager"}
    assert users["analyst-01"] == {"analyst"}
    assert users["risk-01"] == {"risk_controller"}
