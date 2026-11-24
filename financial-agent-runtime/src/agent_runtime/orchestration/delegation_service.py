"""Delegation 状态与关系的持久化服务（遵循 Tenant RLS）。"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4, uuid5

from sqlalchemy import or_, select, update

from agent_runtime.auth import can_access_run
from agent_runtime.contracts import (
    AgentTreeAnomaliesRead,
    AgentTreeNodeRead,
    AgentTreeRead,
    Principal,
)
from agent_runtime.db import AgentDelegationRecord as DelegationRow, RunRecord, session_for_tenant
from agent_runtime.orchestration.models import (
    DELEGATION_TRANSITIONS,
    TERMINAL_DELEGATION_STATUSES,
    AgentDelegationRecord,
    CapabilityGrant,
    DelegationStatus,
)
from agent_runtime.orchestration.token_service import CapabilityDenied, CapabilityTokenService

ORCHESTRATION_NAMESPACE = UUID("6f1e5d2c-8b3a-4f47-9c21-0b6a7d4e3f90")


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def deterministic_child_run_id(tenant_id: str, parent_run_id: str, node_id: str, attempt: int) -> UUID:
    return uuid5(ORCHESTRATION_NAMESPACE, f"child-run:{tenant_id}:{parent_run_id}:{node_id}:{attempt}")


def deterministic_delegation_key(tenant_id: str, parent_run_id: str, node_id: str, attempt: int) -> str:
    return f"delegation:{tenant_id}:{parent_run_id}:{node_id}:{attempt}"


def child_workflow_id(tenant_id: str, child_run_id: UUID | str) -> str:
    return f"agent-run/{tenant_id}/{child_run_id}"


def _row_to_record(row: DelegationRow) -> AgentDelegationRecord:
    return AgentDelegationRecord(
        delegation_id=row.delegation_id,
        tenant_id=row.tenant_id,
        parent_run_id=row.parent_run_id,
        child_run_id=row.child_run_id,
        root_run_id=getattr(row, "root_run_id", None),
        parent_agent_id=row.parent_agent_id,
        child_agent_id=row.child_agent_id,
        capability_token_id=row.capability_token_id,
        delegation_key=row.delegation_key,
        child_workflow_id=row.child_workflow_id,
        status=row.status,
        graph_id=row.graph_id,
        node_id=row.node_id,
        depth=row.depth,
        max_depth=row.max_depth,
        attempt=row.attempt,
        timeout_seconds=row.timeout_seconds,
        deadline_at=_utc(row.deadline_at) if row.deadline_at else None,
        result_summary=row.result_summary,
        error=row.error,
        artifact_refs=row.artifact_refs or [],
        created_at=_utc(row.created_at) if row.created_at else None,
        updated_at=_utc(row.updated_at) if getattr(row, "updated_at", None) else None,
        started_at=_utc(row.started_at) if row.started_at else None,
        completed_at=_utc(row.completed_at) if row.completed_at else None,
    )


async def _get_run(session, tenant_id: str, run_id: UUID | str) -> RunRecord:
    row = (await session.execute(
        select(RunRecord).where(RunRecord.tenant_id == tenant_id, RunRecord.run_id == UUID(str(run_id)))
    )).scalar_one_or_none()
    if row is None:
        raise CapabilityDenied("run_not_found", str(run_id))
    return row


class DelegationService:
    @staticmethod
    async def _resolve_root_run_id(session, tenant_id: str, parent: RunRecord) -> UUID:
        """Resolve the root execution of a delegation tree.

        ROOT parent -> the parent itself. SUB_AGENT parent -> inherit the parent's
        root_run_id (or its parent_run_id as a one-step fallback for legacy rows).
        All lookups are tenant-scoped and RLS-protected; a missing parent link is a
        hard failure rather than silently creating a second root.
        """
        parent_run_id = UUID(str(parent.run_id))
        if parent.run_kind != "SUB_AGENT":
            return parent_run_id
        link = (await session.execute(
            select(DelegationRow)
            .where(
                DelegationRow.tenant_id == tenant_id,
                DelegationRow.child_run_id == parent_run_id,
            )
            .order_by(DelegationRow.created_at.desc())
            .limit(1)
        )).scalars().first()
        if link is None:
            raise CapabilityDenied("sub_agent_parent_link_missing", str(parent_run_id))
        return link.root_run_id or UUID(str(link.parent_run_id))

    @staticmethod
    async def create_delegation(
        *,
        tenant_id: str,
        parent_run_id: UUID | str,
        parent_token_id: UUID | str,
        requested: CapabilityGrant,
        parent_agent_id: str,
        child_agent_id: str,
        node_id: str,
        attempt: int = 1,
        graph_id: str | None = None,
        child_run_id: UUID | str | None = None,
        actor: Principal | None = None,
    ) -> tuple[AgentDelegationRecord, bool]:
        if not node_id or attempt < 1:
            raise CapabilityDenied("invalid_delegation_binding")
        resolved_child_run = child_run_id or deterministic_child_run_id(
            tenant_id, str(parent_run_id), node_id, attempt,
        )
        delegation_key = deterministic_delegation_key(tenant_id, str(parent_run_id), node_id, attempt)
        workflow_id = child_workflow_id(tenant_id, resolved_child_run)

        async with session_for_tenant(tenant_id) as session:
            parent = await _get_run(session, tenant_id, parent_run_id)
            child = await _get_run(session, tenant_id, resolved_child_run)
            root_run_id = await DelegationService._resolve_root_run_id(
                session, tenant_id, parent,
            )
            existing = (await session.execute(
                select(DelegationRow).where(
                    DelegationRow.tenant_id == tenant_id,
                    DelegationRow.delegation_key == delegation_key,
                )
            )).scalar_one_or_none()
            if existing is not None:
                if (str(existing.parent_run_id) != str(parent_run_id)
                        or str(existing.child_run_id) != str(resolved_child_run)
                        or existing.node_id != node_id
                        or existing.attempt != attempt):
                    raise CapabilityDenied("delegation_key_conflict")
                return _row_to_record(existing), False
            by_workflow = (await session.execute(
                select(DelegationRow).where(
                    DelegationRow.tenant_id == tenant_id,
                    DelegationRow.child_workflow_id == workflow_id,
                )
            )).scalar_one_or_none()
            if by_workflow is not None:
                raise CapabilityDenied("child_workflow_already_bound")

        if parent.status in {"SUCCEEDED", "FAILED", "CANCELLED"}:
            raise CapabilityDenied("parent_run_terminal")
        if child.run_kind != "SUB_AGENT":
            raise CapabilityDenied("invalid_child_run_kind")

        child_token = await CapabilityTokenService.attenuate(
            parent_token_id,
            requested,
            child_run_id=resolved_child_run,
            subject_agent_id=child_agent_id,
            tenant_id=tenant_id,
            actor=actor,
        )
        now = _utc(datetime.now(timezone.utc))
        async with session_for_tenant(tenant_id) as session:
            row = DelegationRow(
                delegation_id=uuid4(),
                tenant_id=tenant_id,
                parent_run_id=UUID(str(parent_run_id)),
                child_run_id=UUID(str(resolved_child_run)),
                root_run_id=root_run_id,
                parent_agent_id=parent_agent_id,
                child_agent_id=child_agent_id,
                capability_token_id=child_token.token_id,
                delegation_key=delegation_key,
                child_workflow_id=workflow_id,
                status="ISSUED",
                graph_id=graph_id,
                node_id=node_id,
                depth=child_token.depth,
                max_depth=child_token.max_depth,
                attempt=attempt,
                timeout_seconds=requested.timeout_seconds,
                deadline_at=child_token.expires_at,
                artifact_refs=[],
                created_at=now,
            )
            session.add(row)
            await session.flush()
            # RLS 的 tenant 上下文是事务级设置；必须在 commit 结束事务前物化
            # 服务端生成的列，否则 refresh 会看不到该行。
            await session.refresh(row, attribute_names=["updated_at"])
            await session.commit()
            return _row_to_record(row), True

    @staticmethod
    async def get_delegation(tenant_id: str, delegation_id: UUID | str) -> AgentDelegationRecord:
        async with session_for_tenant(tenant_id) as session:
            row = (await session.execute(
                select(DelegationRow).where(
                    DelegationRow.tenant_id == tenant_id,
                    DelegationRow.delegation_id == UUID(str(delegation_id)),
                )
            )).scalar_one_or_none()
        if row is None:
            raise CapabilityDenied("delegation_not_found", str(delegation_id))
        return _row_to_record(row)

    @staticmethod
    async def _transition(
        tenant_id: str,
        delegation_id: UUID | str,
        target: DelegationStatus,
        **updates: Any,
    ) -> AgentDelegationRecord:
        now = _utc(datetime.now(timezone.utc))
        async with session_for_tenant(tenant_id) as session:
            row = (await session.execute(
                select(DelegationRow).where(
                    DelegationRow.tenant_id == tenant_id,
                    DelegationRow.delegation_id == UUID(str(delegation_id)),
                )
            )).scalar_one_or_none()
            if row is None:
                raise CapabilityDenied("delegation_not_found", str(delegation_id))
            if target not in DELEGATION_TRANSITIONS[row.status]:
                raise RuntimeError(f"illegal delegation transition: {row.status} -> {target}")
            row.status = target
            for key, value in updates.items():
                if hasattr(row, key):
                    setattr(row, key, value)
            if target == "RUNNING" and row.started_at is None:
                row.started_at = now
            if target in TERMINAL_DELEGATION_STATUSES:
                row.completed_at = now
            await session.flush()
            # onupdate=func.now() 在 flush 时生成并在 ORM 中标记为 expired；
            # 在事务结束前刷新该列，commit 后即可同步读取（expire_on_commit=False）。
            await session.refresh(row, attribute_names=["updated_at"])
            await session.commit()
            return _row_to_record(row)

    @staticmethod
    async def mark_child_starting(tenant_id: str, delegation_id: UUID | str) -> AgentDelegationRecord:
        return await DelegationService._transition(tenant_id, delegation_id, "CHILD_STARTING")

    @staticmethod
    async def mark_started(tenant_id: str, delegation_id: UUID | str) -> AgentDelegationRecord:
        current = await DelegationService.get_delegation(tenant_id, delegation_id)
        if current.status == "ISSUED":
            await DelegationService.mark_child_starting(tenant_id, delegation_id)
        return await DelegationService._transition(tenant_id, delegation_id, "RUNNING")

    @staticmethod
    async def mark_waiting(tenant_id: str, delegation_id: UUID | str) -> AgentDelegationRecord:
        return await DelegationService._transition(tenant_id, delegation_id, "WAITING")

    @staticmethod
    async def mark_terminal(
        tenant_id: str,
        delegation_id: UUID | str,
        status: DelegationStatus,
        *,
        result_summary: dict[str, Any] | None = None,
        error: str | None = None,
        artifact_refs: list[dict[str, Any]] | None = None,
    ) -> AgentDelegationRecord:
        if status not in TERMINAL_DELEGATION_STATUSES:
            raise RuntimeError(f"not a terminal delegation status: {status}")
        current = await DelegationService.get_delegation(tenant_id, delegation_id)
        target = status if current.status in DELEGATION_TRANSITIONS and status in DELEGATION_TRANSITIONS[current.status] else None
        if target is None and current.status != status:
            raise RuntimeError(f"illegal delegation transition: {current.status} -> {status}")
        if current.status == status:
            return current
        return await DelegationService._transition(
            tenant_id, delegation_id, status,
            result_summary=result_summary,
            error=error,
            artifact_refs=artifact_refs or [],
        )

    @staticmethod
    async def mark_cancel_requested(
        tenant_id: str,
        delegation_id: UUID | str,
        *,
        error: str | None = None,
    ) -> tuple[AgentDelegationRecord, bool]:
        """记录"取消请求已发出但 Child 尚未确认终止"。

        非终态标记：仅当当前状态非终态且非 CANCEL_REQUESTED 时写入；
        已是 CANCEL_REQUESTED 或终态时不改写（幂等）。返回 (record, won)。
        Temporal History 仍是执行权威，DB 仅为投影。
        """
        async with session_for_tenant(tenant_id) as session:
            result = await session.execute(
                update(DelegationRow)
                .where(
                    DelegationRow.tenant_id == tenant_id,
                    DelegationRow.delegation_id == UUID(str(delegation_id)),
                    DelegationRow.status.notin_(tuple(TERMINAL_DELEGATION_STATUSES)),
                    DelegationRow.status != "CANCEL_REQUESTED",
                )
                .values(status="CANCEL_REQUESTED", error=error)
            )
            won = result.rowcount == 1
            await session.commit()
        current = await DelegationService.get_delegation(tenant_id, delegation_id)
        return current, won

    @staticmethod
    async def settle_terminal(
        tenant_id: str,
        delegation_id: UUID | str,
        status: DelegationStatus,
        *,
        result_summary: dict[str, Any] | None = None,
        error: str | None = None,
        artifact_refs: list[dict[str, Any]] | None = None,
    ) -> tuple[AgentDelegationRecord, bool]:
        """条件更新终态：仅当当前状态非终态时写入。

        通过单条 `UPDATE ... WHERE status NOT IN (terminal)` 保证 Cancel/Timeout/
        Complete 并发时先到者获胜、后到者不覆盖，天然幂等。返回 (record, won)。
        won=True 表示本次写入生效；won=False 表示已是终态，返回的 record 为既有终态。
        """
        if status not in TERMINAL_DELEGATION_STATUSES:
            raise RuntimeError(f"not a terminal delegation status: {status}")
        now = _utc(datetime.now(timezone.utc))
        async with session_for_tenant(tenant_id) as session:
            result = await session.execute(
                update(DelegationRow)
                .where(
                    DelegationRow.tenant_id == tenant_id,
                    DelegationRow.delegation_id == UUID(str(delegation_id)),
                    DelegationRow.status.notin_(tuple(TERMINAL_DELEGATION_STATUSES)),
                )
                .values(
                    status=status,
                    result_summary=result_summary,
                    error=error,
                    artifact_refs=artifact_refs or [],
                    completed_at=now,
                )
            )
            won = result.rowcount == 1
            await session.commit()
        current = await DelegationService.get_delegation(tenant_id, delegation_id)
        return current, won

    @staticmethod
    async def cancel_delegation(tenant_id: str, delegation_id: UUID | str) -> AgentDelegationRecord:
        current = await DelegationService.get_delegation(tenant_id, delegation_id)
        if current.status in TERMINAL_DELEGATION_STATUSES:
            return current
        return await DelegationService._transition(tenant_id, delegation_id, "CANCELLED")

    @staticmethod
    async def get_agent_tree(tenant_id: str, root_run_id: UUID | str) -> list[AgentDelegationRecord]:
        """Return all delegation edges belonging to a root execution.

        Kept for backwards compatibility. Uses root_run_id pushdown when present
        and falls back to walking parent links for legacy rows whose root is null.
        ACL filtering is intentionally NOT applied here (internal callers only).
        """
        records = await DelegationService._load_tree_records(tenant_id, UUID(str(root_run_id)))
        return DelegationService._order_tree(records, root_run_id)

    @staticmethod
    async def _load_tree_records(
        tenant_id: str, root_run_id: UUID, *, max_depth: int | None = None
    ) -> list[AgentDelegationRecord]:
        """Load tree edges with root pushdown.

        Two sources are combined and de-duplicated by delegation_id:
        1. rows whose root_run_id == root (fast path, indexed);
        2. legacy rows whose root_run_id is NULL, reached by walking parent links
           from the requested root (bounded, prevents full-tenant scans).
        """
        async with session_for_tenant(tenant_id) as session:
            rows = list((await session.execute(
                select(DelegationRow).where(
                    DelegationRow.tenant_id == tenant_id,
                    or_(
                        DelegationRow.root_run_id == root_run_id,
                        DelegationRow.parent_run_id == root_run_id,
                    ),
                )
            )).scalars().all())
            # Walk legacy (root IS NULL) rows that hang below any discovered edge.
            frontier = [UUID(str(row.child_run_id)) for row in rows]
            visited = {str(row.delegation_id) for row in rows}
            hops = 0
            max_hops = max_depth if max_depth is not None else 64
            while frontier and hops < max_hops:
                hops += 1
                batch = (await session.execute(
                    select(DelegationRow).where(
                        DelegationRow.tenant_id == tenant_id,
                        DelegationRow.root_run_id.is_(None),
                        DelegationRow.parent_run_id.in_(frontier),
                    )
                )).scalars().all()
                frontier = []
                for row in batch:
                    key = str(row.delegation_id)
                    if key in visited:
                        continue
                    visited.add(key)
                    rows.append(row)
                    frontier.append(UUID(str(row.child_run_id)))
        return [_row_to_record(row) for row in rows]

    @staticmethod
    def _order_tree(
        records: list[AgentDelegationRecord], root_run_id: UUID | str
    ) -> list[AgentDelegationRecord]:
        children: dict[str, list[AgentDelegationRecord]] = {}
        for record in records:
            children.setdefault(str(record.parent_run_id), []).append(record)
        ordered: list[AgentDelegationRecord] = []
        queue = [str(root_run_id)]
        seen: set[str] = set()
        while queue:
            current = queue.pop(0)
            if current in seen:
                continue
            seen.add(current)
            for record in children.get(current, []):
                ordered.append(record)
                if str(record.child_run_id) not in seen:
                    queue.append(str(record.child_run_id))
        return ordered

    @staticmethod
    async def get_agent_tree_view(
        tenant_id: str,
        root_run_id: UUID | str,
        principal: Principal,
        *,
        max_depth: int | None = None,
        max_nodes: int = 500,
    ) -> AgentTreeRead:
        """ACL-filtered Agent Tree view safe for the Monitoring API.

        Every node is checked individually against the Run ACL. A parent having
        access does NOT grant access to its children. Hidden nodes are omitted
        entirely; only a boolean `has_hidden_children` flag is exposed (never a
        count, id, name or error). tenant_id always comes from the authenticated
        principal, never from client input.
        """
        root = UUID(str(root_run_id))
        records = await DelegationService._load_tree_records(
            tenant_id, root, max_depth=max_depth,
        )
        all_ids: set[UUID] = {root}
        for record in records:
            all_ids.add(record.parent_run_id)
            all_ids.add(record.child_run_id)

        async with session_for_tenant(tenant_id) as session:
            run_rows = (await session.execute(
                select(RunRecord).where(
                    RunRecord.tenant_id == tenant_id,
                    RunRecord.run_id.in_(all_ids),
                )
            )).scalars().all()
        runs: dict[str, RunRecord] = {str(row.run_id): row for row in run_rows}

        root_run = runs.get(str(root))
        root_visible = bool(
            root_run
            and root_run.deleted_at is None
            and can_access_run(principal, root_run.user_id)
        )

        def visible(run_id: UUID) -> bool:
            row = runs.get(str(run_id))
            return bool(row and row.deleted_at is None and can_access_run(principal, row.user_id))

        children: dict[str, list[AgentDelegationRecord]] = {}
        for record in records:
            children.setdefault(str(record.parent_run_id), []).append(record)

        nodes: list[AgentTreeNodeRead] = []
        node_by_id: dict[str, AgentTreeNodeRead] = {}
        tree_has_hidden = False
        queue: list[tuple[str, int]] = [(str(root), 0)]
        seen: set[str] = {str(root)}

        def mark_parent_hidden(parent_key: str) -> None:
            # If the parent is a visible node, flag it; otherwise (root or an
            # already-hidden node) propagate to the tree-level boolean. We never
            # expose how many descendants were hidden.
            parent_node = node_by_id.get(parent_key)
            if parent_node is not None:
                parent_node.has_hidden_children = True
            else:
                nonlocal tree_has_hidden
                tree_has_hidden = True

        while queue:
            current, level = queue.pop(0)
            for record in children.get(current, []):
                child_key = str(record.child_run_id)
                if child_key in seen:
                    continue
                seen.add(child_key)
                omit = False
                if max_depth is not None and level + 1 > max_depth:
                    omit = True
                elif not visible(record.child_run_id) or not visible(record.parent_run_id):
                    omit = True
                elif len(nodes) >= max_nodes:
                    omit = True
                if omit:
                    mark_parent_hidden(current)
                    continue
                node = DelegationService._to_tree_node(record)
                nodes.append(node)
                node_by_id[child_key] = node
                queue.append((child_key, level + 1))
        return AgentTreeRead(
            root_execution_id=root,
            root_visible=root_visible,
            nodes=nodes,
            has_hidden_children=tree_has_hidden or any(n.has_hidden_children for n in nodes),
        )

    @staticmethod
    def _to_tree_node(record: AgentDelegationRecord) -> AgentTreeNodeRead:
        def iso(value: datetime | None) -> str | None:
            return _utc(value).isoformat() if value else None
        return AgentTreeNodeRead(
            execution_id=record.child_run_id,
            parent_execution_id=record.parent_run_id,
            root_execution_id=record.root_run_id,
            delegation_id=record.delegation_id,
            workflow_id=record.child_workflow_id,
            agent_type=record.child_agent_id,
            agent_name=record.node_id,
            depth=record.depth,
            status=record.status,
            started_at=iso(record.started_at),
            completed_at=iso(record.completed_at),
            created_at=iso(record.created_at),
            updated_at=iso(record.updated_at),
            has_hidden_children=False,
        )

    @staticmethod
    async def detect_tree_anomalies(
        tenant_id: str, root_run_id: UUID | str, principal: Principal
    ) -> AgentTreeAnomaliesRead:
        """Detect orphans, cycles and duplicate links with per-Run ACL filtering.

        Every edge is checked against the Run ACL independently: a caller with
        access to the root does NOT gain access to a child owned by another
        user. Hidden runs never leak their UUID, name, status, structure, count
        or error. The tenant_id is taken from the authenticated principal only.

        - orphan: a delegation whose parent_run_id has no run in this tenant
          (genuinely broken).
        - cycle: a parent chain that revisits a node.
        - duplicate link: the same child_run_id registered under more than one
          parent.
        """
        if tenant_id != principal.tenant_id:
            raise ValueError("tenant_id must come from the authenticated principal")

        records = await DelegationService._load_tree_records(
            tenant_id, UUID(str(root_run_id)),
        )

        all_ids: set[UUID] = set()
        for record in records:
            all_ids.add(record.parent_run_id)
            all_ids.add(record.child_run_id)

        existing_run_ids: set[str] = set()
        visible_run_ids: set[str] = set()
        if all_ids:
            async with session_for_tenant(tenant_id) as session:
                run_rows = (await session.execute(
                    select(RunRecord.run_id, RunRecord.user_id, RunRecord.deleted_at).where(
                        RunRecord.tenant_id == tenant_id,
                        RunRecord.run_id.in_(all_ids),
                    )
                )).all()
            for run_id, owner_id, deleted_at in run_rows:
                key = str(run_id)
                existing_run_ids.add(key)
                if deleted_at is None and can_access_run(principal, owner_id):
                    visible_run_ids.add(key)

        # An edge is visible only when its child is accessible. A parent that
        # exists but is NOT accessible is dropped entirely: reporting it as an
        # orphan would both misrepresent ACL-hidden data and disclose structure.
        visible_records = [
            record for record in records
            if str(record.child_run_id) in visible_run_ids
            and (
                str(record.parent_run_id) in visible_run_ids
                or str(record.parent_run_id) not in existing_run_ids
            )
        ]

        orphans = sorted({
            record.child_run_id for record in visible_records
            if str(record.parent_run_id) not in existing_run_ids
        })

        children: dict[str, list[UUID]] = {}
        for record in visible_records:
            children.setdefault(str(record.parent_run_id), []).append(record.child_run_id)
        cycles: list[list[UUID]] = []
        visiting: set[str] = set()
        visited: set[str] = set()

        def walk(node: str, path: list[UUID]) -> None:
            if node in visiting:
                cycle_start = next(
                    (i for i, item in enumerate(path) if item == UUID(node)), 0
                )
                cycles.append(path[cycle_start:] + [UUID(node)])
                return
            if node in visited:
                return
            visiting.add(node)
            for child in children.get(node, []):
                walk(str(child), path + [child])
            visiting.discard(node)
            visited.add(node)

        for parent in list(children.keys()):
            walk(parent, [UUID(parent)])

        child_parents: dict[str, set[str]] = {}
        for record in visible_records:
            child_parents.setdefault(str(record.child_run_id), set()).add(
                str(record.parent_run_id)
            )
        duplicate_links = sorted({
            UUID(key) for key, parents in child_parents.items() if len(parents) > 1
        })
        return AgentTreeAnomaliesRead(
            orphans=orphans, cycles=cycles, duplicate_links=duplicate_links,
        )
