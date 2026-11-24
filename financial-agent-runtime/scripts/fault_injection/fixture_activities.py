"""Dedicated fault-injection activities with Heartbeat Details progress cursors."""
from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from typing import Any

import httpx
from temporalio import activity

logger = logging.getLogger(__name__)

MOCK_SERVICE_URL = os.environ.get("FI_MOCK_SERVICE_URL", "http://mock-external:8002")
SIGNAL_FILE_DIR = "/app/fi-data/signals"


def _write_signal(run_id: str, signal: str, payload: dict[str, Any]) -> None:
    """Write a test-observable progress signal to shared volume (not app DB)."""
    from pathlib import Path
    d = Path(SIGNAL_FILE_DIR)
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{run_id}.{signal}.json").write_text(
        json.dumps({"run_id": run_id, "signal": signal, "payload": payload, "at": datetime.now(timezone.utc).isoformat()}),
        encoding="utf-8",
    )


@activity.defn
async def fi_slow_activity(payload: dict[str, Any]) -> dict[str, Any]:
    """Long-running activity: >=120s, heartbeat every 2s, cursor in Heartbeat Details.

    At 40% progress it writes an observable signal so the orchestrator can SIGKILL
    the worker mid-activity. On retry it resumes from the last heartbeat cursor.
    """
    run_id = payload["run_id"]
    duration = int(payload.get("duration_seconds", 120))
    interval = int(payload.get("heartbeat_interval_seconds", 2))
    notify_pct = int(payload.get("notify_at_pct", 40))
    item_count = int(payload.get("item_count", 60))
    notified = False

    # Heartbeat Details: last committed cursor (items fully processed).
    details: dict[str, Any] = {
        "stage": "fi_slow",
        "run_id": run_id,
        "cursor": 0,
        "processed_items": [],
        "attempt": activity.info().attempt,
    }
    # Recover the last committed cursor from Temporal Heartbeat Details on retry.
    start_cursor = int(payload.get("resume_cursor", 0))
    heartbeat_details = activity.info().heartbeat_details
    recovered_from_details = False
    for detail in reversed(list(heartbeat_details)):
        try:
            prior = json.loads(detail) if isinstance(detail, (str, bytes)) else detail
            if isinstance(prior, dict) and prior.get("stage") == "fi_slow" and prior.get("run_id") == run_id:
                start_cursor = int(prior.get("cursor", start_cursor))
                recovered_from_details = activity.info().attempt > 1
                break
        except (ValueError, TypeError, AttributeError):
            continue
    details["cursor"] = start_cursor
    details["resumed_from_cursor"] = start_cursor
    details["recovered_from_heartbeat_details"] = recovered_from_details
    elapsed = 0.0
    item_every = duration / item_count

    while elapsed < duration:
        await asyncio.sleep(interval)
        elapsed += interval
        pct = min(100, int(elapsed / duration * 100))
        cursor = min(item_count, start_cursor + int(elapsed / item_every))
        # Only mark items committed up to the last heartbeat (never beyond confirmed cursor).
        details["cursor"] = cursor
        details["processed_items"] = list(range(start_cursor, cursor))
        details["elapsed_seconds"] = elapsed
        details["progress_pct"] = pct
        activity.heartbeat(json.dumps(details, ensure_ascii=False))
        logger.info("fi_slow heartbeat run=%s pct=%s cursor=%s attempt=%s", run_id, pct, cursor, activity.info().attempt)
        if pct >= notify_pct and not notified:
            _write_signal(run_id, "reached_target_pct", {"pct": pct, "cursor": cursor, "duration": duration})
            notified = True

    return {
        "run_id": run_id,
        "status": "SUCCEEDED",
        "final_cursor": details["cursor"],
        "resumed_from_cursor": details["resumed_from_cursor"],
        "recovered_from_heartbeat_details": recovered_from_details,
        "processed_items": details["processed_items"],
        "attempt": activity.info().attempt,
    }


@activity.defn
async def fi_external_effect_activity(payload: dict[str, Any]) -> dict[str, Any]:
    """Perform an idempotent external side effect against the mock service."""
    run_id = payload["run_id"]
    tenant_id = payload["tenant_id"]
    idempotency_key = payload["idempotency_key"]
    operation = payload.get("operation", "write")
    attempt = activity.info().attempt
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.post(f"{MOCK_SERVICE_URL}/effects", json={
            "idempotency_key": idempotency_key,
            "tenant_id": tenant_id,
            "run_id": run_id,
            "operation": operation,
            "payload": f"attempt={attempt}",
        })
        response.raise_for_status()
        result = response.json()
    activity.heartbeat(json.dumps({"stage": "effect", "attempt": attempt, "result": result}))
    if result.get("duplicate"):
        _write_signal(run_id, "effect_reused", {"attempt": attempt, "idempotency_key": idempotency_key})
    else:
        _write_signal(run_id, "effect_committed", {"attempt": attempt, "idempotency_key": idempotency_key})
    # Simulate crash window: effect committed, Activity Completion not yet sent.
    delay = int(payload.get("delay_after_effect_seconds", 0))
    if delay:
        for _ in range(delay):
            await asyncio.sleep(1)
            activity.heartbeat(json.dumps({"stage": "post_effect_wait", "attempt": attempt, "result": result}))
    return {"idempotency_key": idempotency_key, "attempt": attempt, **result}


@activity.defn
async def fi_unknown_effect_activity(payload: dict[str, Any]) -> dict[str, Any]:
    """Write side effect then raise a transport-style error to simulate UNKNOWN."""
    run_id = payload["run_id"]
    tenant_id = payload["tenant_id"]
    idempotency_key = payload["idempotency_key"]
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.post(f"{MOCK_SERVICE_URL}/effects", json={
            "idempotency_key": idempotency_key,
            "tenant_id": tenant_id,
            "run_id": run_id,
            "operation": "unknown-write",
            "payload": "pre-completion-crash-simulation",
        })
        response.raise_for_status()
    raise TimeoutError("simulated transport error after external effect, before Activity completion")
