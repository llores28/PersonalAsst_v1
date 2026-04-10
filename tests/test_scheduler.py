"""Tests for Phase 4 scheduling system."""

import pytest

from src.agents.scheduler_agent import (
    create_scheduler_agent,
    SCHEDULER_INSTRUCTIONS,
)
from src.scheduler.jobs import send_reminder, morning_brief, summarize_new_emails


class TestSchedulerAgent:
    """Test scheduler agent creation and instructions."""

    def test_create_scheduler_agent(self) -> None:
        agent = create_scheduler_agent()
        assert agent.name == "SchedulerAgent"
        assert len(agent.tools) == 4  # create_reminder, create_morning_brief, list_schedules, cancel_schedule
        assert "Current mode: scheduler" in agent.instructions

    def test_create_scheduler_agent_with_bound_user_registers_bound_tools(self) -> None:
        agent = create_scheduler_agent(bound_user_id=42)
        tool_names = {tool.name for tool in agent.tools}
        assert "create_my_reminder" in tool_names
        assert "create_my_morning_brief" in tool_names
        assert "list_my_schedules" in tool_names
        assert "cancel_my_schedule" in tool_names
        assert "The current internal scheduler user id is `42`" in agent.instructions

    def test_instructions_contain_time_parsing_guide(self) -> None:
        assert "every Monday at 9am" in SCHEDULER_INSTRUCTIONS
        assert "day_of_week" in SCHEDULER_INSTRUCTIONS
        assert "interval" in SCHEDULER_INSTRUCTIONS

    def test_instructions_contain_capabilities(self) -> None:
        assert "reminder" in SCHEDULER_INSTRUCTIONS.lower()
        assert "morning brief" in SCHEDULER_INSTRUCTIONS.lower()
        assert "cancel" in SCHEDULER_INSTRUCTIONS.lower()

    def test_scheduler_agent_supports_briefing_mode(self) -> None:
        agent = create_scheduler_agent(mode="briefing")
        assert "Current mode: briefing" in agent.instructions
        assert "Group related updates into short sections" in agent.instructions


class TestSchedulerEngine:
    """Test scheduler engine configuration."""

    @pytest.mark.skipif(
        not pytest.importorskip("apscheduler", reason="apscheduler not installed locally"),
        reason="apscheduler not installed locally",
    )
    def test_sync_db_url_conversion(self) -> None:
        from src.scheduler.engine import _get_sync_db_url
        url = _get_sync_db_url()
        assert "postgresql://" in url
        assert "asyncpg" not in url


class TestJobCallables:
    """Test job callable signatures and structure."""

    def test_send_reminder_is_async(self) -> None:
        import asyncio
        assert asyncio.iscoroutinefunction(send_reminder)

    def test_morning_brief_is_async(self) -> None:
        import asyncio
        assert asyncio.iscoroutinefunction(morning_brief)

    def test_summarize_new_emails_is_async(self) -> None:
        import asyncio
        assert asyncio.iscoroutinefunction(summarize_new_emails)

    def test_safe_job_wrapper_is_async(self) -> None:
        import asyncio
        from src.scheduler.jobs import safe_job_wrapper
        assert asyncio.iscoroutinefunction(safe_job_wrapper)


class TestBoundToolsCallImpl:
    """Regression: bound tools must call _impl functions, not @function_tool objects."""

    def test_bound_tools_source_calls_impl(self) -> None:
        """Verify _build_bound_scheduler_tools source calls _*_impl, not FunctionTool."""
        import inspect
        from src.agents.scheduler_agent import _build_bound_scheduler_tools

        source = inspect.getsource(_build_bound_scheduler_tools)
        # Must call plain _impl functions
        assert "_create_reminder_impl(" in source
        assert "_create_morning_brief_impl(" in source
        assert "_list_schedules_impl(" in source
        assert "_cancel_schedule_impl(" in source
        # Must NOT directly call the @function_tool-decorated names
        for bad in ["await create_reminder(", "await create_morning_brief(",
                     "await list_schedules(", "await cancel_schedule("]:
            assert bad not in source, (
                f"Bound tools must NOT call '{bad.strip()}' — "
                "that's a FunctionTool object, not callable"
            )

    def test_impl_functions_are_plain_async(self) -> None:
        """Verify _*_impl functions are plain coroutine functions, not FunctionTool."""
        import asyncio
        from src.agents.scheduler_agent import (
            _create_reminder_impl,
            _create_morning_brief_impl,
            _list_schedules_impl,
            _cancel_schedule_impl,
        )

        for fn in [_create_reminder_impl, _create_morning_brief_impl,
                    _list_schedules_impl, _cancel_schedule_impl]:
            assert asyncio.iscoroutinefunction(fn), f"{fn.__name__} must be a plain async function"
            assert type(fn).__name__ != "FunctionTool", f"{fn.__name__} should NOT be a FunctionTool"


@pytest.mark.skipif(
    not pytest.importorskip("apscheduler", reason="apscheduler not installed locally"),
    reason="apscheduler not installed locally",
)
class TestDateTriggerParam:
    """Regression: APScheduler 4.x uses run_time, not run_date."""

    def test_add_one_shot_job_uses_run_time_param(self) -> None:
        """Verify engine source uses DateTrigger(run_time=...) not run_date."""
        import inspect
        from src.scheduler.engine import add_one_shot_job

        source = inspect.getsource(add_one_shot_job)
        assert "run_time=" in source, "add_one_shot_job must use DateTrigger(run_time=...)"
        assert "run_date=" not in source, "run_date is APScheduler 3.x API — use run_time"

    def test_naive_datetime_gets_timezone(self) -> None:
        """Verify engine adds timezone to naive datetimes."""
        import inspect
        from src.scheduler.engine import add_one_shot_job

        source = inspect.getsource(add_one_shot_job)
        assert "tzinfo is None" in source, "Must handle naive datetimes"
        assert "ZoneInfo" in source, "Must use ZoneInfo for timezone attachment"


class TestOrchestratorPhase4:
    """Test orchestrator Phase 4 integration."""

    def test_scheduler_agent_accessible(self) -> None:
        from src.agents.scheduler_agent import create_scheduler_agent
        agent = create_scheduler_agent()
        assert agent.name == "SchedulerAgent"
