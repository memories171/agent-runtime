"""Deterministic Task Graph runner executed inside AgentRunWorkflow.

只使用 Temporal 确定性 API（execute_activity / now / uuid4 / sleep / wait_condition）
和 asyncio.gather 做节点级 fan-out；禁止 IO、线程、进程和非确定性库调用。
"""
from __future__ import annotations

import asyncio
import copy
import json
from datetime import timedelta
from typing import Any
from uuid import uuid5

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from agent_runtime.config import settings
    from agent_runtime.activities import (
        agent_decide_activity,
        append_history_event_activity,
        authorize_tool_activity,
        create_approval_binding_activity,
        execute_tool_activity,
        validate_approval_binding_activity,
        consume_approval_binding_activity,
    )
    from agent_runtime.activities_context import assemble_context_activity
    from agent_runtime.graph.activities import (
        append_graph_event_activity,
        persist_graph_activity,
        plan_graph_activity,
        save_node_result_activity,
        update_graph_status_activity,
        update_node_status_activity,
        validate_graph_proposal_activity,
    )
    from agent_runtime.graph.models import (
        GraphDefinition, GraphEventType, GraphLimits, GraphProposal, NodeResult, NodeStatus,
    )
    from agent_runtime.graph.policies import ErrorClass, backoff_seconds, classify_error, should_retry
    from agent_runtime.graph.scheduler import (
        build_aggregation_context, evaluate_dependencies, select_ready, validate_node_output,
    )
    from agent_runtime.graph.state_machine import apply_node_status
    from agent_runtime.graph.validate import build_definition, validate_proposal
    from agent_runtime.orchestration.delegation_service import ORCHESTRATION_NAMESPACE
    from agent_runtime.orchestration.runner import execute_sub_agent_node
    from agent_runtime.recovery.invocations import make_tool_idempotency_key

DEFAULT_LIMITS = {
    "max_nodes": 30,
    "max_graph_depth": 10,
    "max_concurrency": 5,
    "max_node_steps": 8,
    "allowed_high_risk_nodes": [],
    "allowed_agent_roles": [],
}
PROMPT_VERSION = "RUNTIME-PROMPT-v1"


def _event_payload(
    definition: GraphDefinition,
    event_type: GraphEventType,
    *,
    node_id: str | None = None,
    duration_ms: int = 0,
    token_usage: dict[str, Any] | None = None,
    **extra: Any,
) -> dict[str, Any]:
    return {
        "graph_id": definition.graph_id,
        "run_id": definition.run_id,
        "tenant_id": definition.tenant_id,
        "event_type": event_type,
        "node_id": node_id,
        "parent_node_id": None,
        "execution_duration_ms": duration_ms,
        "token_usage": token_usage or {},
        "payload": extra,
    }


async def _emit(definition: GraphDefinition, event_type: GraphEventType, **kwargs: Any) -> None:
    await workflow.execute_activity(
        append_graph_event_activity,
        _event_payload(definition, event_type, **kwargs),
        start_to_close_timeout=timedelta(seconds=15),
        retry_policy=RetryPolicy(maximum_attempts=3),
    )


async def _set_node_status(definition: GraphDefinition, node_id: str, status: NodeStatus, **extra: Any) -> None:
    """All node-state persistence must go through Temporal Activities."""
    payload: dict[str, Any] = {
        "tenant_id": definition.tenant_id, "graph_id": definition.graph_id,
        "node_key": node_id, "status": status,
    }
    payload.update(extra)
    await workflow.execute_activity(
        update_node_status_activity, payload,
        start_to_close_timeout=timedelta(seconds=15),
        retry_policy=RetryPolicy(maximum_attempts=3),
    )


async def _set_graph_status(definition: GraphDefinition, graph_status: str) -> None:
    await workflow.execute_activity(
        update_graph_status_activity,
        {"tenant_id": definition.tenant_id, "graph_id": definition.graph_id, "graph_status": graph_status},
        start_to_close_timeout=timedelta(seconds=15),
        retry_policy=RetryPolicy(maximum_attempts=3),
    )


async def _persist_result(result: NodeResult) -> None:
    await workflow.execute_activity(
        save_node_result_activity, result.model_dump(mode="json"),
        start_to_close_timeout=timedelta(seconds=15),
        retry_policy=RetryPolicy(maximum_attempts=3),
    )


def _sync_state(state: dict[str, Any], definition: GraphDefinition, states: dict[str, NodeStatus],
                blocked: dict[str, list[str]], results: dict[str, NodeResult], event_cursor: int) -> None:
    completed = [nid for nid, status in states.items() if status == "COMPLETED"]
    state["graph"] = {
        "graph_id": definition.graph_id,
        "graph_version": definition.graph_version,
        "node_states": states,
        "completed_nodes": completed,
        "pending_nodes": [nid for nid, status in states.items() if status == "PENDING"],
        "failed_nodes": [nid for nid, status in states.items() if status == "FAILED"],
        "active_node_ids": [
            nid for nid, status in states.items()
            if status in {"RUNNING", "WAITING_APPROVAL", "WAITING_INPUT", "RETRY_WAIT"}
        ],
        "dependency_cursor": dict(blocked),
        "graph_event_cursor": event_cursor,
    }
    state["graph_state_ref"] = state["graph"]
    state["task_graph_version"] = f"v{definition.graph_version}"
    # Checkpoint PlanState requires step dictionaries, not bare node IDs.
    state["completed_steps"] = [{"node_id": nid, "status": "COMPLETED"} for nid in completed]
    state["pending_steps"] = [
        {"node_id": nid, "status": status}
        for nid, status in states.items() if status == "PENDING"
    ]
    state["current_node"] = {
        "node_id": next((nid for nid, status in states.items() if status == "RUNNING"), ""),
        "graph_id": definition.graph_id,
    }


def _node_base_state(run_input: Any, state: dict[str, Any], definition: GraphDefinition,
                     node: Any, upstream: dict[str, NodeResult]) -> dict[str, Any]:
    if node.node_type == "aggregation":
        context_payload = copy.deepcopy(build_aggregation_context(definition, upstream))
    else:
        relevant = {
            nid: result.model_copy(deep=True) for nid, result in upstream.items()
            if nid in node.dependencies
        }
        context_payload = {
            "schema_version": "graph-node-v1",
            "root_goal": definition.root_goal,
            "acceptance_criteria": definition.acceptance_criteria,
            "node": {
                "node_id": node.node_id,
                "name": node.name,
                "description": node.description,
                "context_policy": node.context_policy,
            },
            "upstream_nodes": [
                {
                    "node_id": nid,
                    "status": result.status,
                    "output": result.output,
                    "artifact_refs": result.artifact_refs,
                    "source_event_ids": result.source_event_ids,
                    "error": result.error,
                }
                for nid, result in sorted(relevant.items())
            ],
            "instruction": (
                "只使用 upstream_nodes 中 status=COMPLETED 的结果。"
                "任务完成后调用 finish 并返回符合 output_schema 的对象；"
                "缺失或失败的上游必须作为数据缺口说明。"
            ),
        }
    history = [
        {"type": "user_input", "content": f"根目标：{definition.root_goal}"},
        {"type": "user_input", "content": f"当前节点：{node.name}。{node.description}"},
    ]
    for nid, result in sorted(upstream.items()):
        if nid in node.dependencies or node.node_type == "aggregation":
            history.append({
                "type": "tool_result",
                "tool": f"graph_node:{nid}",
                "result": copy.deepcopy({
                    "status": result.status,
                    "output": result.output,
                    "error": result.error,
                    "artifact_refs": result.artifact_refs,
                }),
            })
    return {
        "run_id": run_input.run_id,
        "tenant_id": run_input.tenant_id,
        "user_id": run_input.user_id,
        "session_id": run_input.session_id,
        "agent_id": run_input.agent_id,
        "prompt": f"{definition.root_goal}\n\n当前子任务：{node.description}",
        "model_name": run_input.model_name,
        "harness_version": run_input.harness_version,
        "principal": dict(state.get("principal") or {}),
        "history": history,
        "memory_context": list(state.get("memory_context") or []),
        "skills": list(state.get("skills") or []),
        "tool_calls": [],
        "llm_calls": [],
        "context_model_payload": context_payload,
        "graph_node": {
            "graph_id": definition.graph_id,
            "graph_version": definition.graph_version,
            "node_id": node.node_id,
            "node_type": node.node_type,
            "context_policy": node.context_policy,
        },
    }


async def _execute_node(
    wf: Any,
    run_input: Any,
    state: dict[str, Any],
    definition: GraphDefinition,
    node: Any,
    upstream: dict[str, NodeResult],
    max_node_steps: int,
    attempt: int,
    emit: Any,
) -> tuple[NodeResult, list[dict[str, Any]], int, int, str | None]:
    """执行单个节点；失败时抛异常交由 retry 包装器处理。"""
    node_state = _node_base_state(run_input, state, definition, node, upstream)
    token_input = 0
    token_output = 0
    context_pack_id: str | None = None
    started = workflow.now()
    tool_events: list[dict[str, Any]] = []
    assembled: dict[str, Any] = {}
    capability_token_id = state.get("capability_token_id")

    if node.node_type == "sub_agent":
        child_state = dict(state)
        shutdown_reason = getattr(wf, "_shutdown_reason", None)
        frozen = state.get("orchestration_limits") or {}
        child_state["child_cancel_grace_seconds"] = (
            frozen.get("parent_cancel_grace_seconds", settings.orchestration_parent_cancel_grace_seconds)
            if shutdown_reason == "cancel"
            else frozen.get("child_cancel_grace_seconds", settings.orchestration_child_cancel_grace_seconds)
        )
        child_state["shutdown_reason"] = shutdown_reason
        result = await execute_sub_agent_node(
            definition=definition, node=node, state=child_state,
            upstream=upstream, attempt=attempt, emit=emit,
            registry=getattr(wf, "_child_registry", None),
        )
        return result, [], 0, 0, None

    for _ in range(max_node_steps):
        if getattr(wf, "_cancel_requested", False):
            raise RuntimeError("graph node cancelled")
        try:
            assembled = await workflow.execute_activity(
                assemble_context_activity,
                {
                    "context_pack_id": str(workflow.uuid4()),
                    "run_id": run_input.run_id,
                    "tenant_id": run_input.tenant_id,
                    "agent_id": run_input.agent_id,
                    "model_name": run_input.model_name,
                    "harness_version": run_input.harness_version,
                    "original_user_goal": definition.root_goal,
                    "acceptance_criteria": definition.acceptance_criteria,
                    "task_objectives": [{"node_id": node.node_id, "objective": node.description}],
                    "immutable_constraints": list((run_input.context_config or {}).get("immutable_constraints") or []),
                    "current_plan": [
                        definition.node_map[nid].model_dump(mode="json")
                        for nid in [*node.dependencies, node.node_id]
                        if nid in definition.node_map
                    ],
                    "completed_steps": [
                        {"node_id": nid, "status": "COMPLETED"}
                        for nid in node.dependencies
                        if nid in upstream and upstream[nid].status == "COMPLETED"
                    ],
                    "pending_steps": [{"node_id": node.node_id, "status": "RUNNING"}],
                    "current_step": node.name,
                    "current_step_status": "RUNNING",
                    "events": node_state["history"],
                    "memory": node_state["memory_context"],
                    "tool_results": [item for item in node_state["history"] if item.get("type") == "tool_result"],
                    "summary": None,
                    "event_cursor": len(node_state["history"]),
                    "compression_count": 0,
                    "node_id": node.node_id,
                    "parent_node_ids": list(node.dependencies),
                    "graph_id": definition.graph_id,
                    "graph_version": definition.graph_version,
                    "node_type": node.node_type,
                    "context_policy": node.context_policy,
                    "node_context_payload": node_state["context_model_payload"],
                },
                start_to_close_timeout=timedelta(seconds=45),
                retry_policy=RetryPolicy(maximum_attempts=3, initial_interval=timedelta(seconds=1)),
            )
            context_pack_id = assembled.get("context_pack_id")
            node_state["context_model_payload"] = assembled["model_payload"]
        except Exception as context_exc:
            # Do not silently fall back to the legacy truncated-history payload:
            # a DB/RLS/storage failure must be visible in the node result.
            node_state.pop("context_model_payload", None)
            node_state["context_degradation_reason"] = (
                f"context_assembly_failed: {type(context_exc).__name__}"
            )
            await emit(
                "NODE_FAILED",
                node_id=node.node_id,
                event="CONTEXT_ASSEMBLY_FAILED",
                error=f"{type(context_exc).__name__}: {str(context_exc)[:300]}",
            )

        decision = await workflow.execute_activity(
            agent_decide_activity,
            node_state,
            start_to_close_timeout=timedelta(minutes=min(5, max(1, node.timeout_seconds // 60))),
            heartbeat_timeout=timedelta(seconds=45),
            retry_policy=RetryPolicy(maximum_attempts=3, initial_interval=timedelta(seconds=2)),
        )
        usage = decision.pop("_provider_usage", {}) or {}
        token_input += max(1, len(json.dumps(node_state, ensure_ascii=False, default=str)) // 4)
        token_output += max(1, len(json.dumps(decision, ensure_ascii=False, default=str)) // 4)
        action = decision.get("action")

        if action == "finish":
            output = decision.get("result") or {"summary": decision.get("message", "节点完成")}
            valid, reason = validate_node_output(output, node.output_schema)
            if not valid:
                raise ValueError(f"schema_error: node {node.node_id} output invalid: {reason}")
            return (
                NodeResult(
                    node_id=node.node_id,
                    graph_id=definition.graph_id,
                    tenant_id=definition.tenant_id,
                    run_id=definition.run_id,
                    status="COMPLETED",
                    output=output,
                    token_input=token_input,
                    token_output=token_output,
                    artifact_refs=list(assembled.get("artifact_refs") or []),
                    context_pack_id=context_pack_id,
                ),
                tool_events, token_input, token_output, context_pack_id,
            )

        if action in {"ask_user", "request_approval"}:
            waiting_status = "WAITING_INPUT" if action == "ask_user" else "WAITING_APPROVAL"
            apply_node_status(state["graph"]["node_states"], node.node_id, waiting_status)
            await _set_node_status(definition, node.node_id, waiting_status)
            await _emit(definition, "NODE_WAITING_INPUT" if action == "ask_user" else "NODE_WAITING_APPROVAL",
                        node_id=node.node_id, message=decision.get("message", ""))
            await workflow.wait_condition(
                lambda: getattr(wf, "_user_input", None) is not None
                or getattr(wf, "_approval", None) is not None
                or getattr(wf, "_cancel_requested", False)
            )
            apply_node_status(state["graph"]["node_states"], node.node_id, "RUNNING")
            if getattr(wf, "_user_input", None) is not None:
                node_state["history"].append({"type": "user_input", "content": wf._user_input})
                wf._user_input = None
            if getattr(wf, "_approval", None) is not None:
                node_state["history"].append({"type": "approval", "decision": wf._approval})
                wf._approval = None
            continue

        if action != "tool_call":
            raise ValueError(f"invalid_argument: unsupported node action {action}")

        tool_call = decision.get("tool") or {}
        tool_name = tool_call.get("name")
        arguments = tool_call.get("arguments") or {}
        auth = await workflow.execute_activity(
            authorize_tool_activity,
            {
                "tool_name": tool_name,
                "tenant_id": definition.tenant_id,
                "principal": node_state["principal"],
                "capability_token_id": capability_token_id,
            },
            start_to_close_timeout=timedelta(seconds=15),
            retry_policy=RetryPolicy(maximum_attempts=3),
        )
        if not auth.get("allowed"):
            raise PermissionError(f"permission_denied: tool {tool_name}: {auth.get('reason')}")

        tool_call_id = str(tool_call.get("call_id") or workflow.uuid4())
        idempotency_key = make_tool_idempotency_key(definition.tenant_id, run_input.run_id, tool_call_id)
        approval_granted = False
        consume_version = 0
        if auth.get("requires_approval"):
            binding = await workflow.execute_activity(
                create_approval_binding_activity,
                {
                    "tenant_id": definition.tenant_id,
                    "run_id": run_input.run_id,
                    "tool_call_id": tool_call_id,
                    "tool_name": tool_name,
                    "target_resource_id": str(arguments.get("subject_id", "")),
                    "operation_type": tool_name,
                    "arguments": arguments,
                    "requested_by": run_input.user_id,
                    "policy_version": "v1",
                    "risk_level": auth.get("risk_level", "HIGH"),
                },
                start_to_close_timeout=timedelta(seconds=20),
                retry_policy=RetryPolicy(maximum_attempts=3),
            )
            apply_node_status(state["graph"]["node_states"], node.node_id, "WAITING_APPROVAL")
            await _set_node_status(definition, node.node_id, "WAITING_APPROVAL")
            await _emit(definition, "NODE_WAITING_APPROVAL", node_id=node.node_id, tool=tool_name)
            await workflow.wait_condition(
                lambda: getattr(wf, "_approval", None) is not None or getattr(wf, "_cancel_requested", False)
            )
            approval = getattr(wf, "_approval", None) or {}
            wf._approval = None
            if not approval.get("approved"):
                raise PermissionError(f"approval_denied: node {node.node_id}")
            validated = await workflow.execute_activity(
                validate_approval_binding_activity,
                {
                    "tenant_id": definition.tenant_id,
                    "run_id": run_input.run_id,
                    "tool_call_id": tool_call_id,
                    "tool_name": tool_name,
                    "target_resource_id": str(arguments.get("subject_id", "")),
                    "arguments": arguments,
                    "policy_version": "v1",
                    "approver_id": approval.get("approver_user_id"),
                },
                start_to_close_timeout=timedelta(seconds=20),
                retry_policy=RetryPolicy(maximum_attempts=2),
            )
            consume_version = int(validated["version"])
            approval_granted = True
            apply_node_status(state["graph"]["node_states"], node.node_id, "RUNNING")

        tool_start = workflow.now()
        result = await workflow.execute_activity(
            execute_tool_activity,
            {
                "run_id": run_input.run_id,
                "tenant_id": definition.tenant_id,
                "tool_name": tool_name,
                "arguments": arguments,
                "principal": node_state["principal"],
                "approval_granted": approval_granted,
                "idempotency_key": idempotency_key,
                "tool_call_id": tool_call_id,
                "capability_token_id": capability_token_id,
                "fencing_token": node.execution_order,
                "lease_owner": getattr(workflow.info(), "task_queue", "worker"),
            },
            start_to_close_timeout=timedelta(hours=1),
            schedule_to_close_timeout=timedelta(hours=2),
            heartbeat_timeout=timedelta(seconds=30),
            retry_policy=RetryPolicy(
                maximum_attempts=3, initial_interval=timedelta(seconds=3),
                maximum_interval=timedelta(seconds=30), backoff_coefficient=2.0,
            ),
        )
        observation = {"type": "tool_result", "tool": tool_name, "result": result, "node_id": node.node_id}
        node_state["history"].append(observation)
        tool_events.append({
            "call_id": tool_call_id,
            "tool": tool_name,
            "node_id": node.node_id,
            "status": "SUCCESS",
            "started_at": tool_start.isoformat(),
            "duration_ms": int((workflow.now() - tool_start).total_seconds() * 1000),
            "idempotency_key": idempotency_key,
        })
        if approval_granted:
            await workflow.execute_activity(
                consume_approval_binding_activity,
                {"tenant_id": definition.tenant_id, "approval_id": binding["approval_id"], "version": consume_version},
                start_to_close_timeout=timedelta(seconds=20),
                retry_policy=RetryPolicy(maximum_attempts=3),
            )
        await workflow.execute_activity(
            append_history_event_activity,
            {"run_id": run_input.run_id, "tenant_id": definition.tenant_id,
             "event_type": "TOOL_OBSERVED", "payload": observation},
            start_to_close_timeout=timedelta(seconds=15),
        )

    raise ValueError(f"invalid_argument: node {node.node_id} exceeded max_node_steps without finish")


async def _run_node_with_retry(
    wf: Any,
    run_input: Any,
    state: dict[str, Any],
    definition: GraphDefinition,
    node: Any,
    upstream: dict[str, NodeResult],
    max_node_steps: int,
    emit: Any,
) -> NodeResult:
    attempt = 0
    while True:
        attempt += 1
        try:
            result, tool_events, token_input, token_output, pack_id = await _execute_node(
                wf, run_input, state, definition, node, upstream, max_node_steps, attempt, emit,
            )
            result.attempts = attempt
            return result
        except Exception as exc:
            error_class = classify_error(exc)
            error_text = f"{type(exc).__name__}: {str(exc)[:1000]}"
            if should_retry(error_class, attempt, node.retry_policy.max_attempts):
                if state["graph"]["node_states"].get(node.node_id) == "RUNNING":
                    apply_node_status(state["graph"]["node_states"], node.node_id, "RETRY_WAIT")
                await _set_node_status(
                    definition, node.node_id, "RETRY_WAIT",
                    attempt_count=attempt, last_error=error_text,
                )
                await _emit(definition, "NODE_RETRY", node_id=node.node_id,
                            attempt=attempt, error=error_text, error_class=error_class)
                await workflow.sleep(timedelta(seconds=backoff_seconds(
                    attempt,
                    node.retry_policy.initial_interval_seconds,
                    node.retry_policy.backoff_coefficient,
                    node.retry_policy.maximum_interval_seconds,
                )))
                apply_node_status(state["graph"]["node_states"], node.node_id, "READY")
                apply_node_status(state["graph"]["node_states"], node.node_id, "RUNNING")
                continue
            apply_node_status(state["graph"]["node_states"], node.node_id, "FAILED")
            return NodeResult(
                node_id=node.node_id,
                graph_id=definition.graph_id,
                tenant_id=definition.tenant_id,
                run_id=definition.run_id,
                status="FAILED",
                error=error_text,
                error_class=error_class,
                attempts=attempt,
            )


async def execute_graph(wf: Any, run_input: Any, state: dict[str, Any]) -> dict[str, Any]:
    """在现有 Durable Workflow 内执行完整 DAG。"""
    graph_config = dict(getattr(run_input, "graph_config", None) or {})
    frozen_limits = dict(getattr(run_input, "orchestration_limits", None) or {})
    if not graph_config.get("enabled", True):
        raise ValueError("graph mode is disabled")
    limits_data = {**DEFAULT_LIMITS, **(graph_config.get("limits") or {})}
    limits_data.setdefault(
        "allowed_agent_roles",
        frozen_limits.get("allowed_agent_roles")
        or sorted(item.strip() for item in settings.orchestration_allowed_agent_roles.split(",") if item.strip()),
    )
    limits = GraphLimits.model_validate(limits_data)

    state["workflow_input"] = run_input.model_dump(mode="json")
    state["orchestration_limits"] = frozen_limits
    orchestration_config = dict(getattr(run_input, "orchestration_config", None) or {})

    def _limit(name: str, default: int, ceiling: int) -> int:
        try:
            value = int(orchestration_config.get(name, default))
        except (TypeError, ValueError):
            value = default
        return max(1, min(value, ceiling))

    state["orchestration"] = {
        "root_capability_token_id": str(uuid5(
            ORCHESTRATION_NAMESPACE,
            f"root-token:{run_input.tenant_id}:{run_input.run_id}",
        )),
        "root_capability_token_expires_at": (
            workflow.now() + timedelta(seconds=max(60, run_input.max_duration_seconds))
        ).isoformat(),
        # Caller config may tighten the validated limits, never widen them.
        "max_depth": frozen_limits.get("max_depth", settings.orchestration_max_depth),
        "max_steps": limits.max_node_steps,
    }
    # Caller config may tighten the validated limits, never widen them.
    state["orchestration"]["max_depth"] = _limit(
        "max_depth",
        frozen_limits.get("max_depth", settings.orchestration_max_depth),
        frozen_limits.get("max_depth", settings.orchestration_max_depth),
    )
    state["orchestration"]["max_steps"] = _limit(
        "max_steps", limits.max_node_steps, limits.max_node_steps
    )
    allowed_permissions = set(state.get("principal", {}).get("permissions") or [])
    allowed_permissions |= set(state.get("principal", {}).get("scopes") or [])

    graph_id = str(graph_config.get("graph_id") or f"graph-{workflow.uuid4()}")
    graph_version = int(graph_config.get("graph_version", 1))
    proposal_payload = dict(graph_config.get("proposed_graph") or {})
    if not proposal_payload:
        proposal_payload = await workflow.execute_activity(
            plan_graph_activity,
            {
                "run_id": run_input.run_id,
                "tenant_id": run_input.tenant_id,
                "root_goal": run_input.prompt,
                "acceptance_criteria": list(graph_config.get("acceptance_criteria") or []),
                "allowed_permissions": sorted(allowed_permissions),
                "limits": limits.model_dump(mode="json"),
                "model_name": run_input.model_name,
            },
            start_to_close_timeout=timedelta(seconds=int(graph_config.get("planning_timeout_seconds", 120))),
            heartbeat_timeout=timedelta(seconds=45),
            retry_policy=RetryPolicy(maximum_attempts=2),
        )
    proposal = GraphProposal.model_validate(proposal_payload)
    report = validate_proposal(
        proposal,
        limits=limits,
        allowed_permissions=allowed_permissions,
        graph_id=graph_id,
        run_id=run_input.run_id,
        tenant_id=run_input.tenant_id,
        graph_version=graph_version,
    )
    if not report.valid:
        message = "; ".join(f"{issue.code}: {issue.message}" for issue in report.issues)
        raise ValueError(f"graph proposal rejected: {message}")

    await workflow.execute_activity(
        validate_graph_proposal_activity,
        {
            "proposal": proposal.model_dump(mode="json"),
            "limits": limits.model_dump(mode="json"),
            "allowed_permissions": sorted(allowed_permissions),
            "graph_id": graph_id,
            "run_id": run_input.run_id,
            "tenant_id": run_input.tenant_id,
            "graph_version": graph_version,
        },
        start_to_close_timeout=timedelta(seconds=20),
        retry_policy=RetryPolicy(maximum_attempts=2),
    )
    definition_payload = await workflow.execute_activity(
        persist_graph_activity,
        {
            "proposal": proposal.model_dump(mode="json"),
            "limits": limits.model_dump(mode="json"),
            "allowed_permissions": sorted(allowed_permissions),
            "graph_id": graph_id,
            "run_id": run_input.run_id,
            "tenant_id": run_input.tenant_id,
            "graph_version": graph_version,
            "planner_model": run_input.model_name,
        },
        start_to_close_timeout=timedelta(seconds=30),
        retry_policy=RetryPolicy(maximum_attempts=3),
    )
    definition = GraphDefinition.model_validate(definition_payload)
    await _emit(definition, "GRAPH_CREATED", nodes=len(definition.nodes), version=graph_version)
    await _emit(definition, "GRAPH_VALIDATED", depth=report.max_depth, order=report.execution_order)
    await _set_graph_status(definition, "RUNNING")
    await _emit(definition, "GRAPH_STARTED", max_concurrency=definition.max_concurrency)

    states: dict[str, NodeStatus] = {node.node_id: "PENDING" for node in definition.nodes}
    results: dict[str, NodeResult] = {}
    blocked: dict[str, list[str]] = {}
    event_cursor = 3

    async def emit(event_type: GraphEventType, **kwargs: Any) -> None:
        nonlocal event_cursor
        await _emit(definition, event_type, **kwargs)
        event_cursor += 1

    graph_status = "RUNNING"
    graph_started_at = workflow.now()
    checkpoint_step = 0
    max_concurrency = min(limits.max_concurrency, definition.max_concurrency)
    _sync_state(state, definition, states, blocked, results, event_cursor)
    await wf._save_checkpoint(run_input, checkpoint_step, state, "任务图启动前")
    checkpoint_step += 1

    while True:
        if getattr(wf, "_cancel_requested", False):
            for nid, status in list(states.items()):
                if status not in {"COMPLETED", "FAILED", "CANCELLED", "TIMED_OUT", "SKIPPED"}:
                    apply_node_status(states, nid, "CANCELLED")
            graph_status = "CANCELLED"
            break

        if getattr(wf, "_pause_requested", False):
            await wf._save_checkpoint(run_input, checkpoint_step, state, "任务图暂停前")
            checkpoint_step += 1
            await _set_graph_status(definition, "PAUSED")
            await _emit(definition, "GRAPH_PAUSED")
            await workflow.wait_condition(
                lambda: getattr(wf, "_resume_requested", False) or getattr(wf, "_cancel_requested", False)
            )
            wf._pause_requested = False
            wf._resume_requested = False
            await _set_graph_status(definition, "RUNNING")
            await _emit(definition, "GRAPH_RESUMED")
            continue

        evaluation = evaluate_dependencies(definition, states)
        blocked = evaluation.blocked

        for nid in evaluation.skipped:
            apply_node_status(states, nid, "SKIPPED")
            await _set_node_status(definition, nid, "SKIPPED")
            await _emit(definition, "NODE_SKIPPED", node_id=nid, reason="upstream_failed")
            event_cursor += 1

        if evaluation.stop_all:
            for nid in evaluation.cancelled:
                apply_node_status(states, nid, "CANCELLED")
                await _set_node_status(definition, nid, "CANCELLED")
                await _emit(definition, "NODE_CANCELLED", node_id=nid, reason="fail_fast")
                event_cursor += 1
            graph_status = "FAILED"
            break

        if evaluation.wait_manual:
            await _set_graph_status(definition, "WAITING")
            await workflow.wait_condition(
                lambda: getattr(wf, "_resume_requested", False)
                or getattr(wf, "_approval", None) is not None
                or getattr(wf, "_cancel_requested", False)
            )
            if getattr(wf, "_cancel_requested", False):
                graph_status = "CANCELLED"
                break
            wf._resume_requested = False
            wf._approval = None
            # Dependencies remain unresolved after manual intervention: skip them
            # once so the graph terminates instead of livelocking in WAITING/RUNNING.
            for nid in sorted(evaluation.blocked):
                if states.get(nid) == "PENDING":
                    apply_node_status(states, nid, "SKIPPED")
                    await _set_node_status(definition, nid, "SKIPPED")
                    await _emit(definition, "NODE_SKIPPED", node_id=nid, reason="manual_intervention")
                    event_cursor += 1
            graph_status = "FAILED"
            break

        ready = select_ready(definition, evaluation, max_concurrency)
        if not ready:
            if all(status in {"COMPLETED", "FAILED", "CANCELLED", "TIMED_OUT", "SKIPPED"} for status in states.values()):
                break
            await workflow.sleep(timedelta(seconds=2))
            continue

        for nid in ready:
            apply_node_status(states, nid, "READY")
            await _set_node_status(definition, nid, "READY")
            await _emit(definition, "NODE_READY", node_id=nid)
            event_cursor += 1

        async def _launch(node_id: str):
            node = definition.node_map[node_id]
            apply_node_status(states, node_id, "RUNNING")
            await _set_node_status(definition, node_id, "RUNNING")
            await _emit(definition, "NODE_STARTED", node_id=node_id)
            started = workflow.now()
            try:
                result = await _run_node_with_retry(
                    wf, run_input, state, definition, node, dict(results), limits.max_node_steps, emit,
                )
            except asyncio.CancelledError:
                # 取消路径：不再从子协程调度新的 workflow activity（此时 workflow
                # event loop 可能已进入关闭窗口，execute_activity 会抛
                # NotInWorkflowEventLoopError）。状态由上游取消流程统一收尾。
                apply_node_status(states, node_id, "CANCELLED")
                raise
            except BaseException as exc:
                # A single node must never abort the whole gather batch. Record an
                # explicit FAILED result so dependencies can apply failure policy.
                error_text = f"{type(exc).__name__}: {str(exc)[:1000]}"
                apply_node_status(states, node_id, "FAILED")
                await _set_node_status(
                    definition, node_id, "FAILED", last_error=error_text,
                )
                await _emit(definition, "NODE_FAILED", node_id=node_id, error=error_text)
                result = NodeResult(
                    node_id=node_id, graph_id=definition.graph_id,
                    tenant_id=definition.tenant_id, run_id=run_input.run_id,
                    status="FAILED", error=error_text,
                    error_class=classify_error(exc),
                )
            result.duration_ms = int((workflow.now() - started).total_seconds() * 1000)
            return node, result

        launched = await asyncio.gather(*[_launch(nid) for nid in ready], return_exceptions=True)
        for index, item in enumerate(launched):
            if isinstance(item, BaseException):
                # Infrastructure-level failure: fail every node in this batch that
                # has no terminal result, so the next loop cannot re-select it.
                graph_status = "FAILED"
                node_id = ready[index]
                node = definition.node_map[node_id]
                error_text = f"{type(item).__name__}: {str(item)[:1000]}"
                apply_node_status(states, node_id, "FAILED")
                await _set_node_status(definition, node_id, "FAILED", last_error=error_text)
                result = NodeResult(
                    node_id=node_id, graph_id=definition.graph_id,
                    tenant_id=definition.tenant_id, run_id=run_input.run_id,
                    status="FAILED", error=error_text,
                    error_class=classify_error(item),
                )
                results[node_id] = result
                await _persist_result(result)
                await _emit(definition, "NODE_FAILED", node_id=node_id, error=error_text)
                event_cursor += 1
                continue
            node, result = item
            results[node.node_id] = result
            if states.get(node.node_id) in {
                "RUNNING", "READY", "RETRY_WAIT", "WAITING_APPROVAL", "WAITING_INPUT",
            }:
                apply_node_status(states, node.node_id, result.status)
            state["history"].append({
                "type": "graph_node_result",
                "node_id": node.node_id,
                "result": result.output,
                "status": result.status,
                "error": result.error,
            })
            await _persist_result(result)
            result_event = (
                "NODE_COMPLETED" if result.status == "COMPLETED"
                else "NODE_TIMED_OUT" if result.status == "TIMED_OUT"
                else "NODE_CANCELLED" if result.status == "CANCELLED"
                else "NODE_FAILED"
            )
            await _emit(
                definition,
                result_event,
                node_id=node.node_id,
                duration_ms=result.duration_ms,
                token_usage={"input": result.token_input, "output": result.token_output},
                attempts=result.attempts,
                error=result.error,
            )
            event_cursor += 1

        _sync_state(state, definition, states, blocked, results, event_cursor)
        await wf._save_checkpoint(run_input, checkpoint_step, state, f"任务图批次完成：{','.join(ready)}")
        checkpoint_step += 1
        await wf._persist_telemetry(
            run_input, graph_started_at, graph_started_at + timedelta(seconds=run_input.max_duration_seconds),
            f"任务图批次完成：{','.join(ready)}",
            min(95, int(len(results) / max(len(definition.nodes), 1) * 100)),
            sum(r.token_input for r in results.values()),
            sum(r.token_output for r in results.values()),
            [], [],
        )

    aggregation = build_aggregation_context(definition, results)
    has_failure = any(result.status != "COMPLETED" for result in results.values()) or graph_status == "FAILED"
    if graph_status not in {"CANCELLED", "FAILED", "TIMED_OUT"}:
        graph_status = "FAILED" if has_failure else "SUCCEEDED"
    await _set_graph_status(definition, graph_status)
    graph_event = (
        "GRAPH_TIMED_OUT" if graph_status == "TIMED_OUT"
        else "GRAPH_CANCELLED" if graph_status == "CANCELLED"
        else "GRAPH_FAILED" if graph_status == "FAILED"
        else "GRAPH_COMPLETED"
    )
    await _emit(
        definition,
        graph_event,
        failed_nodes=[nid for nid, result in results.items() if result.status != "COMPLETED"],
    )
    _sync_state(state, definition, states, blocked, results, event_cursor + 1)
    await wf._save_checkpoint(run_input, checkpoint_step, state, "任务图完成")
    return {
        "status": graph_status,
        "graph_id": graph_id,
        "graph_version": graph_version,
        "result": {
            "summary": "任务图执行完成" if not has_failure else "任务图执行完成，但存在数据缺口",
            "graph_aggregation": aggregation,
            "node_results": {nid: result.model_dump(mode="json") for nid, result in results.items()},
        },
    }
