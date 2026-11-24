"""B5.2: replay a real history exported from the isolated Staging Temporal."""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from temporalio.client import Client
from temporalio.worker import Replayer

from agent_runtime.workflow import AgentRunWorkflow

ADDRESS = "localhost:7234"


async def main() -> int:
    client = await Client.connect(ADDRESS, namespace="default")
    # Pick the most recent completed AgentRunWorkflow from staging.
    workflows = []
    async for wf in client.list_workflows("WorkflowType = 'AgentRunWorkflow' and ExecutionStatus = 'Completed'"):
        workflows.append(wf)
    if not workflows:
        print("NO_COMPLETED_WORKFLOW")
        return 2
    workflows.sort(key=lambda w: w.start_time or 0, reverse=True)
    handle = client.get_workflow_handle(workflows[0].id, run_id=workflows[0].run_id)
    history = await handle.fetch_history()
    events = list(history.events)
    await Replayer(workflows=[AgentRunWorkflow]).replay_workflow(history)
    print(f"REPLAY_OK workflow_id={workflows[0].id} run_id={workflows[0].run_id} events={len(events)}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
