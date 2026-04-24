"""Tests for /tickets and /ticket command handlers and inline keyboard callbacks.

Covers:
- /tickets: list open tickets, empty state
- /ticket approve <id>: calls approve_ticket_deploy
- /ticket close <id>: marks ticket closed
- /ticket (no args): shows usage
- cb_repair_approve: inline button approve flow
- cb_repair_skip: inline button skip flow
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _make_message(text: str = "", user_id: int = 12345, is_owner: bool = True) -> MagicMock:
    """Build a minimal aiogram Message mock."""
    msg = MagicMock()
    msg.text = text
    msg.from_user = MagicMock()
    msg.from_user.id = user_id
    msg.answer = AsyncMock()
    return msg


def _make_callback(data: str, user_id: int = 12345) -> MagicMock:
    """Build a minimal aiogram CallbackQuery mock."""
    cb = MagicMock()
    cb.data = data
    cb.from_user = MagicMock()
    cb.from_user.id = user_id
    cb.answer = AsyncMock()
    cb.message = MagicMock()
    cb.message.edit_text = AsyncMock()
    cb.message.answer = AsyncMock()
    return cb


def _make_ticket(id: int, title: str, status: str, priority: str = "medium") -> MagicMock:
    from datetime import datetime, timezone
    t = MagicMock()
    t.id = id
    t.title = title
    t.status = status
    t.priority = priority
    t.created_at = datetime(2026, 4, 10, 12, 0, 0, tzinfo=timezone.utc)
    return t


class TestCmdTickets:
    @pytest.mark.asyncio
    async def test_lists_open_tickets(self) -> None:
        from src.bot.handlers import cmd_tickets

        tickets = [
            _make_ticket(1, "Email agent crash", "plan_ready"),
            _make_ticket(2, "Scheduler null pointer", "ready_for_deploy"),
        ]

        mock_user = MagicMock()
        mock_user.id = 99

        mock_session = AsyncMock()
        mock_user_result = MagicMock()
        mock_user_result.scalar_one_or_none.return_value = mock_user
        mock_tickets_result = MagicMock()
        mock_tickets_result.scalars.return_value.all.return_value = tickets

        def execute_side_effect(query):
            # First call = user lookup, second call = tickets query
            if not hasattr(execute_side_effect, "call_count"):
                execute_side_effect.call_count = 0
            execute_side_effect.call_count += 1
            if execute_side_effect.call_count == 1:
                return mock_user_result
            return mock_tickets_result

        mock_session.execute = AsyncMock(side_effect=execute_side_effect)

        msg = _make_message("/tickets", user_id=12345)

        with (
            patch("src.bot.handlers.is_allowed", new=AsyncMock(return_value=True)),
            patch("src.db.session.async_session") as mock_ctx,
        ):
            mock_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_ctx.return_value.__aexit__ = AsyncMock(return_value=False)
            await cmd_tickets(msg)

        msg.answer.assert_awaited_once()
        response = msg.answer.call_args.args[0]
        assert "#1" in response
        assert "Email agent crash" in response
        assert "#2" in response
        assert "Scheduler null pointer" in response

    @pytest.mark.asyncio
    async def test_shows_empty_state_when_no_tickets(self) -> None:
        from src.bot.handlers import cmd_tickets

        mock_user = MagicMock()
        mock_user.id = 99

        mock_session = AsyncMock()
        mock_user_result = MagicMock()
        mock_user_result.scalar_one_or_none.return_value = mock_user
        mock_empty_result = MagicMock()
        mock_empty_result.scalars.return_value.all.return_value = []

        call_count = {"n": 0}

        def execute_side_effect(query):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return mock_user_result
            return mock_empty_result

        mock_session.execute = AsyncMock(side_effect=execute_side_effect)
        msg = _make_message("/tickets", user_id=12345)

        with (
            patch("src.bot.handlers.is_allowed", new=AsyncMock(return_value=True)),
            patch("src.db.session.async_session") as mock_ctx,
        ):
            mock_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_ctx.return_value.__aexit__ = AsyncMock(return_value=False)
            await cmd_tickets(msg)

        msg.answer.assert_awaited_once()
        text = msg.answer.call_args.args[0]
        assert "no open repair tickets" in text.lower() or "clean" in text.lower()

    @pytest.mark.asyncio
    async def test_not_allowed_returns_early(self) -> None:
        from src.bot.handlers import cmd_tickets

        msg = _make_message("/tickets", user_id=99999)
        with patch("src.bot.handlers.is_allowed", new=AsyncMock(return_value=False)):
            await cmd_tickets(msg)

        msg.answer.assert_not_awaited()


class TestCmdTicket:
    @pytest.mark.asyncio
    async def test_shows_usage_when_no_args(self) -> None:
        from src.bot.handlers import cmd_ticket

        msg = _make_message("/ticket", user_id=12345)
        with (
            patch("src.bot.handlers.is_allowed", new=AsyncMock(return_value=True)),
            patch("src.bot.handlers.settings") as mock_settings,
        ):
            mock_settings.owner_telegram_id = 12345
            await cmd_ticket(msg)

        msg.answer.assert_awaited_once()
        text = msg.answer.call_args.args[0]
        assert "approve" in text.lower()
        assert "close" in text.lower()

    @pytest.mark.asyncio
    async def test_approve_calls_approve_ticket_deploy(self) -> None:
        from src.bot.handlers import cmd_ticket

        msg = _make_message("/ticket approve 5", user_id=12345)
        mock_deploy = AsyncMock(return_value="✅ Deploy completed.")

        with (
            patch("src.bot.handlers.is_allowed", new=AsyncMock(return_value=True)),
            patch("src.bot.handlers.settings") as mock_settings,
            patch("src.repair.engine.approve_ticket_deploy", mock_deploy),
        ):
            mock_settings.owner_telegram_id = 12345
            await cmd_ticket(msg)

        mock_deploy.assert_awaited_once_with(5, 12345)

    @pytest.mark.asyncio
    async def test_close_marks_ticket_closed(self) -> None:
        from src.bot.handlers import cmd_ticket

        mock_ticket = MagicMock()
        mock_ticket.status = "plan_ready"
        mock_session = AsyncMock()
        mock_session.get = AsyncMock(return_value=mock_ticket)

        msg = _make_message("/ticket close 3", user_id=12345)

        with (
            patch("src.bot.handlers.is_allowed", new=AsyncMock(return_value=True)),
            patch("src.bot.handlers.settings") as mock_settings,
            patch("src.db.session.async_session") as mock_ctx,
        ):
            mock_settings.owner_telegram_id = 12345
            mock_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_ctx.return_value.__aexit__ = AsyncMock(return_value=False)
            await cmd_ticket(msg)

        assert mock_ticket.status == "closed"
        mock_session.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_non_owner_blocked(self) -> None:
        from src.bot.handlers import cmd_ticket

        msg = _make_message("/ticket approve 1", user_id=99999)
        with (
            patch("src.bot.handlers.is_allowed", new=AsyncMock(return_value=True)),
            patch("src.bot.handlers.settings") as mock_settings,
        ):
            mock_settings.owner_telegram_id = 12345  # different from 99999
            await cmd_ticket(msg)

        msg.answer.assert_awaited_once()
        assert "owner" in msg.answer.call_args.args[0].lower()

    @pytest.mark.asyncio
    async def test_invalid_ticket_id_rejected(self) -> None:
        from src.bot.handlers import cmd_ticket

        msg = _make_message("/ticket approve abc", user_id=12345)
        with (
            patch("src.bot.handlers.is_allowed", new=AsyncMock(return_value=True)),
            patch("src.bot.handlers.settings") as mock_settings,
        ):
            mock_settings.owner_telegram_id = 12345
            await cmd_ticket(msg)

        msg.answer.assert_awaited_once()
        assert "invalid" in msg.answer.call_args.args[0].lower()


class TestCallbackRepairApprove:
    @pytest.mark.asyncio
    async def test_approve_calls_deploy(self) -> None:
        from src.bot.handlers import cb_repair_approve

        cb = _make_callback("repair_approve:7", user_id=12345)
        mock_deploy = AsyncMock(return_value="✅ Deploy completed.")

        with (
            patch("src.bot.handlers.is_allowed", new=AsyncMock(return_value=True)),
            patch("src.bot.handlers.settings") as mock_settings,
            patch("src.repair.engine.approve_ticket_deploy", mock_deploy),
        ):
            mock_settings.owner_telegram_id = 12345
            await cb_repair_approve(cb)

        mock_deploy.assert_awaited_once_with(7, 12345)
        cb.message.answer.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_non_owner_blocked(self) -> None:
        from src.bot.handlers import cb_repair_approve

        cb = _make_callback("repair_approve:7", user_id=99999)
        with (
            patch("src.bot.handlers.is_allowed", new=AsyncMock(return_value=True)),
            patch("src.bot.handlers.settings") as mock_settings,
        ):
            mock_settings.owner_telegram_id = 12345
            await cb_repair_approve(cb)

        cb.answer.assert_awaited_once()
        # text is positional in callback.answer("message", show_alert=True)
        call = cb.answer.call_args
        text = (call.args[0] if call.args else call.kwargs.get("text", "")).lower()
        assert "owner" in text

    @pytest.mark.asyncio
    async def test_invalid_id_shows_alert(self) -> None:
        from src.bot.handlers import cb_repair_approve

        cb = _make_callback("repair_approve:notanumber", user_id=12345)
        with (
            patch("src.bot.handlers.is_allowed", new=AsyncMock(return_value=True)),
            patch("src.bot.handlers.settings") as mock_settings,
        ):
            mock_settings.owner_telegram_id = 12345
            await cb_repair_approve(cb)

        cb.answer.assert_awaited_once()
        assert cb.answer.call_args.kwargs.get("show_alert") is True


class TestCallbackRepairSkip:
    @pytest.mark.asyncio
    async def test_skip_edits_message(self) -> None:
        from src.bot.handlers import cb_repair_skip

        cb = _make_callback("repair_skip:3", user_id=12345)
        with patch("src.bot.handlers.is_allowed", new=AsyncMock(return_value=True)):
            await cb_repair_skip(cb)

        cb.message.edit_text.assert_awaited_once()
        text = cb.message.edit_text.call_args.args[0]
        assert "#3" in text
        assert "approve" in text.lower() or "ready" in text.lower()

    @pytest.mark.asyncio
    async def test_not_allowed_returns_early(self) -> None:
        from src.bot.handlers import cb_repair_skip

        cb = _make_callback("repair_skip:3", user_id=99999)
        with patch("src.bot.handlers.is_allowed", new=AsyncMock(return_value=False)):
            await cb_repair_skip(cb)

        cb.message.edit_text.assert_not_awaited()
