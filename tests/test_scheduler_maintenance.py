"""Tests for the system-level nightly memory eviction job.

100% mocked — no APScheduler, no Mem0, no DB. We test the iteration / retry
/ failure-isolation contract of `nightly_memory_eviction` directly.
"""

from unittest.mock import AsyncMock, patch

import pytest

from src.scheduler.maintenance import (
    _USER_RETRY_ATTEMPTS,
    nightly_memory_eviction,
)


@pytest.fixture
def fast_user_retry(monkeypatch):
    """Disable the per-user retry's exponential backoff so failure tests
    don't take ~10s each."""
    from tenacity import wait_none

    from src.scheduler import maintenance
    original = maintenance._prune_one_user.retry.wait
    maintenance._prune_one_user.retry.wait = wait_none()
    yield
    maintenance._prune_one_user.retry.wait = original


class TestUserListAcquisition:
    """The job either uses an explicit user list or queries the DB."""

    async def test_explicit_user_ids_skip_db_query(self):
        # When user_ids is passed, the DB is never touched.
        with patch("src.scheduler.maintenance._prune_one_user",
                   AsyncMock(return_value={"reason": "under_cap", "total": 0})):
            report = await nightly_memory_eviction(user_ids=[42, 100, 200])
        assert report["users_processed"] == 3
        assert report["users_under_cap"] == 3
        assert report["users_failed"] == 0


class TestPerUserOutcomes:
    """Each user's status is classified correctly in the report."""

    async def test_under_cap_user_counted_separately(self):
        with patch("src.scheduler.maintenance._prune_one_user",
                   AsyncMock(return_value={"reason": "under_cap", "total": 50})):
            report = await nightly_memory_eviction(user_ids=[1])
        assert report["users_under_cap"] == 1
        assert report["users_evicted"] == 0
        assert report["details"][0]["status"] == "under_cap"
        assert report["details"][0]["total"] == 50

    async def test_evicted_user_counted_with_evict_count(self):
        with patch("src.scheduler.maintenance._prune_one_user",
                   AsyncMock(return_value={
                       "evicted": 800, "summaries_added": 8, "total": 8500,
                   })):
            report = await nightly_memory_eviction(user_ids=[7])
        assert report["users_evicted"] == 1
        assert report["users_under_cap"] == 0
        assert report["details"][0]["status"] == "evicted"
        assert report["details"][0]["evicted"] == 800
        assert report["details"][0]["summaries_added"] == 8

    async def test_partial_failure_classified_distinctly(self):
        # Report comes back with `error` key but evicted=0 — the prune ran
        # but couldn't write summaries. Don't conflate with successful evict.
        with patch("src.scheduler.maintenance._prune_one_user",
                   AsyncMock(return_value={
                       "evicted": 0, "summaries_added": 2,
                       "error": "summary_write_failed",
                   })):
            report = await nightly_memory_eviction(user_ids=[9])
        assert report["users_failed"] == 1
        assert report["users_evicted"] == 0
        assert report["details"][0]["status"] == "partial_or_error"
        assert report["details"][0]["error"] == "summary_write_failed"


class TestFailureIsolation:
    """One user's failure must not abort the batch — that's the contract."""

    async def test_one_users_exception_doesnt_stop_others(self, fast_user_retry):
        # User 2 always raises; users 1 and 3 succeed.
        async def fake_prune(tg_id, *, cap):
            if tg_id == 2:
                raise RuntimeError("Mem0 connection refused")
            return {"reason": "under_cap", "total": 10}

        with patch("src.scheduler.maintenance._prune_one_user",
                   AsyncMock(side_effect=fake_prune)):
            report = await nightly_memory_eviction(user_ids=[1, 2, 3])

        assert report["users_processed"] == 3
        assert report["users_under_cap"] == 2  # 1 and 3 succeeded
        assert report["users_failed"] == 1     # only 2 failed
        # Failure detail captured.
        failed_detail = next(d for d in report["details"] if d.get("status") == "error")
        assert failed_detail["user_id"] == 2
        assert "Mem0 connection refused" in failed_detail["error"]

    async def test_all_users_fail_returns_complete_report(self, fast_user_retry):
        # Worst case: every user fails. The job still returns a report rather
        # than raising — APScheduler EVENT_JOB_ERROR should only fire for
        # genuine bugs in the iteration code, not per-user data issues.
        with patch("src.scheduler.maintenance._prune_one_user",
                   AsyncMock(side_effect=RuntimeError("Mem0 down"))):
            report = await nightly_memory_eviction(user_ids=[1, 2, 3])
        assert report["users_processed"] == 3
        assert report["users_failed"] == 3
        assert report["users_evicted"] == 0


class TestRetryBehavior:
    """`_prune_one_user` is wrapped in tenacity — verify it actually retries."""

    async def test_retries_on_transient_failure(self, fast_user_retry, monkeypatch):
        # Patch the inner module's prune_user_memories (the unwrapped target)
        # so we can count attempts. The tenacity-decorated wrapper sits in
        # maintenance._prune_one_user and re-imports prune_user_memories
        # lazily, so we patch on the eviction_runner module.
        attempts = {"count": 0}

        async def flaky(user_id, *, cap):
            attempts["count"] += 1
            if attempts["count"] < 3:
                raise RuntimeError("transient blip")
            return {"reason": "under_cap", "total": 5}

        with patch("src.memory.eviction_runner.prune_user_memories",
                   AsyncMock(side_effect=flaky)):
            report = await nightly_memory_eviction(user_ids=[55])

        # Tenacity retried twice, third call succeeded.
        assert attempts["count"] == 3
        assert report["users_under_cap"] == 1
        assert report["users_failed"] == 0

    async def test_gives_up_after_max_attempts(self, fast_user_retry):
        attempts = {"count": 0}

        async def always_fails(user_id, *, cap):
            attempts["count"] += 1
            raise RuntimeError("Mem0 unreachable")

        with patch("src.memory.eviction_runner.prune_user_memories",
                   AsyncMock(side_effect=always_fails)):
            report = await nightly_memory_eviction(user_ids=[55])

        assert attempts["count"] == _USER_RETRY_ATTEMPTS  # 3 attempts then give up
        assert report["users_failed"] == 1


class TestEmptyUserList:
    async def test_no_users_returns_empty_report(self):
        report = await nightly_memory_eviction(user_ids=[])
        assert report["users_processed"] == 0
        assert report["users_under_cap"] == 0
        assert report["users_evicted"] == 0
        assert report["users_failed"] == 0
        assert report["details"] == []


class TestDbQueryFailure:
    async def test_db_query_failure_surfaces_in_report(self, monkeypatch):
        """When user_ids isn't provided AND the DB query throws, the job
        must NOT raise — it should report the error and exit cleanly so
        APScheduler doesn't accumulate failures on a transient DB issue."""
        # Patch sqlalchemy import inside the function to throw.
        import sys
        from types import SimpleNamespace
        from unittest.mock import MagicMock

        fake_session_module = MagicMock()
        fake_session_module.async_session = MagicMock(
            side_effect=RuntimeError("DB unreachable")
        )
        monkeypatch.setitem(sys.modules, "src.db.session", fake_session_module)

        report = await nightly_memory_eviction()
        assert "error" in report
        assert "user_query_failed" in report["error"]
        # No users were processed — pre-iteration failure.
        assert report["users_processed"] == 0
