"""Tests for repair pipeline notification helpers.

Covers:
- notify_owner_of_error (Telegram push on error detection)
- notify_ticket_created (Telegram push on ticket creation)
- notify_fix_ready (Telegram inline keyboard on fix-ready)
- notify_low_risk_applied (Telegram push after auto-apply)
- send_ticket_created_email (email on ticket creation)
- send_fix_ready_email (email when fix is verified)
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ── Bot notification helpers ─────────────────────────────────────────────

class TestNotifyOwnerOfError:
    @pytest.mark.asyncio
    async def test_sends_message_with_error_summary(self) -> None:
        from src.bot.notifications import notify_owner_of_error

        mock_bot = AsyncMock()
        mock_bot.session = AsyncMock()
        with patch("src.bot.notifications._get_bot", return_value=mock_bot):
            await notify_owner_of_error(
                user_telegram_id=12345,
                error_summary="Tool call failed: invalid API key",
                user_message="list my emails",
            )

        mock_bot.send_message.assert_awaited_once()
        call_kwargs = mock_bot.send_message.call_args.kwargs
        assert call_kwargs["chat_id"] == 12345
        assert "error" in call_kwargs["text"].lower() or "atlas detected" in call_kwargs["text"].lower()
        assert "fix it" in call_kwargs["text"].lower()

    @pytest.mark.asyncio
    async def test_swallows_exception_silently(self) -> None:
        from src.bot.notifications import notify_owner_of_error

        with patch("src.bot.notifications._get_bot", side_effect=RuntimeError("no bot")):
            # Should not raise
            await notify_owner_of_error(
                user_telegram_id=12345,
                error_summary="Something broke",
            )


class TestNotifyTicketCreated:
    @pytest.mark.asyncio
    async def test_sends_message_with_ticket_info(self) -> None:
        from src.bot.notifications import notify_ticket_created

        mock_bot = AsyncMock()
        mock_bot.session = AsyncMock()
        with patch("src.bot.notifications._get_bot", return_value=mock_bot):
            await notify_ticket_created(
                user_telegram_id=12345,
                ticket_id=42,
                title="Email agent null pointer",
                status="debug_analysis_ready",
                confidence=0.85,
            )

        mock_bot.send_message.assert_awaited_once()
        text = mock_bot.send_message.call_args.kwargs["text"]
        assert "#42" in text
        assert "Email agent null pointer" in text
        assert "85%" in text

    @pytest.mark.asyncio
    async def test_swallows_exception_silently(self) -> None:
        from src.bot.notifications import notify_ticket_created

        with patch("src.bot.notifications._get_bot", side_effect=RuntimeError("no bot")):
            await notify_ticket_created(12345, 1, "test", "open")


class TestNotifyFixReady:
    @pytest.mark.asyncio
    async def test_sends_message_with_inline_keyboard(self) -> None:
        from src.bot.notifications import notify_fix_ready

        mock_bot = AsyncMock()
        mock_bot.session = AsyncMock()
        with patch("src.bot.notifications._get_bot", return_value=mock_bot):
            await notify_fix_ready(
                user_telegram_id=12345,
                ticket_id=7,
                title="Fix null check in email agent",
                affected_files=["src/agents/email_agent.py"],
                branch_name="repair/abc123",
            )

        mock_bot.send_message.assert_awaited_once()
        call_kwargs = mock_bot.send_message.call_args.kwargs
        assert call_kwargs["chat_id"] == 12345
        assert "#7" in call_kwargs["text"]
        # Inline keyboard should have approve + skip buttons
        keyboard = call_kwargs["reply_markup"]
        assert keyboard is not None
        buttons = keyboard.inline_keyboard[0]
        cb_data = {btn.callback_data for btn in buttons}
        assert "repair_approve:7" in cb_data
        assert "repair_skip:7" in cb_data

    @pytest.mark.asyncio
    async def test_swallows_exception_silently(self) -> None:
        from src.bot.notifications import notify_fix_ready

        with patch("src.bot.notifications._get_bot", side_effect=RuntimeError("no bot")):
            await notify_fix_ready(12345, 1, "test", [], "")


class TestNotifyLowRiskApplied:
    @pytest.mark.asyncio
    async def test_sends_message_with_result(self) -> None:
        from src.bot.notifications import notify_low_risk_applied

        mock_bot = AsyncMock()
        mock_bot.session = AsyncMock()
        with patch("src.bot.notifications._get_bot", return_value=mock_bot):
            await notify_low_risk_applied(
                user_telegram_id=12345,
                title="Clear stale Redis key",
                result_summary="✅ Cleared Redis key `session:abc`",
            )

        mock_bot.send_message.assert_awaited_once()
        text = mock_bot.send_message.call_args.kwargs["text"]
        assert "Clear stale Redis key" in text
        assert "Cleared Redis key" in text


# ── Email notification helpers ───────────────────────────────────────────

class TestSendTicketCreatedEmail:
    @pytest.mark.asyncio
    async def test_calls_workspace_tool_with_correct_subject(self) -> None:
        from src.repair.notifications import send_ticket_created_email

        mock_call = AsyncMock(return_value="OK")
        with patch("src.repair.notifications._send_via_workspace", mock_call):
            await send_ticket_created_email(
                ticket_id=5,
                title="Null pointer in scheduler",
                status="debug_analysis_ready",
                error_summary="NullPointerException in scheduler.engine",
                affected_files=["src/scheduler/engine.py"],
                confidence=0.9,
            )

        mock_call.assert_awaited_once()
        subject, body = mock_call.call_args.args
        assert "Ticket #5" in subject
        assert "Null pointer in scheduler" in subject
        assert "NullPointerException" in body
        assert "src/scheduler/engine.py" in body
        assert "90%" in body

    @pytest.mark.asyncio
    async def test_handles_workspace_failure_gracefully(self) -> None:
        from src.repair.notifications import send_ticket_created_email

        with patch("src.repair.notifications._send_via_workspace", AsyncMock(return_value=False)):
            # Should not raise
            await send_ticket_created_email(
                ticket_id=1,
                title="Test",
                status="open",
                error_summary="test",
                affected_files=[],
            )


class TestSendFixReadyEmail:
    @pytest.mark.asyncio
    async def test_calls_workspace_tool_with_correct_subject(self) -> None:
        from src.repair.notifications import send_fix_ready_email

        mock_call = AsyncMock(return_value="OK")
        with patch("src.repair.notifications._send_via_workspace", mock_call):
            await send_fix_ready_email(
                ticket_id=9,
                title="Fix the scheduler null pointer",
                affected_files=["src/scheduler/engine.py"],
                branch_name="repair/abc123",
                verification_summary="pytest → exit 0",
            )

        mock_call.assert_awaited_once()
        subject, body = mock_call.call_args.args
        assert "Fix Ready" in subject
        assert "Ticket #9" in subject
        assert "repair/abc123" in body
        assert "pytest → exit 0" in body
        assert "/ticket approve 9" in body

    @pytest.mark.asyncio
    async def test_handles_workspace_failure_gracefully(self) -> None:
        from src.repair.notifications import send_fix_ready_email

        with patch("src.repair.notifications._send_via_workspace", AsyncMock(return_value=False)):
            await send_fix_ready_email(ticket_id=1, title="Test", affected_files=[])


# ── Pipeline retry guard ─────────────────────────────────────────────────

class TestPipelineRetryGuard:
    @pytest.mark.asyncio
    async def test_blocks_after_max_retries(self) -> None:
        from src.repair import engine

        engine._PIPELINE_ATTEMPT_COUNTS.clear()

        error_desc = "test_blocks_after_max_retries unique error"
        fingerprint = f"99:{error_desc[:80]}"

        # Pre-load to max
        engine._PIPELINE_ATTEMPT_COUNTS[fingerprint] = engine._PIPELINE_MAX_ATTEMPTS

        result = await engine.run_self_healing_pipeline(
            user_telegram_id=99,
            error_description=error_desc,
        )

        assert result["success"] is False
        assert result["decision"] == "MAX_RETRIES_EXCEEDED"
        assert "paused" in result["message"].lower() or "blocked" in result["message"].lower()

    @pytest.mark.asyncio
    async def test_allows_first_attempt(self) -> None:
        from src.repair import engine
        from unittest.mock import patch, AsyncMock

        engine._PIPELINE_ATTEMPT_COUNTS.clear()

        # Mock out the debugger so the pipeline doesn't actually run LLM
        mock_analysis = MagicMock()
        mock_analysis.confidence_score = 0.1  # low enough to abort early cleanly
        mock_analysis.error_summary = "test error"

        with patch("src.agents.debugger_agent.run_debugger_analysis", new=AsyncMock(return_value=mock_analysis)):
            result = await engine.run_self_healing_pipeline(
                user_telegram_id=100,
                error_description="unique error for first attempt test",
            )

        # Should NOT be blocked — may fail for other reasons but not MAX_RETRIES
        assert result.get("decision") != "MAX_RETRIES_EXCEEDED"
