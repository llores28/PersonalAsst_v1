"""Smoke tests for the repair-queue worker (process_repair_queue) and the
BackgroundJob startup recovery (restore_running_background_jobs).

These workers were added on 2026-04-30 to fix two production bugs:

1. Background jobs were inserted in the DB but never ticked because
   create_background_job passed `job_args=` to add_interval_job() — the
   correct kwarg is `kwargs=`. The TypeError was swallowed by a bare
   `except Exception` so failures looked like successes.

2. Repair tickets accumulated in the DB with no autonomous worker to
   advance them. The pipeline (run_self_healing_pipeline) was reactive-
   only.

Functional + DB-level coverage lives in the live container smoke test
(see scripts/smoke_repair_queue.py); these unit tests just lock down the
public contract so a refactor can't silently delete the workers.
"""
from __future__ import annotations

import inspect


def test_process_repair_queue_is_async_with_no_required_args():
    """The scheduler registers this as a periodic job with no kwargs. If
    the signature changes, start_scheduler will crash at boot."""
    from src.scheduler.maintenance import process_repair_queue

    sig = inspect.signature(process_repair_queue)
    assert inspect.iscoroutinefunction(process_repair_queue)
    # No required parameters
    for name, p in sig.parameters.items():
        assert p.default is not inspect.Parameter.empty, (
            f"process_repair_queue gained a required arg '{name}'; the scheduler "
            "registration in src/scheduler/engine.py:start_scheduler will fail."
        )


def test_restore_running_background_jobs_is_async_with_no_required_args():
    """Called from start_scheduler at boot — same contract as above."""
    from src.scheduler.maintenance import restore_running_background_jobs

    sig = inspect.signature(restore_running_background_jobs)
    assert inspect.iscoroutinefunction(restore_running_background_jobs)
    for name, p in sig.parameters.items():
        assert p.default is not inspect.Parameter.empty, (
            f"restore_running_background_jobs gained a required arg '{name}'"
        )


def test_create_background_job_uses_kwargs_not_job_args():
    """Regression for the 2026-04-23 TypeError. The body of
    create_background_job must call add_interval_job(... kwargs=...) — NOT
    job_args=. We verify by inspecting the source so the lock survives a
    refactor that goes back to a wrong kwarg."""
    from src.agents import background_job

    src = inspect.getsource(background_job.create_background_job)
    assert "job_args=" not in src, (
        "create_background_job uses `job_args=` again — that's the wrong "
        "kwarg name and triggers the silent-TypeError bug. Use `kwargs=`."
    )
    assert "kwargs=" in src, "create_background_job no longer passes kwargs to add_interval_job"


def test_create_background_job_reraises_on_scheduling_failure():
    """Regression for swallow-the-error half of the 2026-04-23 bug. When
    add_interval_job raises, the function MUST propagate so the caller
    sees the failure instead of getting a stale 'ok, watching' response."""
    from src.agents import background_job

    src = inspect.getsource(background_job.create_background_job)
    # The fix path: log → mark failed → raise
    assert "raise" in src, (
        "create_background_job no longer re-raises scheduling failures. "
        "Reverting to the silent-warning pattern reintroduces the bug "
        "where rows live forever in 'running' state."
    )
    assert 'status = "failed"' in src or "status='failed'" in src, (
        "create_background_job no longer marks the row 'failed' on "
        "scheduling failure — the dashboard will silently misreport state."
    )
