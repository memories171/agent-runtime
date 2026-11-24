"""Pure parallelism calculations over delegation attempt intervals.

This module intentionally has no database, Temporal, FastAPI or network access.
All inputs (including ``now``) are supplied by the caller.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable
from uuid import UUID

from agent_runtime.orchestration.models import TERMINAL_DELEGATION_STATUSES

ACTIVE_DELEGATION_STATUSES = frozenset({"RUNNING", "WAITING", "CANCEL_REQUESTED"})
NOT_STARTED_DELEGATION_STATUSES = frozenset({"ISSUED", "CHILD_STARTING"})


def _as_utc(value: datetime) -> datetime:
    """Normalize a datetime to UTC. Naive datetimes are treated as UTC."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


@dataclass(frozen=True)
class DelegationInterval:
    """A single delegation attempt. It is never deduplicated by node_id."""

    delegation_id: UUID | str | None
    child_run_id: UUID | str | None
    node_id: str | None
    status: str
    started_at: datetime | None
    completed_at: datetime | None


@dataclass(frozen=True)
class ParallelismResult:
    current_parallelism: int
    max_parallelism: int
    average_parallelism: float
    total_agent_active_time: float
    delegation_attempt_count: int
    distinct_child_run_count: int
    distinct_node_count: int
    unknown_interval_count: int
    partial_interval_count: int
    valid_interval_count: int


def calculate_parallelism(
    records: Iterable[DelegationInterval],
    *,
    start_time: datetime,
    end_time: datetime,
    now: datetime,
) -> ParallelismResult:
    """Calculate active-agent parallelism over ``[start_time, end_time)``.

    Every ``DelegationInterval`` is one delegation attempt. Failed retries are
    separate attempts and are never collapsed by ``node_id``.

    Interval semantics:
    - closed-open ``[started_at, completed_at)``
    - terminal status: requires both timestamps and ``completed_at >= started_at``
    - active status: requires ``started_at`` and no ``completed_at``; end is
      ``min(now, end_time)`` and the interval is reported as partial
    - not-started status: contributes no interval
    - invalid/missing timestamps contribute to ``unknown_interval_count``
    - ``created_at`` is never used to infer ``started_at``
    """
    window_start = _as_utc(start_time)
    window_end = _as_utc(end_time)
    current_time = _as_utc(now)
    if window_end <= window_start:
        raise ValueError("end_time must be greater than start_time")

    window_seconds = (window_end - window_start).total_seconds()

    deltas: dict[datetime, int] = {}
    clipped_intervals: list[tuple[datetime, datetime, datetime, bool]] = []
    total_seconds = 0.0
    unknown_interval_count = 0
    partial_interval_count = 0
    valid_interval_count = 0

    child_run_ids: set[str] = set()
    node_ids: set[str] = set()
    attempt_count = 0

    for record in records:
        attempt_count += 1
        if record.child_run_id is not None:
            child_run_ids.add(str(record.child_run_id))
        if record.node_id is not None:
            node_ids.add(record.node_id)

        status = record.status
        if status in NOT_STARTED_DELEGATION_STATUSES:
            continue

        if status in ACTIVE_DELEGATION_STATUSES:
            if record.started_at is None or record.completed_at is not None:
                unknown_interval_count += 1
                continue
            interval_end = min(current_time, window_end)
            partial_interval_count += 1
        elif status in TERMINAL_DELEGATION_STATUSES:
            if record.started_at is None or record.completed_at is None:
                unknown_interval_count += 1
                continue
            interval_end = _as_utc(record.completed_at)
        else:
            unknown_interval_count += 1
            continue

        interval_start = _as_utc(record.started_at)
        if interval_end < interval_start:
            unknown_interval_count += 1
            continue

        valid_interval_count += 1
        is_open = status in ACTIVE_DELEGATION_STATUSES
        clipped_start = max(interval_start, window_start)
        clipped_end = min(interval_end, window_end)
        if clipped_end <= clipped_start:
            # A zero-length interval after clipping contributes no active time.
            continue

        total_seconds += (clipped_end - clipped_start).total_seconds()
        clipped_intervals.append((interval_start, clipped_start, clipped_end, is_open))
        deltas[clipped_start] = deltas.get(clipped_start, 0) + 1
        deltas[clipped_end] = deltas.get(clipped_end, 0) - 1

    ordered = sorted(deltas.items())

    running = 0
    max_parallelism = 0
    for timestamp, delta in ordered:
        running += delta
        if timestamp < window_end:
            max_parallelism = max(max_parallelism, max(0, running))

    current_parallelism = 0
    if window_start <= current_time < window_end:
        for interval_start, clipped_start, clipped_end, is_open in clipped_intervals:
            if is_open:
                if interval_start <= current_time:
                    current_parallelism += 1
            elif clipped_start <= current_time < clipped_end:
                current_parallelism += 1

    return ParallelismResult(
        current_parallelism=current_parallelism,
        max_parallelism=max_parallelism,
        average_parallelism=total_seconds / window_seconds,
        total_agent_active_time=total_seconds,
        delegation_attempt_count=attempt_count,
        distinct_child_run_count=len(child_run_ids),
        distinct_node_count=len(node_ids),
        unknown_interval_count=unknown_interval_count,
        partial_interval_count=partial_interval_count,
        valid_interval_count=valid_interval_count,
    )
