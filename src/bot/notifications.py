"""Proactive Telegram push notifications for the repair pipeline.

Provides fire-and-forget helpers to alert the owner when:
- A tool/agent error is detected (prompt to say 'fix it')
- A repair ticket has been created (ticket # + summary)
- A sandbox-verified fix is ready for deploy approval

All functions are safe to call from any async context.
Errors are swallowed with a warning log — notifications must never break the main flow.
"""

from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)


def _get_bot():
    """Create a short-lived Bot instance for sending a notification."""
    from aiogram import Bot
    from src.settings import settings
    return Bot(token=settings.telegram_bot_token)


async def notify_owner_of_error(
    user_telegram_id: int,
    error_summary: str,
    user_message: str = "",
) -> None:
    """Push a Telegram message to the owner when a tool error is detected.

    Args:
        user_telegram_id: Telegram ID to send the notification to.
        error_summary: Short description of what went wrong.
        user_message: The original user request that triggered the error.
    """
    try:
        context = f"\n*Your request:* _{user_message[:120]}_" if user_message else ""
        text = (
            "⚠️ *Atlas detected an error*\n\n"
            f"{error_summary[:300]}"
            f"{context}\n\n"
            "Say *'fix it'* to start automatic diagnosis and repair."
        )
        bot = _get_bot()
        try:
            await bot.send_message(
                chat_id=user_telegram_id,
                text=text,
                parse_mode="Markdown",
            )
        finally:
            await bot.session.close()
    except Exception as exc:
        logger.warning("notify_owner_of_error failed: %s", exc)


async def notify_ticket_created(
    user_telegram_id: int,
    ticket_id: int,
    title: str,
    status: str,
    confidence: float = 0.0,
) -> None:
    """Push a Telegram message when a repair ticket is created.

    Args:
        user_telegram_id: Telegram ID to notify.
        ticket_id: The newly created ticket ID.
        title: Ticket title.
        status: Initial ticket status (open / debug_analysis_ready).
        confidence: Debugger confidence score (0.0–1.0).
    """
    try:
        conf_pct = f"{confidence:.0%}" if confidence > 0 else "pending"
        text = (
            f"🎫 *Repair Ticket #{ticket_id} Created*\n\n"
            f"*Title:* {title[:200]}\n"
            f"*Status:* `{status}`\n"
            f"*Confidence:* {conf_pct}\n\n"
            "Use /tickets to view all open tickets."
        )
        bot = _get_bot()
        try:
            await bot.send_message(
                chat_id=user_telegram_id,
                text=text,
                parse_mode="Markdown",
            )
        finally:
            await bot.session.close()
    except Exception as exc:
        logger.warning("notify_ticket_created failed: %s", exc)


async def notify_fix_ready(
    user_telegram_id: int,
    ticket_id: int,
    title: str,
    affected_files: list[str],
    branch_name: str = "",
) -> None:
    """Push a Telegram message with an inline 'Apply fix now?' button.

    Called after sandbox verification passes and the fix is ready_for_deploy.

    Args:
        user_telegram_id: Telegram ID to notify.
        ticket_id: Repair ticket ID.
        title: Ticket title / fix description.
        affected_files: Files modified by the patch.
        branch_name: Git branch the patch lives on.
    """
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

    try:
        files_str = ", ".join(f"`{f}`" for f in affected_files[:5]) or "unknown"
        branch_info = f"\n*Branch:* `{branch_name}`" if branch_name else ""
        text = (
            f"✅ *Fix Ready — Ticket #{ticket_id}*\n\n"
            f"*{title[:200]}*\n"
            f"*Files:* {files_str}"
            f"{branch_info}\n\n"
            "All sandbox tests passed. Do you want to apply this fix now?"
        )
        keyboard = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(
                text="✅ Apply fix now",
                callback_data=f"repair_approve:{ticket_id}",
            ),
            InlineKeyboardButton(
                text="❌ Skip for now",
                callback_data=f"repair_skip:{ticket_id}",
            ),
        ]])
        bot = _get_bot()
        try:
            await bot.send_message(
                chat_id=user_telegram_id,
                text=text,
                parse_mode="Markdown",
                reply_markup=keyboard,
            )
        finally:
            await bot.session.close()
    except Exception as exc:
        logger.warning("notify_fix_ready failed: %s", exc)


async def notify_low_risk_applied(
    user_telegram_id: int,
    title: str,
    result_summary: str,
) -> None:
    """Push a Telegram message after a low-risk fix is auto-applied.

    Args:
        user_telegram_id: Telegram ID to notify.
        title: Fix title.
        result_summary: What was done.
    """
    try:
        text = (
            f"🔧 *Low-risk fix auto-applied*\n\n"
            f"*{title[:200]}*\n\n"
            f"{result_summary[:500]}"
        )
        bot = _get_bot()
        try:
            await bot.send_message(
                chat_id=user_telegram_id,
                text=text,
                parse_mode="Markdown",
            )
        finally:
            await bot.session.close()
    except Exception as exc:
        logger.warning("notify_low_risk_applied failed: %s", exc)


# Default dedup TTL: just under the weekly heartbeat cadence so a stuck user
# gets re-nudged each Monday but never twice within the same week.
_OAUTH_REAUTH_DEDUP_TTL = 6 * 86400  # 6 days


async def notify_oauth_reauth_required(
    user_telegram_id: int,
    *,
    email: Optional[str] = None,
    dedup_ttl_seconds: int = _OAUTH_REAUTH_DEDUP_TTL,
) -> bool:
    """Telegram nudge when the OAuth heartbeat detects a revoked Google token.

    The weekly heartbeat (`weekly_oauth_heartbeat`) classifies a user as
    `auth_failed` when Google's auto-refresh path returns a permanent error —
    idle 6-month expiry, password change, scope revoke, or eviction from the
    100-token-per-client cap. The user must run `/connect google` to re-consent.

    Dedup: a Redis key (`notification_sent:{user_id}:oauth_reauth`) suppresses
    repeats inside the TTL window. If Redis is unreachable, we still send
    rather than fail closed — a duplicate nudge is preferable to a silent
    auth failure on a critical-path integration.

    Returns:
        True if a Telegram message was sent, False if dedup-suppressed or the
        send itself failed (logged).
    """
    dedup_key = f"notification_sent:{user_telegram_id}:oauth_reauth"
    redis = None
    try:
        from src.memory.conversation import get_redis
        redis = await get_redis()
        if await redis.exists(dedup_key):
            logger.info(
                "OAuth reauth nudge for user %s suppressed by dedup key",
                user_telegram_id,
            )
            return False
    except Exception as exc:
        logger.warning(
            "OAuth reauth dedup check failed for user %s (will still send): %s",
            user_telegram_id, exc,
        )
        redis = None

    try:
        account_line = f"\n*Account:* `{email}`" if email else ""
        text = (
            "🔐 *Google access needs re-authorization*\n\n"
            "Atlas's weekly check found your Google connection has expired or "
            "been revoked. This typically happens after a long idle period, a "
            "password change, or if access was removed from your Google account."
            f"{account_line}\n\n"
            "Restore Gmail / Calendar / Drive access:\n"
            "`/connect google`"
        )
        bot = _get_bot()
        try:
            await bot.send_message(
                chat_id=user_telegram_id,
                text=text,
                parse_mode="Markdown",
            )
        finally:
            await bot.session.close()
    except Exception as exc:
        logger.warning("notify_oauth_reauth_required failed: %s", exc)
        return False

    # Only mark as nudged after a successful send so transient bot failures
    # don't suppress next week's retry.
    if redis is not None:
        try:
            await redis.set(dedup_key, "1", ex=dedup_ttl_seconds)
        except Exception as exc:
            logger.warning(
                "Could not set OAuth reauth dedup key for user %s: %s",
                user_telegram_id, exc,
            )
    return True
