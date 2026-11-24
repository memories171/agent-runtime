"""Task Graph 数据访问层：定义、节点状态、事件与结果（DB 只做投影/审计）。"""
from __future__ import annotations

import asyncio
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import func, select, update

from agent_runtime.db import (
    GraphEventRecord,
    TaskGraphEdgeRecord,
    TaskGraphNodeRecord,
    TaskGraphRecord,
    TaskGraphVersionRecord,
    session_for_tenant,
)
from agent_runtime.graph.models import (
    GraphDefinition, GraphEvent, GraphEventType, NodeResult, NodeStatus, ValidationReport,
)


async def upsert_definition(
    definition: GraphDefinition,
    validation: ValidationReport | None = None,
    planner_model: str | None = None,
) -> dict[str, Any]:
    async with session_for_tenant(definition.tenant_id) as session:
        row = await session.get(TaskGraphRecord, definition.graph_id)
        payload = definition.model_dump(mode="json")
        if row is None:
            row = TaskGraphRecord(graph_id=definition.graph_id)
            session.add(row)
        row.tenant_id = definition.tenant_id
        row.run_id = UUID(definition.run_id)
        row.graph_version = definition.graph_version
        row.graph_status = definition.graph_status
        row.root_goal = definition.root_goal
        row.acceptance_criteria = definition.acceptance_criteria
        row.max_concurrency = definition.max_concurrency
        row.failure_policy = definition.failure_policy
        row.planner_model = planner_model
        row.validation_report = validation.model_dump(mode="json") if validation else {}
        row.definition_json = payload
        await session.flush()

        version_exists = await session.scalar(
            select(TaskGraphVersionRecord.version_id).where(
                TaskGraphVersionRecord.tenant_id == definition.tenant_id,
                TaskGraphVersionRecord.graph_id == definition.graph_id,
                TaskGraphVersionRecord.graph_version == definition.graph_version,
            )
        )
        if version_exists is None:
            session.add(TaskGraphVersionRecord(
                version_id=uuid4(),
                tenant_id=definition.tenant_id,
                graph_id=definition.graph_id,
                graph_version=definition.graph_version,
                definition_json=payload,
                validation_report=validation.model_dump(mode="json") if validation else {},
                planner_model=planner_model,
            ))
            await session.flush()

        existing_nodes = {
            item.node_key: item for item in (await session.execute(
                select(TaskGraphNodeRecord).where(
                    TaskGraphNodeRecord.tenant_id == definition.tenant_id,
                    TaskGraphNodeRecord.graph_id == definition.graph_id,
                )
            )).scalars()
        }
        for node in definition.nodes:
            node_row = existing_nodes.get(node.node_id)
            if node_row is None:
                node_row = TaskGraphNodeRecord(node_id=uuid4())
                session.add(node_row)
            node_row.graph_id = definition.graph_id
            node_row.tenant_id = definition.tenant_id
            node_row.node_key = node.node_id
            node_row.node_type = node.node_type
            node_row.name = node.name
            node_row.description = node.description
            node_row.input_schema = node.input_schema
            node_row.output_schema = node.output_schema
            node_row.dependencies = node.dependencies
            node_row.status = "PENDING"
            node_row.retry_policy = node.retry_policy.model_dump(mode="json")
            node_row.timeout_seconds = node.timeout_seconds
            node_row.required_permissions = node.required_permissions
            node_row.context_policy = node.context_policy
            node_row.execution_order = node.execution_order

        existing_edges = {
            item.edge_key: item for item in (await session.execute(
                select(TaskGraphEdgeRecord).where(
                    TaskGraphEdgeRecord.tenant_id == definition.tenant_id,
                    TaskGraphEdgeRecord.graph_id == definition.graph_id,
                )
            )).scalars()
        }
        for edge in definition.edges:
            edge_row = existing_edges.get(edge.edge_id)
            if edge_row is None:
                edge_row = TaskGraphEdgeRecord(edge_id=uuid4())
                session.add(edge_row)
            edge_row.graph_id = definition.graph_id
            edge_row.tenant_id = definition.tenant_id
            edge_row.edge_key = edge.edge_id
            edge_row.source_node_id = edge.source_node_id
            edge_row.target_node_id = edge.target_node_id
            edge_row.dependency_type = edge.dependency_type
            edge_row.condition = edge.condition
        await session.commit()
    return payload


def _row_to_definition(row: TaskGraphRecord) -> GraphDefinition:
    return GraphDefinition.model_validate(row.definition_json or {})


async def load_definition(tenant_id: str, graph_id: str, version: int | None = None) -> GraphDefinition | None:
    async with session_for_tenant(tenant_id) as session:
        current = (await session.execute(
            select(TaskGraphRecord).where(
                TaskGraphRecord.tenant_id == tenant_id,
                TaskGraphRecord.graph_id == graph_id,
            ).order_by(TaskGraphRecord.graph_version.desc()).limit(1)
        )).scalar_one_or_none()
        if current is None:
            return None
        if version is None or version == current.graph_version:
            return _row_to_definition(current)
        snapshot = (await session.execute(
            select(TaskGraphVersionRecord).where(
                TaskGraphVersionRecord.tenant_id == tenant_id,
                TaskGraphVersionRecord.graph_id == graph_id,
                TaskGraphVersionRecord.graph_version == version,
            )
        )).scalar_one_or_none()
        if snapshot is None:
            return None
        return GraphDefinition.model_validate(snapshot.definition_json)


async def latest_definition_for_run(tenant_id: str, run_id: str) -> GraphDefinition | None:
    async with session_for_tenant(tenant_id) as session:
        row = (await session.execute(
            select(TaskGraphRecord).where(
                TaskGraphRecord.tenant_id == tenant_id,
                TaskGraphRecord.run_id == UUID(run_id),
            ).order_by(TaskGraphRecord.graph_version.desc()).limit(1)
        )).scalar_one_or_none()
        return _row_to_definition(row) if row else None


async def append_graph_event(event: GraphEvent) -> GraphEvent:
    """Append an event with a per-graph sequence, tolerating concurrent writers."""
    last_error: Exception | None = None
    for attempt in range(8):
        try:
            async with session_for_tenant(event.tenant_id) as session:
                sequence = await session.scalar(
                    select(func.coalesce(func.max(GraphEventRecord.sequence_no), 0) + 1).where(
                        GraphEventRecord.tenant_id == event.tenant_id,
                        GraphEventRecord.graph_id == event.graph_id,
                    )
                )
                row = GraphEventRecord(
                    event_id=uuid4(),
                    graph_id=event.graph_id,
                    tenant_id=event.tenant_id,
                    run_id=UUID(event.run_id),
                    sequence_no=int(sequence or 1),
                    event_type=event.event_type,
                    node_id=event.node_id,
                    parent_node_id=event.parent_node_id,
                    execution_duration_ms=event.execution_duration_ms,
                    token_usage=event.token_usage,
                    payload=event.payload,
                )
                session.add(row)
                await session.commit()
                return GraphEvent(
                    graph_id=event.graph_id,
                    run_id=event.run_id,
                    tenant_id=event.tenant_id,
                    event_type=event.event_type,
                    sequence_no=row.sequence_no,
                    node_id=event.node_id,
                    parent_node_id=event.parent_node_id,
                    execution_duration_ms=event.execution_duration_ms,
                    token_usage=event.token_usage,
                    payload=event.payload,
                    created_at=row.created_at,
                )
        except Exception as exc:
            last_error = exc
            await asyncio.sleep(0.05 * (attempt + 1))
    raise last_error or RuntimeError("failed to append graph event")


async def update_node_status(
    tenant_id: str,
    graph_id: str,
    node_key: str,
    status: NodeStatus,
    **fields: Any,
) -> None:
    allowed = {
        "attempt_count", "last_error", "result_json", "token_input", "token_output",
        "duration_ms", "artifact_refs", "context_pack_id", "source_event_ids",
    }
    values: dict[str, Any] = {"status": status}
    values.update({key: value for key, value in fields.items() if key in allowed})
    async with session_for_tenant(tenant_id) as session:
        await session.execute(
            update(TaskGraphNodeRecord).where(
                TaskGraphNodeRecord.tenant_id == tenant_id,
                TaskGraphNodeRecord.graph_id == graph_id,
                TaskGraphNodeRecord.node_key == node_key,
            ).values(**values)
        )
        await session.commit()


async def update_graph_status(tenant_id: str, graph_id: str, status: str) -> None:
    async with session_for_tenant(tenant_id) as session:
        await session.execute(
            update(TaskGraphRecord).where(
                TaskGraphRecord.tenant_id == tenant_id,
                TaskGraphRecord.graph_id == graph_id,
            ).values(graph_status=status)
        )
        await session.commit()


async def save_node_result(result: NodeResult) -> None:
    async with session_for_tenant(result.tenant_id) as session:
        row = (await session.execute(
            select(TaskGraphNodeRecord).where(
                TaskGraphNodeRecord.tenant_id == result.tenant_id,
                TaskGraphNodeRecord.graph_id == result.graph_id,
                TaskGraphNodeRecord.node_key == result.node_id,
            )
        )).scalar_one_or_none()
        if row is not None:
            row.status = result.status
            row.attempt_count = result.attempts
            row.last_error = result.error
            row.result_json = result.output
            row.token_input = result.token_input
            row.token_output = result.token_output
            row.duration_ms = result.duration_ms
            row.artifact_refs = result.artifact_refs
            row.context_pack_id = result.context_pack_id
            row.source_event_ids = result.source_event_ids
            await session.commit()


async def list_nodes(tenant_id: str, graph_id: str) -> list[dict[str, Any]]:
    async with session_for_tenant(tenant_id) as session:
        rows = (await session.execute(
            select(TaskGraphNodeRecord).where(
                TaskGraphNodeRecord.tenant_id == tenant_id,
                TaskGraphNodeRecord.graph_id == graph_id,
            ).order_by(TaskGraphNodeRecord.execution_order.asc())
        )).scalars()
        return [_node_to_dict(row) for row in rows]


async def get_node(tenant_id: str, graph_id: str, node_key: str) -> dict[str, Any] | None:
    async with session_for_tenant(tenant_id) as session:
        row = (await session.execute(
            select(TaskGraphNodeRecord).where(
                TaskGraphNodeRecord.tenant_id == tenant_id,
                TaskGraphNodeRecord.graph_id == graph_id,
                TaskGraphNodeRecord.node_key == node_key,
            )
        )).scalar_one_or_none()
        return _node_to_dict(row) if row else None


def _node_to_dict(row: TaskGraphNodeRecord) -> dict[str, Any]:
    return {
        "node_id": row.node_key,
        "row_id": str(row.node_id),
        "graph_id": row.graph_id,
        "node_type": row.node_type,
        "name": row.name,
        "description": row.description,
        "input_schema": row.input_schema,
        "output_schema": row.output_schema,
        "dependencies": row.dependencies,
        "status": row.status,
        "retry_policy": row.retry_policy,
        "timeout_seconds": row.timeout_seconds,
        "required_permissions": row.required_permissions,
        "context_policy": row.context_policy,
        "execution_order": row.execution_order,
        "attempt_count": row.attempt_count,
        "last_error": row.last_error,
        "result": row.result_json,
        "token_usage": {"input": row.token_input, "output": row.token_output},
        "duration_ms": row.duration_ms,
        "artifact_refs": row.artifact_refs,
        "context_pack_id": row.context_pack_id,
        "source_event_ids": row.source_event_ids,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


async def list_events(tenant_id: str, graph_id: str, limit: int = 500) -> list[dict[str, Any]]:
    async with session_for_tenant(tenant_id) as session:
        rows = (await session.execute(
            select(GraphEventRecord).where(
                GraphEventRecord.tenant_id == tenant_id,
                GraphEventRecord.graph_id == graph_id,
            ).order_by(GraphEventRecord.sequence_no.asc()).limit(limit)
        )).scalars()
        return [{
            "event_id": str(row.event_id),
            "graph_id": row.graph_id,
            "run_id": str(row.run_id),
            "sequence_no": row.sequence_no,
            "event_type": row.event_type,
            "node_id": row.node_id,
            "parent_node_id": row.parent_node_id,
            "execution_duration_ms": row.execution_duration_ms,
            "token_usage": row.token_usage,
            "payload": row.payload,
            "created_at": row.created_at.isoformat() if row.created_at else None,
        } for row in rows]
