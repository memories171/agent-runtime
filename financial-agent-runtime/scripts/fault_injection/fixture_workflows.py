"""Dedicated fault-injection workflows on an isolated task queue."""
from __future__ import annotations

from datetime import timedelta
from typing import Any

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from scripts.fault_injection.fixture_activities import (
        fi_slow_activity,
        fi_external_effect_activity,
        fi_unknown_effect_activity,
    )
    from agent_runtime.activities import (
        create_approval_binding_activity,
        validate_approval_binding_activity,
        consume_approval_binding_activity,
    )


@workflow.defn(name="FISlowWorkflow")
class FISlowWorkflow:
    """Long activity with Heartbeat Details cursor recovery + pause/resume."""

    def __init__(self) -> None:
        self._pause_requested = False
        self._resume_requested = False
        self._processed_commands: set[str] = set()

    @workflow.signal
    async def pause(self, payload: dict[str, Any] | None = None) -> None:
        cmd = (payload or {}).get("command_id")
        if cmd:
            if cmd in self._processed_commands:
                return
            self._processed_commands.add(cmd)
        self._pause_requested = True

    @workflow.signal
    async def resume(self, payload: dict[str, Any] | None = None) -> None:
        cmd = (payload or {}).get("command_id")
        if cmd:
            if cmd in self._processed_commands:
                return
            self._processed_commands.add(cmd)
        self._resume_requested = True

    @workflow.query
    def get_state(self) -> dict[str, Any]:
        return {
            "pause_requested": self._pause_requested,
            "resume_requested": self._resume_requested,
            "processed_commands_count": len(self._processed_commands),
        }

    @workflow.run
    async def run(self, params: dict[str, Any]) -> dict[str, Any]:
        run_id = params["run_id"]
        duration = int(params.get("duration_seconds", 120))
        if self._pause_requested:
            await workflow.wait_condition(lambda: self._resume_requested)
            self._pause_requested = False
            self._resume_requested = False

        result = await workflow.execute_activity(
            fi_slow_activity,
            params,
            start_to_close_timeout=timedelta(seconds=max(300, duration + 120)),
            heartbeat_timeout=timedelta(seconds=30),
            retry_policy=RetryPolicy(
                maximum_attempts=5,
                initial_interval=timedelta(seconds=2),
                backoff_coefficient=1.5,
            ),
        )
        # Safe-boundary pause: after the non-idempotent-safe activity completes.
        if self._pause_requested:
            await workflow.wait_condition(lambda: self._resume_requested)
            self._pause_requested = False
            self._resume_requested = False
        return {
            "run_id": run_id,
            "status": "SUCCEEDED",
            "final_cursor": result["final_cursor"],
            "resumed_from_cursor": result.get("resumed_from_cursor"),
            "recovered_from_heartbeat_details": result.get("recovered_from_heartbeat_details"),
            "attempt": result["attempt"],
            "workflow_id": workflow.info().workflow_id,
        }


@workflow.defn(name="FIEffectWorkflow")
class FIEffectWorkflow:
    """Idempotent external effect via mock service, retries reuse same key."""

    @workflow.run
    async def run(self, params: dict[str, Any]) -> dict[str, Any]:
        result = await workflow.execute_activity(
            fi_external_effect_activity,
            params,
            start_to_close_timeout=timedelta(seconds=60),
            retry_policy=RetryPolicy(maximum_attempts=5, initial_interval=timedelta(seconds=1)),
        )
        return {"status": "SUCCEEDED", **result}


@workflow.defn(name="FIUnknownWorkflow")
class FIUnknownWorkflow:
    """Effect committed then uncertain error -> invocation goes UNKNOWN."""

    @workflow.run
    async def run(self, params: dict[str, Any]) -> dict[str, Any]:
        try:
            await workflow.execute_activity(
                fi_unknown_effect_activity,
                params,
                start_to_close_timeout=timedelta(seconds=60),
                retry_policy=RetryPolicy(maximum_attempts=1),
            )
        except Exception as exc:
            return {"status": "UNKNOWN", "error": type(exc).__name__, "run_id": params["run_id"]}
        return {"status": "UNEXPECTED_SUCCESS", "run_id": params["run_id"]}


@workflow.defn(name="FIApprovalWorkflow")
class FIApprovalWorkflow:
    """Waits on a real ApprovalBinding via the production approval activities.

    Used to verify Worker SIGKILL while suspended in wait_condition: the binding
    remains PENDING in PostgreSQL and a replayed/decided signal revalidates and
    consumes it exactly once after the worker restarts.
    """

    def __init__(self) -> None:
        self._approval: dict[str, Any] | None = None
        self._processed_commands: set[str] = set()
        self._approval_id: str | None = None

    @workflow.signal
    async def provide_approval(self, payload: dict[str, Any]) -> None:
        cmd = payload.get("command_id")
        if cmd:
            if cmd in self._processed_commands:
                return
            self._processed_commands.add(cmd)
        self._approval = payload

    @workflow.query
    def get_state(self) -> dict[str, Any]:
        return {
            "has_approval": self._approval is not None,
            "processed_commands_count": len(self._processed_commands),
            "approval_id": self._approval_id,
        }

    @workflow.run
    async def run(self, params: dict[str, Any]) -> dict[str, Any]:
        tenant_id = params["tenant_id"]
        run_id = params["run_id"]
        tool_call_id = params["tool_call_id"]
        arguments = params.get("arguments", {"account": "ACC-FI", "amount": 100})
        binding = await workflow.execute_activity(
            create_approval_binding_activity,
            {
                "tenant_id": tenant_id, "run_id": run_id, "tool_call_id": tool_call_id,
                "tool_name": "fixture.external_write", "target_resource_id": "ACC-FI",
                "operation_type": "fixture.write", "arguments": arguments,
                "requested_by": "fi-requester", "policy_version": "v1", "risk_level": "HIGH",
            },
            start_to_close_timeout=timedelta(seconds=30),
        )
        approval_id = binding["approval_id"]
        self._approval_id = approval_id
        await workflow.wait_condition(lambda: self._approval is not None)
        approval = self._approval or {}
        approved = bool(approval.get("approved"))
        approver_id = approval.get("approver_user_id")
        if not approved:
            return {"status": "REJECTED", "approval_id": approval_id, "run_id": run_id}
        validated = await workflow.execute_activity(
            validate_approval_binding_activity,
            {
                "tenant_id": tenant_id, "run_id": run_id, "tool_call_id": tool_call_id,
                "tool_name": "fixture.external_write", "target_resource_id": "ACC-FI",
                "arguments": arguments, "policy_version": "v1", "approver_id": approver_id,
            },
            start_to_close_timeout=timedelta(seconds=30),
        )
        await workflow.execute_activity(
            consume_approval_binding_activity,
            {"tenant_id": tenant_id, "approval_id": approval_id, "version": validated["version"]},
            start_to_close_timeout=timedelta(seconds=30),
        )
        return {
            "status": "CONSUMED", "approval_id": approval_id, "run_id": run_id,
            "version": validated["version"], "approver_id": approver_id,
            "workflow_id": workflow.info().workflow_id,
        }
