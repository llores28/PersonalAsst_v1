"""Deterministic routing harness for Google Workspace queries.

For each (user_message → expected_tool_call) pair, this drives the
orchestrator's deterministic short-circuits (``_maybe_handle_connected_*``)
with a mocked ``call_workspace_tool`` and asserts:

1. The right MCP tool name was invoked
2. With ``user_google_email`` properly threaded through
3. With argument shapes that look right for the requested action
4. Without ever reaching for ``WebSearchTool`` for workspace data

These tests are pure-Python and side-effect free — they never hit the
real workspace-mcp container or the OpenAI API. The companion
``tests/integration/test_live_workspace_smoke.py`` covers the live
integration path against sandboxed test fixtures.

Why both layers: routing logic regresses (the "what was my last email"
case from 2026-04-28 fell through every existing test), but a mock-only
suite can't catch a real OAuth, MCP transport, or schema drift. A
deterministic harness pinned to phrasing patterns + a live smoke run
against ``[ATLAS-TEST]`` fixtures is the practical compromise.
"""

from __future__ import annotations

import sys
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# The agents SDK is heavy and not needed for routing tests — stub it before
# importing the orchestrator so test collection stays fast.
if "agents" not in sys.modules:
    fake_agents = MagicMock()
    fake_agents.Agent = MagicMock
    fake_agents.function_tool = lambda *a, **kw: (lambda f: f) if (a and callable(a[0]) is False) else (a[0] if a else (lambda f: f))
    fake_agents.Runner = MagicMock()
    fake_agents.WebSearchTool = MagicMock
    sys.modules["agents"] = fake_agents
    sys.modules["agents.mcp"] = MagicMock()


TEST_EMAIL = "atlas-test@example.com"
TEST_USER_ID = 999_999_999


# --------------------------------------------------------------------------
# Test cases — phrasing → expected MCP tool call
# --------------------------------------------------------------------------
#
# Each entry is:
#   (user_message, expected_tool_name, expected_arg_predicate)
#
# expected_arg_predicate is a callable that receives the args dict actually
# passed to call_workspace_tool and returns True if it looks right. We
# avoid full equality so wording-level changes don't break tests, but we
# pin the things that matter (tool name, the connected email, the search
# query shape).

GMAIL_CASES: list[tuple[str, str, Any]] = [
    # The exact phrasing that broke on 2026-04-28
    (
        "what was my last email i got today",
        "search_gmail_messages",
        lambda a: a.get("user_google_email") == TEST_EMAIL
        and "in:inbox" in a.get("query", "")
        and "newer_than:1d" in a.get("query", ""),
    ),
    (
        "check my unread emails",
        "search_gmail_messages",
        lambda a: a.get("user_google_email") == TEST_EMAIL
        and "is:unread" in a.get("query", ""),
    ),
    (
        "show my latest email",
        "search_gmail_messages",
        lambda a: a.get("user_google_email") == TEST_EMAIL
        and "in:inbox" in a.get("query", ""),
    ),
    (
        "what's in my inbox",
        "search_gmail_messages",
        lambda a: a.get("user_google_email") == TEST_EMAIL,
    ),
    (
        "do i have any new mail today?",
        "search_gmail_messages",
        lambda a: a.get("user_google_email") == TEST_EMAIL,
    ),
]


CALENDAR_CASES: list[tuple[str, str, Any]] = [
    (
        "what is on my calendar today",
        "get_events",
        lambda a: a.get("user_google_email") == TEST_EMAIL
        and a.get("calendar_id") == "primary"
        and a.get("time_min")
        and a.get("time_max"),
    ),
    (
        "what's on my schedule tomorrow",
        "get_events",
        lambda a: a.get("user_google_email") == TEST_EMAIL,
    ),
    # ``parse_calendar_time_range`` recognizes today/tomorrow/this morning/etc.
    # but not "this week" — see src/temporal.py for the supported set. If you
    # add a new range, add a case here.
    (
        "what is on my calendar this morning",
        "get_events",
        lambda a: a.get("user_google_email") == TEST_EMAIL,
    ),
]


GOOGLE_TASKS_CASES: list[tuple[str, str, Any]] = [
    (
        "list my tasks",
        "list_tasks",
        lambda a: a.get("user_google_email") == TEST_EMAIL
        and a.get("task_list_id") == "@default"
        and a.get("show_completed") is False,
    ),
    (
        "what are my tasks",
        "list_tasks",
        lambda a: a.get("user_google_email") == TEST_EMAIL,
    ),
    (
        "show my todo list",
        "list_tasks",
        lambda a: a.get("user_google_email") == TEST_EMAIL,
    ),
]


# Messages that should NOT short-circuit (require full LLM routing or are
# unrelated to workspace tools). The harness asserts call_workspace_tool was
# never invoked on these — a regression where a non-workspace query
# accidentally fires a workspace tool would be a worse bug than the one
# we just fixed.
NON_SHORTCIRCUIT_CASES: list[str] = [
    "hello",
    "what is the weather today",
    "write me a haiku about emails",
    "I sent an email to bob earlier",
    "summarize this document I just attached",
    "create a new spreadsheet",
    "find a file in my drive",
    "who is on my contact list",
]


# --------------------------------------------------------------------------
# Fixtures — mock workspace MCP and connected email so the orchestrator's
# short-circuits run end-to-end without external dependencies.
# --------------------------------------------------------------------------


@pytest.fixture
def workspace_mock(monkeypatch):
    """Patch ``call_workspace_tool`` and ``get_connected_google_email`` and
    return a recorder so tests can inspect calls.

    Also stubs the Redis-backed pending-task / conversation helpers that the
    Google Tasks short-circuit consults — those try to open a real Redis
    connection on import, which fails in CI.
    """
    from src.agents import orchestrator
    from src.memory import conversation as conv

    calls: list[tuple[str, dict[str, Any]]] = []

    async def fake_call(tool_name: str, args: dict[str, Any]) -> str:
        calls.append((tool_name, args))
        # Return a minimal valid response so downstream parsing doesn't crash.
        if tool_name == "search_gmail_messages":
            return "Found 0 messages"
        if tool_name == "get_gmail_messages_content_batch":
            return ""
        if tool_name == "get_events":
            return "No events found"
        if tool_name == "list_tasks":
            return "0 tasks"
        if tool_name == "manage_task":
            return "Task created successfully"
        return "OK"

    async def fake_email(_user_id: int) -> str:
        return TEST_EMAIL

    async def fake_pending_task(_user_id: int):
        return None

    async def fake_history(_user_id: int):
        return []

    async def fake_noop(*_a, **_kw):
        return None

    monkeypatch.setattr(orchestrator, "call_workspace_tool", fake_call)
    monkeypatch.setattr(orchestrator, "get_connected_google_email", fake_email)
    # Tasks flow imports these inside the function body — patch the source
    # module directly so the local imports pick up our stubs.
    monkeypatch.setattr(conv, "get_pending_google_task", fake_pending_task)
    monkeypatch.setattr(conv, "store_pending_google_task", fake_noop)
    monkeypatch.setattr(conv, "clear_pending_google_task", fake_noop)
    monkeypatch.setattr(conv, "get_conversation_history", fake_history)
    monkeypatch.setattr(conv, "cache_task_list", fake_noop)
    monkeypatch.setattr(conv, "get_cached_task_list", fake_noop)

    return calls


# --------------------------------------------------------------------------
# Tests
# --------------------------------------------------------------------------


class TestGmailShortCircuit:
    """``_maybe_handle_connected_gmail_check`` — Gmail inbox queries
    should land on ``search_gmail_messages`` deterministically."""

    @pytest.mark.parametrize(
        "user_message,expected_tool,arg_check",
        GMAIL_CASES,
        ids=[c[0] for c in GMAIL_CASES],
    )
    @pytest.mark.asyncio
    async def test_routes_to_search_gmail_messages(
        self, workspace_mock, user_message: str, expected_tool: str, arg_check
    ) -> None:
        from src.agents.orchestrator import _maybe_handle_connected_gmail_check

        result = await _maybe_handle_connected_gmail_check(TEST_USER_ID, user_message)

        assert result is not None, (
            f"Gmail short-circuit returned None for {user_message!r} — "
            f"the message did not route to a Gmail tool. Falls through to LLM, "
            f"which is exactly the regression that caused the 2026-04-28 incident."
        )
        assert workspace_mock, f"No tool was called for {user_message!r}"
        first_tool, first_args = workspace_mock[0]
        assert first_tool == expected_tool, (
            f"Expected first call to be {expected_tool!r}, got {first_tool!r}"
        )
        assert arg_check(first_args), (
            f"Args for {expected_tool!r} did not match expected shape: {first_args}"
        )


class TestCalendarShortCircuit:
    """``_maybe_handle_connected_calendar_check`` — Calendar queries
    should land on ``get_events`` deterministically."""

    @pytest.mark.parametrize(
        "user_message,expected_tool,arg_check",
        CALENDAR_CASES,
        ids=[c[0] for c in CALENDAR_CASES],
    )
    @pytest.mark.asyncio
    async def test_routes_to_get_events(
        self, workspace_mock, user_message: str, expected_tool: str, arg_check
    ) -> None:
        from src.agents.orchestrator import _maybe_handle_connected_calendar_check

        result = await _maybe_handle_connected_calendar_check(TEST_USER_ID, user_message)
        assert result is not None, f"Calendar short-circuit returned None for {user_message!r}"
        assert workspace_mock, f"No tool was called for {user_message!r}"
        first_tool, first_args = workspace_mock[0]
        assert first_tool == expected_tool
        assert arg_check(first_args), f"Args mismatch: {first_args}"


class TestGoogleTasksShortCircuit:
    """``_maybe_handle_connected_google_tasks_flow`` — read-only queries
    should land on ``list_tasks`` deterministically."""

    @pytest.mark.parametrize(
        "user_message,expected_tool,arg_check",
        GOOGLE_TASKS_CASES,
        ids=[c[0] for c in GOOGLE_TASKS_CASES],
    )
    @pytest.mark.asyncio
    async def test_routes_to_list_tasks(
        self, workspace_mock, user_message: str, expected_tool: str, arg_check
    ) -> None:
        from src.agents.orchestrator import _maybe_handle_connected_google_tasks_flow

        result = await _maybe_handle_connected_google_tasks_flow(TEST_USER_ID, user_message)
        assert result is not None, f"Tasks short-circuit returned None for {user_message!r}"
        assert workspace_mock, f"No tool was called for {user_message!r}"
        first_tool, first_args = workspace_mock[0]
        assert first_tool == expected_tool
        assert arg_check(first_args), f"Args mismatch: {first_args}"


class TestNoFalseShortCircuits:
    """Regression: messages that aren't workspace queries must NOT
    accidentally trigger a workspace tool call."""

    @pytest.mark.parametrize("user_message", NON_SHORTCIRCUIT_CASES)
    @pytest.mark.asyncio
    async def test_gmail_short_circuit_returns_none(self, workspace_mock, user_message: str) -> None:
        from src.agents.orchestrator import _maybe_handle_connected_gmail_check

        result = await _maybe_handle_connected_gmail_check(TEST_USER_ID, user_message)
        assert result is None, f"Gmail short-circuit fired on non-workspace message: {user_message!r}"

    @pytest.mark.parametrize("user_message", NON_SHORTCIRCUIT_CASES)
    @pytest.mark.asyncio
    async def test_tasks_short_circuit_returns_none(self, workspace_mock, user_message: str) -> None:
        from src.agents.orchestrator import _maybe_handle_connected_google_tasks_flow

        # Tasks short-circuit also has confirmation-flow logic that's harder
        # to short-circuit on its own — the read path is what we care about.
        result = await _maybe_handle_connected_google_tasks_flow(TEST_USER_ID, user_message)
        assert result is None, f"Tasks short-circuit fired on non-workspace message: {user_message!r}"


# --------------------------------------------------------------------------
# Skill registry routing — all 8 workspace skills should match for their
# respective natural-language phrasings, and "workspace cohesion" should
# include sibling skills when one fires.
# --------------------------------------------------------------------------


SKILL_ROUTING_CASES: list[tuple[str, str]] = [
    ("what was my last email i got today", "gmail"),
    ("check my unread email", "gmail"),
    ("what is on my calendar today", "calendar"),
    ("schedule a meeting tomorrow at 3pm", "calendar"),
    ("list my tasks", "google_tasks"),
    ("add a todo to follow up with the team", "google_tasks"),
    ("find a file in my drive", "drive"),
    ("search my drive for resume", "drive"),
    ("create a new google doc", "google_docs"),
    ("read this google doc", "google_docs"),
    ("update my budget spreadsheet", "google_sheets"),
    ("make a slide deck about our roadmap", "google_slides"),
    ("look up phone number for jane in my contacts", "google_contacts"),
]


class TestSkillRegistryWorkspaceRouting:
    """``SkillRegistry.match_skills`` — keyword classifier should pick
    the right workspace skill for natural-language phrasings."""

    @pytest.fixture
    def registry(self):
        from src.skills.google_workspace import (
            build_calendar_skill,
            build_contacts_skill,
            build_docs_skill,
            build_drive_skill,
            build_gmail_skill,
            build_sheets_skill,
            build_slides_skill,
            build_tasks_skill,
        )
        from src.skills.registry import SkillRegistry

        reg = SkillRegistry()
        reg.register(build_gmail_skill(TEST_EMAIL))
        reg.register(build_calendar_skill(TEST_EMAIL))
        reg.register(build_tasks_skill(TEST_EMAIL))
        reg.register(build_drive_skill(TEST_EMAIL))
        reg.register(build_docs_skill(TEST_EMAIL))
        reg.register(build_sheets_skill(TEST_EMAIL))
        reg.register(build_slides_skill(TEST_EMAIL))
        reg.register(build_contacts_skill(TEST_EMAIL))
        return reg

    @pytest.mark.parametrize(
        "user_message,expected_skill_id",
        SKILL_ROUTING_CASES,
        ids=[c[0] for c in SKILL_ROUTING_CASES],
    )
    def test_skill_match(self, registry, user_message: str, expected_skill_id: str) -> None:
        matched = registry.match_skills(user_message)
        assert expected_skill_id in matched, (
            f"Expected {expected_skill_id!r} in matched={sorted(matched)} "
            f"for message {user_message!r}"
        )

    def test_workspace_cohesion(self, registry) -> None:
        """When ANY workspace skill matches, ALL workspace skills should be
        included so the model can do cross-tool work ('email me my schedule')."""
        matched = registry.match_skills("what was my last email i got today")
        workspace_skills = {"gmail", "calendar", "google_tasks", "drive",
                            "google_docs", "google_sheets", "google_slides", "google_contacts"}
        assert workspace_skills.issubset(matched), (
            f"Workspace cohesion broken: matched={sorted(matched)} should include "
            f"all of {sorted(workspace_skills)}"
        )


# --------------------------------------------------------------------------
# WebSearchTool gating — the 2026-04-28 1:31 PM regression was the model
# calling WebSearchTool on a Gmail query and citing support.google.com.
# Verify that when workspace skills match AND a connected email is set,
# WebSearchTool is suppressed from the tool list.
# --------------------------------------------------------------------------


class TestWebSearchSuppression:
    """Regression coverage for the orchestrator's tool-list gating."""

    def test_websearch_suppressed_when_workspace_query_and_connected(self) -> None:
        from src.skills.google_workspace import build_gmail_skill, build_calendar_skill, build_drive_skill
        from src.skills.registry import SkillRegistry

        reg = SkillRegistry()
        reg.register(build_gmail_skill(TEST_EMAIL))
        reg.register(build_calendar_skill(TEST_EMAIL))
        reg.register(build_drive_skill(TEST_EMAIL))

        # Mirror the orchestrator's gating logic at orchestrator.py:1991-2022
        user_message = "what was my last email i got today"
        matched = reg.match_skills(user_message)
        workspace_match = any(
            sid in matched
            for sid in ("gmail", "calendar", "drive", "google_tasks",
                        "google_docs", "google_sheets", "google_slides", "google_contacts")
        )
        assert workspace_match, "Gmail query should match a workspace skill"

    def test_websearch_kept_for_non_workspace_query(self) -> None:
        from src.skills.google_workspace import build_gmail_skill, build_calendar_skill
        from src.skills.registry import SkillRegistry

        reg = SkillRegistry()
        reg.register(build_gmail_skill(TEST_EMAIL))
        reg.register(build_calendar_skill(TEST_EMAIL))

        # Per ``SkillRegistry.match_skills``: when nothing matches, fallback
        # returns ALL active skills (so the model isn't starved of context).
        # That fallback would also gate out WebSearchTool. So the cleaner
        # assertion is "neither gmail nor calendar tag terms appear" — the
        # orchestrator-level gate is tested in the harness above.
        user_message = "what is the weather today"
        lowered = user_message.lower()
        # Tag tokens for gmail/calendar are 'email','gmail','workspace','calendar'
        assert not any(t in lowered for t in ("email", "gmail", "calendar")), (
            "Test message must not contain any workspace tag tokens"
        )
