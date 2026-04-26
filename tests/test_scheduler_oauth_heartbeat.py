"""Tests for the weekly OAuth heartbeat job.

Mocks workspace_mcp.call_workspace_tool and Redis. Covers the four primary
paths: ok, auth_failed, transient (rate limit / connection / generic tool
error), and exception-from-wrapper.
"""

from unittest.mock import AsyncMock, patch

import pytest

from src.scheduler.maintenance import (
    _classify_workspace_response,
    weekly_oauth_heartbeat,
)


class TestClassifier:
    """Pure-function — covers the bracketed-tag conventions from
    src/integrations/workspace_mcp.py."""

    def test_plain_text_is_ok(self):
        assert _classify_workspace_response(
            "Connected as alice@example.com (user)"
        ) == "ok"

    def test_auth_error_tag_is_auth_failed(self):
        assert _classify_workspace_response(
            "[AUTH ERROR] Google authorization expired or is missing for "
            "get_user_profile. Tell the user to run /connect google to "
            "re-authorize."
        ) == "auth_failed"

    def test_rate_limit_tag_is_transient(self):
        assert _classify_workspace_response(
            "[RATE LIMIT] get_user_profile could not complete after retries"
        ) == "transient"

    def test_connection_error_tag_is_transient(self):
        assert _classify_workspace_response(
            "[CONNECTION ERROR] Could not connect to the Google Workspace service"
        ) == "transient"

    def test_generic_tool_error_is_transient(self):
        # We don't promote ambiguous errors to auth_failed (would cause noisy
        # reauth prompts on flaky MCP runs).
        assert _classify_workspace_response(
            "[TOOL ERROR] something else broke"
        ) == "transient"

    def test_empty_response_is_transient(self):
        assert _classify_workspace_response("") == "transient"


class TestHeartbeatJob:
    async def test_explicit_user_ids_skip_redis_scan(self):
        with patch(
            "src.integrations.workspace_mcp.call_workspace_tool",
            AsyncMock(return_value="Connected as alice@example.com"),
        ):
            report = await weekly_oauth_heartbeat(user_ids=[1, 2, 3])
        assert report["users_checked"] == 3
        assert report["users_ok"] == 3
        assert report["users_auth_failed"] == 0
        assert report["users_transient"] == 0

    async def test_auth_failure_classified_correctly(self):
        with patch(
            "src.integrations.workspace_mcp.call_workspace_tool",
            AsyncMock(return_value="[AUTH ERROR] expired"),
        ), patch(
            "src.scheduler.maintenance._send_reauth_nudge",
            AsyncMock(return_value=True),
        ):
            report = await weekly_oauth_heartbeat(user_ids=[42])
        assert report["users_auth_failed"] == 1
        assert report["users_ok"] == 0
        assert report["users_nudged"] == 1
        assert report["details"][0]["status"] == "auth_failed"
        assert report["details"][0]["nudge_sent"] is True

    async def test_auth_failure_calls_telegram_nudge_with_correct_id(self):
        # Verify the nudge helper is invoked once per auth_failed user with
        # exactly the user's telegram ID (no off-by-one in iteration).
        nudge_mock = AsyncMock(return_value=True)
        with patch(
            "src.integrations.workspace_mcp.call_workspace_tool",
            AsyncMock(return_value="[AUTH ERROR] revoked"),
        ), patch(
            "src.scheduler.maintenance._send_reauth_nudge", nudge_mock,
        ):
            await weekly_oauth_heartbeat(user_ids=[777])
        nudge_mock.assert_awaited_once_with(777)

    async def test_auth_failure_with_failed_nudge_still_reports(self):
        # A failed nudge (returns False) should NOT be counted in users_nudged
        # but the user is still counted in users_auth_failed.
        with patch(
            "src.integrations.workspace_mcp.call_workspace_tool",
            AsyncMock(return_value="[AUTH ERROR] expired"),
        ), patch(
            "src.scheduler.maintenance._send_reauth_nudge",
            AsyncMock(return_value=False),
        ):
            report = await weekly_oauth_heartbeat(user_ids=[88])
        assert report["users_auth_failed"] == 1
        assert report["users_nudged"] == 0
        assert report["details"][0]["nudge_sent"] is False

    async def test_mixed_outcomes_per_user(self):
        # User 1 ok, user 2 auth_failed, user 3 transient.
        responses = {
            1: "Connected as alice@example.com",
            2: "[AUTH ERROR] revoked",
            3: "[CONNECTION ERROR] sidecar down",
        }
        # Track the next call's user via a mutable index so the AsyncMock returns
        # the right response for each invocation. We can't easily key off the
        # call args because they're tool_name + {} (no user-id param), so we
        # rely on call ordering.
        calls = iter([responses[1], responses[2], responses[3]])

        async def fake_call(tool, args):
            return next(calls)

        with patch(
            "src.integrations.workspace_mcp.call_workspace_tool", fake_call,
        ), patch(
            "src.scheduler.maintenance._send_reauth_nudge",
            AsyncMock(return_value=True),
        ):
            report = await weekly_oauth_heartbeat(user_ids=[1, 2, 3])

        assert report["users_checked"] == 3
        assert report["users_ok"] == 1
        assert report["users_auth_failed"] == 1
        assert report["users_transient"] == 1
        assert report["users_nudged"] == 1

    async def test_wrapper_raising_is_treated_as_transient(self):
        # The wrapper's contract is to return strings, not raise. If it does
        # raise, we shouldn't crash the batch — log and continue.
        with patch(
            "src.integrations.workspace_mcp.call_workspace_tool",
            AsyncMock(side_effect=RuntimeError("unexpected")),
        ):
            report = await weekly_oauth_heartbeat(user_ids=[55])
        assert report["users_transient"] == 1
        assert report["users_ok"] == 0
        assert "unexpected" in report["details"][0]["error"]

    async def test_empty_user_list_returns_clean_report(self):
        report = await weekly_oauth_heartbeat(user_ids=[])
        assert report["users_checked"] == 0
        assert report["users_ok"] == 0
        assert report["users_auth_failed"] == 0
        assert report["users_transient"] == 0
        assert report["users_nudged"] == 0
        assert report["details"] == []

    async def test_redis_scan_returns_telegram_ids(self, monkeypatch):
        # When user_ids isn't passed, the job scans Redis for
        # `google_email:{user_id}` keys.
        fake_redis = AsyncMock()

        async def fake_scan(match=None, count=None):
            for key in ["google_email:111", "google_email:222", "google_email:bad"]:
                yield key

        fake_redis.scan_iter = fake_scan
        monkeypatch.setattr(
            "src.memory.conversation.get_redis",
            AsyncMock(return_value=fake_redis),
        )

        with patch(
            "src.integrations.workspace_mcp.call_workspace_tool",
            AsyncMock(return_value="ok"),
        ):
            report = await weekly_oauth_heartbeat()

        # 'bad' isn't an int — silently skipped.
        assert report["users_checked"] == 2
        assert report["users_ok"] == 2

    async def test_redis_scan_failure_reports_error(self, monkeypatch):
        # If Redis itself is down, surface in the report rather than crash.
        monkeypatch.setattr(
            "src.memory.conversation.get_redis",
            AsyncMock(side_effect=RuntimeError("redis unreachable")),
        )
        report = await weekly_oauth_heartbeat()
        assert "error" in report
        assert "user_scan_failed" in report["error"]
        assert report["users_checked"] == 0
