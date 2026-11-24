"""Read-only execution metrics service with independent tree integrity checks.

The service never relies on ``root_run_id`` pushdown for scope discovery, never
modifies persisted delegation records and never mutates delegation status.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable
from uuid import UUID

from sqlalchemy import select

from agent_runtime.auth import can_access_run
from agent_runtime.contracts import DataQuality, ExecutionMetricsRead, Principal
from agent_runtime.db import AgentDelegationRecord as DelegationRow, RunRecord, session_for_tenant
from agent_runtime.monitoring.parallelism import (
    DelegationInterval,
    calculate_parallelism,
)
from agent_runtime.orchestration.delegation_service import _row_to_record
from agent_runtime.orchestration.models import AgentDelegationRecord

REASON_ROOT_MISMATCH = "ROOT_MISMATCH"
REASON_ROOT_MISSING = "ROOT_MISSING"
REASON_CYCLE = "CYCLE_DETECTED"
REASON_AMBIGUOUS = "AMBIGUOUS_PARENT_CHAIN"
REASON_DUPLICATE = "DUPLICATE_CHILD_LINK"
REASON_CROSS_TENANT = "CROSS_TENANT_OR_MISSING_RUN"
REASON_HIDDEN = "HIDDEN_RUNS_EXCLUDED"
REASON_INCOMPLETE_SCOPE = "INCOMPLETE_VISIBLE_SCOPE"
REASON_NO_VISIBLE = "NO_VISIBLE_DELEGATIONS"
REASON_UNKNOWN_INTERVALS = "UNKNOWN_INTERVALS"
REASON_NO_VALID_INTERVALS = "NO_VALID_INTERVALS"
REASON_ROOT_UNRESOLVED = "ROOT_UNRESOLVED"
REASON_PARTIAL_SCOPE = "PARTIAL_SCOPE"
REASON_ACCESS_DENIED = "ACCESS_DENIED"
REASON_COMPUTE_ERROR = "COMPUTE_ERROR"

EXCLUSION_REASONS = frozenset({REASON_CYCLE, REASON_AMBIGUOUS, REASON_CROSS_TENANT})
# Reasons that make the aggregate unusable as a trustworthy zero/value.
UNAVAILABLE_REASONS = frozenset({
    REASON_INCOMPLETE_SCOPE, REASON_NO_VISIBLE, REASON_NO_VALID_INTERVALS,
    REASON_ROOT_UNRESOLVED,
})
# Reasons that only degrade the aggregate (visible scope still trustworthy).
DEGRADED_REASONS = frozenset({
    REASON_HIDDEN, REASON_ROOT_MISMATCH, REASON_ROOT_MISSING, REASON_DUPLICATE,
    REASON_PARTIAL_SCOPE, REASON_ACCESS_DENIED,
})


def _key(value: UUID | str) -> str:
    return str(value)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


@dataclass(frozen=True)
class IntegrityFinding:
    """Internal finding. Affected identifiers are never serialized by the API."""

    reason: str
    delegation_ids: frozenset[str]
    child_run_ids: frozenset[str]

    @property
    def is_exclusion(self) -> bool:
        return self.reason in EXCLUSION_REASONS


@dataclass(frozen=True)
class ScopeLoad:
    all_records: tuple[AgentDelegationRecord, ...]
    scope_records: tuple[AgentDelegationRecord, ...]
    visible_run_ids: frozenset[str]
    missing_run_ids: frozenset[str]
    root_in_delegations: bool


class TreeIntegrityPrecheck:
    """Independent, read-only integrity analysis over delegation edges.

    Scope is discovered from the parent/child graph, not from ``root_run_id``.
    A wrong non-NULL ``root_run_id`` therefore cannot silently drop a subtree.
    """

    @staticmethod
    def analyze(
        all_records: Iterable[AgentDelegationRecord],
        scope_records: Iterable[AgentDelegationRecord],
        root_run_id: UUID | str,
        missing_run_ids: frozenset[str],
    ) -> tuple[list[IntegrityFinding], frozenset[str]]:
        records = tuple(all_records)
        scoped = tuple(scope_records)
        root_key = _key(root_run_id)

        by_child: dict[str, list[AgentDelegationRecord]] = {}
        for record in records:
            by_child.setdefault(_key(record.child_run_id), []).append(record)

        duplicate_children = {
            child for child, rows in by_child.items() if len(rows) > 1
        }
        ambiguous_runs: set[str] = set(duplicate_children)
        parent_map: dict[str, str] = {}
        parent_options: dict[str, set[str]] = {}
        for child, rows in by_child.items():
            parent_options[child] = {_key(row.parent_run_id) for row in rows}
            if len(rows) == 1:
                parent_map[child] = _key(rows[0].parent_run_id)

        cycle_state: dict[str, int] = {}
        cycle_runs: set[str] = set()
        for start in parent_options:
            if cycle_state.get(start) == 1:
                continue
            path: list[str] = [start]
            cycle_state[start] = 0
            stack: list[tuple[str, object]] = [
                (start, iter(sorted(parent_options.get(start, set()))))
            ]
            while stack:
                node, parents = stack[-1]
                try:
                    parent = next(parents)  # type: ignore[arg-type]
                except StopIteration:
                    cycle_state[node] = 1
                    stack.pop()
                    path.pop()
                    continue
                if cycle_state.get(parent) == 0:
                    cycle_runs.update(path[path.index(parent):])
                elif cycle_state.get(parent) is None:
                    cycle_state[parent] = 0
                    path.append(parent)
                    stack.append((parent, iter(sorted(parent_options.get(parent, set())))))

        done: dict[str, tuple[str | None, str | None]] = {}
        visiting: dict[str, int] = {}

        def resolve(start: str) -> tuple[str | None, str | None]:
            stack: list[str] = []
            current = start
            resolved_root: str | None
            reason: str | None

            while True:
                if current in done:
                    resolved_root, reason = done[current]
                    break
                if current in visiting:
                    reason = REASON_CYCLE
                    resolved_root = None
                    break
                if current in ambiguous_runs:
                    reason = REASON_AMBIGUOUS
                    resolved_root = None
                    break
                parent = parent_map.get(current)
                if parent is None:
                    resolved_root = current
                    reason = None
                    break
                visiting[current] = len(stack)
                stack.append(current)
                current = parent

            for node in reversed(stack):
                done[node] = (resolved_root, reason)
                visiting.pop(node, None)
            done.setdefault(start, (resolved_root, reason))
            return done[start]

        def hits_cycle(start: str) -> bool:
            cursor = start
            seen: set[str] = set()
            while cursor and cursor not in seen:
                if cursor in cycle_runs:
                    return True
                seen.add(cursor)
                cursor = parent_map.get(cursor, "")
            return False

        grouped: dict[str, set[str]] = {}

        def add(reason: str, record: AgentDelegationRecord) -> None:
            grouped.setdefault(reason, set()).add(_key(record.delegation_id))

        child_ids: dict[str, set[str]] = {}

        def add_child(reason: str, record: AgentDelegationRecord) -> None:
            child_ids.setdefault(reason, set()).add(_key(record.child_run_id))

        excluded: set[str] = set()

        for record in scoped:
            child_key = _key(record.child_run_id)
            parent_key = _key(record.parent_run_id)

            if child_key in duplicate_children or parent_key in duplicate_children:
                add(REASON_DUPLICATE, record)
                add_child(REASON_DUPLICATE, record)

            derived_root, reason = resolve(child_key)
            if hits_cycle(child_key) or reason == REASON_CYCLE:
                add(REASON_CYCLE, record)
                add_child(REASON_CYCLE, record)
                excluded.add(_key(record.delegation_id))
            elif reason == REASON_AMBIGUOUS:
                add(REASON_AMBIGUOUS, record)
                add_child(REASON_AMBIGUOUS, record)
                excluded.add(_key(record.delegation_id))
            elif derived_root is not None:
                if record.root_run_id is None:
                    add(REASON_ROOT_MISSING, record)
                    add_child(REASON_ROOT_MISSING, record)
                elif _key(record.root_run_id) != derived_root:
                    add(REASON_ROOT_MISMATCH, record)
                    add_child(REASON_ROOT_MISMATCH, record)

            if child_key in missing_run_ids or parent_key in missing_run_ids:
                add(REASON_CROSS_TENANT, record)
                add_child(REASON_CROSS_TENANT, record)
                excluded.add(_key(record.delegation_id))

        # A duplicated child can make the parent chain itself ambiguous even if
        # the current row resolves without hitting the duplicated node.
        for record in scoped:
            if _key(record.delegation_id) in excluded:
                continue
            cursor = _key(record.parent_run_id)
            seen: set[str] = set()
            while cursor and cursor not in seen and cursor in parent_map:
                if cursor in ambiguous_runs:
                    add(REASON_AMBIGUOUS, record)
                    add_child(REASON_AMBIGUOUS, record)
                    excluded.add(_key(record.delegation_id))
                    break
                seen.add(cursor)
                cursor = parent_map[cursor]

        findings = [
            IntegrityFinding(
                reason=reason,
                delegation_ids=frozenset(delegation_ids),
                child_run_ids=frozenset(child_ids.get(reason, set())),
            )
            for reason, delegation_ids in sorted(grouped.items())
        ]
        return findings, frozenset(excluded)


class ExecutionMetricsService:
    """Load visible delegation attempts and calculate read-only metrics."""

    @staticmethod
    async def _load_scope(
        *, tenant_id: str, root_run_id: UUID, principal: Principal
    ) -> ScopeLoad:
        async with session_for_tenant(tenant_id) as session:
            rows = tuple((await session.execute(
                select(DelegationRow).where(DelegationRow.tenant_id == tenant_id)
            )).scalars().all())

            run_ids: set[str] = {_key(root_run_id)}
            for row in rows:
                run_ids.add(_key(row.parent_run_id))
                run_ids.add(_key(row.child_run_id))

            run_rows: list[tuple[str, str, datetime | None]] = []
            if run_ids:
                run_rows = list((await session.execute(
                    select(RunRecord.run_id, RunRecord.user_id, RunRecord.deleted_at).where(
                        RunRecord.tenant_id == tenant_id,
                        RunRecord.run_id.in_([UUID(item) for item in run_ids]),
                    )
                )).all())

        records = tuple(_row_to_record(row) for row in rows)

        visible_run_ids: set[str] = set()
        found_run_ids: set[str] = set()
        for run_id, owner_id, deleted_at in run_rows:
            key = _key(run_id)
            found_run_ids.add(key)
            if deleted_at is None and can_access_run(principal, owner_id):
                visible_run_ids.add(key)

        adjacency: dict[str, set[str]] = {}
        for record in records:
            parent_key = _key(record.parent_run_id)
            child_key = _key(record.child_run_id)
            adjacency.setdefault(parent_key, set()).add(child_key)
            adjacency.setdefault(child_key, set()).add(parent_key)

        component: set[str] = set()
        queue = deque([_key(root_run_id)])
        while queue:
            current = queue.popleft()
            if current in component:
                continue
            component.add(current)
            queue.extend(adjacency.get(current, ()))

        scope_records = tuple(
            record for record in records
            if _key(record.parent_run_id) in component
            or _key(record.child_run_id) in component
        )
        root_key = _key(root_run_id)
        root_in_delegations = any(
            _key(record.parent_run_id) == root_key
            for record in records
        )
        return ScopeLoad(
            all_records=records,
            scope_records=scope_records,
            visible_run_ids=frozenset(visible_run_ids),
            missing_run_ids=frozenset(run_ids - found_run_ids),
            root_in_delegations=root_in_delegations,
        )

    @staticmethod
    def _intervals(records: Iterable[AgentDelegationRecord]) -> list[DelegationInterval]:
        return [
            DelegationInterval(
                delegation_id=record.delegation_id,
                child_run_id=record.child_run_id,
                node_id=record.node_id,
                status=record.status,
                started_at=_as_utc(record.started_at) if record.started_at else None,
                completed_at=_as_utc(record.completed_at) if record.completed_at else None,
            )
            for record in records
        ]

    @staticmethod
    def _visible_scope_reachable(
        *, root_run_id: UUID, records: Iterable[AgentDelegationRecord], visible_run_ids: frozenset[str]
    ) -> bool:
        adjacency: dict[str, set[str]] = {}
        for record in records:
            parent_key = _key(record.parent_run_id)
            child_key = _key(record.child_run_id)
            adjacency.setdefault(parent_key, set()).add(child_key)
            adjacency.setdefault(child_key, set()).add(parent_key)

        reached: set[str] = set()
        queue = deque([_key(root_run_id)])
        while queue:
            current = queue.popleft()
            if current in reached or current not in visible_run_ids:
                continue
            reached.add(current)
            queue.extend(adjacency.get(current, ()))

        return all(
            _key(record.child_run_id) in reached and _key(record.parent_run_id) in reached
            for record in records
        )

    @staticmethod
    async def collect(
        *,
        tenant_id: str,
        root_run_id: UUID,
        principal: Principal,
        start_time: datetime,
        end_time: datetime,
        now: datetime,
    ) -> ExecutionMetricsRead:
        """Collect metrics for visible delegation attempts only."""
        if tenant_id != principal.tenant_id:
            raise ValueError("tenant_id must come from the authenticated principal")

        scope = await ExecutionMetricsService._load_scope(
            tenant_id=tenant_id, root_run_id=root_run_id, principal=principal,
        )
        findings, excluded_ids = TreeIntegrityPrecheck.analyze(
            scope.all_records, scope.scope_records, root_run_id, scope.missing_run_ids,
        )

        visible_scope = tuple(
            record for record in scope.scope_records
            if _key(record.child_run_id) in scope.visible_run_ids
            and _key(record.parent_run_id) in scope.visible_run_ids
        )
        hidden_exists = len(visible_scope) != len(scope.scope_records)

        healthy_visible = tuple(
            record for record in visible_scope if _key(record.delegation_id) not in excluded_ids
        )

        result = calculate_parallelism(
            ExecutionMetricsService._intervals(healthy_visible),
            start_time=start_time,
            end_time=end_time,
            now=now,
        )

        reasons: set[str] = set()
        visible_ids = {_key(record.delegation_id) for record in visible_scope}
        for finding in findings:
            if finding.delegation_ids & visible_ids:
                reasons.add(finding.reason)

        if hidden_exists:
            reasons.add(REASON_HIDDEN)
            reasons.add(REASON_ACCESS_DENIED)

        # P1-1: an empty scope is NOT proof of "zero parallel agents". It can be
        # a wrong root in another component or a run with no delegation edges.
        # Never return a trustworthy all-zero OK in that case; report MISSING.
        root_unresolved = not scope.scope_records or not scope.root_in_delegations
        if root_unresolved:
            reasons.add(REASON_ROOT_UNRESOLVED)
        elif not visible_scope:
            reasons.add(REASON_NO_VISIBLE)
            reasons.add(REASON_ACCESS_DENIED)
        elif not ExecutionMetricsService._visible_scope_reachable(
            root_run_id=root_run_id,
            records=healthy_visible,
            visible_run_ids=scope.visible_run_ids,
        ):
            reasons.add(REASON_INCOMPLETE_SCOPE)
            reasons.add(REASON_PARTIAL_SCOPE)

        if result.unknown_interval_count:
            reasons.add(REASON_UNKNOWN_INTERVALS)
            reasons.add(REASON_COMPUTE_ERROR)
        if visible_scope and result.valid_interval_count == 0:
            reasons.add(REASON_NO_VALID_INTERVALS)

        # Unified data-quality classification. Precedence: unusable > denied >
        # missing > partial/degraded > ok. Legacy reason sets keep their
        # historical states so existing API clients are not broken.
        severe = bool(reasons & UNAVAILABLE_REASONS)
        if root_unresolved:
            data_quality = DataQuality.MISSING
        elif hidden_exists and not visible_scope:
            # All delegation edges were filtered by ACL: this is permission
            # denial, not an empty tree. Never report it as OK/zero.
            data_quality = DataQuality.DENIED
        elif severe:
            data_quality = DataQuality.UNAVAILABLE
        elif reasons or result.unknown_interval_count:
            data_quality = DataQuality.DEGRADED
        else:
            data_quality = DataQuality.OK

        return ExecutionMetricsRead(
            current_parallelism=result.current_parallelism,
            max_parallelism=result.max_parallelism,
            average_parallelism=result.average_parallelism,
            total_agent_active_time=result.total_agent_active_time,
            delegation_attempt_count=result.delegation_attempt_count,
            distinct_child_run_count=result.distinct_child_run_count,
            distinct_node_count=result.distinct_node_count,
            unknown_interval_count=result.unknown_interval_count,
            partial_interval_count=result.partial_interval_count,
            data_quality=data_quality,
            unavailable_reasons=sorted(reasons),
        )
