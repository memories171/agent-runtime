"""Repair Agent Tree root_run_id backfill with cycle-safe explicit path tracking.

Revision ID: 0016_repair_tree_root_backfill
Revises: 0015_agent_monitoring_tree

0015 used ``depth < 32`` as both a recursion guard and a root proxy, so a legal
34-level chain was assigned the depth-32 intermediate node as its root. This
migration re-resolves every delegation with an explicit visited-run/edge path,
stops on cycles, ambiguous parents, cross-tenant runs and a hard safety cap.
Unresolvable rows are set back to NULL and reported in a permanent audit table.

0015 is already deployed in the local database and no shared-environment
evidence permits editing it, so this is intentionally a follow-up migration.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0016_repair_tree_root_backfill"
down_revision = "0015_agent_monitoring_tree"
branch_labels = None
depends_on = None

REPORT_TABLE = "agent_delegation_root_backfill_anomalies"
MAX_HOPS = 1024

_CREATE_REPORT_TABLE = f"""
CREATE TABLE IF NOT EXISTS {REPORT_TABLE} (
    tenant_id VARCHAR(128) NOT NULL,
    delegation_id UUID NOT NULL,
    reason TEXT NOT NULL,
    root_run_id_before UUID,
    detected_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, delegation_id, reason)
)
"""

_CLEAR_REPORT_TABLE = f"DELETE FROM {REPORT_TABLE}"

_RESOLVE_ROOTS = f"""
CREATE TEMP TABLE delegation_root_resolution AS
WITH RECURSIVE walk (
    tenant_id,
    delegation_id,
    current_run_id,
    path_runs,
    path_edges,
    cycle_detected,
    ambiguous_parent,
    cross_tenant_run,
    depth_cap_hit,
    done,
    hops
) AS (
    SELECT
        d.tenant_id,
        d.delegation_id,
        d.parent_run_id,
        ARRAY[d.parent_run_id]::uuid[],
        ARRAY[]::uuid[],
        false,
        false,
        EXISTS (
            SELECT 1 FROM agent_runs ar
            WHERE ar.run_id = d.parent_run_id AND ar.tenant_id <> d.tenant_id
        ),
        false,
        EXISTS (
            SELECT 1 FROM agent_runs ar
            WHERE ar.run_id = d.parent_run_id AND ar.tenant_id <> d.tenant_id
        ),
        1
    FROM agent_delegations d
    UNION ALL
    SELECT
        w.tenant_id,
        w.delegation_id,
        CASE
            WHEN p.parent_count = 1
                 AND NOT w.done
                 AND NOT w.cycle_detected
                 AND NOT w.ambiguous_parent
                 AND NOT w.cross_tenant_run
                 AND NOT s.is_cycle
                 AND NOT s.is_cross
                 AND w.hops + 1 < {MAX_HOPS}
                THEN p.parent_run_id
            ELSE w.current_run_id
        END,
        CASE
            WHEN p.parent_count = 1
                 AND NOT w.done
                 AND NOT w.cycle_detected
                 AND NOT w.ambiguous_parent
                 AND NOT w.cross_tenant_run
                 AND NOT s.is_cycle
                 AND NOT s.is_cross
                 AND w.hops + 1 < {MAX_HOPS}
                THEN w.path_runs || p.parent_run_id
            ELSE w.path_runs
        END,
        CASE
            WHEN p.parent_count = 1
                 AND NOT w.done
                 AND NOT w.cycle_detected
                 AND NOT w.ambiguous_parent
                 AND NOT w.cross_tenant_run
                 AND NOT s.is_cycle
                 AND NOT s.is_cross
                 AND w.hops + 1 < {MAX_HOPS}
                THEN w.path_edges || p.delegation_id
            ELSE w.path_edges
        END,
        w.cycle_detected OR s.is_cycle,
        w.ambiguous_parent OR p.parent_count > 1,
        w.cross_tenant_run OR s.is_cross,
        w.depth_cap_hit OR w.hops + 1 >= {MAX_HOPS},
        w.done
            OR p.parent_count <> 1
            OR s.is_cycle
            OR s.is_cross
            OR w.hops + 1 >= {MAX_HOPS},
        w.hops + 1
    FROM walk w
    LEFT JOIN LATERAL (
        SELECT
            count(*)::int AS parent_count,
            min(p.delegation_id::text)::uuid AS delegation_id,
            min(p.parent_run_id::text)::uuid AS parent_run_id
        FROM agent_delegations p
        WHERE p.tenant_id = w.tenant_id AND p.child_run_id = w.current_run_id
    ) p ON true
    CROSS JOIN LATERAL (
        SELECT
            COALESCE(
                p.parent_count = 1
                AND (p.parent_run_id = ANY(w.path_runs) OR p.delegation_id = ANY(w.path_edges)),
                false
            ) AS is_cycle,
            COALESCE(
                p.parent_count = 1
                AND EXISTS (
                    SELECT 1 FROM agent_runs ar
                    WHERE ar.run_id = p.parent_run_id AND ar.tenant_id <> w.tenant_id
                ),
                false
            ) AS is_cross
    ) s
    WHERE NOT w.done AND w.hops < {MAX_HOPS}
)
SELECT
    tenant_id,
    delegation_id,
    bool_or(cycle_detected) AS had_cycle,
    bool_or(ambiguous_parent) AS had_ambiguous_parent,
    bool_or(cross_tenant_run) AS had_cross_tenant,
    bool_or(depth_cap_hit OR hops >= {MAX_HOPS}) AS hit_depth_cap,
    count(DISTINCT CASE
        WHEN done
             AND NOT cycle_detected
             AND NOT ambiguous_parent
             AND NOT cross_tenant_run
             AND NOT depth_cap_hit
            THEN current_run_id
    END) AS root_count,
    min(CASE
        WHEN done
             AND NOT cycle_detected
             AND NOT ambiguous_parent
             AND NOT cross_tenant_run
             AND NOT depth_cap_hit
            THEN current_run_id::text
    END)::uuid AS resolved_root_run_id
FROM walk
GROUP BY tenant_id, delegation_id
"""

_RECORD_ANOMALIES = f"""
INSERT INTO {REPORT_TABLE}
    (tenant_id, delegation_id, reason, root_run_id_before, detected_at)
SELECT
    r.tenant_id,
    r.delegation_id,
    CASE
        WHEN r.had_cycle THEN 'cycle'
        WHEN r.had_ambiguous_parent THEN 'ambiguous_parent'
        WHEN r.had_cross_tenant THEN 'cross_tenant_run'
        WHEN r.hit_depth_cap THEN 'depth_cap'
        WHEN r.root_count <> 1 THEN 'ambiguous_root'
        ELSE 'ok'
    END,
    d.root_run_id,
    now()
FROM delegation_root_resolution r
JOIN agent_delegations d
  ON d.tenant_id = r.tenant_id AND d.delegation_id = r.delegation_id
WHERE r.had_cycle
   OR r.had_ambiguous_parent
   OR r.had_cross_tenant
   OR r.hit_depth_cap
   OR r.root_count <> 1
ON CONFLICT (tenant_id, delegation_id, reason) DO UPDATE
SET root_run_id_before = EXCLUDED.root_run_id_before,
    detected_at = EXCLUDED.detected_at
"""

_REPAIR_ROOTS = """
UPDATE agent_delegations d
SET root_run_id = CASE
    WHEN r.had_cycle
         OR r.had_ambiguous_parent
         OR r.had_cross_tenant
         OR r.hit_depth_cap
         OR r.root_count <> 1
        THEN NULL
    ELSE r.resolved_root_run_id
END
FROM delegation_root_resolution r
WHERE d.tenant_id = r.tenant_id AND d.delegation_id = r.delegation_id
  AND d.root_run_id IS DISTINCT FROM CASE
      WHEN r.had_cycle
           OR r.had_ambiguous_parent
           OR r.had_cross_tenant
           OR r.hit_depth_cap
           OR r.root_count <> 1
          THEN NULL
      ELSE r.resolved_root_run_id
  END
"""

_DROP_TEMP_TABLE = "DROP TABLE IF EXISTS pg_temp.delegation_root_resolution"

ROOT_REPAIR_STATEMENTS = (
    _CREATE_REPORT_TABLE,
    _CLEAR_REPORT_TABLE,
    _DROP_TEMP_TABLE,
    _RESOLVE_ROOTS,
    _RECORD_ANOMALIES,
    _REPAIR_ROOTS,
    _DROP_TEMP_TABLE,
)


def upgrade():
    for statement in ROOT_REPAIR_STATEMENTS:
        op.execute(sa.text(statement))
    op.execute(sa.text(f"""
        DO $$
        DECLARE anomaly_count integer;
        BEGIN
            SELECT count(*) INTO anomaly_count FROM {REPORT_TABLE};
            RAISE NOTICE '0016 root backfill: % anomalous delegation(s); see {REPORT_TABLE}',
                anomaly_count;
        END
        $$
    """))


def downgrade():
    # 根因回填结果不可从数据本身逆向还原；downgrade 只移除审计报告表，
    # 由 0015 的 downgrade 负责删除 root_run_id 列。
    op.execute(sa.text(f"DROP TABLE IF EXISTS {REPORT_TABLE}"))
