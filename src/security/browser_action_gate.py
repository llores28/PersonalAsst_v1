"""Approval gate for autonomous browser actions.

The browser-use skill (src/skills/browser.py) drives a real Chromium in
the assistant container. Read-only actions (navigate, scroll, scrape,
screenshot) run autonomously. Anything that mutates remote state — form
submit, click apply, complete checkout, post message — is blocked by
this gate until the owner approves it via a Telegram inline button.

Why Redis instead of the OrgApprovalGate table:
    The browser skill lives at the assistant level (no org context).
    OrgApprovalGate.org_id is NOT NULL with a CASCADE FK, so an org-less
    browser approval can't write there. Redis is also a better fit for
    this short-lived, per-request, ephemeral request/response — no DB
    churn, instant pub/sub via BLPOP, naturally expires.

The pattern:
    1. browser-use is about to perform a write action
    2. We call `request_browser_approval(user_id, summary, context)`:
        - Generates a short request_id
        - RPUSHes a "pending" marker for the dashboard if you want to surface
          live (we just store an audit copy in audit_log instead)
        - Sends a Telegram message with two callback-data buttons
        - BLPOPs `browser_approval:{user_id}:{request_id}` (timeout from settings)
    3. The user clicks; the callback handler in src/bot/handlers.py
       RPUSHes "approve" or "reject" into the same key
    4. We unblock and return True/False
    5. Timeout → ApprovalTimeout
"""
from __future__ import annotations

import json
import logging
import secrets
from typing import Any, Optional

logger = logging.getLogger(__name__)


# Browser-use action verbs that mutate remote state. Any other action name
# (e.g. `go_to_url`, `extract_content`, `screenshot`, `scroll_down`,
# `read_text`) is treated as a read and runs without a gate.
WRITE_ACTION_VERBS = frozenset({
    # Form-fill + submit family
    "click_submit",
    "submit_form",
    "fill_then_submit",
    # Buttons that take real-world action on the remote site
    "click_apply",
    "click_purchase",
    "click_checkout",
    "click_send",
    "click_post",
    "click_pay",
    "click_buy",
    "click_book",
    "click_subscribe",
    # Generic write verbs browser-use sometimes emits
    "send_message",
    "post_message",
    "purchase",
    "checkout",
    "place_order",
    # Account changes
    "create_account",
    "delete_account",
    "change_password",
    "update_profile",
})


# Substrings that, if found in a click target's accessible label, mark a
# normally-ambiguous "click" as a write. Matches case-insensitively.
WRITE_LABEL_HINTS = (
    "submit", "apply", "purchase", "checkout", "send", "post", "pay",
    "buy", "book", "subscribe", "delete", "confirm", "place order",
)


class ApprovalDenied(Exception):
    """User clicked Reject on a browser approval prompt."""


class ApprovalTimeout(Exception):
    """No decision arrived within browser_use_total_timeout_seconds."""


def is_write_action(action_name: str, action_args: Optional[dict] = None) -> bool:
    """Decide whether a browser-use action requires approval.

    True if either:
      - The action verb itself is in WRITE_ACTION_VERBS, OR
      - It's a click (or similar interaction) whose accessible label
        contains a write hint substring (Apply, Submit, etc.).

    Read actions (`go_to_url`, `extract_content`, `screenshot`, `scroll_*`,
    `read_*`) are gate-free.
    """
    name = (action_name or "").strip().lower()
    if name in WRITE_ACTION_VERBS:
        return True
    if name.startswith("click") or name in {"press_key", "press"}:
        # Inspect the click target label/text if present
        label_blob = ""
        if isinstance(action_args, dict):
            for key in ("label", "text", "name", "selector", "target", "element_text"):
                v = action_args.get(key)
                if isinstance(v, str):
                    label_blob += " " + v.lower()
        if label_blob and any(hint in label_blob for hint in WRITE_LABEL_HINTS):
            return True
    return False


async def request_browser_approval(
    user_telegram_id: int,
    action_name: str,
    summary: str,
    context: Optional[dict] = None,
) -> bool:
    """Block until the user approves or rejects the action via Telegram.

    Returns:
        True if approved, raises ApprovalDenied / ApprovalTimeout otherwise.

    Never returns False — callers can rely on a True-or-raise contract so
    the browser-use action chain can `await gate(...)` linearly.
    """
    from src.memory.conversation import get_redis
    from src.settings import settings

    request_id = secrets.token_urlsafe(12)
    key = f"browser_approval:{user_telegram_id}:{request_id}"

    redis = await get_redis()
    # Set a sentinel + TTL so we can also let the dashboard list pending
    # browser approvals if it wants to. The actual decision flows via
    # BLPOP on the same key.
    sentinel_key = f"browser_approval_pending:{user_telegram_id}:{request_id}"
    await redis.set(
        sentinel_key,
        json.dumps({
            "action": action_name,
            "summary": summary[:500],
            "context": context or {},
        }),
        ex=settings.browser_use_total_timeout_seconds,
    )

    try:
        await _send_browser_approval_prompt(
            user_telegram_id=user_telegram_id,
            request_id=request_id,
            action_name=action_name,
            summary=summary,
        )
    except Exception as exc:
        logger.warning("Could not send browser-approval prompt: %s", exc)
        # Without a way to notify the user, fail closed — never auto-approve.
        raise ApprovalDenied(f"Could not deliver approval prompt: {exc}") from exc

    timeout = settings.browser_use_total_timeout_seconds
    logger.info(
        "browser-approval requested user=%d req=%s action=%s timeout=%ds",
        user_telegram_id, request_id, action_name, timeout,
    )
    try:
        result = await redis.blpop(key, timeout=timeout)
    except Exception as exc:
        logger.warning("Redis BLPOP for browser-approval failed: %s", exc)
        raise ApprovalDenied(f"approval channel error: {exc}") from exc
    finally:
        # Best-effort cleanup of the sentinel
        try:
            await redis.delete(sentinel_key)
        except Exception:
            pass

    if result is None:
        logger.warning("browser-approval timeout user=%d req=%s", user_telegram_id, request_id)
        raise ApprovalTimeout(f"No response within {timeout}s")

    # blpop returns (key, value); value is bytes when decode_responses=False,
    # str when True. Atlas's get_redis() already configures decode_responses=True.
    _key, decision = result
    decision = decision if isinstance(decision, str) else decision.decode("utf-8", errors="ignore")
    decision = decision.strip().lower()

    if decision == "approve":
        logger.info("browser-approval APPROVED user=%d req=%s", user_telegram_id, request_id)
        return True
    logger.info("browser-approval REJECTED user=%d req=%s decision=%s",
                user_telegram_id, request_id, decision)
    raise ApprovalDenied(f"User rejected: {decision}")


async def gate(
    user_telegram_id: int,
    action_name: str,
    action_args: Optional[dict] = None,
    summary: Optional[str] = None,
) -> bool:
    """The single entry point the BrowserRunner calls before each action.

    Args:
        user_telegram_id: Owner's Telegram ID — receives the approval prompt.
        action_name: browser-use's action verb (e.g. `click_submit`, `go_to_url`).
        action_args: The action's keyword args; used to inspect click labels.
        summary: Human-readable description of what's about to happen.
            If omitted, falls back to a generic phrasing built from the
            action name + args.

    Returns:
        True for approved/read actions. Raises for rejected/timeout/errors.
    """
    if not is_write_action(action_name, action_args):
        return True

    if not summary:
        summary = f"Browser action `{action_name}` with args {action_args!r}"

    return await request_browser_approval(
        user_telegram_id=user_telegram_id,
        action_name=action_name,
        summary=summary,
        context={"args": action_args or {}},
    )


async def _send_browser_approval_prompt(
    *,
    user_telegram_id: int,
    request_id: str,
    action_name: str,
    summary: str,
) -> None:
    """Telegram message with two inline buttons keyed to the request_id."""
    from aiogram import Bot
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    from src.settings import settings

    text = (
        "🤖 *Browser approval needed*\n\n"
        f"*Action:* `{action_name}`\n"
        f"*Why:* {summary[:400]}\n\n"
        "Reply within the timeout window or the action will be cancelled."
    )
    keyboard = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(
            text="✅ Approve",
            callback_data=f"browse_approve:{request_id}",
        ),
        InlineKeyboardButton(
            text="❌ Reject",
            callback_data=f"browse_reject:{request_id}",
        ),
    ]])
    bot = Bot(token=settings.telegram_bot_token)
    try:
        await bot.send_message(
            chat_id=user_telegram_id,
            text=text,
            parse_mode="Markdown",
            reply_markup=keyboard,
        )
    finally:
        await bot.session.close()


async def record_browser_decision(
    user_telegram_id: int,
    request_id: str,
    decision: str,
) -> None:
    """Called by the Telegram callback handler when the user clicks
    approve/reject. RPUSHes the decision into the BLPOP key the gate is
    waiting on.

    Args:
        decision: "approve" or "reject".
    """
    from src.memory.conversation import get_redis
    from src.settings import settings

    redis = await get_redis()
    key = f"browser_approval:{user_telegram_id}:{request_id}"
    await redis.rpush(key, decision.strip().lower())
    # TTL so the key cleans itself up if the gate had already timed out
    await redis.expire(key, max(60, settings.browser_use_total_timeout_seconds))
