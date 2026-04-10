"""Google Workspace Tools QA Harness — one playbook, one runner.

This module defines a DATA-DRIVEN scenario playbook for every Google tool
routing path.  Adding a new scenario = adding one dict.  No new test code.

Two interfaces:
  1. pytest — parametrized via tests/test_google_tools_qa.py
  2. Agent-callable — validate_google_tools() returns structured report

Architecture decision: ONE harness validates ALL specialists.
You do NOT need a separate agent/validator per specialist.
The playbook covers:
  - 4 direct handlers (gmail, calendar, tasks, pending-gmail-send)
  - 12 connected tool wrappers (email 6, calendar 2, tasks 4)
  - Multi-step sequences (draft→send, calendar→email, tasks→complete)
  - Error paths (auth failures, MCP errors, missing recipients)
  - Edge cases (empty results, disambiguation prompts)
"""

from __future__ import annotations

import dataclasses
import logging
from typing import Any

logger = logging.getLogger(__name__)


# ────────────────────────────────────────────────────────────────────────
# SCENARIO DATA MODEL
# ────────────────────────────────────────────────────────────────────────


@dataclasses.dataclass(frozen=True)
class ToolCallExpectation:
    """Expected MCP tool call from a handler."""
    tool_name: str
    required_args: dict[str, Any] = dataclasses.field(default_factory=dict)


@dataclasses.dataclass(frozen=True)
class Scenario:
    """One user-interaction scenario in the QA playbook."""
    id: str
    description: str
    handler: str  # "gmail", "calendar", "tasks", "pending_gmail", "orchestrator"
    specialist: str  # "EmailAgent", "CalendarAgent", "TasksAgent", "DriveAgent", "none"
    user_phrase: str
    canned_mcp_responses: list[str] = dataclasses.field(default_factory=list)
    expected_tool_calls: list[ToolCallExpectation] = dataclasses.field(default_factory=list)
    output_must_contain: list[str] = dataclasses.field(default_factory=list)
    output_must_not_contain: list[str] = dataclasses.field(default_factory=list)
    # For multi-step: pre-loaded state
    preset_pending_gmail: dict[str, str | None] | None = None
    preset_pending_task: dict[str, Any] | None = None
    preset_history: list[dict[str, str]] | None = None
    # Expected handler result
    expect_none: bool = False  # handler returns None → falls through to orchestrator
    expect_error_in_output: bool = False
    # Tags for filtering
    tags: tuple[str, ...] = ()


# ────────────────────────────────────────────────────────────────────────
# SCENARIO PLAYBOOK — the single source of truth
# ────────────────────────────────────────────────────────────────────────

PLAYBOOK: list[Scenario] = [
    # ── GMAIL DIRECT HANDLER ─────────────────────────────────────────
    Scenario(
        id="gmail.check_inbox",
        description="Simple inbox check → search + batch fetch",
        handler="gmail",
        specialist="EmailAgent",
        user_phrase="check my email",
        canned_mcp_responses=[
            "Found 2 messages matching 'in:inbox'\nMessage ID: msg001\nMessage ID: msg002",
            (
                "Message ID: msg001\nSubject: Team Update\n"
                "From: Alice <alice@example.com>\nDate: 2026-03-19\n"
                "We shipped the new release today.\n---\n"
                "Message ID: msg002\nSubject: Invoice #1234\n"
                "From: Billing <billing@example.com>\nDate: 2026-03-18\n"
                "Amount paid: $29.99\n"
            ),
        ],
        expected_tool_calls=[
            ToolCallExpectation("search_gmail_messages", {"query": "in:inbox", "page_size": 10}),
            ToolCallExpectation("get_gmail_messages_content_batch", {"format": "full"}),
        ],
        output_must_contain=["Here are your latest emails", "Alice", "Team Update"],
        tags=("gmail", "read", "direct_handler"),
    ),
    Scenario(
        id="gmail.latest_unread",
        description="Single latest unread email → search(page_size=1) + batch",
        handler="gmail",
        specialist="EmailAgent",
        user_phrase="latest unread email",
        canned_mcp_responses=[
            "Found 1 message matching 'in:inbox is:unread'\nMessage ID: unread001",
            (
                "Message ID: unread001\nSubject: Security Alert\n"
                "From: Google <no-reply@google.com>\nDate: 2026-03-19\n"
                "We noticed a new sign-in to your account.\n"
            ),
        ],
        expected_tool_calls=[
            ToolCallExpectation("search_gmail_messages", {"query": "in:inbox is:unread", "page_size": 1}),
            ToolCallExpectation("get_gmail_messages_content_batch", {}),
        ],
        output_must_contain=["latest unread email", "Security Alert"],
        tags=("gmail", "read", "direct_handler", "single_message"),
    ),
    Scenario(
        id="gmail.empty_inbox",
        description="No emails found → friendly empty message",
        handler="gmail",
        specialist="EmailAgent",
        user_phrase="check my unread emails",
        canned_mcp_responses=[
            "Found 0 messages matching 'in:inbox is:unread'",
        ],
        expected_tool_calls=[
            ToolCallExpectation("search_gmail_messages", {"query": "in:inbox is:unread"}),
        ],
        output_must_contain=["don't have any matching emails"],
        tags=("gmail", "read", "direct_handler", "empty"),
    ),
    Scenario(
        id="gmail.auth_error",
        description="Gmail auth failure → helpful error with reconnect hint",
        handler="gmail",
        specialist="EmailAgent",
        user_phrase="check my inbox",
        canned_mcp_responses=["__RAISE__:invalid_grant: Token expired"],
        expected_tool_calls=[
            ToolCallExpectation("search_gmail_messages", {}),
        ],
        output_must_contain=["couldn't access Gmail", "/connect google"],
        expect_error_in_output=True,
        tags=("gmail", "error", "direct_handler"),
    ),

    # ── CALENDAR DIRECT HANDLER ──────────────────────────────────────
    Scenario(
        id="calendar.today",
        description="Today's calendar → get_events with today's range",
        handler="calendar",
        specialist="CalendarAgent",
        user_phrase="whats on my calendar today",
        canned_mcp_responses=[
            '- "Standup" (Starts: 2026-03-19T09:00:00-05:00, Ends: 2026-03-19T09:30:00-05:00)\n'
            '  Location: Zoom\n  Description: Daily standup\n'
        ],
        expected_tool_calls=[
            ToolCallExpectation("get_events", {"calendar_id": "primary", "detailed": True}),
        ],
        output_must_contain=["schedule for today", "Standup"],
        tags=("calendar", "read", "direct_handler"),
    ),
    Scenario(
        id="calendar.next_week",
        description="Next week calendar → get_events with next-week range",
        handler="calendar",
        specialist="CalendarAgent",
        user_phrase="whats on my calendar next week",
        canned_mcp_responses=[
            '- "Flight to FLL" (Starts: 2026-03-28T12:10:00-05:00, Ends: 2026-03-28T14:45:00-05:00)\n'
            '  Location: Houston IAH\n  Description: UA 1318\n'
        ],
        expected_tool_calls=[
            ToolCallExpectation("get_events", {"calendar_id": "primary"}),
        ],
        output_must_contain=["schedule for next week", "Flight to FLL"],
        tags=("calendar", "read", "direct_handler"),
    ),
    Scenario(
        id="calendar.empty",
        description="No events → clear calendar message",
        handler="calendar",
        specialist="CalendarAgent",
        user_phrase="whats on my calendar today",
        canned_mcp_responses=["0 events found for the specified range"],
        expected_tool_calls=[
            ToolCallExpectation("get_events", {}),
        ],
        output_must_contain=["clear"],
        tags=("calendar", "read", "direct_handler", "empty"),
    ),
    Scenario(
        id="calendar.auth_error",
        description="Calendar auth failure → helpful error",
        handler="calendar",
        specialist="CalendarAgent",
        user_phrase="show my calendar",
        canned_mcp_responses=["__RAISE__:calendar forbidden"],
        expected_tool_calls=[
            ToolCallExpectation("get_events", {}),
        ],
        output_must_contain=["couldn't access Google Calendar", "/connect google"],
        expect_error_in_output=True,
        tags=("calendar", "error", "direct_handler"),
    ),

    # ── GOOGLE TASKS DIRECT HANDLER ──────────────────────────────────
    Scenario(
        id="tasks.list",
        description="List tasks → list_tasks on @default list",
        handler="tasks",
        specialist="TasksAgent",
        user_phrase="show my tasks",
        canned_mcp_responses=[
            "Tasks in list @default for user@example.com:\n"
            "- Buy groceries (ID: t001)\n  Status: needsAction\n"
            "- Call dentist (ID: t002)\n  Status: needsAction\n"
        ],
        expected_tool_calls=[
            ToolCallExpectation("list_tasks", {"task_list_id": "@default", "show_completed": False}),
        ],
        output_must_contain=["Buy groceries", "Call dentist"],
        tags=("tasks", "read", "direct_handler"),
    ),
    Scenario(
        id="tasks.empty",
        description="No tasks → friendly empty message",
        handler="tasks",
        specialist="TasksAgent",
        user_phrase="list my tasks",
        canned_mcp_responses=["0 tasks found"],
        expected_tool_calls=[
            ToolCallExpectation("list_tasks", {}),
        ],
        output_must_contain=["don't have any pending"],
        tags=("tasks", "read", "direct_handler", "empty"),
    ),
    Scenario(
        id="tasks.confirm_create",
        description="Pending task confirmation → manage_task create",
        handler="tasks",
        specialist="TasksAgent",
        user_phrase="yes",
        preset_pending_task={
            "title": "Buy groceries",
            "due": "2026-03-20T14:00:00Z",
            "label": "tomorrow at 9:00 AM",
        },
        canned_mcp_responses=["created"],
        expected_tool_calls=[
            ToolCallExpectation("manage_task", {
                "action": "create",
                "task_list_id": "@default",
                "title": "Buy groceries",
            }),
        ],
        output_must_contain=["Done", "Buy groceries", "Google Tasks"],
        tags=("tasks", "write", "direct_handler", "confirmation"),
    ),
    Scenario(
        id="tasks.complete_single",
        description="Mark single recent task as completed",
        handler="tasks",
        specialist="TasksAgent",
        user_phrase="mark this task as completed",
        preset_history=[
            {
                "role": "assistant",
                "content": (
                    "Tasks in list @default for user@example.com:\n"
                    "- Buy groceries (ID: t001)\n  Status: needsAction\n"
                ),
            },
            {"role": "user", "content": "mark this task as completed"},
        ],
        canned_mcp_responses=["completed"],
        expected_tool_calls=[
            ToolCallExpectation("manage_task", {
                "action": "update",
                "task_id": "t001",
                "status": "completed",
            }),
        ],
        output_must_contain=["Done", "Buy groceries", "completed"],
        tags=("tasks", "write", "direct_handler", "completion"),
    ),
    Scenario(
        id="tasks.complete_disambiguate",
        description="Multiple recent tasks → asks user to pick one",
        handler="tasks",
        specialist="TasksAgent",
        user_phrase="complete this task",
        preset_history=[
            {
                "role": "assistant",
                "content": (
                    "Tasks in list @default for user@example.com:\n"
                    "- Buy groceries (ID: t001)\n  Status: needsAction\n"
                    "- Call dentist (ID: t002)\n  Status: needsAction\n"
                ),
            },
            {"role": "user", "content": "complete this task"},
        ],
        canned_mcp_responses=[],
        expected_tool_calls=[],
        output_must_contain=["multiple recent Google Tasks", "1)", "2)"],
        tags=("tasks", "read", "direct_handler", "disambiguation"),
    ),
    Scenario(
        id="tasks.list_error",
        description="Tasks list error → helpful error with reconnect hint",
        handler="tasks",
        specialist="TasksAgent",
        user_phrase="show my google tasks",
        canned_mcp_responses=["__RAISE__:Unauthorized: token expired"],
        expected_tool_calls=[
            ToolCallExpectation("list_tasks", {}),
        ],
        output_must_contain=["Google Tasks error", "/connect google"],
        expect_error_in_output=True,
        tags=("tasks", "error", "direct_handler"),
    ),

    # ── PENDING GMAIL SEND (multi-step) ──────────────────────────────
    Scenario(
        id="pending_gmail.send_confirmation",
        description="User says 'send it' with complete pending payload → sends via MCP",
        handler="pending_gmail",
        specialist="EmailAgent",
        user_phrase="send it",
        preset_pending_gmail={
            "to": "betty@example.com",
            "subject": "Flight Itinerary",
            "body": "Hi Betty,\n\nHere are the flight details.\n\nBest,\nAtlas",
        },
        canned_mcp_responses=["sent"],
        expected_tool_calls=[
            ToolCallExpectation("send_gmail_message", {
                "to": "betty@example.com",
                "subject": "Flight Itinerary",
            }),
        ],
        output_must_contain=["Done", "sent", "betty@example.com"],
        tags=("gmail", "write", "direct_handler", "multi_step"),
    ),
    Scenario(
        id="pending_gmail.missing_recipient",
        description="User says 'send it' but no recipient → asks for email",
        handler="pending_gmail",
        specialist="EmailAgent",
        user_phrase="send it",
        preset_pending_gmail={
            "to": None,
            "subject": "Flight Itinerary",
            "body": "Hi,\n\nHere are the flight details.",
        },
        canned_mcp_responses=[],
        expected_tool_calls=[],
        output_must_contain=["recipient's email address"],
        tags=("gmail", "write", "direct_handler", "clarification"),
    ),
    Scenario(
        id="pending_gmail.provide_recipient",
        description="User provides recipient email after clarification",
        handler="pending_gmail",
        specialist="EmailAgent",
        user_phrase="send it to betty@example.com",
        preset_pending_gmail={
            "to": None,
            "subject": "Flight Itinerary",
            "body": "Hi,\n\nHere are the flight details.",
        },
        canned_mcp_responses=[],
        expected_tool_calls=[],
        output_must_contain=["betty@example.com", "send it"],
        output_must_not_contain=["sent the email"],
        tags=("gmail", "write", "direct_handler", "clarification"),
    ),

    # ── FALL-THROUGH TO ORCHESTRATOR (no direct handler fires) ───────
    Scenario(
        id="orchestrator.email_search",
        description="Email search by keyword → no direct handler, needs orchestrator+EmailAgent",
        handler="orchestrator",
        specialist="EmailAgent",
        user_phrase="find email from United about my flight confirmation",
        expect_none=True,
        tags=("gmail", "read", "orchestrator_routed"),
    ),
    Scenario(
        id="orchestrator.email_draft",
        description="Draft email → no direct handler, needs orchestrator+EmailAgent",
        handler="orchestrator",
        specialist="EmailAgent",
        user_phrase="draft an email to bob@example.com about the meeting",
        expect_none=True,
        tags=("gmail", "write", "orchestrator_routed"),
    ),
    Scenario(
        id="orchestrator.calendar_create",
        description="Create calendar event → no direct handler, needs orchestrator+CalendarAgent",
        handler="orchestrator",
        specialist="CalendarAgent",
        user_phrase="create a meeting with Sarah tomorrow at 2pm",
        expect_none=True,
        tags=("calendar", "write", "orchestrator_routed"),
    ),
    Scenario(
        id="orchestrator.drive_search",
        description="Drive file search → no direct handler, needs orchestrator+DriveAgent",
        handler="orchestrator",
        specialist="DriveAgent",
        user_phrase="find my presentation about Q1 results on Drive",
        expect_none=True,
        tags=("drive", "read", "orchestrator_routed"),
    ),
    Scenario(
        id="orchestrator.cross_tool_calendar_email",
        description="Cross-tool: calendar data in email → orchestrator chains manage_calendar→manage_email",
        handler="orchestrator",
        specialist="EmailAgent",
        user_phrase="draft an email to betty with my flight info from my calendar",
        expect_none=True,
        tags=("gmail", "calendar", "cross_tool", "orchestrator_routed"),
    ),
    Scenario(
        id="orchestrator.organize_inbox",
        description="Complex inbox operation → orchestrator+EmailAgent",
        handler="orchestrator",
        specialist="EmailAgent",
        user_phrase="help me organize my inbox emails by category",
        expect_none=True,
        tags=("gmail", "read", "orchestrator_routed", "complex"),
    ),
]


# ────────────────────────────────────────────────────────────────────────
# SCENARIO RUNNER
# ────────────────────────────────────────────────────────────────────────


@dataclasses.dataclass
class ScenarioResult:
    """Result of running one scenario."""
    scenario_id: str
    passed: bool
    tool_calls_captured: list[dict[str, Any]]
    handler_output: str | None
    failures: list[str]

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


async def run_scenario(scenario: Scenario) -> ScenarioResult:
    """Execute one scenario with mocked MCP + Redis and return structured result."""
    from unittest.mock import AsyncMock, patch

    failures: list[str] = []
    captured_calls: list[dict[str, Any]] = []
    mcp_response_index = 0

    async def mock_call_workspace_tool(tool_name: str, arguments: dict) -> str:
        nonlocal mcp_response_index
        captured_calls.append({"tool_name": tool_name, "arguments": arguments})
        if mcp_response_index < len(scenario.canned_mcp_responses):
            response = scenario.canned_mcp_responses[mcp_response_index]
            mcp_response_index += 1
            if response.startswith("__RAISE__:"):
                raise RuntimeError(response[len("__RAISE__:"):])
            return response
        return ""

    connected_email = "user@example.com"
    patches = {
        "src.agents.orchestrator.get_connected_google_email": AsyncMock(return_value=connected_email),
        "src.agents.orchestrator.call_workspace_tool": AsyncMock(side_effect=mock_call_workspace_tool),
    }

    # Redis conversation mocks
    patches["src.memory.conversation.get_pending_google_task"] = AsyncMock(
        return_value=scenario.preset_pending_task
    )
    patches["src.memory.conversation.store_pending_google_task"] = AsyncMock()
    patches["src.memory.conversation.clear_pending_google_task"] = AsyncMock()
    patches["src.memory.conversation.get_pending_gmail_send"] = AsyncMock(
        return_value=scenario.preset_pending_gmail
    )
    patches["src.memory.conversation.store_pending_gmail_send"] = AsyncMock()
    patches["src.memory.conversation.clear_pending_gmail_send"] = AsyncMock()
    patches["src.memory.conversation.store_pending_clarification"] = AsyncMock()
    patches["src.memory.conversation.clear_pending_clarification"] = AsyncMock()
    patches["src.memory.conversation.get_conversation_history"] = AsyncMock(
        return_value=scenario.preset_history or []
    )
    # Task list caching mocks
    patches["src.memory.conversation.cache_task_list"] = AsyncMock()
    patches["src.memory.conversation.get_cached_task_list"] = AsyncMock(return_value=None)

    result_text: str | None = None

    # Use contextlib.ExitStack to apply all patches
    import contextlib
    with contextlib.ExitStack() as stack:
        for target, mock_obj in patches.items():
            stack.enter_context(patch(target, new=mock_obj))

        try:
            if scenario.handler == "gmail":
                from src.agents.orchestrator import _maybe_handle_connected_gmail_check
                result_text = await _maybe_handle_connected_gmail_check(12345, scenario.user_phrase)
            elif scenario.handler == "calendar":
                from src.agents.orchestrator import _maybe_handle_connected_calendar_check
                result_text = await _maybe_handle_connected_calendar_check(12345, scenario.user_phrase)
            elif scenario.handler == "tasks":
                from src.agents.orchestrator import _maybe_handle_connected_google_tasks_flow
                result_text = await _maybe_handle_connected_google_tasks_flow(12345, scenario.user_phrase)
            elif scenario.handler == "pending_gmail":
                from src.agents.orchestrator import _maybe_handle_pending_connected_gmail_send
                result_text = await _maybe_handle_pending_connected_gmail_send(12345, scenario.user_phrase)
            elif scenario.handler == "orchestrator":
                # For orchestrator-routed scenarios, verify no direct handler fires
                from src.agents.orchestrator import (
                    _maybe_handle_connected_gmail_check,
                    _maybe_handle_connected_calendar_check,
                    _maybe_handle_connected_google_tasks_flow,
                )
                gmail_result = await _maybe_handle_connected_gmail_check(12345, scenario.user_phrase)
                calendar_result = await _maybe_handle_connected_calendar_check(12345, scenario.user_phrase)
                tasks_result = await _maybe_handle_connected_google_tasks_flow(12345, scenario.user_phrase)
                if gmail_result is not None:
                    failures.append(f"Expected orchestrator routing but gmail handler returned: {gmail_result[:80]}")
                if calendar_result is not None:
                    failures.append(f"Expected orchestrator routing but calendar handler returned: {calendar_result[:80]}")
                if tasks_result is not None:
                    failures.append(f"Expected orchestrator routing but tasks handler returned: {tasks_result[:80]}")
                result_text = None
        except Exception as exc:
            failures.append(f"Handler raised unexpected exception: {exc}")

    # Validate expectations
    if scenario.expect_none:
        if result_text is not None:
            failures.append(f"Expected None (fall-through) but got: {result_text[:120]}")
    else:
        if result_text is None:
            failures.append("Expected a response but handler returned None")
        else:
            for must_contain in scenario.output_must_contain:
                if must_contain.lower() not in result_text.lower():
                    failures.append(f"Output missing expected text: '{must_contain}'")
            for must_not_contain in scenario.output_must_not_contain:
                if must_not_contain.lower() in result_text.lower():
                    failures.append(f"Output contains forbidden text: '{must_not_contain}'")

    # Validate tool calls
    for i, expected_call in enumerate(scenario.expected_tool_calls):
        if i >= len(captured_calls):
            failures.append(f"Expected tool call #{i+1} to '{expected_call.tool_name}' but only {len(captured_calls)} calls were made")
            continue
        actual = captured_calls[i]
        if actual["tool_name"] != expected_call.tool_name:
            failures.append(f"Call #{i+1}: expected tool '{expected_call.tool_name}', got '{actual['tool_name']}'")
        for key, expected_value in expected_call.required_args.items():
            actual_value = actual["arguments"].get(key)
            if actual_value != expected_value:
                failures.append(f"Call #{i+1} arg '{key}': expected {expected_value!r}, got {actual_value!r}")

    return ScenarioResult(
        scenario_id=scenario.id,
        passed=len(failures) == 0,
        tool_calls_captured=captured_calls,
        handler_output=result_text,
        failures=failures,
    )


async def validate_google_tools(
    tags_filter: tuple[str, ...] | None = None,
) -> list[dict[str, Any]]:
    """Run the full QA playbook and return structured results.

    This function can be called by the repair agent to validate
    Google tool routing after code changes.

    Args:
        tags_filter: If set, only run scenarios matching ALL given tags.

    Returns:
        List of result dicts with scenario_id, passed, failures, etc.
    """
    scenarios = PLAYBOOK
    if tags_filter:
        scenarios = [
            s for s in scenarios
            if all(tag in s.tags for tag in tags_filter)
        ]

    results = []
    for scenario in scenarios:
        result = await run_scenario(scenario)
        results.append(result.to_dict())

    passed = sum(1 for r in results if r["passed"])
    total = len(results)
    logger.info("Google Tools QA: %d/%d scenarios passed", passed, total)

    return results
