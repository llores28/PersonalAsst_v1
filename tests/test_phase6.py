"""Tests for Phase 6 — Polish & Advanced features.

Note: Sandbox safety, manifest validation, guardrail patterns, and tool
registry tests live in test_tools.py and test_safety_agent.py respectively.
This file covers voice, backup, curator, and the smoke-level agent creation
checks that are not duplicated elsewhere.
"""



class TestVoiceModule:
    """Test voice transcription module."""

    def test_voice_module_exists(self) -> None:
        from src.bot import voice
        assert hasattr(voice, "transcribe_voice")

    def test_transcribe_voice_is_async(self) -> None:
        import asyncio
        from src.bot.voice import transcribe_voice
        assert asyncio.iscoroutinefunction(transcribe_voice)


class TestBackupModule:
    """Test PostgreSQL backup module."""

    def test_backup_module_exists(self) -> None:
        from src.scheduler import backup
        assert hasattr(backup, "run_pg_backup")
        assert hasattr(backup, "scheduled_backup")

    def test_backup_functions_are_async(self) -> None:
        import asyncio
        from src.scheduler.backup import run_pg_backup, scheduled_backup
        assert asyncio.iscoroutinefunction(run_pg_backup)
        assert asyncio.iscoroutinefunction(scheduled_backup)

    def test_backup_dir_constant(self) -> None:
        from pathlib import PurePosixPath
        from src.scheduler.backup import BACKUP_DIR
        # Compare as PurePosixPath to handle Windows vs Linux path separators
        assert PurePosixPath(BACKUP_DIR.as_posix()) == PurePosixPath("/app/backups")


class TestCuratorAgent:
    """Test curator agent for weekly self-improvement."""

    def test_curator_instructions_have_json_schema(self) -> None:
        from src.agents.curator_agent import CURATOR_INSTRUCTIONS
        assert "persona_adjustments" in CURATOR_INSTRUCTIONS
        assert "memories_to_prune" in CURATOR_INSTRUCTIONS
        assert "new_procedural_memories" in CURATOR_INSTRUCTIONS
        assert "quality_summary" in CURATOR_INSTRUCTIONS

    def test_curator_confidence_threshold(self) -> None:
        from src.agents.curator_agent import CURATOR_INSTRUCTIONS
        assert "0.7" in CURATOR_INSTRUCTIONS

    def test_run_weekly_curation_is_async(self) -> None:
        import asyncio
        from src.agents.curator_agent import run_weekly_curation
        assert asyncio.iscoroutinefunction(run_weekly_curation)


class TestAllAgentsCreatable:
    """Verify every agent can be instantiated without errors."""

    def test_email_agent(self) -> None:
        from src.agents.email_agent import create_email_agent
        assert create_email_agent().name == "EmailAgent"

    def test_calendar_agent(self) -> None:
        from src.agents.calendar_agent import create_calendar_agent
        assert create_calendar_agent().name == "CalendarAgent"

    def test_drive_agent(self) -> None:
        from src.agents.drive_agent import create_drive_agent
        assert create_drive_agent().name == "DriveAgent"

    def test_memory_agent(self) -> None:
        from src.agents.memory_agent import create_memory_agent
        assert create_memory_agent().name == "MemoryAgent"

    def test_scheduler_agent(self) -> None:
        from src.agents.scheduler_agent import create_scheduler_agent
        assert create_scheduler_agent().name == "SchedulerAgent"

    def test_tool_factory_agent(self) -> None:
        from src.agents.tool_factory_agent import create_tool_factory_agent
        assert create_tool_factory_agent().name == "ToolFactoryAgent"

    def test_orchestrator_static(self) -> None:
        from src.agents.orchestrator import create_orchestrator
        assert create_orchestrator().name == "PersonalAssistant"
