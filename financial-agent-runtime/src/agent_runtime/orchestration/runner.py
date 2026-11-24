"""Sub-Agent 的 Temporal 确定性编排（不含任何直接 IO）。"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable
from uuid import uuid4

from temporalio import workflow
from temporalio.common import RetryPolicy, WorkflowIDReusePolicy
from temporalio.exceptions import ChildWorkflowError
from temporalio.workflow import ParentClosePolicy, execute_activity, start_child_workflow

from agent_runtime.graph.models import GraphDefinition, GraphNode, NodeResult
from agent_runtime.orchestration.activities import (
    create_delegation_activity,
    mark_delegation_cancel_requested_activity,
    mark_delegation_started_activity,
    settle_delegation_activity,
)

EmitFn = Callable[..., Awaitable[None]]


def _parse_deadline(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


async def _child_deadline_timer(seconds: float) -> None:
    """Child 独立 Durable Timer；到点正常返回，被取消时抛 CancelledError。"""
    try:
        await workflow.wait_condition(lambda: False, timeout=max(0.001, seconds))
    except TimeoutError:
        return


async def _settle(
    tenant_id: str,
    delegation_id: str,
    status: str,
    error: str,
    result_summary: dict[str, Any] | None,
) -> bool:
    settled = await execute_activity(
        settle_delegation_activity,
        {
            "tenant_id": tenant_id,
            "delegation_id": delegation_id,
            "status": status,
            "error": error,
            "result_summary": result_summary,
        },
        start_to_close_timeout=timedelta(seconds=15),
        retry_policy=RetryPolicy(maximum_attempts=3),
    )
    return bool(settled.get("won"))


async def _mark_cancel_requested(
    tenant_id: str,
    delegation_id: str,
    error: str,
) -> bool:
    """投影"取消请求已发出但 Child 未确认终止"（非终态，不伪装 CANCELLED）。"""
    result = await execute_activity(
        mark_delegation_cancel_requested_activity,
        {"tenant_id": tenant_id, "delegation_id": delegation_id, "error": error},
        start_to_close_timeout=timedelta(seconds=15),
        retry_policy=RetryPolicy(maximum_attempts=3),
    )
    return bool(result.get("won"))


async def _await_child_termination(handle, grace: float, extra_wait: float) -> bool:
    """等待 Child 真正终止后再返回（不虚报 CANCELLED）。

    先等宽限期，再等一段额外时间（Child 有 execution_timeout 兜底）。返回
    Child 是否已实际终止——只有终止后才允许写 CANCELLED/TIMED_OUT 终态。
    仍未终止时返回 False：保留 CANCEL_REQUESTED，靠 ParentClosePolicy.TERMINATE 兜底。
    """
    try:
        done_wait, _ = await workflow.wait({handle}, timeout=max(0.001, grace))
        if handle in done_wait:
            return True
    except Exception:
        pass
    try:
        await workflow.wait({handle}, timeout=max(0.001, extra_wait))
        return True
    except Exception:
        return False  # 仍未终止：保留 CANCEL_REQUESTED，靠 ParentClosePolicy.TERMINATE 兜底


async def execute_sub_agent_node(
    *,
    definition: GraphDefinition,
    node: GraphNode,
    state: dict[str, Any],
    upstream: dict[str, NodeResult],
    attempt: int,
    emit: EmitFn,
    registry: Any = None,
) -> NodeResult:
    """启动并等待一个 Child Workflow，把结果转换成现有 NodeResult。

    registry 为 workflow 运行时的 ChildRegistry（仅内存、replay 重建），可为 None
    以保持旧调用链兼容。Cancel/Timeout 只影响本 Child，不取消其他 Child。
    """
    from agent_runtime.workflow import AgentRunWorkflow

    run_input = state["workflow_input"]
    tenant_id = definition.tenant_id
    principal = state["principal"]
    root_token_id = state["orchestration"]["root_capability_token_id"]
    root_expires_at = state["orchestration"]["root_capability_token_expires_at"]
    dependency_ids = set(node.dependencies)
    scoped_upstream = {nid: item for nid, item in upstream.items() if nid in dependency_ids}
    timeout_seconds = node.timeout_seconds
    started_at = workflow.now()
    child_cancel_grace = float(state.get("child_cancel_grace_seconds", 15))

    def _node_result(status: str, summary: dict[str, Any], error: str | None = None) -> NodeResult:
        return NodeResult(
            node_id=node.node_id,
            graph_id=definition.graph_id,
            tenant_id=tenant_id,
            run_id=run_input["run_id"],
            status=status,
            output=summary,
            error=error,
            error_class="child_timeout" if status == "TIMED_OUT" else (
                "child_cancelled" if status == "CANCELLED" else None
            ),
            attempts=attempt,
            duration_ms=int((workflow.now() - started_at).total_seconds() * 1000),
            artifact_refs=summary.get("artifact_refs") or [],
            source_event_ids=[str(uuid4())],
            completed_at=workflow.now(),
        )

    # 取消/超时已被接受：不得再启动新的 Child。
    if registry is not None and registry.closed:
        reason = registry.closed_reason or "cancel"
        status = "CANCELLED" if reason != "timeout" else "TIMED_OUT"
        summary = {
            "child_run_id": "", "delegation_id": "", "status": status,
            "error": "parent_shutdown_before_start", "artifact_refs": [],
        }
        await emit("NODE_CANCELLED", node_id=node.node_id, payload={"reason": reason})
        return _node_result(status, summary, "parent_shutdown_before_start")

    created = await execute_activity(
        create_delegation_activity,
        {
            "tenant_id": tenant_id,
            "parent_run_id": run_input["run_id"],
            "principal": principal,
            "session_id": run_input.get("session_id"),
            "root_token_id": str(root_token_id),
            "root_expires_at": root_expires_at,
            "root_goal": definition.root_goal,
            "graph_id": definition.graph_id,
            "node": node.model_dump(mode="json"),
            "attempt": attempt,
            "upstream": [item.model_dump(mode="json") for item in scoped_upstream.values()],
            "child_context": {
                "task_parameters": node.model_dump(mode="json"),
                "context_references": [dict(ref) for ref in node.context_references],
                "upstream_nodes": [item.model_dump(mode="json") for item in scoped_upstream.values()],
                "artifact_refs": [
                    ref for item in scoped_upstream.values() for ref in (item.artifact_refs or [])
                ],
            },
            "max_depth": int(state.get("orchestration", {}).get("max_depth", 3)),
            "max_steps": int(state.get("orchestration", {}).get("max_steps", 8)),
            "max_duration_seconds": run_input["max_duration_seconds"],
            "parent_agent_id": run_input["agent_id"],
            "model_name": run_input.get("model_name"),
            "harness_version": run_input.get("harness_version", "dev"),
        },
        start_to_close_timeout=timedelta(seconds=30),
        retry_policy=RetryPolicy(maximum_attempts=2, initial_interval=timedelta(seconds=1)),
    )
    delegation = created["delegation"]
    child_input = created["child_input"]
    child_workflow_id = created["child_workflow_id"]
    delegation_id = str(delegation["delegation_id"])
    child_run_id = str(child_input["run_id"])
    await emit(
        "AGENT_DELEGATED",
        node_id=node.node_id,
        payload={
            "delegation_id": delegation_id,
            "child_run_id": child_run_id,
            "child_workflow_id": child_workflow_id,
            "attempt": attempt,
            "created": created.get("created", False),
        },
    )

    # create_delegation_activity 期间可能已收到 Cancel：此时不得启动 Child。
    if registry is not None and registry.closed:
        reason = registry.closed_reason or "cancel"
        status = "CANCELLED" if reason != "timeout" else "TIMED_OUT"
        await _settle(
            tenant_id, delegation_id, status, "parent_shutdown_before_start",
            {"child_run_id": child_run_id, "delegation_id": delegation_id, "status": status},
        )
        await emit("AGENT_CANCELLED", node_id=node.node_id,
                   payload={"delegation_id": delegation_id, "reason": reason})
        return _node_result(
            status,
            {"child_run_id": child_run_id, "delegation_id": delegation_id,
             "status": status, "artifact_refs": []},
            "parent_shutdown_before_start",
        )

    handle = await start_child_workflow(
        AgentRunWorkflow.run,
        arg=child_input,
        id=child_workflow_id,
        task_queue=workflow.info().task_queue,
        result_type=dict[str, Any],
        execution_timeout=timedelta(seconds=timeout_seconds + 60),
        parent_close_policy=ParentClosePolicy.TERMINATE,
        id_reuse_policy=WorkflowIDReusePolicy.ALLOW_DUPLICATE,
        retry_policy=RetryPolicy(maximum_attempts=1),
        memo={"delegation_id": delegation_id, "parent_run_id": run_input["run_id"]},
    )
    if registry is not None:
        from agent_runtime.orchestration.child_registry import ChildHandleEntry
        registry.register(ChildHandleEntry(
            delegation_id=delegation_id,
            child_run_id=child_run_id,
            child_workflow_id=child_workflow_id,
            node_id=node.node_id,
            handle=handle,
            lifecycle="RUNNING",
        ))

    await execute_activity(
        mark_delegation_started_activity,
        {"tenant_id": tenant_id, "delegation_id": delegation_id},
        start_to_close_timeout=timedelta(seconds=15),
        retry_policy=RetryPolicy(maximum_attempts=3),
    )
    await emit(
        "AGENT_STARTED",
        node_id=node.node_id,
        payload={"delegation_id": delegation_id, "child_run_id": child_run_id},
    )

    # Cancel 与 Child Start 竞态：启动完成后若已关闭，立即取消本 Child。
    if registry is not None and registry.closed:
        try:
            handle.cancel()
        except Exception:
            pass

    child_result: dict[str, Any] | None = None
    try:
        deadline = _parse_deadline(delegation.get("deadline_at"))
        remaining = (deadline - workflow.now()).total_seconds() if deadline else timeout_seconds
        timer = asyncio.create_task(_child_deadline_timer(remaining))
        # shutdown waiter：Parent Cancel/Timeout 时 registry 被 close，立即唤醒本节点。
        shutdown_waiter = asyncio.create_task(
            workflow.wait_condition(lambda: registry is not None and registry.closed)
        ) if registry is not None else None
        try:
            wait_set = {handle, timer}
            if shutdown_waiter is not None:
                wait_set.add(shutdown_waiter)
            done, _ = await workflow.wait(
                wait_set, return_when=asyncio.FIRST_COMPLETED
            )
            shutdown_triggered = shutdown_waiter is not None and shutdown_waiter in done
            if shutdown_triggered and handle not in done:
                reason = registry.closed_reason or "cancel"
                status = "TIMED_OUT" if reason == "timeout" else "CANCELLED"
                request_msg = f"parent {reason}: cancellation requested"
                # 三阶段区分：① 请求已发出（非终态投影）→ ② 等 Child 确认 → ③ 确认后写终态。
                await _mark_cancel_requested(tenant_id, delegation_id, request_msg)
                try:
                    handle.cancel()
                except Exception:
                    pass
                confirmed = await _await_child_termination(
                    handle, child_cancel_grace, extra_wait=float(timeout_seconds)
                )
                if confirmed:
                    message = f"parent {reason}: child cancellation confirmed"
                    summary = {
                        "child_run_id": child_run_id, "delegation_id": delegation_id,
                        "status": status, "error": message, "artifact_refs": [],
                    }
                    await _settle(tenant_id, delegation_id, status, message, summary)
                    await emit("CHILD_CANCEL_PROPAGATED" if status == "CANCELLED" else "CHILD_TIMEOUT",
                               node_id=node.node_id,
                               payload={"delegation_id": delegation_id, "reason": reason})
                else:
                    # 超时仍未终止：不虚报 CANCELLED，保留 CANCEL_REQUESTED，
                    # 靠 ParentClosePolicy.TERMINATE 在 Parent 关闭时做最后保护。
                    message = f"parent {reason}: child not terminated (cancel pending)"
                    summary = {
                        "child_run_id": child_run_id, "delegation_id": delegation_id,
                        "status": "CANCEL_REQUESTED", "error": message, "artifact_refs": [],
                    }
                    await emit("CHILD_CANCEL_UNCONFIRMED", node_id=node.node_id,
                               payload={"delegation_id": delegation_id, "child_run_id": child_run_id,
                                        "child_workflow_id": child_workflow_id, "reason": reason})
                return _node_result(status, summary, message)
            if timer in done and handle not in done:
                request_msg = f"child deadline exceeded after {int(remaining)}s: cancellation requested"
                await _mark_cancel_requested(tenant_id, delegation_id, request_msg)
                try:
                    handle.cancel()
                except Exception:
                    pass
                confirmed = await _await_child_termination(
                    handle, child_cancel_grace, extra_wait=float(timeout_seconds)
                )
                if confirmed:
                    message = f"child deadline exceeded after {int(remaining)}s"
                    summary = {
                        "child_run_id": child_run_id,
                        "delegation_id": delegation_id,
                        "status": "TIMED_OUT",
                        "error": message,
                        "artifact_refs": [],
                    }
                    await _settle(tenant_id, delegation_id, "TIMED_OUT", message, summary)
                    await emit("CHILD_TIMEOUT", node_id=node.node_id,
                               payload={"delegation_id": delegation_id, "child_run_id": child_run_id})
                else:
                    message = f"child deadline exceeded after {int(remaining)}s: not confirmed within grace"
                    summary = {
                        "child_run_id": child_run_id, "delegation_id": delegation_id,
                        "status": "CANCEL_REQUESTED", "error": message, "artifact_refs": [],
                    }
                    await emit("CHILD_CANCEL_UNCONFIRMED", node_id=node.node_id,
                               payload={"delegation_id": delegation_id, "child_run_id": child_run_id,
                                        "child_workflow_id": child_workflow_id, "reason": "child_timeout"})
                return _node_result("TIMED_OUT", summary, message)
            child_result = handle.result()
        finally:
            if not timer.done():
                timer.cancel()
            if shutdown_waiter is not None and not shutdown_waiter.done():
                shutdown_waiter.cancel()
    except (asyncio.CancelledError, Exception) as exc:
        message = f"{type(exc).__name__}: {str(exc)[:500]}"
        shutdown_active = registry is not None and registry.closed
        if not shutdown_active:
            # 真正的 Child 失败：写 FAILED 并抛出，交给 Graph FailurePolicy 处理。
            summary = {
                "child_run_id": child_run_id, "delegation_id": delegation_id,
                "status": "FAILED", "error": message, "artifact_refs": [],
            }
            await _settle(tenant_id, delegation_id, "FAILED", message, summary)
            await emit("AGENT_FAILED", node_id=node.node_id,
                       payload={"delegation_id": delegation_id, "error": message})
            raise
        # Parent Cancel/Timeout 触发的取消/超时。
        is_timeout = (
            (registry is not None and registry.closed_reason == "timeout")
            or (state.get("shutdown_reason") == "timeout")
        )
        status = "TIMED_OUT" if is_timeout else "CANCELLED"
        summary = {
            "child_run_id": child_run_id, "delegation_id": delegation_id,
            "status": status, "error": message, "artifact_refs": [],
        }
        await _settle(tenant_id, delegation_id, status, message, summary)
        await emit(
            "CHILD_CANCEL_PROPAGATED" if status == "CANCELLED" else "CHILD_TIMEOUT",
            node_id=node.node_id,
            payload={"delegation_id": delegation_id, "reason": message},
        )
        return _node_result(status, summary, message)
    finally:
        if registry is not None:
            registry.discard(delegation_id)

    duration_ms = int((workflow.now() - started_at).total_seconds() * 1000)
    status = str(child_result.get("status", "FAILED"))
    result_payload = child_result.get("result")
    if not isinstance(result_payload, dict):
        result_payload = {"summary": str(result_payload or "")}
    result_summary = {
        "child_run_id": child_run_id,
        "delegation_id": delegation_id,
        "status": status,
        "result_summary": result_payload,
        "artifact_refs": result_payload.get("artifact_refs") or [],
        "error": None,
        "execution_metadata": {
            "child_workflow_id": child_workflow_id,
            "capability_token_id": delegation["capability_token_id"],
            "duration_ms": duration_ms,
            "attempt": attempt,
            "agent_role": node.agent_role or "executor",
        },
    }
    if status != "SUCCEEDED":
        await _settle(tenant_id, delegation_id, "FAILED", f"child status {status}", result_summary)
        await emit("AGENT_FAILED", node_id=node.node_id,
                   execution_duration_ms=duration_ms,
                   payload={"delegation_id": delegation_id, "error": f"child status {status}"})
        raise ChildWorkflowError(f"child workflow returned status {status}")
    await _settle(tenant_id, delegation_id, "COMPLETED", "", result_summary)
    await emit(
        "AGENT_COMPLETED",
        node_id=node.node_id,
        execution_duration_ms=duration_ms,
        payload={"delegation_id": delegation_id, "child_run_id": child_run_id},
    )
    return NodeResult(
        node_id=node.node_id,
        graph_id=definition.graph_id,
        tenant_id=tenant_id,
        run_id=run_input["run_id"],
        status="COMPLETED",
        output=result_summary,
        attempts=attempt,
        duration_ms=duration_ms,
        artifact_refs=result_summary["artifact_refs"],
        source_event_ids=[str(uuid4())],
        completed_at=workflow.now(),
    )
