"""Structural tests for Agent Tree migrations (no database required)."""
from __future__ import annotations

import importlib.util
from pathlib import Path

VERSIONS = Path(__file__).resolve().parents[1] / "migrations" / "versions"
MIGRATION_0015 = VERSIONS / "0015_agent_monitoring_tree.py"
MIGRATION_0016 = VERSIONS / "0016_repair_agent_tree_root_backfill.py"


def _load(path: Path):
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_head_and_parent():
    module = _load(MIGRATION_0016)
    assert module.revision == "0016_repair_tree_root_backfill"
    assert module.down_revision == "0015_agent_monitoring_tree"


def test_upgrade_adds_columns_backfill_and_indexes():
    source = MIGRATION_0015.read_text(encoding="utf-8")
    assert "root_run_id UUID" in source
    assert "updated_at TIMESTAMPTZ" in source
    assert "WITH RECURSIVE lineage" in source
    assert "p.tenant_id = l.tenant_id" in source  # tenant-safe backfill
    assert "l.depth < 32" in source  # historical 0015 logic, repaired by 0016
    assert "ix_agent_delegation_root" in source


def test_0015_is_unchanged_and_0016_repairs_with_explicit_paths():
    old_source = MIGRATION_0015.read_text(encoding="utf-8")
    assert "l.depth < 32" in old_source
    source = MIGRATION_0016.read_text(encoding="utf-8")
    assert "path_runs" in source
    assert "path_edges" in source
    assert "is_cycle" in source
    assert "had_cross_tenant" in source
    assert "MAX_HOPS = 1024" in source  # hard safety bound, not a root proxy
    assert "agent_delegation_root_backfill_anomalies" in source


def test_downgrade_is_reversible():
    source = MIGRATION_0015.read_text(encoding="utf-8")
    downgrade = source.split("def downgrade():", 1)[1]
    assert "DROP COLUMN IF EXISTS updated_at" in downgrade
    assert "DROP COLUMN IF EXISTS root_run_id" in downgrade
    assert "DROP INDEX IF EXISTS" in downgrade
