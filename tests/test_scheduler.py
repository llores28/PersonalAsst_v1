"""Tests for Phase 4 scheduling system."""

import pytest
from unittest.mock import MagicMock

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

    def test_instructions_contain_time_parsing_guide(self) -> None:
        assert "every Monday at 9am" in SCHEDULER_INSTRUCTIONS
        assert "day_of_week" in SCHEDULER_INSTRUCTIONS
        assert "interval" in SCHEDULER_INSTRUCTIONS

    def test_instructions_contain_capabilities(self) -> None:
        assert "reminder" in SCHEDULER_INSTRUCTIONS.lower()
        assert "morning brief" in SCHEDULER_INSTRUCTIONS.lower()
        assert "cancel" in SCHEDULER_INSTRUCTIONS.lower()


class TestSchedulerEngine:
    """Test scheduler engine configuration."""

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


class TestOrchestratorPhase4:
    """Test orchestrator Phase 4 integration."""

    def test_orchestrator_has_scheduler_import(self) -> None:
        from src.agents.orchestrator import create_scheduler_agent
        agent = create_scheduler_agent()
        assert agent.name == "SchedulerAgent"
