"""Tests for scheduler observability: per-job health tracking + aggregate snapshot.

100% mocked — no APScheduler runtime, no Redis. We test the pure-function
event applier and the listener's I/O contract via mocked redis.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from apscheduler import JobOutcome

from src.scheduler.observability import (
    _apply_event,
    _on_job_released,
    _outcome_to_status,
    get_health_snapshot,
)


def _fake_job_released(
    *,
    schedule_id: str = "test_job",
    outcome: JobOutcome = JobOutcome.success,
    duration_ms: int = 100,
    exception_type: str | None = None,
    exception_message: str | None = None,
):
    """Build a JobReleased event-like object (we pass attrs.field-shaped data
    directly so we don't depend on the real attrs class' constructor signature
    — the listener only reads attributes)."""
    now = datetime.now(timezone.utc)
    started = now - timedelta(milliseconds=duration_ms)
    event = MagicMock()
    event.timestamp = now
    event.started_at = started
    event.scheduled_start = started
    event.schedule_id = schedule_id
    event.task_id = "task-id"
    event.job_id = uuid4()
    event.scheduler_id = "test-scheduler"
    event.outcome = outcome
    event.exception_type = exception_type
    event.exception_message = exception_message
    event.exception_traceback = None
    return event


# --------------------------------------------------------------------------
# Pure helpers
# --------------------------------------------------------------------------

class TestOutcomeToStatus:
    def test_success_maps_to_name(self):
        assert _outcome_to_status(JobOutcome.success) == "success"

    def test_error_maps_to_name(self):
        assert _outcome_to_status(JobOutcome.error) == "error"

    def test_missed_deadline_maps_to_name(self):
        assert _outcome_to_status(JobOutcome.missed_start_deadline) \
            == "missed_start_deadline"


class TestApplyEvent:
    def test_first_success_initializes_counters(self):
        record = {"schedule_id": "j1", "consecutive_failures": 0,
                  "total_runs": 0, "total_failures": 0}
        event = _fake_job_released(schedule_id="j1", outcome=JobOutcome.success,
                                   duration_ms=250)
        out = _apply_event(record, event)
        assert out["last_status"] == "success"
        assert out["consecutive_failures"] == 0
        assert out["total_runs"] == 1
        assert out["total_failures"] == 0
        assert out["last_duration_ms"] == 250
        assert out["last_error"] is None

    def test_failure_increments_consecutive(self):
        record = {"schedule_id": "j1", "consecutive_failures": 2,
                  "total_runs": 5, "total_failures": 2}
        event = _fake_job_released(
            outcome=JobOutcome.error,
            exception_type="RuntimeError",
            exception_message="boom",
        )
        out = _apply_event(record, event)
        assert out["consecutive_failures"] == 3
        assert out["total_failures"] == 3
        assert out["total_runs"] == 6
        assert "RuntimeError" in out["last_error"]
        assert "boom" in out["last_error"]

    def test_success_resets_consecutive_failures(self):
        record = {"schedule_id": "j1", "consecutive_failures": 5,
                  "total_runs": 10, "total_failures": 5}
        event = _fake_job_released(outcome=JobOutcome.success)
        out = _apply_event(record, event)
        assert out["consecutive_failures"] == 0
        # Total counters preserved.
        assert out["total_runs"] == 11
        assert out["total_failures"] == 5

    def test_missed_deadline_counts_as_failure(self):
        record = {"schedule_id": "j1", "consecutive_failures": 0,
                  "total_runs": 0, "total_failures": 0}
        event = _fake_job_released(outcome=JobOutcome.missed_start_deadline)
        out = _apply_event(record, event)
        assert out["last_status"] == "missed_start_deadline"
        assert out["consecutive_failures"] == 1
        assert out["total_failures"] == 1

    def test_no_started_at_means_no_duration(self):
        record = {"schedule_id": "j1", "consecutive_failures": 0,
                  "total_runs": 0, "total_failures": 0}
        event = _fake_job_released()
        event.started_at = None  # e.g. job was missed before it ever started
        out = _apply_event(record, event)
        assert out["last_duration_ms"] is None


# --------------------------------------------------------------------------
# Listener (I/O integration with mocked Redis)
# --------------------------------------------------------------------------

class TestOnJobReleased:
    async def test_writes_record_to_redis_on_success(self, monkeypatch):
        fake_redis = AsyncMock()
        fake_redis.get = AsyncMock(return_value=None)  # no prior record
        fake_redis.set = AsyncMock()
        monkeypatch.setattr(
            "src.memory.conversation.get_redis",
            AsyncMock(return_value=fake_redis),
        )

        event = _fake_job_released(
            schedule_id="memory_eviction", outcome=JobOutcome.success,
            duration_ms=320,
        )
        await _on_job_released(event)

        fake_redis.set.assert_called_once()
        args = fake_redis.set.call_args
        # Key namespacing.
        assert args.args[0] == "scheduler_health:memory_eviction"
        # JSON payload contains the outcome.
        import json as _json
        payload = _json.loads(args.args[1])
        assert payload["last_status"] == "success"
        assert payload["consecutive_failures"] == 0
        # TTL applied.
        assert args.kwargs.get("ex") == 30 * 86400

    async def test_does_not_raise_when_redis_get_fails(self, monkeypatch):
        # The listener's whole point is non-blocking observability — exceptions
        # in Redis must NEVER propagate up into the scheduler.
        fake_redis = AsyncMock()
        fake_redis.get = AsyncMock(side_effect=ConnectionError("redis down"))
        fake_redis.set = AsyncMock()  # may or may not be called, both fine
        monkeypatch.setattr(
            "src.memory.conversation.get_redis",
            AsyncMock(return_value=fake_redis),
        )

        event = _fake_job_released()
        # Must not raise.
        await _on_job_released(event)

    async def test_uses_adhoc_id_when_schedule_id_is_none(self, monkeypatch):
        fake_redis = AsyncMock()
        fake_redis.get = AsyncMock(return_value=None)
        fake_redis.set = AsyncMock()
        monkeypatch.setattr(
            "src.memory.conversation.get_redis",
            AsyncMock(return_value=fake_redis),
        )

        event = _fake_job_released(schedule_id=None)
        event.schedule_id = None
        await _on_job_released(event)

        key = fake_redis.set.call_args.args[0]
        assert key.startswith("scheduler_health:adhoc:")

    async def test_listener_swallows_get_redis_failure(self, monkeypatch):
        # Deeper failure mode — get_redis itself raises.
        monkeypatch.setattr(
            "src.memory.conversation.get_redis",
            AsyncMock(side_effect=RuntimeError("no redis")),
        )
        event = _fake_job_released()
        await _on_job_released(event)  # must not raise


# --------------------------------------------------------------------------
# Health snapshot endpoint helper
# --------------------------------------------------------------------------

class TestGetHealthSnapshot:
    async def test_no_jobs_returns_unknown(self, monkeypatch):
        fake_redis = AsyncMock()

        async def empty_iter(match=None, count=None):
            for x in []:
                yield x

        fake_redis.scan_iter = empty_iter
        monkeypatch.setattr(
            "src.memory.conversation.get_redis",
            AsyncMock(return_value=fake_redis),
        )
        snap = await get_health_snapshot()
        assert snap["status"] == "unknown"
        assert snap["jobs"] == []
        assert snap["summary"]["total_jobs"] == 0

    async def test_healthy_when_zero_failures(self, monkeypatch):
        fake_redis = AsyncMock()
        keys = [
            "scheduler_health:memory_eviction",
            "scheduler_health:oauth_heartbeat",
        ]
        records = [
            {"schedule_id": "memory_eviction", "consecutive_failures": 0,
             "total_runs": 5, "last_status": "success"},
            {"schedule_id": "oauth_heartbeat", "consecutive_failures": 0,
             "total_runs": 2, "last_status": "success"},
        ]

        async def fake_scan(match=None, count=None):
            for k in keys:
                yield k

        fake_redis.scan_iter = fake_scan

        import json as _json
        async def fake_get(key):
            for k, r in zip(keys, records):
                if k == key:
                    return _json.dumps(r)
            return None

        fake_redis.get = fake_get
        monkeypatch.setattr(
            "src.memory.conversation.get_redis",
            AsyncMock(return_value=fake_redis),
        )
        snap = await get_health_snapshot()
        assert snap["status"] == "healthy"
        assert snap["summary"]["total_jobs"] == 2
        assert snap["summary"]["any_failing"] is False
        assert snap["summary"]["max_consecutive_failures"] == 0

    async def test_degraded_when_threshold_hit(self, monkeypatch):
        fake_redis = AsyncMock()
        keys = ["scheduler_health:flaky"]
        records = [
            {"schedule_id": "flaky", "consecutive_failures": 4,
             "total_runs": 10, "last_status": "error",
             "last_error": "RuntimeError: still down"},
        ]

        async def fake_scan(match=None, count=None):
            for k in keys:
                yield k

        fake_redis.scan_iter = fake_scan

        import json as _json
        async def fake_get(key):
            return _json.dumps(records[0]) if key == keys[0] else None

        fake_redis.get = fake_get
        monkeypatch.setattr(
            "src.memory.conversation.get_redis",
            AsyncMock(return_value=fake_redis),
        )
        snap = await get_health_snapshot()
        assert snap["status"] == "degraded"
        assert snap["summary"]["max_consecutive_failures"] == 4
        assert snap["summary"]["any_failing"] is True
