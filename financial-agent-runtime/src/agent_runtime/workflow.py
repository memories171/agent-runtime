from __future__ import annotations

import asyncio
import json
from datetime import timedelta
from typing import Any

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio import exceptions as temporal_exceptions


def _restored_step_number(current_step: Any) -> int:
    """Best-effort step index from a restored ``current_step`` label.

    Snapshot ``current_step`` is typically a label (e.g. ``"step-7"``). We only
    extract a trailing integer; unknown formats contribute 0. Completed steps
    is the authoritative progress signal.
    """
    if isinstance(current_step, int):
        return max(0, current_step)
    if isinstance(current_step, str):
        digits = ""
        for ch in reversed(current_step):
            if ch.isdigit():
                digits = ch + digits
            else:
                break
        if digits:
            return max(0, int(digits))
    return 0


with workflow.unsafe.imports_passed_through():
    from agent_runtime.config import settings
    from agent_runtime.activities import (
        agent_decide_activity,
        append_history_event_activity,
        authorize_tool_activity,
        create_approval_binding_activity,
        validate_approval_binding_activity,
        consume_approval_binding_activity,
        execute_tool_activity,
        mark_worker_failure_recovered_activity,
        persist_checkpoint_activity,
        persist_status_activity,
        persist_telemetry_activity,
        persist_worker_failure_activity,
        record_checkpoint_restore_activity,
        record_worker_recovery_activity,
        retrieve_memory_activity,
        load_skills_activity,
    )
    from agent_runtime.contracts import WorkflowInput
    from agent_runtime.checkpoint.restore_contract import RestoreContract, inject_restore_state
    from agent_runtime.recovery.invocations import make_tool_idempotency_key
    from agent_runtime.orchestration.child_registry import ChildHandleEntry, ChildRegistry
    from agent_runtime.graph.runner import execute_graph
    from agent_runtime.orchestration.activities import settle_delegation_activity
    from agent_runtime.activities_context import (
        assemble_context_activity,
        summarize_context_activity,
        load_context_state_activity,
    )

PROMPT_VERSION = "RUNTIME-PROMPT-v1"
# Rolling-summary trigger: once this many new history events accumulate, summarize.
SUMMARY_THRESHOLD_EVENTS = 16


@workflow.defn
class AgentRunWorkflow:
    def __init__(self) -> None:
        self._pause_requested = False
        self._resume_requested = False
        self._cancel_requested = False
        self._user_input: str | None = None
        self._approval: dict[str, Any] | None = None
        self._status = "CREATED"
        self._step = 0
        self._processed_commands: set[str] = set()
        # Context Manager state (all deterministic; rebuilt from activities on replay).
        self._context_event_cursor = 0
        self._context_compression_count = 0
        self._context_summary: dict[str, Any] | None = None
        self._context_pack_id: str | None = None
        # Runtime child registry + shutdown coordination (rebuilt by Temporal replay).
        self._child_registry: ChildRegistry = ChildRegistry()
        self._shutdown_reason: str | None = None  # "cancel" | "timeout"
        self._deadline_reached: bool = False

    def _accept_command(self, payload: dict[str, Any] | None) -> bool:
        """Signal 去重：Outbox 至少一次投递，重复 command_id 只生效一次。"""
        command_id = (payload or {}).get("command_id")
        if not command_id:
            return True
        if command_id in self._processed_commands:
            return False
        self._processed_commands.add(command_id)
        return True

    @workflow.signal
    async def pause(self, payload: dict[str, Any] | None = None) -> None:
        if not self._accept_command(payload):
            return
        self._pause_requested = True

    @workflow.signal
    async def resume(self, payload: dict[str, Any] | None = None) -> None:
        if not self._accept_command(payload):
            return
        self._resume_requested = True

    @workflow.signal
    async def cancel(self) -> None:
        self._cancel_requested = True
        # 用户 Cancel 永远优先：即使 Timeout 已先触发，仍升级为 cancel。
        self._shutdown_reason = "cancel"

    @workflow.signal
    async def provide_input(self, value: str) -> None:
        self._user_input = value

    @workflow.signal
    async def provide_approval(self, payload: dict[str, Any]) -> None:
        if not self._accept_command(payload):
            return
        self._approval = payload

    @workflow.query
    def get_state(self) -> dict[str, Any]:
        return {"status": self._status, "step": self._step}

    @workflow.query
    def get_child_registry(self) -> list[dict[str, str]]:
        """只读查询运行时 child 注册表摘要（不含 handle）。"""
        return self._child_registry.snapshot()

    def _request_shutdown(self, reason: str) -> None:
        """接受取消/超时；用户 Cancel 优先，关闭 registry 阻止新 Child。"""
        if self._shutdown_reason == "cancel":
            return
        if self._shutdown_reason is None:
            self._shutdown_reason = reason
        self._cancel_requested = True

    async def _emit_run_event(self, run_id: str, tenant_id: str, event_type: str, payload: dict[str, Any]) -> None:
        await workflow.execute_activity(
            append_history_event_activity,
            {"run_id": run_id, "tenant_id": tenant_id, "event_type": event_type, "payload": payload},
            start_to_close_timeout=timedelta(seconds=15),
            retry_policy=RetryPolicy(maximum_attempts=3),
        )

    async def _settle_child_delegation(
        self, tenant_id: str, delegation_id: str, status: str, reason: str, result_summary: dict[str, Any] | None = None
    ) -> None:
        """幂等写入 child 终态；已完成 Child 不会被取消/超时覆盖。"""
        await workflow.execute_activity(
            settle_delegation_activity,
            {
                "tenant_id": tenant_id,
                "delegation_id": delegation_id,
                "status": status,
                "error": reason,
                "result_summary": result_summary,
            },
            start_to_close_timeout=timedelta(seconds=15),
            retry_policy=RetryPolicy(maximum_attempts=3),
        )

    async def _cancel_all_children(self, tenant_id: str, run_id: str) -> None:
        """接受关闭：关闭 registry 阻止新 Child；各执行中的 sub_agent 节点自行 cancel
        自己的 handle 并等宽限期。此处仅记录取消请求，不直接触碰 handle，避免与
        execute_sub_agent_node 的等待发生双重取消竞态。"""
        self._child_registry.close(self._shutdown_reason or "cancel")
        entries = self._child_registry.active()
        await self._emit_run_event(run_id, tenant_id, "AGENT_CANCEL_REQUESTED", {
            "reason": self._shutdown_reason,
            "active_delegations": [e.delegation_id for e in entries],
        })

    async def _shutdown_watchdog(
        self, run_id: str, tenant_id: str, start_time, max_duration_seconds: int
    ) -> None:
        """Durable watchdog：等待 Cancel 信号或 Parent Deadline（Timer），触发后取消所有活跃 Child。"""
        elapsed = (workflow.now() - start_time).total_seconds()
        remaining = max(0.0, float(max_duration_seconds) - elapsed)
        try:
            await workflow.wait_condition(
                lambda: self._cancel_requested, timeout=remaining
            )
        except TimeoutError:
            # 用户 Cancel 优先：_request_shutdown 在 reason=cancel 时直接返回。
            self._request_shutdown("timeout")
            self._deadline_reached = True
        # 用户 Cancel 优先：signal 已把 reason 设为 cancel 时不覆盖。
        if self._shutdown_reason == "timeout":
            await self._emit_run_event(run_id, tenant_id, "PARENT_TIMEOUT", {
                "max_duration_seconds": max_duration_seconds,
            })
        await self._cancel_all_children(tenant_id, run_id)

    async def _close_watchdog(self, watchdog_task: asyncio.Task | None, *, drain: bool) -> None:
        """drain=True 等待 watchdog 完成所有活跃 Child 取消与宽限期后再关闭 Parent；
        drain=False 在成功路径取消 watchdog，避免遗留未等待 task 破坏 replay 确定性。"""
        if watchdog_task is None:
            if drain:
                await self._cancel_all_children(getattr(self, "_tenant_id", ""), "")
            return
        if watchdog_task.done():
            return
        if drain:
            try:
                await asyncio.shield(watchdog_task)
            except Exception:
                pass
        else:
            watchdog_task.cancel()
            try:
                await asyncio.shield(watchdog_task)
            except (asyncio.CancelledError, Exception):
                pass

    def _shutdown_status(self, fallback: str) -> str:
        """Cancel 优先于 Timeout；无 shutdown 时用调用方 fallback。"""
        if self._shutdown_reason == "cancel":
            return "CANCELLED"
        if self._shutdown_reason == "timeout":
            return "TIMED_OUT"
        return fallback

    async def _set_status(self, run_id: str, status: str, **extra: Any) -> None:
        self._status = status
        payload = {"run_id": run_id, "status": status, "tenant_id": getattr(self, "_tenant_id", ""), **extra}
        await workflow.execute_activity(
            persist_status_activity,
            payload,
            start_to_close_timeout=timedelta(seconds=15),
            retry_policy=RetryPolicy(maximum_attempts=5),
        )

    async def _persist_telemetry(
        self,
        run_input: WorkflowInput,
        start_time,
        deadline,
        current_step: str,
        progress_pct: int,
        token_input: int,
        token_output: int,
        tool_calls: list[dict[str, Any]],
        llm_calls: list[dict[str, Any]],
    ) -> None:
        now = workflow.now()
        await workflow.execute_activity(
            persist_telemetry_activity,
            {
                "run_id": run_input.run_id,
                "tenant_id": run_input.tenant_id,
                "model_name": run_input.model_name,
                "harness_version": run_input.harness_version,
                "prompt_version": PROMPT_VERSION,
                "duration_seconds": int((now - start_time).total_seconds()),
                "heartbeat_at": now.isoformat(),
                "deadline_at": deadline.isoformat(),
                "current_step": current_step,
                "progress_pct": max(0, min(100, progress_pct)),
                "token_input": token_input,
                "token_output": token_output,
                "estimated_cost_usd": round(token_input * 0.000002 + token_output * 0.000008, 6),
                "tool_calls": tool_calls,
                "llm_calls": llm_calls,
            },
            start_to_close_timeout=timedelta(seconds=15),
            retry_policy=RetryPolicy(maximum_attempts=3),
        )

    async def _save_checkpoint(
        self, run_input: WorkflowInput, step: int, state: dict[str, Any], current_step: str
    ) -> str:
        checkpoint_id = str(workflow.uuid4())
        workflow_state = dict(state)
        workflow_state.update({
            "current_step": current_step,
            "status": self._status,
            "max_duration_seconds": run_input.max_duration_seconds,
            "remaining_steps": max(0, run_input.max_steps - step),
            "context_pack_ref": self._context_pack_id,
            "context_event_cursor": self._context_event_cursor,
            "context_summary_ref": (self._context_summary or {}).get("summary_id"),
            "context_compression_count": self._context_compression_count,
        })
        info = workflow.info()
        await workflow.execute_activity(
            persist_checkpoint_activity,
            {
                "checkpoint_id": checkpoint_id,
                "run_id": run_input.run_id,
                "tenant_id": run_input.tenant_id,
                "step": step,
                "summary": f"保存步骤 {step} 的完整 Agent State 快照：{current_step}",
                "worker_id": getattr(info, "task_queue", "temporal-worker"),
                "idempotency_key": f"{run_input.run_id}:checkpoint:{step}",
                "state": workflow_state,
                "workflow_id": info.workflow_id,
                "workflow_run_id": info.run_id,
            },
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=RetryPolicy(maximum_attempts=3),
        )
        return checkpoint_id

    @staticmethod
    def _context_events(state: dict[str, Any]) -> list[dict[str, Any]]:
        events = []
        for idx, item in enumerate(state.get("history", [])):
            events.append({
                "sequence": idx,
                "event_id": f"history-{idx}",
                "event_type": item.get("type", "EVENT"),
                "payload": {k: v for k, v in item.items() if k != "type"},
                "relevance_score": 1.0 if item.get("type") in {"user_input", "approval"} else 0.5,
            })
        return events

    async def _maybe_summarize(self, run_input: WorkflowInput, state: dict[str, Any]) -> None:
        events = self._context_events(state)
        if len(events) - self._context_event_cursor < SUMMARY_THRESHOLD_EVENTS:
            return
        batch = events[self._context_event_cursor:]
        result = await workflow.execute_activity(
            summarize_context_activity,
            {
                "run_id": run_input.run_id,
                "tenant_id": run_input.tenant_id,
                "model_version": run_input.model_name or "",
                "source_event_start": self._context_event_cursor,
                "source_event_end": self._context_event_cursor + len(batch),
                "source_event_ids": [e["event_id"] for e in batch],
                "events": batch,
                "previous_summary": (self._context_summary or {}).get("summary_content", ""),
                "summary_version": ((self._context_summary or {}).get("summary_version", 0)) + 1,
            },
            start_to_close_timeout=timedelta(minutes=2),
            retry_policy=RetryPolicy(maximum_attempts=2),
        )
        if result.get("status") == "STORED":
            self._context_summary = result
            self._context_event_cursor = int(result.get("source_event_end", self._context_event_cursor))
        else:
            # Summary failure is non-fatal: safe-truncation fallback keeps goals/status.
            self._context_event_cursor = self._context_event_cursor + len(batch)
            self._context_compression_count += 1

    async def _assemble_context(self, run_input: WorkflowInput, state: dict[str, Any]) -> None:
        context_config = dict(run_input.context_config or {})
        child_seed = dict(run_input.child_context or {})
        task_objectives = list(context_config.get("task_objectives") or [])
        immutable_constraints = list(context_config.get("immutable_constraints") or [])
        artifact_references: list[dict[str, Any]] = []
        node_context_payload: dict[str, Any] | None = None
        if child_seed:
            task_objectives = [{
                "objective": child_seed.get("task_instruction") or run_input.prompt,
                "agent_role": child_seed.get("agent_role"),
                "task_parameters": child_seed.get("task_parameters") or {},
            }]
            immutable_constraints = list(child_seed.get("immutable_constraints") or immutable_constraints)
            artifact_references = list(child_seed.get("artifact_refs") or [])
            node_context_payload = {"upstream_nodes": list(child_seed.get("upstream_nodes") or [])}
        result = await workflow.execute_activity(
            assemble_context_activity,
            {
                "context_pack_id": str(workflow.uuid4()),
                "run_id": run_input.run_id,
                "tenant_id": run_input.tenant_id,
                "agent_id": run_input.agent_id,
                "model_name": run_input.model_name,
                "model_version": context_config.get("model_version"),
                "harness_version": run_input.harness_version,
                "original_user_goal": child_seed.get("task_instruction") or run_input.prompt,
                "immutable_constraints": immutable_constraints,
                "acceptance_criteria": list(context_config.get("acceptance_criteria") or []),
                "task_objectives": task_objectives,
                "artifact_references": artifact_references,
                "node_context_payload": node_context_payload,
                "parent_run_id": child_seed.get("parent_run_id"),
                "delegation_id": child_seed.get("delegation_id"),
                "capability_token_id": child_seed.get("capability_token_id"),
                "context_source": "child_projection" if child_seed else "root",
                "source_context_pack_ids": list(child_seed.get("source_context_pack_ids") or []),
                "policy_references": list(context_config.get("policy_references") or []),
                "current_plan": list(state.get("plan") or []),
                "completed_steps": list(state.get("completed_steps") or []),
                "pending_steps": list(state.get("pending_steps") or []),
                "current_step": state.get("current_step", ""),
                "current_step_status": state.get("current_step_status", ""),
                "events": self._context_events(state),
                "memory": list(state.get("memory_context") or []),
                "tool_results": [
                    item for item in state.get("history", []) if item.get("type") == "tool_result"
                ],
                "summary": self._context_summary,
                "event_cursor": len(state.get("history", [])),
                "compression_count": self._context_compression_count,
                "budget_config": dict(context_config.get("budget_config") or {}),
                "context_window_override": int(context_config.get("context_window_override", 0)),
            },
            start_to_close_timeout=timedelta(seconds=45),
            retry_policy=RetryPolicy(maximum_attempts=3, initial_interval=timedelta(seconds=1)),
        )
        # Store the assembled payload for the model activity; keep the id for telemetry.
        state["context_model_payload"] = result["model_payload"]
        self._context_pack_id = result.get("context_pack_id")
        if result.get("degraded"):
            self._context_compression_count += 1
        state["last_context_pack_id"] = self._context_pack_id

    async def _record_failure(
        self, run_input: WorkflowInput, reason: str, checkpoint_id: str | None, retry_count: int, recovery_count: int
    ) -> str:
        failure_id = str(workflow.uuid4())
        await workflow.execute_activity(
            persist_worker_failure_activity,
            {
                "failure_id": failure_id,
                "run_id": run_input.run_id,
                "tenant_id": run_input.tenant_id,
                "worker_id": getattr(workflow.info(), "task_queue", "temporal-worker"),
                "reason": reason,
                "checkpoint_id": checkpoint_id,
                "recovery_count": recovery_count,
                "retry_count": retry_count,
                "recovery_position": checkpoint_id or "workflow start",
                "idempotency_key": f"{run_input.run_id}:failure:{failure_id}",
            },
            start_to_close_timeout=timedelta(seconds=15),
            retry_policy=RetryPolicy(maximum_attempts=3),
        )
        await workflow.execute_activity(
            append_history_event_activity,
            {
                "run_id": run_input.run_id,
                "tenant_id": run_input.tenant_id,
                "event_type": "RECOVERING",
                "payload": {"failure_id": failure_id, "checkpoint_id": checkpoint_id},
            },
            start_to_close_timeout=timedelta(seconds=15),
        )
        return failure_id

    async def _mark_recovered(self, run_input: WorkflowInput, failure_id: str, checkpoint_id: str | None, recovery_count: int) -> None:
        await workflow.execute_activity(
            mark_worker_failure_recovered_activity,
            {
                "failure_id": failure_id,
                "checkpoint_id": checkpoint_id,
                "recovery_count": recovery_count,
                "tenant_id": run_input.tenant_id,
            },
            start_to_close_timeout=timedelta(seconds=15),
            retry_policy=RetryPolicy(maximum_attempts=3),
        )

    @workflow.run
    async def run(self, raw_input: dict[str, Any]) -> dict[str, Any]:
        run_input = WorkflowInput.model_validate(raw_input)
        run_id = run_input.run_id
        self._tenant_id = run_input.tenant_id
        start_time = workflow.now()
        deadline = start_time + timedelta(seconds=run_input.max_duration_seconds)
        last_checkpoint_id: str | None = None
        recovery_count = 0
        token_input = 0
        token_output = 0
        tool_calls: list[dict[str, Any]] = []
        llm_calls: list[dict[str, Any]] = []
        state: dict[str, Any] = {
            "run_id": run_id,
            "tenant_id": run_input.tenant_id,
            "user_id": run_input.user_id,
            "session_id": run_input.session_id,
            "agent_id": run_input.agent_id,
            "prompt": run_input.prompt,
            "model_name": run_input.model_name,
            "harness_version": run_input.harness_version,
            "capability_token_id": run_input.capability_token_id,
            "run_kind": run_input.run_kind,
            "parent_run_id": run_input.parent_run_id,
            "delegation_id": run_input.delegation_id,
            "principal": {
                "tenant_id": run_input.tenant_id,
                "user_id": run_input.user_id,
                "scopes": run_input.scopes,
                "organization_id": run_input.organization_id,
                "roles": run_input.roles,
                "permissions": run_input.permissions,
                "authentication_method": run_input.authentication_method,
                "token_expires_at": run_input.token_expires_at.isoformat() if run_input.token_expires_at else None,
            },
            "history": [],
            "memory_context": [],
            "skills": [],
            "tool_calls": tool_calls,
            "llm_calls": llm_calls,
        }
        # B7: inject verified checkpoint business state into this NEW run.
        # The decision is derived only from the immutable WorkflowInput, so it
        # is deterministic under replay. workflow.patched keeps older histories
        # (which never carry a restore contract) replaying on the new branch.
        restore_contract_raw = getattr(run_input, "restore_contract", None)
        if restore_contract_raw:
            if workflow.patched("b7-checkpoint-restore-v1"):
                contract = RestoreContract.model_validate(restore_contract_raw)
                inject_restore_state(state, contract)
                tool_calls = state["tool_calls"]
                llm_calls = state["llm_calls"]
                self._step = max(
                    self._step,
                    len(state.get("completed_steps") or []),
                    _restored_step_number(state.get("current_step")),
                )
                self._restored_confirmed_keys = set(contract.confirmed_tool_results.keys())
                self._restore_contract = contract
                token_input = int(state.get("token_input", 0) or 0)
                token_output = int(state.get("token_output", 0) or 0)
        self._child_cancel_grace_seconds = (
            run_input.child_cancel_grace_seconds
            if getattr(run_input, "child_cancel_grace_seconds", None) is not None
            else settings.orchestration_child_cancel_grace_seconds
        )
        watchdog_task: asyncio.Task | None = None
        try:
            await self._set_status(run_id, "RUNNING", event_type="RUN_STARTED")
            if getattr(self, "_restore_contract", None) is not None:
                rc = self._restore_contract
                await workflow.execute_activity(
                    record_checkpoint_restore_activity,
                    {
                        "tenant_id": run_input.tenant_id,
                        "run_id": run_id,
                        "user_id": rc.restored_by_user_id,
                        "source_run_id": rc.source.source_run_id,
                        "checkpoint_id": rc.source.checkpoint_id,
                        "checkpoint_sequence": rc.source.checkpoint_sequence,
                        "state_digest": rc.source.state_digest,
                        "idempotency_key": f"restore:{run_id}:{rc.source.checkpoint_id}",
                    },
                    start_to_close_timeout=timedelta(seconds=15),
                    retry_policy=RetryPolicy(maximum_attempts=3),
                )
            watchdog_task = asyncio.create_task(self._shutdown_watchdog(
                run_id, run_input.tenant_id, start_time, run_input.max_duration_seconds,
            ))
            await self._persist_telemetry(run_input, start_time, deadline, "初始化", 0, 0, 0, tool_calls, llm_calls)
            state["memory_context"] = await workflow.execute_activity(
                retrieve_memory_activity,
                {"principal": state["principal"], "query": run_input.prompt},
                start_to_close_timeout=timedelta(seconds=20),
                retry_policy=RetryPolicy(maximum_attempts=2),
            )
            if run_input.skill_ids:
                state["skills"] = await workflow.execute_activity(
                    load_skills_activity,
                    run_input.skill_ids,
                    start_to_close_timeout=timedelta(seconds=15),
                    retry_policy=RetryPolicy(maximum_attempts=2),
                )

            graph_config = dict(getattr(run_input, "graph_config", None) or {})
            if graph_config.get("enabled"):
                graph_result = await execute_graph(self, run_input, state)
                node_results = (graph_result.get("result") or {}).get("node_results") or {}
                graph_token_input = sum(int(item.get("token_input", 0)) for item in node_results.values())
                graph_token_output = sum(int(item.get("token_output", 0)) for item in node_results.values())
                graph_status = graph_result["status"]
                # Parent 收到 cancel/timeout：先等 watchdog 完成所有活跃 Child 的取消与宽限期。
                if self._shutdown_reason is not None:
                    await self._close_watchdog(watchdog_task, drain=True)
                    final_status = self._shutdown_status(graph_status)
                    await self._set_status(
                        run_id, final_status, error=f"parent {self._shutdown_reason}",
                        event_type="RUN_TIMED_OUT" if final_status == "TIMED_OUT" else "RUN_CANCELLED",
                    )
                    return {"status": final_status, "result": graph_result["result"]}
                await self._close_watchdog(watchdog_task, drain=False)
                await self._persist_telemetry(
                    run_input, start_time, deadline,
                    "任务图完成" if graph_status == "SUCCEEDED" else "任务图未完成",
                    100 if graph_status == "SUCCEEDED" else 99,
                    graph_token_input, graph_token_output, [], [],
                )
                if graph_status == "SUCCEEDED":
                    await self._set_status(
                        run_id, "SUCCEEDED", result=graph_result["result"], event_type="RUN_SUCCEEDED",
                    )
                else:
                    await self._set_status(
                        run_id, "FAILED" if graph_status == "FAILED" else "CANCELLED",
                        error="graph did not succeed", event_type=(
                            "RUN_CANCELLED" if graph_status == "CANCELLED" else "RUN_FAILED"
                        ),
                    )
                return {"status": graph_status, "result": graph_result["result"]}

            while self._step < run_input.max_steps:
                if self._cancel_requested:
                    await self._close_watchdog(watchdog_task, drain=True)
                    final_status = self._shutdown_status("CANCELLED")
                    await self._set_status(
                        run_id, final_status, event_type=(
                            "RUN_TIMED_OUT" if final_status == "TIMED_OUT" else "RUN_CANCELLED"
                        ),
                    )
                    return {"status": final_status}

                elapsed = (workflow.now() - start_time).total_seconds()
                if elapsed >= run_input.max_duration_seconds:
                    self._request_shutdown("timeout")
                    self._deadline_reached = True
                    continue

                if self._pause_requested:
                    await self._set_status(run_id, "RUNNING", event_type="PAUSING", pause_state="PAUSING")
                    last_checkpoint_id = await self._save_checkpoint(run_input, self._step, state, "任务暂停前")
                    await self._set_status(run_id, "PAUSED", event_type="RUN_PAUSED", pause_state="PAUSED", checkpoint_id=last_checkpoint_id)
                    await workflow.wait_condition(lambda: self._resume_requested or self._cancel_requested)
                    if self._cancel_requested:
                        continue
                    await self._set_status(run_id, "PAUSED", event_type="RESUMING", pause_state="RESUMING")
                    self._pause_requested = False
                    self._resume_requested = False
                    await self._set_status(run_id, "RUNNING", event_type="RUN_RESUMED", pause_state="NONE", checkpoint_id=last_checkpoint_id)

                if self._user_input is not None:
                    state["history"].append({"type": "user_input", "content": self._user_input})
                    self._user_input = None
                    await self._set_status(run_id, "RUNNING", event_type="USER_INPUT_RECEIVED")

                # Context Manager: rolling summary + deterministic ContextPack assembly.
                try:
                    await self._maybe_summarize(run_input, state)
                    await self._assemble_context(run_input, state)
                except Exception:
                    # Context degradation never blocks the run: fall back to legacy
                    # payload (model adapter uses [-16:] only in this fallback path).
                    state.pop("context_model_payload", None)

                decision_start = workflow.now()
                try:
                    decision = await workflow.execute_activity(
                        agent_decide_activity,
                        state,
                        start_to_close_timeout=timedelta(minutes=3),
                        heartbeat_timeout=timedelta(seconds=45),
                        retry_policy=RetryPolicy(maximum_attempts=3, initial_interval=timedelta(seconds=2), maximum_interval=timedelta(seconds=15)),
                    )
                except Exception as exc:
                    failure_id = await self._record_failure(
                        run_input, f"agent_decide_activity failed: {type(exc).__name__}: {str(exc)[:300]}",
                        last_checkpoint_id, recovery_count, recovery_count,
                    )
                    recovery_count += 1
                    if recovery_count > 3:
                        raise
                    await workflow.sleep(timedelta(seconds=2))
                    await self._mark_recovered(run_input, failure_id, last_checkpoint_id, recovery_count)
                    continue

                self._step += 1
                action = decision["action"]
                provider_usage = decision.pop("_provider_usage", {}) or {}
                prompt_tokens = max(1, len(json.dumps(state, ensure_ascii=False, default=str)) // 4)
                completion_tokens = max(1, len(json.dumps(decision, ensure_ascii=False, default=str)) // 4)
                token_input += prompt_tokens
                token_output += completion_tokens
                state["token_input"] = token_input
                state["token_output"] = token_output
                state["estimated_cost_usd"] = round(token_input * 0.000002 + token_output * 0.000008, 6)
                llm_calls.append({
                    "call_id": str(workflow.uuid4()),
                    "model": run_input.model_name,
                    "purpose": "plan_and_decide",
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": completion_tokens,
                    "latency_ms": int((workflow.now() - decision_start).total_seconds() * 1000),
                    "started_at": decision_start.isoformat(),
                    "status": "SUCCESS",
                    "provider_reported": bool(provider_usage.get("reported")),
                    "provider_prompt_tokens": provider_usage.get("prompt_tokens"),
                    "provider_completion_tokens": provider_usage.get("completion_tokens"),
                    "token_delta": (
                        (provider_usage["prompt_tokens"] - prompt_tokens)
                        if provider_usage.get("reported") and provider_usage.get("prompt_tokens") is not None
                        else None
                    ),
                    "context_pack_id": self._context_pack_id,
                })
                progress = min(95, int(self._step / max(run_input.max_steps, 1) * 100))
                await self._persist_telemetry(
                    run_input, start_time, deadline, f"步骤 {self._step}：{action}", progress,
                    token_input, token_output, tool_calls, llm_calls,
                )

                if action == "tool_call":
                    tool_call = decision.get("tool") or {}
                    tool_name = tool_call.get("name")
                    arguments = tool_call.get("arguments") or {}
                    auth = await workflow.execute_activity(
                        authorize_tool_activity,
                        {
                            "tool_name": tool_name,
                            "tenant_id": run_input.tenant_id,
                            "principal": state["principal"],
                            "capability_token_id": state.get("capability_token_id"),
                            "subject_run_id": run_id,
                        },
                        start_to_close_timeout=timedelta(seconds=15),
                        retry_policy=RetryPolicy(maximum_attempts=3),
                    )
                    if not auth.get("allowed"):
                        observation = {"type": "tool_result", "tool": tool_name, "error": "permission_denied", "reason": auth.get("reason")}
                        state["history"].append(observation)
                        tool_calls.append({
                            "call_id": str(workflow.uuid4()), "tool": tool_name or "unknown",
                            "server": "tool-gateway", "status": "FAILED", "started_at": workflow.now().isoformat(),
                            "duration_ms": 0, "risk_level": "denied", "idempotency_key": "-",
                        })
                        await workflow.execute_activity(
                            append_history_event_activity,
                            {"run_id": run_id, "tenant_id": run_input.tenant_id, "event_type": "TOOL_DENIED", "payload": observation},
                            start_to_close_timeout=timedelta(seconds=15),
                        )
                        continue

                    tool_call_id = str(tool_call.get("call_id") or workflow.uuid4())
                    idempotency_key = make_tool_idempotency_key(run_input.tenant_id, run_id, tool_call_id)

                    # B7: if this tool call was already CONFIRMED before the
                    # checkpoint, reuse the recorded observation. Never produce
                    # a second external side effect for the same logical call.
                    confirmed = getattr(self, "_restored_confirmed_keys", set())
                    contract = getattr(self, "_restore_contract", None)
                    reuse_key = f"{tool_name}:{tool_call_id}"
                    if contract is not None and reuse_key in confirmed:
                        recorded = contract.confirmed_tool_results[reuse_key]
                        observation = dict(recorded)
                        observation["restored_from_checkpoint"] = True
                        state["history"].append(observation)
                        tool_calls.append({
                            "call_id": tool_call_id, "tool": tool_name or "unknown",
                            "server": "tool-gateway", "status": "RESTORED_REUSED",
                            "started_at": workflow.now().isoformat(), "duration_ms": 0,
                            "idempotency_key": idempotency_key,
                            "restored_from_checkpoint": contract.source.checkpoint_id,
                        })
                        await workflow.execute_activity(
                            append_history_event_activity,
                            {"run_id": run_id, "tenant_id": run_input.tenant_id,
                             "event_type": "TOOL_RESULT_RESTORED", "payload": observation},
                            start_to_close_timeout=timedelta(seconds=15),
                        )
                        continue

                    approval_granted = False
                    approval_meta: dict[str, Any] = {}
                    if auth.get("requires_approval"):
                        approval_resource = {}
                        target_resource_id = ""
                        if tool_name == "financial.create_review_case":
                            case_type = arguments.get("case_type")
                            approval_resource = {
                                "approval_resource_type": {"KYC": "customer", "AML": "transaction", "TRANSACTION_REVIEW": "account"}.get(case_type),
                                "approval_resource_id": arguments.get("subject_id"),
                            }
                            target_resource_id = str(arguments.get("subject_id", ""))
                        binding = await workflow.execute_activity(
                            create_approval_binding_activity,
                            {
                                "tenant_id": run_input.tenant_id, "run_id": run_id,
                                "tool_call_id": tool_call_id, "tool_name": tool_name,
                                "target_resource_id": target_resource_id,
                                "operation_type": tool_name, "arguments": arguments,
                                "requested_by": run_input.user_id,
                                "policy_version": "v1",
                                "risk_level": auth.get("risk_level", "HIGH"),
                            },
                            start_to_close_timeout=timedelta(seconds=20),
                            retry_policy=RetryPolicy(maximum_attempts=3),
                        )
                        approval_meta = {
                            "approval_id": binding["approval_id"],
                            "target_resource_id": target_resource_id,
                            "approver_id": None,
                        }
                        await self._set_status(
                            run_id, "WAITING_APPROVAL", event_type="APPROVAL_REQUIRED",
                            tool=tool_name, tool_call_id=tool_call_id,
                            approval_id=binding["approval_id"],
                            action_hash=binding["action_hash"],
                            arguments_hash=binding["arguments_hash"],
                            requires_distinct_approver=bool(auth.get("requires_distinct_approver", False)),
                            **approval_resource,
                        )
                        await workflow.wait_condition(lambda: self._approval is not None or self._cancel_requested)
                        if self._cancel_requested:
                            continue
                        approval = self._approval or {}
                        self._approval = None
                        if not approval.get("approved"):
                            state["history"].append({"type": "tool_result", "tool": tool_name, "error": "approval_denied"})
                            await self._set_status(run_id, "RUNNING", event_type="APPROVAL_DENIED", tool=tool_name)
                            continue
                        approval_meta["approver_id"] = approval.get("approver_user_id")
                        approval_granted = True
                        # 执行前重验绑定；参数/策略/租户/过期/撤销任何变化都会拒绝。
                        validated = await workflow.execute_activity(
                            validate_approval_binding_activity,
                            {
                                "tenant_id": run_input.tenant_id, "run_id": run_id,
                                "tool_call_id": tool_call_id, "tool_name": tool_name,
                                "target_resource_id": target_resource_id, "arguments": arguments,
                                "policy_version": "v1", "approver_id": approval_meta["approver_id"],
                            },
                            start_to_close_timeout=timedelta(seconds=20),
                            retry_policy=RetryPolicy(maximum_attempts=2),
                        )
                        approval_meta["consume_version"] = validated["version"]
                        await self._set_status(run_id, "RUNNING", event_type="APPROVAL_GRANTED", tool=tool_name, approval_id=binding["approval_id"])

                    fencing_token = self._step
                    tool_start = workflow.now()
                    try:
                        result = await workflow.execute_activity(
                            execute_tool_activity,
                            {
                                "run_id": run_id,
                                "tenant_id": run_input.tenant_id,
                                "tool_name": tool_name,
                                "arguments": arguments,
                                "principal": state["principal"],
                                "approval_granted": approval_granted,
                                "idempotency_key": idempotency_key,
                                "tool_call_id": tool_call_id,
                                "capability_token_id": state.get("capability_token_id"),
                                "subject_run_id": run_id,
                                "fencing_token": fencing_token,
                                "lease_owner": getattr(workflow.info(), "task_queue", "worker"),
                            },
                            start_to_close_timeout=timedelta(hours=1),
                            schedule_to_close_timeout=timedelta(hours=2),
                            heartbeat_timeout=timedelta(seconds=30),
                            retry_policy=RetryPolicy(
                                maximum_attempts=3,
                                initial_interval=timedelta(seconds=3),
                                maximum_interval=timedelta(seconds=30),
                                backoff_coefficient=2.0,
                            ),
                        )
                        observation = {"type": "tool_result", "tool": tool_name, "result": result}
                        tool_status = "SUCCESS"
                        if approval_granted and approval_meta.get("approval_id"):
                            await workflow.execute_activity(
                                consume_approval_binding_activity,
                                {
                                    "tenant_id": run_input.tenant_id,
                                    "approval_id": approval_meta["approval_id"],
                                    "version": approval_meta["consume_version"],
                                },
                                start_to_close_timeout=timedelta(seconds=20),
                                retry_policy=RetryPolicy(maximum_attempts=3),
                            )
                    except Exception as exc:
                        message = str(exc)
                        needs_reconciliation = "UNKNOWN" in message or "reconcile" in message.lower()
                        observation = {
                            "type": "tool_result", "tool": tool_name,
                            "error": type(exc).__name__, "message": message[:500],
                            "reconciliation_required": needs_reconciliation,
                        }
                        tool_status = "UNKNOWN" if needs_reconciliation else "FAILED"
                    state["history"].append(observation)
                    tool_calls.append({
                        "call_id": str(workflow.uuid4()), "tool": tool_name or "unknown",
                        "server": getattr(workflow.info(), "task_queue", "tool-gateway"),
                        "status": tool_status, "started_at": tool_start.isoformat(),
                        "duration_ms": int((workflow.now() - tool_start).total_seconds() * 1000),
                        "risk_level": auth.get("risk_level", "low"), "idempotency_key": idempotency_key,
                    })
                    await workflow.execute_activity(
                        append_history_event_activity,
                        {"run_id": run_id, "tenant_id": run_input.tenant_id, "event_type": "TOOL_OBSERVED", "payload": observation},
                        start_to_close_timeout=timedelta(seconds=15),
                    )
                    if self._step % 3 == 0:
                        last_checkpoint_id = await self._save_checkpoint(run_input, self._step, state, f"工具 {tool_name} 完成后")
                    continue

                if action == "ask_user":
                    await self._set_status(run_id, "WAITING_INPUT", event_type="USER_INPUT_REQUIRED", message=decision.get("message", "Additional input required"))
                    await workflow.wait_condition(lambda: self._user_input is not None or self._cancel_requested)
                    if self._cancel_requested:
                        continue
                    continue

                if action == "request_approval":
                    await self._set_status(run_id, "WAITING_APPROVAL", event_type="APPROVAL_REQUIRED", message=decision.get("message", "Approval required"))
                    await workflow.wait_condition(lambda: self._approval is not None or self._cancel_requested)
                    if self._cancel_requested:
                        continue
                    state["history"].append({"type": "approval", "decision": self._approval})
                    self._approval = None
                    await self._set_status(run_id, "RUNNING", event_type="APPROVAL_RESOLVED")
                    continue

                if action == "finish":
                    result = decision.get("result") or {"summary": decision.get("message", "Task completed")}
                    last_checkpoint_id = await self._save_checkpoint(run_input, self._step, state, "任务完成前")
                    await self._close_watchdog(watchdog_task, drain=False)
                    await self._persist_telemetry(
                        run_input, start_time, deadline, "完成", 100,
                        token_input, token_output, tool_calls, llm_calls,
                    )
                    await self._set_status(run_id, "SUCCEEDED", result=result, event_type="RUN_SUCCEEDED", checkpoint_id=last_checkpoint_id)
                    return {"status": "SUCCEEDED", "result": result}

                raise ValueError(f"unsupported agent action: {action}")

            raise RuntimeError("run exceeded max_agent_steps")
        except temporal_exceptions.CancelledError:
            # Child/自身被取消：进入明确的 CANCELLED 终态（用户 Cancel 优先）。
            await self._close_watchdog(watchdog_task, drain=True)
            final_status = self._shutdown_status("CANCELLED")
            await self._set_status(
                run_id, final_status,
                error=f"cancelled: {self._shutdown_reason or 'external'}",
                event_type="RUN_CANCELLED",
            )
            raise
        except Exception as exc:
            # shutdown 已触发但主流程以异常收尾：仍等 watchdog 完成 Child 取消。
            if self._shutdown_reason is not None:
                await self._close_watchdog(watchdog_task, drain=True)
            else:
                await self._close_watchdog(watchdog_task, drain=False)
            await self._persist_telemetry(
                run_input, start_time, deadline, "执行失败", min(99, int(self._step / max(run_input.max_steps, 1) * 100)),
                token_input, token_output, tool_calls, llm_calls,
            )
            await self._set_status(run_id, "FAILED", error=f"{type(exc).__name__}: {str(exc)[:1000]}", event_type="RUN_FAILED")
            raise
