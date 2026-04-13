"""Resilience tests for Telegram handler error paths."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

pytest.importorskip("aiogram", reason="aiogram is not installed locally")


@pytest.mark.asyncio
async def test_handle_persona_interview_logs_and_continues_when_session_set_fails() -> None:
    from src.bot.handlers import _handle_persona_interview

    message = SimpleNamespace(
        from_user=SimpleNamespace(id=12345),
        answer=AsyncMock(),
    )

    with (
        patch(
            "src.agents.persona_interview_agent.get_interview_state",
            new=AsyncMock(return_value={"all_complete": False, "current_session": 1}),
        ),
        patch(
            "src.agents.persona_interview_agent.handle_interview_message",
            new=AsyncMock(return_value="Question 1"),
        ),
        patch(
            "src.memory.conversation.set_session_field",
            new=AsyncMock(side_effect=RuntimeError("redis unavailable")),
        ),
        patch("src.bot.handlers.logger.warning") as mock_warning,
    ):
        await _handle_persona_interview(message, user=None, args=["/persona", "interview"])

    message.answer.assert_awaited_once_with("Question 1", parse_mode="Markdown")
    mock_warning.assert_called_once()
