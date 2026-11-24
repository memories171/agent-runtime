"""B4: real 35-minute wall-clock Temporal entrypoint.

This test is intentionally opt-in. It is NOT executed by the fast layer and
must never be used as evidence for time-skipping tests. When enabled it uses a
real Temporal timer, waits real wall-clock time, then replays the workflow
history.
"""
from __future__ import annotations

import asyncio
import os
import time
from contextlib import suppress
from datetime import timedelta
from uuid import uuid4

import pytest
from temporalio import workflow
from temporalio.worker import Replayer, Worker

WALL_CLOCK_ON = os.getenv("RUN_WALL_CLOCK_30M", "").strip().lower() in {"1", "true", "yes"}
TEMPORAL_IT_ON = os.getenv("RUN_TEMPORAL_IT", "").strip().lower() in {"1", "true", "yes"}
TASK_QUEUE = f"b4-wall-clock-{uuid4().hex[:8]}"
SECONDS = int(os.getenv("WALL_CLOCK_SECONDS", "2100"))

pytestmark = [
    pytest.mark.wall_clock_30m,
    pytest.mark.timeout(2700),
    pytest.mark.skipif(
        not WALL_CLOCK_ON,
        reason="未设置 RUN_WALL_CLOCK_30M=1：35 分钟真实墙钟测试未调度",
    ),
]


@workflow.defn
class WallClockTimerWorkflow:
    @workflow.run
    async def run(self, seconds: int) -> dict[str, int]:
        await workflow.sleep(seconds)
        return {"slept_seconds": seconds}


@pytest.fixture(scope="module")
async def wall_clock_client():
    if not TEMPORAL_IT_ON:
        pytest.fail("RUN_WALL_CLOCK_30M=1 需要同时设置 RUN_TEMPORAL_IT=1（真实 Temporal 不得伪装 SKIP）")
    from temporalio.client import Client

    address = os.getenv("TEMPORAL_ADDRESS", "localhost:7233")
    namespace = os.getenv("TEMPORAL_NAMESPACE", "default")
    try:
        client = await asyncio.wait_for(Client.connect(address, namespace=namespace), timeout=10)
    except Exception as exc:
        pytest.fail(f"RUN_WALL_CLOCK_30M=1 已开启，但 Temporal 连接失败（{address}/{namespace}）：{exc!r}")
    yield client


@pytest.fixture(scope="module")
async def wall_clock_worker(wall_clock_client):
    worker = Worker(
        wall_clock_client,
        task_queue=TASK_QUEUE,
        workflows=[WallClockTimerWorkflow],
        max_concurrent_workflow_tasks=1,
    )
    task = asyncio.create_task(worker.run())
    await asyncio.sleep(0.5)
    yield worker
    await worker.shutdown()
    with suppress(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=10)
    if not task.done():
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


@pytest.mark.asyncio
async def test_real_35_minute_wall_clock_workflow_and_replay(wall_clock_client, wall_clock_worker):
    """Real wall-clock timer plus real-history replay; opt-in only."""
    started = time.monotonic()
    handle = await wall_clock_client.start_workflow(
        WallClockTimerWorkflow.run,
        SECONDS,
        id=f"b4-wall-clock-{uuid4()}",
        task_queue=TASK_QUEUE,
        execution_timeout=timedelta(seconds=SECONDS + 300),
    )
    result = await asyncio.wait_for(handle.result(), timeout=SECONDS + 180)
    elapsed = time.monotonic() - started

    assert elapsed >= SECONDS
    assert result == {"slept_seconds": SECONDS}

    history = await handle.fetch_history()
    await Replayer(workflows=[WallClockTimerWorkflow]).replay_workflow(history)
