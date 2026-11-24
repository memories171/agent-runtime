"""Isolated Worker for Task Graph integration tests (never used in production)."""
from __future__ import annotations

import asyncio
import logging

from temporalio.client import Client
from temporalio.worker import Worker

from agent_runtime.activities import (
    append_history_event_activity,
    authorize_tool_activity,
    consume_approval_binding_activity,
    create_approval_binding_activity,
    execute_tool_activity,
    mark_worker_failure_recovered_activity,
    persist_checkpoint_activity,
    persist_status_activity,
    persist_telemetry_activity,
    persist_worker_failure_activity,
    record_worker_recovery_activity,
    retrieve_memory_activity,
    load_skills_activity,
    validate_approval_binding_activity,
)
from agent_runtime.activities_context import (
    assemble_context_activity,
    load_context_state_activity,
    summarize_context_activity,
)
from agent_runtime.config import settings
from agent_runtime.orchestration.activities import (
    build_child_context_activity,
    create_delegation_activity,
    mark_delegation_cancel_requested_activity,
    mark_delegation_started_activity,
    mark_delegation_terminal_activity,
    settle_delegation_activity,
)
from agent_runtime.graph.activities import (
    append_graph_event_activity,
    load_graph_activity,
    load_latest_graph_for_run_activity,
    persist_graph_activity,
    plan_graph_activity,
    replan_graph_activity,
    save_node_result_activity,
    update_graph_status_activity,
    update_node_status_activity,
    validate_graph_proposal_activity,
)
from agent_runtime.workflow import AgentRunWorkflow
from scripts.graph_it_activities import graph_it_slow_decide, graph_it_stubborn_decide

GRAPH_IT_TASK_QUEUE = "agent-runtime-graph-it"


def build_activities(*, stubborn_decide: bool = False) -> list:
    """Production activities plus a test decision activity; excludes production decide.

    stubborn_decide=True 时用不响应取消的变体替换 agent_decide_activity，
    仅用于 ParentClosePolicy.TERMINATE 最后保护验证。
    """
    decide = graph_it_stubborn_decide if stubborn_decide else graph_it_slow_decide
    return [
        persist_status_activity, append_history_event_activity,
        authorize_tool_activity, execute_tool_activity,
        create_approval_binding_activity, validate_approval_binding_activity,
        consume_approval_binding_activity,
        retrieve_memory_activity, load_skills_activity,
        persist_telemetry_activity, persist_checkpoint_activity,
        persist_worker_failure_activity, mark_worker_failure_recovered_activity,
        record_worker_recovery_activity,
        assemble_context_activity, summarize_context_activity, load_context_state_activity,
        plan_graph_activity, validate_graph_proposal_activity, persist_graph_activity,
        load_graph_activity, load_latest_graph_for_run_activity,
        append_graph_event_activity, save_node_result_activity,
        update_node_status_activity, update_graph_status_activity, replan_graph_activity,
        build_child_context_activity, create_delegation_activity, mark_delegation_started_activity,
        mark_delegation_terminal_activity, mark_delegation_cancel_requested_activity,
        settle_delegation_activity,
        decide,
    ]


async def run_worker() -> None:
    client = await Client.connect(settings.temporal_address, namespace=settings.temporal_namespace)
    worker = Worker(
        client,
        task_queue=GRAPH_IT_TASK_QUEUE,
        workflows=[AgentRunWorkflow],
        activities=build_activities(),
        max_concurrent_activities=20,
        max_concurrent_workflow_tasks=20,
    )
    await worker.run()


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run_worker())


if __name__ == "__main__":
    main()
