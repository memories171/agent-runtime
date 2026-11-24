"""阶段 1：Alembic 0013 迁移结构测试（不改动数据库）。"""
from __future__ import annotations

import importlib.util
from pathlib import Path

MIGRATION = (
    Path(__file__).resolve().parents[1]
    / "migrations" / "versions" / "0013_agent_orchestration.py"
)


def _load():
    spec = importlib.util.spec_from_file_location("migration_0013", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_migration_head_and_tables():
    module = _load()
    assert module.revision == "0013_agent_orchestration"
    assert module.down_revision == "0012_task_graph_versions"
    assert set(module.NEW_TABLES) == {"capability_tokens", "agent_delegations"}


def test_upgrade_adds_rls_and_audit_columns():
    source = MIGRATION.read_text(encoding="utf-8")
    assert "ENABLE ROW LEVEL SECURITY" in source
    assert "FORCE ROW LEVEL SECURITY" in source
    assert "tenant_isolation_{table}" in source
    assert "for table in NEW_TABLES:" in source
    for column in ("run_kind", "parent_run_id", "delegation_id", "capability_token_id", "depth"):
        assert column in source
    assert "fk_agent_delegation_graph_node_tenant" in source
    assert "fk_capability_token_parent_tenant" in source


def test_downgrade_is_reversible():
    source = MIGRATION.read_text(encoding="utf-8")
    downgrade = source.split("def downgrade():", 1)[1]
    assert "DROP TABLE IF EXISTS agent_delegations" in downgrade
    assert "DROP TABLE IF EXISTS capability_tokens" in downgrade
    assert "DROP POLICY IF EXISTS tenant_isolation_{table}" in downgrade
    assert "for table in NEW_TABLES:" in downgrade
    assert '_drop_column("agent_runs", "run_kind")' in downgrade
