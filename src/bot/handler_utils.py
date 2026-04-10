import logging
from typing import Any
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


def _exception_message_text(exc: Exception) -> str:
    message = getattr(exc, "message", None)
    if isinstance(message, str):
        return message

    text_args = [arg for arg in exc.args if isinstance(arg, str)]
    if text_args:
        return " ".join(text_args)

    return exc.__class__.__name__


async def _answer_with_markdown_fallback(message: Any, text: str) -> None:
    """Send message, splitting if longer than Telegram's 4096 char limit."""
    MAX_LENGTH = 4096
    
    # If message is short, send normally
    if len(text) <= MAX_LENGTH:
        try:
            await message.answer(text, parse_mode="Markdown")
        except Exception as exc:
            error_text = _exception_message_text(exc).lower()
            if exc.__class__.__name__ != "TelegramBadRequest" or "can't parse entities" not in error_text:
                raise
            logger.warning("Falling back to plain text Telegram reply after Markdown parse failure: %s", error_text)
            await message.answer(text)
        return
    
    # Split long message
    logger.info("Splitting long message of %d chars", len(text))
    parts = []
    current = ""
    
    # Split by lines to avoid breaking markdown entities
    lines = text.split('\n')
    for line in lines:
        # If adding this line would exceed limit, send current part
        if len(current) + len(line) + 1 > MAX_LENGTH and current:
            parts.append(current.rstrip())
            current = line
        else:
            if current:
                current += '\n' + line
            else:
                current = line
    
    # Add the last part
    if current:
        parts.append(current.rstrip())
    
    # Send parts with continuation indicators
    for i, part in enumerate(parts):
        try:
            # Add continuation indicator for multi-part messages
            if len(parts) > 1:
                prefix = f"({i+1}/{len(parts)})\n\n" if i == 0 else f"\n...({i+1}/{len(parts)})\n\n"
                part = prefix + part
            
            await message.answer(part, parse_mode="Markdown")
        except Exception as exc:
            error_text = _exception_message_text(exc).lower()
            if exc.__class__.__name__ != "TelegramBadRequest" or "can't parse entities" not in error_text:
                raise
            logger.warning("Falling back to plain text for part %d: %s", i+1, error_text)
            await message.answer(part)


async def is_allowed(telegram_id: int) -> bool:
    from sqlalchemy import select
    from src.db.session import async_session
    from src.db.models import AllowedUser

    async with async_session() as session:
        result = await session.execute(
            select(AllowedUser).where(AllowedUser.telegram_id == telegram_id)
        )
        return result.scalar_one_or_none() is not None


def _extract_embedded_command(text: str) -> str | None:
    stripped = text.strip()
    lowered = stripped.lower()
    prefixes = ("run ", "use ", "do ", "execute ")

    if stripped.startswith("/"):
        return stripped

    for prefix in prefixes:
        if lowered.startswith(prefix):
            candidate = stripped[len(prefix):].strip()
            if candidate.startswith("/"):
                return candidate

    return None


async def _handle_connect_request(message: Any, command_text: str | None = None) -> None:
    if not await is_allowed(message.from_user.id):
        return

    from src.integrations.workspace_mcp import (
        get_google_auth_url,
        is_google_configured,
        store_connected_google_email,
    )

    args = command_text.split() if command_text else (message.text.split() if message.text else [])

    if len(args) < 2 or args[1].lower() != "google":
        await message.answer(
            "Usage: `/connect google you@gmail.com`\n\n"
            "This connects your Google Workspace (Gmail, Calendar, Drive, and Tasks).",
            parse_mode="Markdown",
        )
        return

    user_google_email = args[2].strip()

    if not is_google_configured():
        await message.answer(
            "Google Workspace is not configured yet.\n\n"
            "The server admin needs to set `GOOGLE_OAUTH_CLIENT_ID` and "
            "`GOOGLE_OAUTH_CLIENT_SECRET` in the `.env` file.",
        )
        return

    try:
        await store_connected_google_email(message.from_user.id, user_google_email)
        oauth_url = await get_google_auth_url(message.from_user.id, user_google_email)
    except Exception as exc:
        logger.exception("Google connect flow failed: %s", exc)
        await message.answer(
            "I couldn't start the Google authorization flow right now. "
            "Please verify the Google OAuth redirect URI is `http://127.0.0.1:8083/oauth2callback` "
            "and that the Workspace MCP sidecar is running with the local OAuth settings."
        )
        return

    if urlparse(oauth_url).hostname in {"localhost", "127.0.0.1"}:
        await message.answer(
            f"Click this link to authorize access to your Google Workspace:\n\n{oauth_url}"
        )
    else:
        from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="Connect Google Workspace", url=oauth_url)]
            ]
        )
        await message.answer(
            "Click the button below to authorize access to your Google Workspace:\n\n"
            "This will allow me to manage your Gmail, Calendar, Drive, and Google Tasks.",
            reply_markup=keyboard,
        )


async def _run_orchestrator_with_text(message: Any, text: str) -> None:
    """Run the orchestrator agent with given text (supports voice transcription).

    This is the canonical implementation — handlers.py imports from here.
    """
    from src.agents.orchestrator import run_orchestrator
    from agents.exceptions import (
        InputGuardrailTripwireTriggered,
        OutputGuardrailTripwireTriggered,
        MaxTurnsExceeded,
    )

    try:
        response_text = await run_orchestrator(
            user_telegram_id=message.from_user.id,
            user_message=text,
        )
        await _answer_with_markdown_fallback(message, response_text)
    except InputGuardrailTripwireTriggered:
        await message.answer(
            "Sorry, my safety filter flagged that message. "
            "If you're trying to manage your email, calendar, or tasks, try rephrasing — "
            "for example: `check my email`, `show my calendar`, or `fix this issue`."
        )
    except OutputGuardrailTripwireTriggered:
        logger.warning("Output guardrail tripped for user %s", message.from_user.id)
        await message.answer(
            "My response was filtered by a safety check. "
            "This can happen when I include email addresses or other details. "
            "Please try rephrasing your request, or ask me to proceed step by step."
        )
    except MaxTurnsExceeded:
        logger.warning("Max turns exceeded for user %s", message.from_user.id)
        await message.answer(
            "I got stuck in a loop trying to complete that request. "
            "Please try rephrasing with more detail — for example, include the "
            "full email address or specify exactly what you'd like me to do."
        )
    except Exception as e:
        error_text = str(e)

        # Stale session recovery: if the API rejects orphaned function_call
        # items from a previous run, clear the session and retry once.
        if "No tool call found for function call output" in error_text:
            logger.warning(
                "Stale session for user %s — clearing and retrying",
                message.from_user.id,
            )
            try:
                from src.agents.orchestrator import _get_agent_session
                sdk_session = await _get_agent_session(message.from_user.id)
                if sdk_session is not None:
                    await sdk_session.clear()
                response_text = await run_orchestrator(
                    user_telegram_id=message.from_user.id,
                    user_message=text,
                )
                await _answer_with_markdown_fallback(message, response_text)
                return
            except Exception as retry_exc:
                logger.exception("Retry after session clear also failed: %s", retry_exc)

        logger.exception("Orchestrator error: %s", e)
        if "model_not_found" in error_text or "does not exist" in error_text:
            await message.answer(
                "The assistant model is configured incorrectly right now. "
                "I need to update the OpenAI model setting before I can help with that."
            )
            return
        await message.answer("Something went wrong. I've logged the error. Please try again.")
