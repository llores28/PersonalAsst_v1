"""Tests for the ``_typing_action`` context manager.

Telegram clears the typing indicator ~5s after each ``sendChatAction`` call,
which is shorter than every orchestration turn. Without this wrapper the
"typing..." dot disappears mid-turn and users think the bot is dead.

The wrapper is backed by ``aiogram.utils.chat_action.ChatActionSender``,
which spawns a background task that re-sends the action on a 5s loop.
These tests assert:

1. The action is sent multiple times during a long body — not just once.
2. If ``ChatActionSender`` is unavailable (or breaks at setup), the body
   still runs to completion and a one-shot fallback is sent.
3. Exceptions raised inside the body propagate and the sender shuts down
   cleanly (no leaked background task).
"""

from __future__ import annotations

import asyncio
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest

# Stub the agents SDK before importing handler_utils — the module pulls
# in a chain of imports that touch the real Agents SDK at definition time.
if "agents" not in sys.modules:
    fake_agents = MagicMock()
    fake_agents.Agent = MagicMock
    fake_agents.function_tool = lambda *a, **kw: (lambda f: f) if (a and not callable(a[0])) else (a[0] if a else (lambda f: f))
    fake_agents.Runner = MagicMock()
    fake_agents.WebSearchTool = MagicMock
    sys.modules["agents"] = fake_agents
    sys.modules["agents.mcp"] = MagicMock()
    sys.modules["agents.exceptions"] = MagicMock(
        InputGuardrailTripwireTriggered=type("InputGuardrailTripwireTriggered", (Exception,), {}),
        OutputGuardrailTripwireTriggered=type("OutputGuardrailTripwireTriggered", (Exception,), {}),
        MaxTurnsExceeded=type("MaxTurnsExceeded", (Exception,), {}),
    )


def _fake_message(chat_id: int = 12345):
    """Build a mock aiogram Message that records ``answer_chat_action`` calls
    and exposes a ``bot.send_chat_action`` (which is what ChatActionSender
    actually uses internally)."""
    bot = MagicMock()
    bot.send_chat_action = AsyncMock(return_value=True)
    msg = MagicMock()
    msg.bot = bot
    msg.chat = MagicMock()
    msg.chat.id = chat_id
    msg.from_user = MagicMock()
    msg.from_user.id = chat_id
    msg.answer_chat_action = AsyncMock(return_value=True)
    return msg, bot


# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_typing_action_sends_multiple_times_during_long_body() -> None:
    """``_typing_action`` should re-send the action on a short interval so
    Telegram never clears the typing dot mid-task. We patch the underlying
    ChatActionSender's interval down to 0.1s so the test stays fast."""
    from src.bot.handler_utils import _typing_action

    msg, bot = _fake_message()

    # Shrink the resend interval so the test finishes quickly. The default
    # is 4.5s — at that rate a 0.5s body would never trigger a re-send and
    # we couldn't tell the difference from the broken one-shot version.
    async with _typing_action(msg, action="typing", interval=0.1):
        await asyncio.sleep(0.5)

    # ChatActionSender uses bot.send_chat_action under the hood. With a
    # 0.1s interval and a 0.5s body we expect at LEAST 3 calls (initial +
    # multiple re-sends). If only 1 fires, we've regressed to the broken
    # one-shot path — that's the bug the user reported.
    assert bot.send_chat_action.await_count >= 3, (
        f"Expected at least 3 typing actions during a 0.5s body with 0.1s "
        f"interval, got {bot.send_chat_action.await_count}. Re-sending isn't "
        f"working — the typing dot will clear mid-turn for real users."
    )


@pytest.mark.asyncio
async def test_typing_action_falls_back_when_sender_unavailable(monkeypatch) -> None:
    """If aiogram's ChatActionSender can't be set up (import failure,
    missing bot ref, etc.), the body must still run and a one-shot
    ``answer_chat_action`` should fire so the user gets *some* feedback."""
    from src.bot import handler_utils

    msg, bot = _fake_message()

    # Force the import inside ``_typing_action`` to raise. Patch the
    # already-imported module so the local ``from ... import`` fails.
    import builtins

    real_import = builtins.__import__

    def _broken_import(name, *args, **kwargs):
        if name == "aiogram.utils.chat_action":
            raise ImportError("simulated: ChatActionSender unavailable")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _broken_import)

    body_ran = False
    async with handler_utils._typing_action(msg, action="typing"):
        body_ran = True
        await asyncio.sleep(0.05)

    assert body_ran, "Body must execute even when ChatActionSender import fails"
    # The fallback path should have fired exactly one one-shot
    # ``answer_chat_action`` so the user briefly sees the dot.
    assert msg.answer_chat_action.await_count == 1, (
        f"Fallback should have sent exactly one chat action, "
        f"got {msg.answer_chat_action.await_count}"
    )


@pytest.mark.asyncio
async def test_typing_action_propagates_body_exceptions() -> None:
    """If the wrapped work raises, the exception must bubble out of the
    context manager and the underlying sender should shut down cleanly
    (no leaked background tasks across test runs)."""
    from src.bot.handler_utils import _typing_action

    msg, _bot = _fake_message()

    class _Boom(RuntimeError):
        pass

    with pytest.raises(_Boom):
        async with _typing_action(msg, action="typing"):
            raise _Boom("simulated orchestrator failure")

    # If a background task leaked, ``asyncio.all_tasks()`` would still
    # contain it. Give the loop a tick to let cleanup settle, then assert
    # nothing related to ChatActionSender is still running.
    await asyncio.sleep(0.05)
    leaked = [
        t for t in asyncio.all_tasks()
        if "ChatActionSender" in (getattr(t.get_coro(), "__qualname__", "") or "")
    ]
    assert not leaked, f"ChatActionSender background task leaked: {leaked}"
