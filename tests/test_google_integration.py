"""Comprehensive integration tests for all Google Workspace specialist agents.

Verifies that Gmail, Calendar, Tasks, Drive, and Scheduler agents:
- Register the correct tools when connected
- Have clear scope boundaries (no overlap)
- Route correctly based on user intent phrases
- Work cohesively with the orchestrator's tool routing rules
"""

import json
from types import SimpleNamespace
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from agents.tool_context import ToolContext
from agents.usage import Usage

from src.agents.email_agent import create_email_agent, EMAIL_INSTRUCTIONS
from src.agents.calendar_agent import create_calendar_agent, CALENDAR_INSTRUCTIONS
from src.agents.drive_agent import create_drive_agent, DRIVE_INSTRUCTIONS
from src.agents.tasks_agent import (
    TASKS_INSTRUCTIONS,
    _format_google_tasks_error,
    create_tasks_agent,
)
from src.agents.scheduler_agent import create_scheduler_agent, SCHEDULER_INSTRUCTIONS


# ── Gmail Agent ──────────────────────────────────────────────────────────────

class TestGmailIntegration:
    """Gmail agent tool registration and scope."""

    def test_gmail_agent_registers_all_connected_tools(self) -> None:
        agent = create_email_agent(connected_google_email="user@example.com")
        tool_names = {tool.name for tool in agent.tools}
        assert "search_connected_gmail_messages" in tool_names
        assert "get_connected_gmail_message_content" in tool_names
        assert "get_connected_gmail_thread_content" in tool_names
        assert "send_connected_gmail_message" in tool_names
        assert "draft_connected_gmail_message" in tool_names
        assert "get_connected_gmail_messages_batch" in tool_names
        assert len(agent.tools) == 6

    def test_gmail_agent_without_connected_email_has_no_tools(self) -> None:
        agent = create_email_agent()
        assert len(agent.tools) == 0

    def test_gmail_instructions_contain_scope(self) -> None:
        assert "email" in EMAIL_INSTRUCTIONS.lower()
        assert "gmail" in EMAIL_INSTRUCTIONS.lower()
        assert "draft" in EMAIL_INSTRUCTIONS.lower()

    def test_gmail_agent_includes_connected_email_in_instructions(self) -> None:
        agent = create_email_agent(connected_google_email="test@example.com")
        assert "test@example.com" in agent.instructions

    def test_gmail_instructions_contain_key_capabilities(self) -> None:
        assert "Read" in EMAIL_INSTRUCTIONS
        assert "send" in EMAIL_INSTRUCTIONS.lower()
        assert "draft" in EMAIL_INSTRUCTIONS.lower()
        assert "confirmation" in EMAIL_INSTRUCTIONS.lower()

    def test_gmail_agent_supports_briefing_mode(self) -> None:
        agent = create_email_agent(mode="briefing")
        assert "Current mode: briefing" in agent.instructions
        assert "Group related updates into short sections" in agent.instructions

    def test_gmail_agent_with_mcp_servers(self) -> None:
        mock_mcp = MagicMock()
        agent = create_email_agent(mcp_servers=[mock_mcp])
        assert len(agent.mcp_servers) == 1

    def test_normalize_gmail_subject_prefers_explicit_subject(self) -> None:
        from src.agents.email_agent import _normalize_gmail_subject
        assert _normalize_gmail_subject("Project Intel meeting", "running 10min late") == "Project Intel meeting"

    def test_normalize_gmail_subject_falls_back_to_body_when_subject_missing(self) -> None:
        from src.agents.email_agent import _normalize_gmail_subject
        assert _normalize_gmail_subject(None, "running 10min late") == "Running 10min late"


# ── Calendar Agent ───────────────────────────────────────────────────────────

class TestCalendarIntegration:
    """Calendar agent tool registration, scope boundary, and negative routing."""

    def test_calendar_agent_registers_connected_tools(self) -> None:
        agent = create_calendar_agent(connected_google_email="user@example.com")
        tool_names = {tool.name for tool in agent.tools}
        assert "get_connected_calendar_events" in tool_names
        assert "manage_connected_calendar_event" in tool_names
        assert len(agent.tools) == 2

    def test_calendar_agent_without_connected_email_has_no_tools(self) -> None:
        agent = create_calendar_agent()
        assert len(agent.tools) == 0

    def test_calendar_instructions_have_scope_boundary(self) -> None:
        assert "does NOT handle tasks" in CALENDAR_INSTRUCTIONS
        assert "reminders" in CALENDAR_INSTRUCTIONS.lower()
        assert "to-do" in CALENDAR_INSTRUCTIONS.lower()

    def test_calendar_instructions_mention_events_only(self) -> None:
        assert "EVENTS only" in CALENDAR_INSTRUCTIONS

    def test_calendar_instructions_contain_key_capabilities(self) -> None:
        assert "View" in CALENDAR_INSTRUCTIONS
        assert "Create" in CALENDAR_INSTRUCTIONS
        assert "confirm" in CALENDAR_INSTRUCTIONS.lower()
        assert "timezone" in CALENDAR_INSTRUCTIONS.lower()

    def test_calendar_agent_supports_briefing_mode(self) -> None:
        agent = create_calendar_agent(mode="briefing")
        assert "Current mode: briefing" in agent.instructions
        assert "Group related updates into short sections" in agent.instructions

    def test_calendar_agent_with_mcp_servers(self) -> None:
        mock_mcp = MagicMock()
        agent = create_calendar_agent(mcp_servers=[mock_mcp])
        assert len(agent.mcp_servers) == 1


# ── Google Tasks Agent ───────────────────────────────────────────────────────

class TestTasksIntegration:
    """Google Tasks agent tool registration and scope."""

    def test_tasks_agent_registers_connected_tools(self) -> None:
        agent = create_tasks_agent(connected_google_email="user@example.com")
        tool_names = {tool.name for tool in agent.tools}
        assert "list_my_task_lists" in tool_names
        assert "list_my_tasks" in tool_names
        assert "manage_my_task" in tool_names
        assert "manage_my_task_list" in tool_names
        assert len(agent.tools) == 4

    def test_tasks_agent_without_connected_email_has_no_tools(self) -> None:
        agent = create_tasks_agent()
        assert len(agent.tools) == 0

    def test_tasks_instructions_have_scope_boundary(self) -> None:
        assert "does NOT create calendar events" in TASKS_INSTRUCTIONS
        assert "does NOT manage internal reminders" in TASKS_INSTRUCTIONS

    def test_tasks_agent_includes_connected_email(self) -> None:
        agent = create_tasks_agent(connected_google_email="test@example.com")
        assert "test@example.com" in agent.instructions

    def test_tasks_error_formatter_includes_exact_error_and_reconnect_hint(self) -> None:
        error_text = _format_google_tasks_error(
            "create the task",
            "user@example.com",
            RuntimeError("insufficient authentication scopes for this request"),
        )

        assert "insufficient authentication scopes for this request" in error_text
        assert "/connect google user@example.com" in error_text
        assert "approve Google Tasks access again" in error_text


# ── Drive Agent ──────────────────────────────────────────────────────────────

class TestDriveIntegration:
    """Drive agent configuration and scope."""

    def test_drive_agent_creates_successfully(self) -> None:
        agent = create_drive_agent()
        assert agent.name == "DriveAgent"

    def test_drive_instructions_contain_capabilities(self) -> None:
        assert "search" in DRIVE_INSTRUCTIONS.lower()
        assert "upload" in DRIVE_INSTRUCTIONS.lower()
        assert "share" in DRIVE_INSTRUCTIONS.lower()

    def test_drive_agent_includes_connected_email(self) -> None:
        agent = create_drive_agent(connected_google_email="test@example.com")
        assert "test@example.com" in agent.instructions

    def test_drive_instructions_contain_key_capabilities(self) -> None:
        assert "Search" in DRIVE_INSTRUCTIONS
        assert "upload" in DRIVE_INSTRUCTIONS.lower()
        assert "download" in DRIVE_INSTRUCTIONS.lower()
        assert "confirm" in DRIVE_INSTRUCTIONS.lower()

    def test_drive_agent_supports_briefing_mode(self) -> None:
        agent = create_drive_agent(mode="briefing")
        assert "Current mode: briefing" in agent.instructions
        assert "Group related updates into short sections" in agent.instructions

    def test_drive_agent_with_mcp_servers(self) -> None:
        mock_mcp = MagicMock()
        agent = create_drive_agent(mcp_servers=[mock_mcp])
        assert len(agent.mcp_servers) == 1

    def test_connected_drive_tools_include_move_and_rename(self) -> None:
        from src.agents.drive_agent import _build_connected_drive_tools

        tool_names = {tool.name for tool in _build_connected_drive_tools("user@example.com")}

        assert "create_connected_drive_folder" in tool_names
        assert "move_connected_drive_file" in tool_names
        assert "rename_connected_drive_file" in tool_names

    @pytest.mark.asyncio
    async def test_create_connected_drive_folder_maps_to_live_schema_names(self) -> None:
        from src.agents.drive_agent import _build_connected_drive_tools

        tool = next(
            item
            for item in _build_connected_drive_tools("user@example.com")
            if item.name == "create_connected_drive_folder"
        )
        ctx = ToolContext(
            context=None,
            usage=Usage(),
            tool_name=tool.name,
            tool_call_id="call-1",
            tool_arguments="{}",
        )

        with (
            patch(
                "src.agents.drive_agent.get_workspace_tool_argument_names",
                new=AsyncMock(return_value={"user_google_email", "name", "parent_folder_id"}),
            ),
            patch("src.agents.drive_agent.call_workspace_tool", new=AsyncMock(return_value="ok")) as mock_call,
        ):
            result = await tool.on_invoke_tool(
                ctx,
                json.dumps({"folder_name": "Finance", "parent_folder_id": "root"}),
            )

        assert result == "ok"
        mock_call.assert_awaited_once_with(
            "create_drive_folder",
            {
                "user_google_email": "user@example.com",
                "name": "Finance",
                "parent_folder_id": "root",
            },
        )

    @pytest.mark.asyncio
    async def test_move_connected_drive_file_uses_update_drive_file_contract(self) -> None:
        from src.agents.drive_agent import _build_connected_drive_tools

        tool = next(
            item
            for item in _build_connected_drive_tools("user@example.com")
            if item.name == "move_connected_drive_file"
        )
        ctx = ToolContext(
            context=None,
            usage=Usage(),
            tool_name=tool.name,
            tool_call_id="call-2",
            tool_arguments="{}",
        )

        with (
            patch(
                "src.agents.drive_agent.get_workspace_tool_argument_names",
                new=AsyncMock(
                    return_value={"user_google_email", "file_id", "add_parents", "remove_parents"}
                ),
            ),
            patch("src.agents.drive_agent.call_workspace_tool", new=AsyncMock(return_value="moved")) as mock_call,
        ):
            result = await tool.on_invoke_tool(
                ctx,
                json.dumps(
                    {
                        "file_id": "file-123",
                        "destination_folder_id": "folder-456",
                        "current_parent_folder_id": "folder-old",
                    }
                ),
            )

        assert result == "moved"
        mock_call.assert_awaited_once_with(
            "update_drive_file",
            {
                "user_google_email": "user@example.com",
                "file_id": "file-123",
                "add_parents": "folder-456",
                "remove_parents": "folder-old",
            },
        )


# ── Scheduler Agent ──────────────────────────────────────────────────────────

class TestSchedulerIntegration:
    """Scheduler agent bound tools and scope."""

    def test_scheduler_with_bound_user_registers_bound_tools(self) -> None:
        agent = create_scheduler_agent(bound_user_id=42)
        tool_names = {tool.name for tool in agent.tools}
        assert "create_my_reminder" in tool_names
        assert "list_my_schedules" in tool_names
        assert "cancel_my_schedule" in tool_names

    def test_scheduler_without_bound_user_has_raw_tools(self) -> None:
        agent = create_scheduler_agent()
        tool_names = {tool.name for tool in agent.tools}
        assert "create_reminder" in tool_names
        assert "list_schedules" in tool_names

    def test_scheduler_instructions_mention_task_routing(self) -> None:
        assert "task" in SCHEDULER_INSTRUCTIONS.lower()
        assert "todo" in SCHEDULER_INSTRUCTIONS.lower()


# ── Orchestrator Tool Wiring ─────────────────────────────────────────────────

class TestOrchestratorWiring:
    """Verify the orchestrator wires all skills correctly via SkillRegistry."""

    @pytest.mark.asyncio
    async def test_orchestrator_registers_workspace_skills_when_google_configured(self) -> None:
        """When Google is configured + connected, all 8 workspace skills are registered."""
        from src.agents.orchestrator import create_orchestrator_async, _registry_cache

        _registry_cache.clear()  # Ensure no stale cache

        with (
            patch("src.agents.orchestrator.build_dynamic_persona_prompt", new=AsyncMock(return_value="prompt")),
            patch("src.agents.orchestrator.create_tool_factory_agent", return_value=MagicMock()),
            patch("src.agents.orchestrator.get_connected_google_email", new=AsyncMock(return_value="user@example.com")),
            patch("src.agents.orchestrator.is_google_configured", return_value=True),
            patch("src.agents.orchestrator.load_dynamic_skills", new=AsyncMock(return_value=[])),
            patch("src.agents.repair_agent.create_repair_agent", return_value=MagicMock()),
        ):
            agent = await create_orchestrator_async(12345, "Alex", scheduler_user_id=1)

        # Agent should have tools from workspace skills
        tool_names = [getattr(t, "name", "") for t in agent.tools]
        assert len(tool_names) > 10, f"Expected many tools, got {len(tool_names)}"

    @pytest.mark.asyncio
    async def test_orchestrator_routing_rules_in_persona(self) -> None:
        """Persona prompt includes Tool Routing Rules from SkillRegistry."""
        from src.agents.orchestrator import create_orchestrator_async, _registry_cache

        _registry_cache.clear()

        with (
            patch("src.agents.orchestrator.build_dynamic_persona_prompt", new=AsyncMock(return_value="prompt")),
            patch("src.agents.orchestrator.create_tool_factory_agent", return_value=MagicMock()),
            patch("src.agents.orchestrator.get_connected_google_email", new=AsyncMock(return_value="user@example.com")),
            patch("src.agents.orchestrator.is_google_configured", return_value=True),
            patch("src.agents.orchestrator.load_dynamic_skills", new=AsyncMock(return_value=[])),
            patch("src.agents.repair_agent.create_repair_agent", return_value=MagicMock()),
        ):
            agent = await create_orchestrator_async(12345, "Alex", scheduler_user_id=1)

        assert "Tool Routing Rules" in agent.instructions
        assert "Email" in agent.instructions
        assert "Calendar" in agent.instructions
        assert "NOT for calendar events" in agent.instructions or "NOT for tasks" in agent.instructions

    @pytest.mark.asyncio
    async def test_orchestrator_without_google_has_no_workspace_tools(self) -> None:
        """When Google is not configured, no workspace tools are registered."""
        from src.agents.orchestrator import create_orchestrator_async, _registry_cache

        _registry_cache.clear()

        with (
            patch("src.agents.orchestrator.build_dynamic_persona_prompt", new=AsyncMock(return_value="prompt")),
            patch("src.agents.orchestrator.create_tool_factory_agent", return_value=MagicMock()),
            patch("src.agents.orchestrator.get_connected_google_email", new=AsyncMock(return_value=None)),
            patch("src.agents.orchestrator.is_google_configured", return_value=False),
            patch("src.agents.orchestrator.load_dynamic_skills", new=AsyncMock(return_value=[])),
            patch("src.agents.repair_agent.create_repair_agent", return_value=MagicMock()),
        ):
            agent = await create_orchestrator_async(12345, "Alex")

        # Should still have internal skills (memory, scheduler) + WebSearchTool
        tool_names = [getattr(t, "name", "") for t in agent.tools]
        assert not any("gmail" in n.lower() for n in tool_names), f"Gmail tools found without Google: {tool_names}"


# ── Workspace Cohesion in Selective Skill Injection ───────────────────────────

class TestWorkspaceCohesion:
    """When ANY workspace skill matches, ALL workspace skills should be included."""

    def _build_workspace_registry(self):
        from src.skills.registry import SkillRegistry
        from src.skills.definition import SkillDefinition, SkillGroup

        registry = SkillRegistry()
        registry.register(SkillDefinition(
            id="drive", group=SkillGroup.GOOGLE_WORKSPACE,
            description="Drive", tools=[lambda: None],
            routing_hints=["Drive: file search, organize, folders"],
            tags=["drive", "files"],
        ))
        registry.register(SkillDefinition(
            id="gmail", group=SkillGroup.GOOGLE_WORKSPACE,
            description="Gmail", tools=[lambda: None],
            routing_hints=["Email: send, draft, inbox, check email"],
            tags=["email", "gmail"],
        ))
        registry.register(SkillDefinition(
            id="docs", group=SkillGroup.GOOGLE_WORKSPACE,
            description="Docs", tools=[lambda: None],
            routing_hints=["Docs: create document, edit document"],
            tags=["docs", "document"],
        ))
        registry.register(SkillDefinition(
            id="memory", group=SkillGroup.INTERNAL,
            description="Memory", tools=[lambda: None],
            routing_hints=["Memory: remember, recall"],
            tags=["memory"],
        ))
        return registry

    def test_drive_message_includes_all_workspace_skills(self):
        """Regression: 'organize my google drive' should inject ALL workspace skills."""
        registry = self._build_workspace_registry()
        matched = registry.match_skills("organize my google drive files and folders")
        assert "drive" in matched
        assert "gmail" in matched, "Gmail should be included via workspace cohesion"
        assert "docs" in matched, "Docs should be included via workspace cohesion"
        assert "memory" in matched, "Internal skills always included"

    def test_docs_message_includes_drive_via_cohesion(self):
        """'edit my document' should also inject Drive tools for multi-step workflows."""
        registry = self._build_workspace_registry()
        matched = registry.match_skills("edit my document and rename it")
        assert "docs" in matched
        assert "drive" in matched, "Drive should be included via workspace cohesion"

    def test_email_message_includes_all_workspace(self):
        registry = self._build_workspace_registry()
        matched = registry.match_skills("check my email inbox")
        assert "gmail" in matched
        assert "drive" in matched, "Drive should be included via workspace cohesion"

    def test_non_workspace_message_no_cohesion(self):
        """Generic message should fall back to all skills, not trigger cohesion."""
        registry = self._build_workspace_registry()
        matched = registry.match_skills("hello, how are you?")
        # Should be all skills (fallback)
        assert len(matched) == 4

    def test_follow_up_list_files_includes_drive(self):
        """Regression: 'list older poorly named files' should match Drive."""
        registry = self._build_workspace_registry()
        matched = registry.match_skills("list older poorly named files and search Drive")
        assert "drive" in matched


# ── Workspace Tool Error Handling ─────────────────────────────────────────────

class TestWorkspaceToolErrorHandling:
    """call_workspace_tool returns actionable error messages instead of raising."""

    @pytest.mark.asyncio
    async def test_connection_error_returns_message_not_exception(self):
        from src.integrations import workspace_mcp

        mock_server = MagicMock()
        mock_server.connect = AsyncMock(side_effect=ConnectionError("MCP down"))
        mock_server.cleanup = AsyncMock()

        with patch.object(workspace_mcp, "create_workspace_mcp_server", return_value=mock_server):
            result = await workspace_mcp.call_workspace_tool("search_drive_files", {"query": "test"})

        assert "[CONNECTION ERROR]" in result
        assert "WebSearch" in result
        mock_server.cleanup.assert_awaited()

    @pytest.mark.asyncio
    async def test_auth_error_returns_reconnect_guidance(self):
        from src.integrations import workspace_mcp

        mock_server = MagicMock()
        mock_server.connect = AsyncMock()
        mock_server.call_tool = AsyncMock(side_effect=RuntimeError("token expired or unauthorized"))
        mock_server.cleanup = AsyncMock()

        with patch.object(workspace_mcp, "create_workspace_mcp_server", return_value=mock_server):
            result = await workspace_mcp.call_workspace_tool("list_drive_items", {"folder_id": "root"})

        assert "[AUTH ERROR]" in result
        assert "/connect google" in result

    @pytest.mark.asyncio
    async def test_generic_tool_error_returns_message(self):
        from src.integrations import workspace_mcp

        mock_server = MagicMock()
        mock_server.connect = AsyncMock()
        mock_server.call_tool = AsyncMock(side_effect=ValueError("bad argument"))
        mock_server.cleanup = AsyncMock()

        with patch.object(workspace_mcp, "create_workspace_mcp_server", return_value=mock_server):
            result = await workspace_mcp.call_workspace_tool("create_drive_folder", {"name": "test"})

        assert "[TOOL ERROR]" in result
        assert "bad argument" in result
        assert "WebSearch" in result


class TestWorkspaceRateLimitHandling:
    """Rate-limit / quota responses retry with backoff and surface as a clean
    [RATE LIMIT] message after exhausting tenacity's retry budget."""

    @pytest.fixture
    def fast_retry(self):
        """Skip the exponential backoff sleeps so retry tests run instantly."""
        from tenacity import wait_none
        from src.integrations import workspace_mcp
        original = workspace_mcp._call_workspace_tool_inner.retry.wait
        workspace_mcp._call_workspace_tool_inner.retry.wait = wait_none()
        yield
        workspace_mcp._call_workspace_tool_inner.retry.wait = original

    @pytest.mark.asyncio
    async def test_429_in_exception_message_triggers_retry(self, fast_retry):
        from src.integrations import workspace_mcp

        mock_server = MagicMock()
        mock_server.connect = AsyncMock()
        # Persistent rate-limit error (every retry sees it).
        mock_server.call_tool = AsyncMock(
            side_effect=RuntimeError("HTTP 429: User Rate Limit Exceeded")
        )
        mock_server.cleanup = AsyncMock()

        with patch.object(workspace_mcp, "create_workspace_mcp_server", return_value=mock_server):
            result = await workspace_mcp.call_workspace_tool("send_email", {"to": "x@y.z"})

        # Tenacity retried 3 times before giving up.
        assert mock_server.call_tool.await_count == 3
        # User-facing surface is [RATE LIMIT], not [TOOL ERROR].
        assert "[RATE LIMIT]" in result
        assert "wait" in result.lower()

    @pytest.mark.asyncio
    async def test_quota_exceeded_in_exception_triggers_retry(self, fast_retry):
        from src.integrations import workspace_mcp

        mock_server = MagicMock()
        mock_server.connect = AsyncMock()
        mock_server.call_tool = AsyncMock(
            side_effect=RuntimeError("Quota exceeded for daily limit")
        )
        mock_server.cleanup = AsyncMock()

        with patch.object(workspace_mcp, "create_workspace_mcp_server", return_value=mock_server):
            result = await workspace_mcp.call_workspace_tool("list_drive_items", {})

        assert mock_server.call_tool.await_count == 3
        assert "[RATE LIMIT]" in result

    @pytest.mark.asyncio
    async def test_rate_limit_in_tool_result_text_triggers_retry(self, fast_retry):
        """Some MCP servers return rate-limit signals as tool result text rather
        than as raised exceptions. Detection must work in both paths."""
        from src.integrations import workspace_mcp

        # Build a fake MCP result whose .content[].text contains the rate-limit phrase.
        rate_limit_content = SimpleNamespace(
            text="Error 429: too many requests. retryAfter: 30s"
        )
        rate_limit_result = SimpleNamespace(content=[rate_limit_content])

        mock_server = MagicMock()
        mock_server.connect = AsyncMock()
        mock_server.call_tool = AsyncMock(return_value=rate_limit_result)
        mock_server.cleanup = AsyncMock()

        with patch.object(workspace_mcp, "create_workspace_mcp_server", return_value=mock_server):
            result = await workspace_mcp.call_workspace_tool("get_calendar_events", {})

        assert mock_server.call_tool.await_count == 3
        assert "[RATE LIMIT]" in result

    @pytest.mark.asyncio
    async def test_non_rate_limit_error_does_not_retry(self, fast_retry):
        """Sanity check: errors that AREN'T rate limits must still go through
        the existing single-attempt path (this is the contract guarded by the
        existing test_generic_tool_error_returns_message test)."""
        from src.integrations import workspace_mcp

        mock_server = MagicMock()
        mock_server.connect = AsyncMock()
        mock_server.call_tool = AsyncMock(side_effect=ValueError("bad argument"))
        mock_server.cleanup = AsyncMock()

        with patch.object(workspace_mcp, "create_workspace_mcp_server", return_value=mock_server):
            result = await workspace_mcp.call_workspace_tool("create_drive_folder", {})

        # Only one attempt — non-transient errors aren't retried.
        assert mock_server.call_tool.await_count == 1
        assert "[TOOL ERROR]" in result

    def test_rate_limit_detection_patterns(self):
        from src.integrations.workspace_mcp import _is_rate_limit
        # Positive cases.
        assert _is_rate_limit("HTTP 429 received")
        assert _is_rate_limit("rate limit exceeded")
        assert _is_rate_limit("RateLimitExceeded")
        assert _is_rate_limit("user rate limit exceeded for project")
        assert _is_rate_limit("Quota exceeded")
        assert _is_rate_limit("too many requests")
        # Negative cases.
        assert not _is_rate_limit("permission denied")
        assert not _is_rate_limit("bad argument")
        assert not _is_rate_limit("file not found")

    def test_retry_after_extraction(self):
        from src.integrations.workspace_mcp import _parse_retry_after
        assert _parse_retry_after('{"retryAfter": "42"}') == 42.0
        assert _parse_retry_after('{"retry_after_seconds": 7}') == 7.0
        assert _parse_retry_after("Retry-After: 30") == 30.0
        assert _parse_retry_after("retry after 5") == 5.0
        assert _parse_retry_after("nothing here") is None


# ── Stale Memory Filter ──────────────────────────────────────────────────────

class TestStaleMemoryFilter:
    """Prevent Mem0 memory poisoning from overriding workspace routing rules."""

    def test_filters_drive_needs_fixing_memory(self):
        from src.memory.persona import _filter_stale_memories

        memories = [
            {"id": "1", "memory": "User prefers dark mode"},
            {"id": "2", "memory": "User expects Google Drive tool access to be fixed via code changes before continuing other tasks"},
            {"id": "3", "memory": "User likes concise responses"},
        ]
        filtered = _filter_stale_memories(memories, workspace_connected=True)
        assert len(filtered) == 2
        assert all("fixed" not in m["memory"] for m in filtered)

    def test_filters_needs_authenticated_memory(self):
        from src.memory.persona import _filter_stale_memories

        memories = [
            {"id": "1", "memory": "Needs authenticated/connected Google Drive access to list and sort private Drive items"},
        ]
        filtered = _filter_stale_memories(memories, workspace_connected=True)
        assert len(filtered) == 0

    def test_keeps_all_when_workspace_not_connected(self):
        from src.memory.persona import _filter_stale_memories

        memories = [
            {"id": "1", "memory": "User expects Google Drive tool access to be fixed"},
            {"id": "2", "memory": "Needs authenticated Drive access"},
        ]
        filtered = _filter_stale_memories(memories, workspace_connected=False)
        assert len(filtered) == 2, "Should keep all memories when workspace is NOT connected"

    def test_keeps_valid_drive_preferences(self):
        from src.memory.persona import _filter_stale_memories

        memories = [
            {"id": "1", "memory": "User prefers the assistant not to use web search and to use connected Google tools instead"},
            {"id": "2", "memory": "Wants Google Drive organized with logical folders"},
        ]
        filtered = _filter_stale_memories(memories, workspace_connected=True)
        assert len(filtered) == 2, "Valid Drive preferences should be kept"

    def test_filters_session_issue_memory(self):
        from src.memory.persona import _filter_stale_memories

        memories = [
            {"id": "1", "memory": "There is a drive session issue that prevents file listing"},
        ]
        filtered = _filter_stale_memories(memories, workspace_connected=True)
        assert len(filtered) == 0
