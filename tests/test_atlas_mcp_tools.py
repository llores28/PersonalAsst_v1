"""Tests for Wave 4.10 — Atlas-as-MCP-server tool contracts.

Atlas's deterministic short-circuits are now exposed as MCP tools that
external agentic runtimes (Hermes, OpenClaw, Claude Code) can call. These
tests pin:

1. **Schema integrity** — the JSON Schemas are well-formed and declare the
   right required fields.
2. **Input validation** — missing required fields and extra unknown fields
   both raise ``AtlasMCPInputError`` before any work happens.
3. **Handler dispatch** — each tool's Python handler exists and is async.
4. **Pass-through correctness** — the calendar/Gmail/tasks handlers thread
   their args through to the right ``workspace_mcp`` calls and apply the
   right formatter. Pinning this contract prevents future refactors from
   silently changing the MCP surface's behavior.
5. **Voice-mode flag** — ``voice_mode=True`` is respected by the calendar
   and Gmail handlers, producing the short conversational format.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest


# --------------------------------------------------------------------------
# Schema integrity
# --------------------------------------------------------------------------


class TestSchemaIntegrity:
    def test_all_three_tools_declared(self) -> None:
        from src.integrations.atlas_mcp_tools import ATLAS_MCP_TOOL_SCHEMAS

        assert set(ATLAS_MCP_TOOL_SCHEMAS) == {
            "atlas_summarize_calendar",
            "atlas_summarize_unread_emails",
            "atlas_list_open_tasks",
        }

    def test_every_tool_has_handler(self) -> None:
        from src.integrations.atlas_mcp_tools import (
            ATLAS_MCP_TOOL_SCHEMAS,
            ATLAS_MCP_TOOL_HANDLERS,
        )

        assert set(ATLAS_MCP_TOOL_SCHEMAS) == set(ATLAS_MCP_TOOL_HANDLERS)

    def test_every_handler_is_async(self) -> None:
        import inspect
        from src.integrations.atlas_mcp_tools import ATLAS_MCP_TOOL_HANDLERS

        for name, handler in ATLAS_MCP_TOOL_HANDLERS.items():
            assert inspect.iscoroutinefunction(handler), (
                f"{name} handler must be async (MCP transport awaits results)"
            )

    @pytest.mark.parametrize("tool_name", [
        "atlas_summarize_calendar",
        "atlas_summarize_unread_emails",
        "atlas_list_open_tasks",
    ])
    def test_user_google_email_is_required(self, tool_name: str) -> None:
        from src.integrations.atlas_mcp_tools import ATLAS_MCP_TOOL_SCHEMAS

        schema = ATLAS_MCP_TOOL_SCHEMAS[tool_name]["inputSchema"]
        assert "user_google_email" in schema["required"], (
            f"{tool_name}: user_google_email must be required (we don't infer "
            f"the connected account on the MCP surface — caller passes it)"
        )

    @pytest.mark.parametrize("tool_name", [
        "atlas_summarize_calendar",
        "atlas_summarize_unread_emails",
        "atlas_list_open_tasks",
    ])
    def test_additional_properties_disallowed(self, tool_name: str) -> None:
        """``additionalProperties: false`` means we reject unknown fields —
        prevents future silently-ignored typos in caller payloads."""
        from src.integrations.atlas_mcp_tools import ATLAS_MCP_TOOL_SCHEMAS

        schema = ATLAS_MCP_TOOL_SCHEMAS[tool_name]["inputSchema"]
        assert schema.get("additionalProperties") is False


# --------------------------------------------------------------------------
# Input validation
# --------------------------------------------------------------------------


class TestInputValidation:
    @pytest.mark.asyncio
    async def test_missing_required_field_raises(self) -> None:
        from src.integrations.atlas_mcp_tools import (
            atlas_summarize_calendar, AtlasMCPInputError,
        )

        with pytest.raises(AtlasMCPInputError, match="user_google_email"):
            await atlas_summarize_calendar({})

    @pytest.mark.asyncio
    async def test_extra_field_raises(self) -> None:
        from src.integrations.atlas_mcp_tools import (
            atlas_summarize_calendar, AtlasMCPInputError,
        )

        with pytest.raises(AtlasMCPInputError, match="unexpected fields"):
            await atlas_summarize_calendar({
                "user_google_email": "test@gmail.com",
                "bogus_field": "drop me",
            })

    @pytest.mark.asyncio
    async def test_unknown_tool_raises(self) -> None:
        from src.integrations.atlas_mcp_tools import _validate, AtlasMCPInputError

        with pytest.raises(AtlasMCPInputError, match="Unknown tool"):
            _validate("atlas_does_not_exist", {})


# --------------------------------------------------------------------------
# Pass-through correctness — handlers thread args to workspace_mcp + formatter
# --------------------------------------------------------------------------


class TestPassThroughCorrectness:
    @pytest.mark.asyncio
    async def test_calendar_handler_calls_get_events_with_user_email(self, monkeypatch) -> None:
        from src.integrations import atlas_mcp_tools, workspace_mcp

        captured: list[tuple[str, dict]] = []

        async def fake_call(tool_name: str, args: dict) -> str:
            captured.append((tool_name, args))
            return "No events found"

        monkeypatch.setattr(workspace_mcp, "call_workspace_tool", fake_call)

        result = await atlas_mcp_tools.atlas_summarize_calendar({
            "user_google_email": "test@gmail.com",
            "time_range": "today",
        })

        assert captured, "calendar handler didn't call workspace_mcp"
        tool_name, args = captured[0]
        assert tool_name == "get_events"
        assert args["user_google_email"] == "test@gmail.com"
        assert args["calendar_id"] == "primary"
        assert "time_min" in args and "time_max" in args
        assert isinstance(result, str)

    @pytest.mark.asyncio
    async def test_gmail_handler_uses_unread_query(self, monkeypatch) -> None:
        from src.integrations import atlas_mcp_tools, workspace_mcp

        captured: list[tuple[str, dict]] = []

        async def fake_call(tool_name: str, args: dict) -> str:
            captured.append((tool_name, args))
            if tool_name == "search_gmail_messages":
                return "Found 0 messages"
            return ""

        monkeypatch.setattr(workspace_mcp, "call_workspace_tool", fake_call)

        result = await atlas_mcp_tools.atlas_summarize_unread_emails({
            "user_google_email": "test@gmail.com",
        })

        assert captured
        tool_name, args = captured[0]
        assert tool_name == "search_gmail_messages"
        assert "is:unread" in args["query"]
        assert "in:inbox" in args["query"]
        # No messages → friendly empty response
        assert "unread email" in result.lower()

    @pytest.mark.asyncio
    async def test_tasks_handler_calls_list_tasks(self, monkeypatch) -> None:
        from src.integrations import atlas_mcp_tools, workspace_mcp

        captured: list[tuple[str, dict]] = []

        async def fake_call(tool_name: str, args: dict) -> str:
            captured.append((tool_name, args))
            return "0 tasks"

        monkeypatch.setattr(workspace_mcp, "call_workspace_tool", fake_call)

        await atlas_mcp_tools.atlas_list_open_tasks({
            "user_google_email": "test@gmail.com",
            "max_results": 5,
        })

        assert captured
        tool_name, args = captured[0]
        assert tool_name == "list_tasks"
        assert args["task_list_id"] == "@default"
        assert args["show_completed"] is False
        assert args["max_results"] == 5


# --------------------------------------------------------------------------
# Voice-mode propagation
# --------------------------------------------------------------------------


class TestVoiceModePropagation:
    @pytest.mark.asyncio
    async def test_calendar_voice_mode_produces_short_paragraph(self, monkeypatch) -> None:
        """When voice_mode=True, the calendar formatter switches to a
        conversational paragraph (no numbered list, no Date/Time/Event labels).
        Pinning this here prevents future refactors from breaking the
        spoken-reply UX through the MCP surface."""
        from src.integrations import atlas_mcp_tools, workspace_mcp

        # Two-event payload mirroring real workspace-mcp output
        async def fake_call(_tool_name: str, _args: dict) -> str:
            return (
                "Found 2 events.\n"
                '- "Investment Sync" (Starts: 2026-04-30T17:00:00+00:00, '
                'Ends: 2026-04-30T18:00:00+00:00)\n'
                "  Location: No Location\n"
                "  Description: No description\n"
                '- "Mortgage" (Starts: 2026-04-30T19:00:00+00:00, '
                'Ends: 2026-05-01T19:00:00+00:00)\n'
                "  Location: No Location\n"
                "  Description: No description\n"
            )

        monkeypatch.setattr(workspace_mcp, "call_workspace_tool", fake_call)

        voice_result = await atlas_mcp_tools.atlas_summarize_calendar({
            "user_google_email": "test@gmail.com",
            "voice_mode": True,
        })
        # Voice format: no numbered list / no field-label structure
        assert "1)" not in voice_result
        assert "Date:" not in voice_result
        assert "Event:" not in voice_result
        # Conversational opener
        assert voice_result.lower().startswith("you have ")
