"""Pure unit tests for delegation-attempt parallelism calculations."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from random import Random
from uuid import uuid4

from agent_runtime.monitoring.parallelism import (
    DelegationInterval,
    calculate_parallelism,
)

UTC = timezone.utc
START = datetime(2026, 1, 1, tzinfo=UTC)
END = START + timedelta(seconds=60)
NOW = START + timedelta(seconds=30)


def _interval(
    *,
    status: str = "COMPLETED",
    start_offset: float | None = 0,
    end_offset: float | None = 10,
    node_id: str | None = None,
    child_id: object | None = None,
):
    return DelegationInterval(
        delegation_id=uuid4(),
        child_run_id=child_id or uuid4(),
        node_id=node_id,
        status=status,
        started_at=START + timedelta(seconds=start_offset) if start_offset is not None else None,
        completed_at=START + timedelta(seconds=end_offset) if end_offset is not None else None,
    )


def _calc(records, *, now: datetime = NOW, end: datetime = END):
    return calculate_parallelism(records, start_time=START, end_time=end, now=now)


def test_empty_collection():
    result = _calc([])
    assert result.current_parallelism == 0
    assert result.max_parallelism == 0
    assert result.average_parallelism == 0
    assert result.total_agent_active_time == 0
    assert result.delegation_attempt_count == 0
    assert result.data_quality if hasattr(result, "data_quality") else True


def test_single_interval():
    result = _calc([_interval(start_offset=5, end_offset=15)])
    assert result.current_parallelism == 0
    assert result.max_parallelism == 1
    assert result.total_agent_active_time == 10
    assert result.average_parallelism == 10 / 60
    assert result.delegation_attempt_count == 1
    assert result.distinct_child_run_count == 1
    assert result.distinct_node_count == 0


def test_multiple_overlaps_and_attempts_are_not_deduplicated():
    node = "same-node"
    child = uuid4()
    result = _calc([
        _interval(start_offset=0, end_offset=10, node_id=node, child_id=child),
        _interval(start_offset=2, end_offset=8, node_id=node, child_id=child),
        _interval(start_offset=5, end_offset=7, node_id=node, child_id=child),
    ])
    assert result.current_parallelism == 0
    assert result.max_parallelism == 3
    assert result.total_agent_active_time == 18
    assert result.average_parallelism == 18 / 60
    assert result.delegation_attempt_count == 3
    assert result.distinct_child_run_count == 1
    assert result.distinct_node_count == 1


def test_same_start_and_same_end_timestamps():
    result = _calc([
        _interval(start_offset=10, end_offset=20),
        _interval(start_offset=10, end_offset=20),
    ])
    assert result.max_parallelism == 2
    assert result.total_agent_active_time == 20
    assert result.current_parallelism == 0


def test_zero_length_interval_contributes_nothing():
    result = _calc([_interval(start_offset=10, end_offset=10)])
    assert result.max_parallelism == 0
    assert result.total_agent_active_time == 0
    assert result.valid_interval_count == 1


def test_open_interval_uses_now_as_end():
    result = _calc([
        _interval(status="RUNNING", start_offset=20, end_offset=None),
        _interval(status="CANCEL_REQUESTED", start_offset=25, end_offset=None),
    ], now=NOW)
    assert result.partial_interval_count == 2
    assert result.current_parallelism == 2
    assert result.max_parallelism == 2
    assert result.total_agent_active_time == 15


def test_waiting_is_active_lifecycle():
    result = _calc([_interval(status="WAITING", start_offset=10, end_offset=None)])
    assert result.current_parallelism == 1
    assert result.partial_interval_count == 1


def test_not_started_statuses_do_not_contribute():
    result = _calc([
        _interval(status="ISSUED", start_offset=None, end_offset=None),
        _interval(status="CHILD_STARTING", start_offset=None, end_offset=None),
    ])
    assert result.delegation_attempt_count == 2
    assert result.max_parallelism == 0
    assert result.unknown_interval_count == 0


def test_missing_started_at_is_unknown():
    result = _calc([
        _interval(status="RUNNING", start_offset=None, end_offset=None),
        _interval(status="COMPLETED", start_offset=None, end_offset=10),
    ])
    assert result.unknown_interval_count == 2
    assert result.max_parallelism == 0
    assert result.total_agent_active_time == 0


def test_terminal_missing_completed_at_is_unknown():
    result = _calc([_interval(status="FAILED", start_offset=1, end_offset=None)])
    assert result.unknown_interval_count == 1
    assert result.total_agent_active_time == 0


def test_completed_before_started_is_unknown():
    result = _calc([_interval(start_offset=20, end_offset=10)])
    assert result.unknown_interval_count == 1
    assert result.total_agent_active_time == 0


def test_active_status_with_completed_at_is_unknown():
    result = _calc([_interval(status="RUNNING", start_offset=1, end_offset=5)])
    assert result.unknown_interval_count == 1
    assert result.total_agent_active_time == 0


def test_window_clipping():
    result = _calc([
        _interval(start_offset=-20, end_offset=5),
        _interval(start_offset=55, end_offset=80),
        _interval(start_offset=10, end_offset=20),
    ])
    assert result.total_agent_active_time == 20
    assert result.max_parallelism == 1
    assert result.average_parallelism == 20 / 60


def test_utc_timezone_conversion():
    other = timezone(timedelta(hours=8))
    record = DelegationInterval(
        delegation_id=uuid4(),
        child_run_id=uuid4(),
        node_id=None,
        status="COMPLETED",
        started_at=(START + timedelta(hours=8)).replace(tzinfo=other),
        completed_at=(START + timedelta(seconds=5, hours=8)).replace(tzinfo=other),
    )
    result = _calc([record])
    assert result.total_agent_active_time == 5


def test_now_outside_window_is_clipped():
    record = _interval(status="RUNNING", start_offset=20, end_offset=None)
    before = _calc([record], now=START - timedelta(seconds=10))
    after = _calc([record], now=END + timedelta(seconds=10))
    assert before.current_parallelism == 0
    assert after.current_parallelism == 0
    assert before.total_agent_active_time == 0
    assert after.total_agent_active_time == 40


def test_random_intervals_match_brute_force_sampling():
    rng = Random(20261121)
    records = []
    for _ in range(60):
        start_offset = rng.randint(0, 55)
        length = rng.randint(0, 20)
        records.append(_interval(start_offset=start_offset, end_offset=start_offset + length))

    result = _calc(records)
    samples = []
    for step in range(600):
        t = START + timedelta(seconds=step / 10)
        samples.append(sum(
            1 for record in records
            if record.started_at <= t < record.completed_at
            and record.started_at < END
        ))
    assert result.max_parallelism == max(samples)
    assert result.current_parallelism == sum(
        1 for record in records if record.started_at <= NOW < record.completed_at
    )
