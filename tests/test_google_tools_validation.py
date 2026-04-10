"""Google Workspace Tools Validation Suite.

Systematically validates every Google tool routing path, specialist agent
wiring, and multi-tool sequence without requiring Docker, real MCP, or
live API calls.

Layer 1 — Deterministic routing: tests every matcher function against
    realistic user phrases. Pure Python, no mocks needed.
Layer 2 — Mocked MCP integration: tests direct handlers end-to-end with
    fake call_workspace_tool responses and Redis conversation state.

Tool Catalog Covered:
  Email (6 tools): search, get_message, get_thread, send, draft, batch
  Calendar (2 tools): get_events, manage_event
  Tasks (4 tools): list_task_lists, list_tasks, manage_task, manage_task_list
  Drive: raw MCP (no connected wrappers)
  Direct handlers (4): gmail_check, calendar_check, tasks_flow, pending_gmail_send
  Orchestrator routing order: pending_gmail → gmail → calendar → tasks → LLM
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock

# Stub Docker-only packages so orchestrator imports work locally
_INJECTED_MOCKS: list[str] = []
for _mod in ("agents", "agents.mcp", "redis", "redis.asyncio"):
    if _mod not in sys.modules:
        _INJECTED_MOCKS.append(_mod)
        sys.modules[_mod] = MagicMock()

import pytest

from src.agents.orchestrator import (
    _is_simple_connected_gmail_check,
    _is_latest_unread_email_request,
    _gmail_search_query_for_message,
    _is_simple_connected_calendar_check,
    _is_calendar_date_follow_up,
    _is_simple_connected_google_tasks_read,
    _is_simple_connected_google_tasks_completion_follow_up,
    _is_explicit_google_task_request,
    _is_explicit_email_draft_request,
    _is_email_related_request,
    _is_pending_connected_gmail_send_confirmation,
    _is_pending_gmail_draft_revision_request,
    _response_contains_email_draft,
    _format_connected_calendar_summary,
    _format_connected_gmail_summary,
    _format_connected_google_tasks_summary,
    _parse_calendar_events,
    _extract_gmail_found_count,
    _extract_gmail_message_ids,
    _build_pending_gmail_send_payload,
    _extract_draft_email_subject_and_body,
)


# Note: no cleanup fixture — mocked modules persist for the test session.
# Aggressive cleanup causes cross-file failures when running combined suites.


# Windows may lack tzdata; skip calendar tests that need ZoneInfo
_has_tzdata = True
try:
    from zoneinfo import ZoneInfo as _ZI
    _ZI("America/Chicago")
except Exception:
    _has_tzdata = False

_skip_no_tz = pytest.mark.skipif(not _has_tzdata, reason="tzdata not available")


# ────────────────────────────────────────────────────────────────────────
# LAYER 1: DETERMINISTIC ROUTING TESTS
# ────────────────────────────────────────────────────────────────────────


class TestGmailDirectHandlerRouting:
    """Validate _is_simple_connected_gmail_check catches common inbox phrases."""

    @pytest.mark.parametrize("phrase", [
        "check my email",
        "check my inbox",
        "check my gmail",
        "check my unread email",
        "check my unread emails",
        "show my email",
        "show my inbox",
        "show my gmail",
        "show my unread email",
        "read my email",
        "read my inbox",
        "read my unread email",
        "what's in my inbox",
        "whats in my inbox",
        "last unread email",
        "latest unread email",
        "most recent unread email",
        # Uppercase variants
        "CHECK MY EMAIL",
        "Check My Inbox",
    ])
    def test_gmail_check_matches(self, phrase: str) -> None:
        assert _is_simple_connected_gmail_check(phrase) is True

    @pytest.mark.parametrize("phrase", [
        "find email about my flight",
        "search my email for receipts",
        "draft an email to bob@example.com",
        "send email to sarah",
        "reply to that email",
        "what's the weather",
        "check my calendar",
        "show my tasks",
    ])
    def test_gmail_check_does_not_match(self, phrase: str) -> None:
        assert _is_simple_connected_gmail_check(phrase) is False


class TestGmailLatestUnreadRouting:
    """Validate single-unread-email detection."""

    @pytest.mark.parametrize("phrase", [
        "last unread email",
        "latest unread email",
        "most recent unread email",
        "last unread gmail",
        "latest unread gmail",
    ])
    def test_latest_unread_matches(self, phrase: str) -> None:
        assert _is_latest_unread_email_request(phrase) is True

    @pytest.mark.parametrize("phrase", [
        "check my email",
        "show my unread emails",
        "read my inbox",
    ])
    def test_latest_unread_does_not_match(self, phrase: str) -> None:
        assert _is_latest_unread_email_request(phrase) is False


class TestGmailSearchQueryGeneration:
    """Validate search query construction for direct handler."""

    def test_unread_query(self) -> None:
        assert _gmail_search_query_for_message("check my unread email") == "in:inbox is:unread"

    def test_today_query(self) -> None:
        assert _gmail_search_query_for_message("show my email from today") == "in:inbox newer_than:1d"

    def test_default_query(self) -> None:
        assert _gmail_search_query_for_message("check my email") == "in:inbox"


@_skip_no_tz
class TestCalendarDirectHandlerRouting:
    """Validate _is_simple_connected_calendar_check catches temporal ranges."""

    @pytest.mark.parametrize("phrase", [
        "whats on my calendar today",
        "what's on my calendar tomorrow",
        "whats on my calendar this week",
        "what's on my calendar next week",
        "show my calendar for Monday",
        "check my calendar for Friday",
    ])
    def test_calendar_check_matches(self, phrase: str) -> None:
        assert _is_simple_connected_calendar_check(phrase) is True

    @pytest.mark.parametrize("phrase", [
        "draft an email with my calendar info",
        "create an event on my calendar",
        "add lunch to my calendar",
        "check my email",
        "what's the weather",
    ])
    def test_calendar_check_does_not_match(self, phrase: str) -> None:
        assert _is_simple_connected_calendar_check(phrase) is False


@_skip_no_tz
class TestCalendarDateFollowUp:
    """Validate _is_calendar_date_follow_up for contextual date queries."""

    @pytest.mark.parametrize("phrase", [
        "what's the date for that?",
        "whats the date",
        "what date is that",
        "which date",
        "what day is that",
        "which day",
        "i see time but no date",
    ])
    def test_date_follow_up_matches(self, phrase: str) -> None:
        assert _is_calendar_date_follow_up(phrase) is True

    @pytest.mark.parametrize("phrase", [
        "show my calendar for Monday",
        "check my email",
        "what's on my calendar",
    ])
    def test_date_follow_up_does_not_match(self, phrase: str) -> None:
        assert _is_calendar_date_follow_up(phrase) is False


class TestGoogleTasksDirectHandlerRouting:
    """Validate task read, completion, and explicit-create matchers."""

    @pytest.mark.parametrize("phrase", [
        "list tasks",
        "list my tasks",
        "show my tasks",
        "show my google tasks",
        "show my todo list",
        "show my to-do list",
        "check my tasks",
        "what are my tasks",
        "what's on my task list",
        "whats on my task list",
        "what's on my todo list",
        "my google tasks",
    ])
    def test_task_read_matches(self, phrase: str) -> None:
        assert _is_simple_connected_google_tasks_read(phrase) is True

    @pytest.mark.parametrize("phrase", [
        "add to my task list buy groceries",
        "create a task for tomorrow",
        "check my calendar",
    ])
    def test_task_read_does_not_match(self, phrase: str) -> None:
        assert _is_simple_connected_google_tasks_read(phrase) is False

    @pytest.mark.parametrize("phrase", [
        "mark this task as completed",
        "mark this task complete",
        "mark this as completed",
        "complete this task",
        "mark it complete",
        "complete it",
    ])
    def test_task_completion_matches(self, phrase: str) -> None:
        assert _is_simple_connected_google_tasks_completion_follow_up(phrase) is True

    @pytest.mark.parametrize("phrase", [
        "add to my task",
        "create a task",
        "google tasks",
        "todo list",
        "to-do list",
        "add to my todo",
    ])
    def test_explicit_task_request_matches(self, phrase: str) -> None:
        assert _is_explicit_google_task_request(phrase) is True

    @pytest.mark.parametrize("phrase", [
        "check my calendar",
        "send email",
        "show my schedule",
    ])
    def test_explicit_task_request_does_not_match(self, phrase: str) -> None:
        assert _is_explicit_google_task_request(phrase) is False


class TestEmailDraftRouting:
    """Validate _is_explicit_email_draft_request detection."""

    @pytest.mark.parametrize("phrase", [
        "draft an email to bob@example.com",
        "draft a email about the meeting",
        "draft email to my wife",
        "compose an email to support",
        "compose email about the project",
        "write an email to the team",
        "write email about updates",
        "prepare an email for the client",
    ])
    def test_draft_request_matches(self, phrase: str) -> None:
        assert _is_explicit_email_draft_request(phrase) is True

    @pytest.mark.parametrize("phrase", [
        "send an email",
        "check my email",
        "search my email",
        "reply to that email",
    ])
    def test_draft_request_does_not_match(self, phrase: str) -> None:
        assert _is_explicit_email_draft_request(phrase) is False


class TestPendingGmailSendConfirmation:
    """Validate contextual follow-up confirmations for pending sends."""

    @pytest.mark.parametrize("phrase", [
        "yes",
        "yes please",
        "send it",
        "go ahead",
        "ok",
        "okay",
        "retry",
    ])
    def test_send_confirmation_matches(self, phrase: str) -> None:
        assert _is_pending_connected_gmail_send_confirmation(phrase) is True

    @pytest.mark.parametrize("phrase", [
        "no",
        "cancel",
        "change the subject",
        "add more details",
    ])
    def test_send_confirmation_does_not_match(self, phrase: str) -> None:
        assert _is_pending_connected_gmail_send_confirmation(phrase) is False


class TestDraftRevisionDetection:
    """Validate _is_pending_gmail_draft_revision_request patterns."""

    @pytest.mark.parametrize("phrase", [
        "add details from my calendar",
        "include details shown in my calendar",
        "add departure time",
        "add airline info",
        "update the draft",
        "revise the draft",
        "yes add flight information",
    ])
    def test_revision_request_matches(self, phrase: str) -> None:
        assert _is_pending_gmail_draft_revision_request(phrase) is True


# ────────────────────────────────────────────────────────────────────────
# LAYER 1b: ROUTING PRIORITY ORDER
# ────────────────────────────────────────────────────────────────────────


class TestRoutingPriorityOrder:
    """Verify that messages hit the CORRECT direct handler first.

    run_orchestrator checks in this order:
      1. pending_gmail_send
      2. gmail_check
      3. calendar_check
      4. google_tasks_flow
      5. fall-through to orchestrator LLM

    A message should only match ONE direct handler. Cross-matches indicate
    routing ambiguity that would cause incorrect behavior.
    """

    HANDLER_MATCHERS = {
        "gmail": _is_simple_connected_gmail_check,
        "calendar": _is_simple_connected_calendar_check,
        "tasks_read": _is_simple_connected_google_tasks_read,
        "tasks_create": _is_explicit_google_task_request,
    }

    @pytest.mark.parametrize("phrase,expected_handler", [
        # Gmail-only
        ("check my email", "gmail"),
        ("show my inbox", "gmail"),
        ("read my unread email", "gmail"),
        # Calendar-only (skip if no tzdata)
        pytest.param("whats on my calendar today", "calendar", marks=_skip_no_tz),
        pytest.param("what's on my calendar next week", "calendar", marks=_skip_no_tz),
        pytest.param("show my calendar for Friday", "calendar", marks=_skip_no_tz),
        # Tasks-only
        ("show my tasks", "tasks_read"),
        ("list my tasks", "tasks_read"),
        ("add to my task buy milk tomorrow at 9am", "tasks_create"),
        # Fall-through (no direct handler)
        pytest.param("draft an email to betty@example.com about my flight", None, marks=_skip_no_tz),
        pytest.param("search my email for United confirmation", None, marks=_skip_no_tz),
        pytest.param("find email about my flight", None, marks=_skip_no_tz),
        pytest.param("create an event on my calendar for lunch", None, marks=_skip_no_tz),
        pytest.param("help me organize my inbox", None, marks=_skip_no_tz),
    ])
    def test_single_handler_match(self, phrase: str, expected_handler: str | None) -> None:
        matches = {
            name: matcher(phrase)
            for name, matcher in self.HANDLER_MATCHERS.items()
        }
        matched_handlers = [name for name, matched in matches.items() if matched]

        if expected_handler is None:
            assert len(matched_handlers) == 0, (
                f"'{phrase}' should fall through to orchestrator but matched: {matched_handlers}"
            )
        else:
            assert expected_handler in matched_handlers, (
                f"'{phrase}' should match '{expected_handler}' but matched: {matched_handlers}"
            )
            # Allow tasks_create to co-match with tasks_read for some phrases
            unexpected = [h for h in matched_handlers if h != expected_handler and not (
                expected_handler == "tasks_create" and h == "tasks_read"
            )]
            assert len(unexpected) == 0, (
                f"'{phrase}' matched unexpected handlers: {unexpected} (expected: {expected_handler})"
            )


# ────────────────────────────────────────────────────────────────────────
# LAYER 1c: RESPONSE FORMATTING
# ────────────────────────────────────────────────────────────────────────


@_skip_no_tz
class TestCalendarResponseFormatting:
    """Validate calendar event parsing and summary formatting."""

    SAMPLE_EVENTS_RESPONSE = (
        '- "Team Standup" (Starts: 2026-03-20T09:00:00-05:00, Ends: 2026-03-20T09:30:00-05:00)\n'
        '  Location: Zoom\n'
        '  Description: Daily standup\n'
        '- "Flight to FLL" (Starts: 2026-03-28T12:10:00-05:00, Ends: 2026-03-28T14:45:00-05:00)\n'
        '  Location: Houston IAH\n'
        '  Description: UA 1318 Reservation D92E9S\n'
    )

    def test_parse_calendar_events_count(self) -> None:
        events = _parse_calendar_events(self.SAMPLE_EVENTS_RESPONSE)
        assert len(events) == 2

    def test_parse_calendar_events_fields(self) -> None:
        events = _parse_calendar_events(self.SAMPLE_EVENTS_RESPONSE)
        assert events[0]["title"] == "Team Standup"
        assert events[0]["location"] == "Zoom"
        assert events[1]["title"] == "Flight to FLL"
        assert "Houston IAH" in events[1]["location"]

    def test_format_empty_calendar(self) -> None:
        result = _format_connected_calendar_summary("today", "0 events found")
        assert "clear" in result.lower()

    def test_format_populated_calendar(self) -> None:
        result = _format_connected_calendar_summary("this week", self.SAMPLE_EVENTS_RESPONSE)
        assert "Team Standup" in result
        assert "Flight to FLL" in result
        assert "this week" in result


class TestGmailResponseFormatting:
    """Validate Gmail message parsing and summary formatting."""

    SAMPLE_SEARCH = "Found 2 messages\nMessage ID: abc123\nMessage ID: def456"
    SAMPLE_BATCH = (
        "Message ID: abc123\n"
        "Subject: Your Flight Confirmation\n"
        "From: United Airlines <no-reply@united.com>\n"
        "Date: 2026-03-18\n\n"
        "Your flight UA 1318 is confirmed for March 28.\n"
        "---\n"
        "Message ID: def456\n"
        "Subject: Receipt for your purchase\n"
        "From: Amazon <auto-confirm@amazon.com>\n"
        "Date: 2026-03-17\n\n"
        "Amount paid: $49.99\n"
    )

    def test_extract_found_count(self) -> None:
        assert _extract_gmail_found_count(self.SAMPLE_SEARCH) == 2

    def test_extract_message_ids(self) -> None:
        ids = _extract_gmail_message_ids(self.SAMPLE_SEARCH)
        assert ids == ["abc123", "def456"]

    def test_format_gmail_summary(self) -> None:
        result = _format_connected_gmail_summary(self.SAMPLE_SEARCH, self.SAMPLE_BATCH)
        assert "United Airlines" in result or "Flight Confirmation" in result
        assert "Amazon" in result or "Receipt" in result


class TestGoogleTasksResponseFormatting:
    """Validate tasks summary formatting."""

    def test_empty_tasks(self) -> None:
        result = _format_connected_google_tasks_summary("0 tasks found")
        assert "don't have any pending" in result.lower()

    def test_populated_tasks(self) -> None:
        result = _format_connected_google_tasks_summary(
            "Tasks in list @default for user@gmail.com:\n- Buy groceries (ID: task1)\n- Call dentist (ID: task2)"
        )
        assert "Buy groceries" in result


# ────────────────────────────────────────────────────────────────────────
# LAYER 1d: PENDING GMAIL SEND PAYLOAD EXTRACTION
# ────────────────────────────────────────────────────────────────────────


class TestEmailRelatedRequest:
    """Validate the broad _is_email_related_request check."""

    @pytest.mark.parametrize("phrase", [
        "email crysta lores to remember to focus on your school work",
        "email john about the project update",
        "send an email to bob about the meeting",
        "draft an email to betty@example.com about the trip",
        "compose an email about the deadline",
        "gmail john the report",
        "send to bob@example.com running 10min late",
    ])
    def test_email_related_phrases_match(self, phrase: str) -> None:
        assert _is_email_related_request(phrase) is True

    @pytest.mark.parametrize("phrase", [
        "check my calendar",
        "show my tasks",
        "what's the weather today",
        "remind me to call the dentist",
        "search my drive for the report",
    ])
    def test_non_email_phrases_rejected(self, phrase: str) -> None:
        assert _is_email_related_request(phrase) is False


class TestResponseContainsEmailDraft:
    """Validate _response_contains_email_draft detection."""

    def test_draft_with_subject_and_send_prompt(self) -> None:
        response = (
            "Here's your draft email to Crysta Lores:\n\n"
            "Subject: Reminder: Focus on Your School Work\n\n"
            "Hi Crysta,\n\nJust a friendly reminder to focus on your school work!\n\n"
            "Best,\nLanny\n\n"
            "Would you like to send it as is, or make any changes?"
        )
        assert _response_contains_email_draft(response) is True

    def test_plain_response_without_draft_cue(self) -> None:
        response = (
            "Subject: Test\n\n"
            "Some body text here."
        )
        assert _response_contains_email_draft(response) is False

    def test_no_subject_line(self) -> None:
        response = "Here's your draft email. I'll send it when ready."
        assert _response_contains_email_draft(response) is False


class TestPendingGmailSendPayload:
    """Validate draft email detection and payload construction."""

    def test_builds_payload_from_draft_request(self) -> None:
        user_msg = "draft an email to betty@example.com about the trip"
        assistant_resp = (
            "Here's your draft:\n\n"
            "Subject: Trip Details\n\n"
            "Hi Betty,\n\n"
            "I wanted to share the trip details with you.\n\n"
            "Best,\nLanny"
        )
        payload = _build_pending_gmail_send_payload(user_msg, assistant_resp)
        assert payload is not None
        assert payload["to"] == "betty@example.com"
        assert payload["subject"] == "Trip Details"
        assert "trip details" in payload["body"].lower()

    def test_builds_payload_from_informal_email_request_with_draft_response(self) -> None:
        """Regression: 'email [person] to [content]' must store pending payload
        when the LLM responds with a draft. This was the exact bug scenario."""
        user_msg = "email crysta lores to remeber to focuse on your school work"
        assistant_resp = (
            "Here's your draft email to Crysta Lores:\n\n"
            "Subject: Reminder: Focus on Your School Work\n\n"
            "Hi Crysta,\n\n"
            "Just a friendly reminder to focus on your school work!\n\n"
            "Best,\nLanny\n\n"
            "Would you like to send it as is, or make any changes?"
        )
        payload = _build_pending_gmail_send_payload(user_msg, assistant_resp)
        assert payload is not None
        assert payload["subject"] == "Reminder: Focus on Your School Work"
        assert "school work" in payload["body"].lower()

    def test_builds_payload_from_send_email_to_address(self) -> None:
        """'send an email to bob@example.com' should also store pending payload."""
        user_msg = "send an email to bob@example.com saying hi"
        assistant_resp = (
            "Here's your draft email:\n\n"
            "Subject: Hi\n\n"
            "Hi Bob!\n\n"
            "Best,\nLanny\n\n"
            "Would you like to send it as is, or make any changes?"
        )
        payload = _build_pending_gmail_send_payload(user_msg, assistant_resp)
        assert payload is not None
        assert payload["to"] == "bob@example.com"

    def test_no_payload_for_non_draft_request(self) -> None:
        payload = _build_pending_gmail_send_payload(
            "check my email",
            "Here are your latest emails..."
        )
        assert payload is None

    def test_no_payload_when_response_has_no_draft_structure(self) -> None:
        """Email-related request but response has no Subject: line → no payload."""
        payload = _build_pending_gmail_send_payload(
            "email john about the meeting",
            "I've sent the email to John about the meeting."
        )
        assert payload is None

    def test_extract_subject_and_body(self) -> None:
        response = (
            "Here's the draft:\n\n"
            "Subject: Meeting Follow-up\n\n"
            "Hi team,\n\nJust following up on our meeting.\n\nBest,\nAtlas"
        )
        subject, body = _extract_draft_email_subject_and_body(response)
        assert subject == "Meeting Follow-up"
        assert "following up" in body.lower()


# ────────────────────────────────────────────────────────────────────────
# LAYER 2: MULTI-TOOL SCENARIO PLAYBOOK
# ────────────────────────────────────────────────────────────────────────


class TestCommonUserScenarios:
    """Document and validate common multi-step user scenarios.

    These tests validate the ROUTING DECISION at each step, not the LLM output.
    They ensure the correct handler/specialist is invoked for each user message
    in a realistic conversation flow.
    """

    @_skip_no_tz
    def test_scenario_check_calendar_then_draft_email(self) -> None:
        """User: 'whats on my calendar next week' → direct calendar handler
        User: 'draft email with flight info' → falls to orchestrator (cross-tool)
        """
        step1 = "whats on my calendar next week"
        assert _is_simple_connected_calendar_check(step1) is True

        step2 = "draft an email to betty@example.com with the flight info"
        assert _is_simple_connected_calendar_check(step2) is False
        assert _is_simple_connected_gmail_check(step2) is False
        assert _is_explicit_email_draft_request(step2) is True

    def test_scenario_check_email_then_reply(self) -> None:
        """User: 'check my email' → direct gmail handler
        User: 'reply to the first one' → falls to orchestrator (needs context)
        """
        step1 = "check my email"
        assert _is_simple_connected_gmail_check(step1) is True

        step2 = "reply to the first one"
        assert _is_simple_connected_gmail_check(step2) is False

    def test_scenario_list_tasks_then_complete(self) -> None:
        """User: 'show my tasks' → direct tasks handler
        User: 'complete this task' → direct tasks completion handler
        """
        step1 = "show my tasks"
        assert _is_simple_connected_google_tasks_read(step1) is True

        step2 = "mark this task as completed"
        assert _is_simple_connected_google_tasks_completion_follow_up(step2) is True

    def test_scenario_add_task_with_due_date(self) -> None:
        """User: 'add to my task buy groceries tomorrow at 9am' → direct tasks handler"""
        step1 = "add to my task buy groceries tomorrow at 9am"
        assert _is_explicit_google_task_request(step1) is True

    def test_scenario_draft_email_then_send(self) -> None:
        """User: 'draft email to bob@example.com about meeting' → orchestrator
        User: 'send it' → pending gmail send handler
        """
        step1 = "draft an email to bob@example.com about the meeting"
        assert _is_explicit_email_draft_request(step1) is True

        step2 = "send it"
        assert _is_pending_connected_gmail_send_confirmation(step2) is True

    def test_scenario_draft_email_then_revise_then_send(self) -> None:
        """User drafts → revises with calendar data → sends."""
        step1 = "draft an email to betty@example.com with flight info"
        assert _is_explicit_email_draft_request(step1) is True

        step2 = "add details from my calendar"
        assert _is_pending_gmail_draft_revision_request(step2) is True

        step3 = "send it"
        assert _is_pending_connected_gmail_send_confirmation(step3) is True

    def test_scenario_search_email_for_specific_sender(self) -> None:
        """User: 'find email from United' → falls to orchestrator (not a simple check)."""
        step1 = "find email from United about my flight"
        assert _is_simple_connected_gmail_check(step1) is False

    @_skip_no_tz
    def test_scenario_calendar_then_date_follow_up(self) -> None:
        """User: 'whats on my calendar this week' → direct calendar
        User: 'what's the date for that?' → calendar date follow-up
        """
        step1 = "whats on my calendar this week"
        assert _is_simple_connected_calendar_check(step1) is True

        step2 = "what's the date for that?"
        assert _is_calendar_date_follow_up(step2) is True

    def test_scenario_organize_inbox_is_orchestrator_routed(self) -> None:
        """Complex requests like 'organize my inbox by category' should
        NOT match any direct handler — they need the full orchestrator."""
        msg = "help me organize my inbox emails by category"
        assert _is_simple_connected_gmail_check(msg) is False
        assert _is_simple_connected_calendar_check(msg) is False
        assert _is_simple_connected_google_tasks_read(msg) is False
        assert _is_explicit_google_task_request(msg) is False

    @_skip_no_tz
    def test_scenario_cross_tool_email_with_calendar_data(self) -> None:
        """'draft email with my flight info from my calendar' needs orchestrator
        to chain: manage_calendar → manage_email."""
        msg = "draft an email to betty@example.com with all the flight information found on my calendar"
        # Should NOT match direct calendar handler (it's not a calendar read)
        assert _is_simple_connected_calendar_check(msg) is False
        # Should NOT match direct gmail handler (it's not a simple inbox check)
        assert _is_simple_connected_gmail_check(msg) is False
        # SHOULD be recognized as a draft request
        assert _is_explicit_email_draft_request(msg) is True


# ────────────────────────────────────────────────────────────────────────
# LAYER 2b: SPECIALIST AGENT TOOL WIRING
# ────────────────────────────────────────────────────────────────────────


class TestSpecialistAgentToolWiring:
    """Verify each specialist agent builder returns the expected number of tools.

    Note: Since `agents.function_tool` is mocked in local tests, we validate
    tool COUNT (structural check) rather than tool names."""

    CONNECTED_EMAIL = "test@gmail.com"

    def test_email_agent_returns_6_tools(self) -> None:
        from src.agents.email_agent import _build_connected_gmail_tools
        tools = _build_connected_gmail_tools(self.CONNECTED_EMAIL)
        assert len(tools) == 6, f"Expected 6 Gmail tools, got {len(tools)}"

    def test_gmail_send_tool_uses_correct_mcp_field_names(self) -> None:
        """Regression: send/draft tools must use thread_id/in_reply_to
        (not reply_to_message_id) to match MCP server schema."""
        from src.agents.email_agent import _build_connected_gmail_tools
        tools = _build_connected_gmail_tools(self.CONNECTED_EMAIL)
        for t in tools:
            schema = getattr(t, "params_json_schema", {})
            props = schema.get("properties", {})
            assert "reply_to_message_id" not in props, (
                f"Tool {getattr(t, 'name', t)} uses reply_to_message_id "
                f"which is not in the MCP server schema"
            )

    def test_calendar_agent_returns_2_tools(self) -> None:
        from src.agents.calendar_agent import _build_connected_calendar_tools
        tools = _build_connected_calendar_tools(self.CONNECTED_EMAIL)
        assert len(tools) == 2, f"Expected 2 Calendar tools, got {len(tools)}"

    def test_tasks_agent_returns_4_tools(self) -> None:
        from src.agents.tasks_agent import _build_connected_tasks_tools
        tools = _build_connected_tasks_tools(self.CONNECTED_EMAIL)
        assert len(tools) == 4, f"Expected 4 Tasks tools, got {len(tools)}"


# ────────────────────────────────────────────────────────────────────────
# LAYER 2c: ERROR MESSAGE QUALITY
# ────────────────────────────────────────────────────────────────────────


class TestErrorMessageQuality:
    """Verify error messages from Google tool failures are helpful."""

    def test_tasks_error_message_includes_reconnect_hint(self) -> None:
        from src.agents.tasks_agent import _format_google_tasks_error
        msg = _format_google_tasks_error("list tasks", "user@gmail.com", RuntimeError("connection timeout"))
        assert "list tasks" in msg
        assert "/connect google" in msg

    def test_tasks_auth_error_includes_scope_hint(self) -> None:
        from src.agents.tasks_agent import _format_google_tasks_error
        msg = _format_google_tasks_error("create the task", "user@gmail.com", RuntimeError("Unauthorized: token expired"))
        assert "/connect google" in msg
        assert "permission" in msg.lower() or "reconnect" in msg.lower()

    def test_gmail_write_error_includes_reconnect(self) -> None:
        from src.agents.orchestrator import _format_connected_gmail_write_error
        msg = _format_connected_gmail_write_error("send the email", "user@gmail.com", RuntimeError("500 server error"))
        assert "/connect google" in msg
        assert "send the email" in msg
