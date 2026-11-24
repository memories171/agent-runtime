"""Independent FI worker on a dedicated task queue. Production worker untouched."""
from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

# Make scripts/ importable as a package inside the container.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from temporalio.client import Client
from temporalio.worker import Worker

from agent_runtime.activities import (
    append_history_event_activity,
    authorize_tool_activity,
    consume_approval_binding_activity,
    create_approval_binding_activity,
    execute_tool_activity,
    persist_checkpoint_activity,
    persist_status_activity,
    validate_approval_binding_activity,
)
from agent_runtime.config import settings
from scripts.fault_injection.fixture_activities import (
    fi_external_effect_activity,
    fi_slow_activity,
    fi_unknown_effect_activity,
)
from scripts.fault_injection.fixture_tools import register_fi_tools
from scripts.fault_injection.fixture_workflows import (
    FIEffectWorkflow,
    FIUnknownWorkflow,
    FISlowWorkflow,
    FIApprovalWorkflow,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

FI_TASK_QUEUE = "agent-runtime-fi"


async def main() -> None:
    register_fi_tools()
    client = await Client.connect(settings.temporal_address, namespace=settings.temporal_namespace)
    worker = Worker(
        client,
        task_queue=FI_TASK_QUEUE,
        workflows=[FISlowWorkflow, FIEffectWorkflow, FIUnknownWorkflow, FIApprovalWorkflow],
        activities=[
            fi_slow_activity, fi_external_effect_activity, fi_unknown_effect_activity,
            authorize_tool_activity, execute_tool_activity,
            persist_status_activity, persist_checkpoint_activity, append_history_event_activity,
            create_approval_binding_activity, validate_approval_binding_activity, consume_approval_binding_activity,
        ],
        max_concurrent_activities=20,
    )
    logger.info("FI worker started on task_queue=%s", FI_TASK_QUEUE)
    await worker.run()


if __name__ == "__main__":
    asyncio.run(main())
