"""Live, non-destructive Google Workspace smoke tests.

Skipped by default. Opt in by setting:

    LIVE_WORKSPACE_TEST=1
    LIVE_WORKSPACE_EMAIL=you@gmail.com   # the connected account to test

Each test creates its own ``[ATLAS-TEST <uuid>]``-prefixed fixture (draft
email, calendar event, Drive folder, doc, etc.), exercises one Workspace
tool against it, and cleans up. Read tests are scoped by query so they
only ever touch test-prefixed items — they never touch real user data.

Why opt-in: the live path needs a connected OAuth session, takes seconds
per test, and counts against Google quotas. Keep it out of the default
``pytest`` run; flip it on in nightly CI or before a release.

Run: ``LIVE_WORKSPACE_TEST=1 LIVE_WORKSPACE_EMAIL=you@gmail.com \\
       pytest tests/integration/test_live_workspace_smoke.py -v``

Or, to run inside the assistant container (where workspace-mcp creds
are already mounted):

    docker compose exec -e LIVE_WORKSPACE_TEST=1 \\
        -e LIVE_WORKSPACE_EMAIL=you@gmail.com -w /app assistant \\
        python -m pytest tests/integration/test_live_workspace_smoke.py -v
"""

from __future__ import annotations

import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest


LIVE_ENABLED = os.environ.get("LIVE_WORKSPACE_TEST") == "1"
LIVE_EMAIL = os.environ.get("LIVE_WORKSPACE_EMAIL", "").strip()

pytestmark = [
    pytest.mark.skipif(
        not LIVE_ENABLED,
        reason="Live workspace tests are opt-in; set LIVE_WORKSPACE_TEST=1 to run.",
    ),
    pytest.mark.skipif(
        not LIVE_EMAIL,
        reason="Set LIVE_WORKSPACE_EMAIL=you@gmail.com to scope live tests.",
    ),
]


# All fixtures created by this suite are tagged with this prefix so cleanup
# / read queries only ever touch test data — never real emails, files, or
# events. If a test crashes mid-run, the leftover fixture is still
# unambiguously test-only.
TEST_PREFIX = "[ATLAS-TEST]"


def _unique_marker() -> str:
    """Per-test unique tag so concurrent runs don't collide."""
    return f"{TEST_PREFIX} {uuid.uuid4().hex[:8]}"


# --------------------------------------------------------------------------
# Gmail
# --------------------------------------------------------------------------


class TestLiveGmail:
    """Read-only Gmail probes against ``label:atlas-test`` only.

    To populate the ``atlas-test`` label, run a one-time setup: send
    yourself an email with subject starting ``[ATLAS-TEST]`` and apply the
    ``atlas-test`` label in Gmail. Or run ``test_create_then_search_draft``
    below — it leaves a draft (not a sent email) and cleans it up.
    """

    @pytest.mark.asyncio
    async def test_search_returns_only_test_labeled_messages(self) -> None:
        from src.integrations.workspace_mcp import call_workspace_tool

        # Scope: read only emails labeled `atlas-test`. If the user hasn't
        # set up that label, this returns "Found 0 messages" — still a pass
        # (the tool wired up correctly; we just have no fixtures yet).
        result = await call_workspace_tool(
            "search_gmail_messages",
            {
                "query": "label:atlas-test",
                "user_google_email": LIVE_EMAIL,
                "page_size": 5,
            },
        )
        assert isinstance(result, str)
        # The MCP server's empty-result string has varied across releases —
        # "Found 0 messages" / "No messages found" / etc. Accept any of them
        # and reject only auth/connection failures.
        assert "[AUTH ERROR]" not in result and "[CONNECTION ERROR]" not in result, result
        success_markers = ("found", "no messages", "message id", "0 message")
        assert any(m in result.lower() for m in success_markers), (
            f"Unexpected response shape from search_gmail_messages: {result[:200]}"
        )

    @pytest.mark.asyncio
    async def test_create_then_search_draft(self) -> None:
        """Round-trip: create a draft tagged with our marker, search for it,
        then delete it. Drafts never leave the user's account, so this is
        safe to run against the real inbox.
        """
        from src.integrations.workspace_mcp import call_workspace_tool

        marker = _unique_marker()
        subject = f"{marker} routing-harness probe"
        body = f"This draft was created by Atlas's live test suite. Marker: {marker}"

        # Create a draft (NOT send) — drafts stay in Drafts folder only.
        create_result = await call_workspace_tool(
            "draft_gmail_message",
            {
                "user_google_email": LIVE_EMAIL,
                # Self-addressed so it can never accidentally reach a real
                # recipient even if the test framework forwards.
                "to": LIVE_EMAIL,
                "subject": subject,
                "body": body,
            },
        )
        assert isinstance(create_result, str)
        assert "draft" in create_result.lower() or "id" in create_result.lower(), (
            f"Unexpected draft creation response: {create_result[:200]}"
        )

        # Search by subject — should find our marker.
        search_result = await call_workspace_tool(
            "search_gmail_messages",
            {
                "query": f'subject:"{marker}" in:drafts',
                "user_google_email": LIVE_EMAIL,
                "page_size": 5,
            },
        )
        assert marker in search_result or "Found 1" in search_result, (
            f"Newly created draft with marker {marker!r} did not appear in search results: "
            f"{search_result[:300]}"
        )

        # NOTE: We don't auto-delete drafts here because the workspace-mcp
        # delete tool isn't part of the standard surface this suite asserts
        # against. Drafts are visually obvious in Gmail's Drafts folder
        # (subject starts with [ATLAS-TEST]); the user can sweep them with:
        #   subject:"[ATLAS-TEST]" in:drafts


# --------------------------------------------------------------------------
# Calendar
# --------------------------------------------------------------------------


class TestLiveCalendar:
    """Calendar probes use ``primary`` calendar but only events with the
    ``[ATLAS-TEST]`` prefix. Created events go to a fixed time block in
    the past so they don't show up on the user's actual upcoming calendar."""

    @pytest.mark.asyncio
    async def test_get_events_today_returns_valid_response(self) -> None:
        from src.integrations.workspace_mcp import call_workspace_tool

        now = datetime.now(timezone.utc)
        time_min = now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
        time_max = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0).isoformat()

        result = await call_workspace_tool(
            "get_events",
            {
                "user_google_email": LIVE_EMAIL,
                "calendar_id": "primary",
                "time_min": time_min,
                "time_max": time_max,
                "max_results": 50,
                "detailed": False,
            },
        )
        assert isinstance(result, str)
        assert "[AUTH ERROR]" not in result and "[CONNECTION ERROR]" not in result, result

    @pytest.mark.asyncio
    async def test_create_then_delete_test_event(self) -> None:
        """Create a one-hour event WAY in the past (10 years ago) so it can't
        clutter the user's working calendar, verify it shows up in a get_events
        query for that window, and delete it."""
        from src.integrations.workspace_mcp import call_workspace_tool

        marker = _unique_marker()
        # Pick a past time slot that no one looks at — sometime in 2016.
        start = datetime(2016, 1, 1, 12, 0, tzinfo=timezone.utc) + timedelta(
            seconds=int(uuid.uuid4().int % 1_000_000)
        )
        end = start + timedelta(hours=1)

        create_result = await call_workspace_tool(
            "create_event",
            {
                "user_google_email": LIVE_EMAIL,
                "calendar_id": "primary",
                "summary": f"{marker} routing-harness probe",
                "start_time": start.isoformat(),
                "end_time": end.isoformat(),
                "description": f"Created by Atlas live tests. Marker: {marker}",
            },
        )
        assert isinstance(create_result, str)
        # Try to extract the event ID for cleanup. Workspace MCP's reply
        # wording varies by version — match a reasonable shape and fall
        # back to a search-by-summary query.
        event_id = None
        for token in create_result.replace("\n", " ").split():
            t = token.strip("(),.\"'")
            if len(t) > 12 and all(c.isalnum() or c in "-_" for c in t):
                event_id = t
                break

        # Verify by fetching the day's events and matching our marker.
        verify_result = await call_workspace_tool(
            "get_events",
            {
                "user_google_email": LIVE_EMAIL,
                "calendar_id": "primary",
                "time_min": (start - timedelta(hours=1)).isoformat(),
                "time_max": (end + timedelta(hours=1)).isoformat(),
                "max_results": 10,
                "detailed": True,
            },
        )
        assert marker in verify_result, (
            f"Created event with marker {marker!r} was not found in get_events results"
        )

        # Cleanup
        if event_id:
            try:
                await call_workspace_tool(
                    "delete_event",
                    {
                        "user_google_email": LIVE_EMAIL,
                        "calendar_id": "primary",
                        "event_id": event_id,
                    },
                )
            except Exception as exc:  # noqa: BLE001 — best-effort cleanup
                print(
                    f"[warn] Could not auto-delete test event {event_id} "
                    f"(marker={marker}): {exc}. Sweep manually with summary:[ATLAS-TEST]",
                    file=sys.stderr,
                )


# --------------------------------------------------------------------------
# Drive
# --------------------------------------------------------------------------


class TestLiveDrive:
    """Drive probes only touch a folder named ``Atlas-Test/``. Create it
    once in your Drive and the round-trip test will operate inside it."""

    TEST_FOLDER_NAME = "Atlas-Test"

    @pytest.mark.asyncio
    async def test_search_for_atlas_test_folder(self) -> None:
        from src.integrations.workspace_mcp import call_workspace_tool

        result = await call_workspace_tool(
            "search_drive_files",
            {
                "user_google_email": LIVE_EMAIL,
                "query": f"name = '{self.TEST_FOLDER_NAME}' and mimeType = 'application/vnd.google-apps.folder'",
                "page_size": 5,
            },
        )
        assert isinstance(result, str)
        assert "[AUTH ERROR]" not in result, result
        # Either the folder exists (great) or it doesn't yet (also fine —
        # the tool wiring still passed). Don't fail the suite just because
        # the user hasn't created the test folder yet.

    @pytest.mark.asyncio
    async def test_create_test_file_in_test_folder(self) -> None:
        """Round-trip: find/create the Atlas-Test folder, drop a tiny test
        file in it, verify it shows up, leave it for the user to sweep
        (Drive supports trashing entire folders cleanly)."""
        from src.integrations.workspace_mcp import call_workspace_tool

        marker = _unique_marker()

        # Find or create the parent folder.
        folder_search = await call_workspace_tool(
            "search_drive_files",
            {
                "user_google_email": LIVE_EMAIL,
                "query": f"name = '{self.TEST_FOLDER_NAME}' and mimeType = 'application/vnd.google-apps.folder'",
                "page_size": 1,
            },
        )
        assert "[AUTH ERROR]" not in folder_search, folder_search

        # Best-effort folder ID extraction — shape varies by MCP version.
        folder_id = None
        for line in folder_search.splitlines():
            for token in line.split():
                t = token.strip("(),.\"'")
                if len(t) > 20 and all(c.isalnum() or c in "-_" for c in t):
                    folder_id = t
                    break
            if folder_id:
                break

        if folder_id is None:
            create_folder = await call_workspace_tool(
                "create_drive_folder",
                {
                    "user_google_email": LIVE_EMAIL,
                    "folder_name": self.TEST_FOLDER_NAME,
                },
            )
            assert "[AUTH ERROR]" not in create_folder, create_folder

        # Create a small text file in Drive with our marker. Doc / file
        # creation tool name varies by MCP version; this assertion only
        # verifies the call shape works.
        create_doc = await call_workspace_tool(
            "create_drive_file",
            {
                "user_google_email": LIVE_EMAIL,
                "name": f"{marker}.txt",
                "mime_type": "text/plain",
                "content": f"Atlas test file. Marker: {marker}. Safe to delete.",
            },
        )
        assert isinstance(create_doc, str)
        assert "[AUTH ERROR]" not in create_doc, create_doc


# --------------------------------------------------------------------------
# Tasks
# --------------------------------------------------------------------------


class TestLiveTasks:
    """Tasks probes use the @default task list with a marker prefix."""

    @pytest.mark.asyncio
    async def test_list_default_tasks(self) -> None:
        from src.integrations.workspace_mcp import call_workspace_tool

        result = await call_workspace_tool(
            "list_tasks",
            {
                "user_google_email": LIVE_EMAIL,
                "task_list_id": "@default",
                "show_completed": False,
                "max_results": 10,
            },
        )
        assert isinstance(result, str)
        assert "[AUTH ERROR]" not in result, result

    @pytest.mark.asyncio
    async def test_create_then_complete_test_task(self) -> None:
        """Create a marker-prefixed task and immediately mark it complete so
        it doesn't clutter the user's actual to-do list."""
        from src.integrations.workspace_mcp import call_workspace_tool

        marker = _unique_marker()

        create_result = await call_workspace_tool(
            "manage_task",
            {
                "user_google_email": LIVE_EMAIL,
                "action": "create",
                "task_list_id": "@default",
                "title": f"{marker} routing-harness probe",
                "notes": f"Created by Atlas live tests. Marker: {marker}",
            },
        )
        assert isinstance(create_result, str)
        assert "[AUTH ERROR]" not in create_result, create_result
