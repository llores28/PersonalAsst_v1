"""Telegram message and command handlers."""

import asyncio
import logging
from typing import Any

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from src.settings import settings

logger = logging.getLogger(__name__)

router = Router()

# Per-user message queues (AD-6: sequential processing)
_user_queues: dict[int, asyncio.Queue] = {}
_user_workers: dict[int, asyncio.Task] = {}


async def _get_user_queue(user_id: int) -> asyncio.Queue:
    if user_id not in _user_queues:
        _user_queues[user_id] = asyncio.Queue()
        _user_workers[user_id] = asyncio.create_task(_process_user_queue(user_id))
    return _user_queues[user_id]


async def _process_user_queue(user_id: int) -> None:
    """Process messages sequentially per user (AD-6)."""
    queue = _user_queues[user_id]
    while True:
        message, handler_coro = await queue.get()
        try:
            await asyncio.wait_for(handler_coro, timeout=settings.agent_timeout_seconds)
        except asyncio.TimeoutError:
            logger.error("Handler timeout for user %d", user_id)
            try:
                await message.answer("That took too long. Please try again.")
            except Exception:
                pass
        except Exception as e:
            logger.exception("Handler error for user %d: %s", user_id, e)
            try:
                await message.answer("Something unexpected happened. I've logged it.")
            except Exception:
                pass
        finally:
            queue.task_done()


async def is_allowed(telegram_id: int) -> bool:
    """Check if user is in the allowlist."""
    from sqlalchemy import select
    from src.db.session import async_session
    from src.db.models import AllowedUser

    async with async_session() as session:
        result = await session.execute(
            select(AllowedUser).where(AllowedUser.telegram_id == telegram_id)
        )
        return result.scalar_one_or_none() is not None


async def is_cost_capped(telegram_id: int) -> bool:
    """Check if user has exceeded daily cost cap."""
    from sqlalchemy import select
    from src.db.session import async_session
    from src.db.models import DailyCost, User
    from datetime import date as date_type

    async with async_session() as session:
        user_result = await session.execute(
            select(User).where(User.telegram_id == telegram_id)
        )
        user = user_result.scalar_one_or_none()
        if not user:
            return False

        cost_result = await session.execute(
            select(DailyCost).where(
                DailyCost.user_id == user.id,
                DailyCost.date == date_type.today(),
            )
        )
        daily = cost_result.scalar_one_or_none()
        if not daily:
            return False

        return float(daily.total_cost_usd) >= settings.daily_cost_cap_usd


@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    """Handle /start — setup wizard."""
    if not await is_allowed(message.from_user.id):
        return

    await message.answer(
        f"Welcome! I'm **{settings.default_assistant_name}**, your personal assistant.\n\n"
        "I can help you with:\n"
        "- Managing your email, calendar, and files\n"
        "- Searching the web\n"
        "- Scheduling reminders\n"
        "- Remembering your preferences\n\n"
        "Type `/help` to see all commands, or just start chatting!",
        parse_mode="Markdown",
    )


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    """Handle /help — show available commands."""
    if not await is_allowed(message.from_user.id):
        return

    await message.answer(
        "**Available Commands:**\n\n"
        "/start — Initial setup\n"
        "/help — Show this help\n"
        "/connect google — Connect Google Workspace\n"
        "/persona — View/edit assistant personality\n"
        "/memory — See what I remember about you\n"
        "/forget <topic> — Ask me to forget something\n"
        "/tools — List available tools\n"
        "/schedules — List scheduled tasks\n"
        "/stats — View usage statistics\n"
        "/cancel <job_id> — Cancel a scheduled task\n\n"
        "**Scheduling:**\n"
        "\u2022 \"Remind me every Monday at 9am to review goals\"\n"
        "\u2022 \"Set up a morning brief at 8am\"\n"
        "\u2022 \"Check my email every 30 minutes\"\n\n"
        "**Memory:**\n"
        "\u2022 \"Remember that I prefer mornings\"\n"
        "\u2022 \"What do you know about me?\"\n"
        "\u2022 \"Forget my coffee preference\"\n\n"
        "**Google Workspace** (after /connect google):\n"
        "\u2022 \"Read my latest emails\"\n"
        "\u2022 \"What's on my calendar today?\"\n"
        "\u2022 \"Find the budget spreadsheet on Drive\"\n\n"
        "**Or just chat naturally!** Ask me anything.",
        parse_mode="Markdown",
    )


@router.message(Command("persona"))
async def cmd_persona(message: Message) -> None:
    """Handle /persona — view or update persona settings (Phase 3: DB-backed)."""
    if not await is_allowed(message.from_user.id):
        return

    from src.memory.persona import get_active_persona, update_persona_field
    from src.db.session import async_session
    from src.db.models import User
    from sqlalchemy import select

    # Get user DB id
    async with async_session() as session:
        result = await session.execute(
            select(User).where(User.telegram_id == message.from_user.id)
        )
        user = result.scalar_one_or_none()
        if not user:
            await message.answer("Send /start first to set up your profile.")
            return

    args = message.text.split(maxsplit=2) if message.text else []

    if len(args) == 1:
        # Show current persona
        persona = await get_active_persona(user.id)
        if persona:
            name = persona["assistant_name"]
            style = persona["personality"].get("style", "friendly")
            traits = persona["personality"].get("traits", [])
            version = persona["version"]
        else:
            name = settings.default_assistant_name
            style = settings.default_persona_style
            traits = ["helpful", "proactive", "concise"]
            version = 0

        traits_str = ", ".join(traits) if isinstance(traits, list) else str(traits)
        await message.answer(
            f"**Current Persona (v{version}):**\n\n"
            f"Name: {name}\n"
            f"Style: {style}\n"
            f"Traits: {traits_str}\n\n"
            "Change with:\n"
            "`/persona name <name>`\n"
            "`/persona style <casual|friendly|professional|brief>`\n"
            "`/persona traits <trait1, trait2, ...>`",
            parse_mode="Markdown",
        )
    elif len(args) >= 3:
        field = args[1].lower()
        value = args[2]
        result_msg = await update_persona_field(user.id, field, value)
        await message.answer(result_msg)
    else:
        await message.answer(
            "Usage:\n"
            "`/persona` — view current\n"
            "`/persona name Atlas` — change name\n"
            "`/persona style casual` — change style\n"
            "`/persona traits helpful, witty, concise` — change traits",
            parse_mode="Markdown",
        )


@router.message(Command("memory"))
async def cmd_memory(message: Message) -> None:
    """Handle /memory — show what the assistant remembers."""
    if not await is_allowed(message.from_user.id):
        return

    from src.memory.mem0_client import get_all_memories

    memories = await get_all_memories(user_id=str(message.from_user.id))

    if not memories:
        await message.answer(
            "I don't have any memories stored yet.\n\n"
            "Tell me things like \"Remember that I prefer mornings\" "
            "and I'll learn your preferences over time."
        )
        return

    lines = [f"**I remember {len(memories)} things about you:**\n"]
    for i, mem in enumerate(memories[:20], 1):  # Show max 20
        text = mem.get("memory", mem.get("text", str(mem)))
        mem_id = mem.get("id", "?")
        lines.append(f"{i}. {text}")

    if len(memories) > 20:
        lines.append(f"\n...and {len(memories) - 20} more.")

    lines.append("\nUse `/forget <topic>` to remove specific memories.")
    await message.answer("\n".join(lines), parse_mode="Markdown")


@router.message(Command("forget"))
async def cmd_forget(message: Message) -> None:
    """Handle /forget <topic> — search and delete matching memories."""
    if not await is_allowed(message.from_user.id):
        return

    args = message.text.split(maxsplit=1) if message.text else []
    if len(args) < 2:
        await message.answer(
            "Usage: `/forget <topic>`\n\n"
            "Example: `/forget coffee preference`\n"
            "This will find and remove memories matching that topic.",
            parse_mode="Markdown",
        )
        return

    topic = args[1]
    from src.memory.mem0_client import search_memories, delete_memory

    memories = await search_memories(topic, user_id=str(message.from_user.id), limit=5)

    if not memories:
        await message.answer(f"I don't have any memories matching \"{topic}\".")
        return

    deleted_count = 0
    for mem in memories:
        mem_id = mem.get("id")
        if mem_id:
            success = await delete_memory(mem_id)
            if success:
                deleted_count += 1

    if deleted_count > 0:
        await message.answer(
            f"Done! Forgot {deleted_count} memory/memories related to \"{topic}\"."
        )
    else:
        await message.answer(
            f"Found memories matching \"{topic}\" but couldn't delete them. "
            "Try `/memory` to see all memories with their IDs."
        )


@router.message(Command("stats"))
async def cmd_stats(message: Message) -> None:
    """Handle /stats — full usage dashboard (Phase 6 upgrade)."""
    if not await is_allowed(message.from_user.id):
        return

    from sqlalchemy import select, func as sqlfunc
    from src.db.session import async_session
    from src.db.models import DailyCost, AuditLog, User, Tool, ScheduledTask
    from datetime import date as date_type

    async with async_session() as session:
        user_result = await session.execute(
            select(User).where(User.telegram_id == message.from_user.id)
        )
        user = user_result.scalar_one_or_none()
        if not user:
            await message.answer("No usage data yet.")
            return

        cost_result = await session.execute(
            select(DailyCost).where(
                DailyCost.user_id == user.id,
                DailyCost.date == date_type.today(),
            )
        )
        daily = cost_result.scalar_one_or_none()

        total_result = await session.execute(
            select(sqlfunc.count(AuditLog.id)).where(AuditLog.user_id == user.id)
        )
        total_interactions = total_result.scalar() or 0

        tool_count = await session.execute(
            select(sqlfunc.count(Tool.id)).where(Tool.is_active == True)
        )
        active_tools = tool_count.scalar() or 0

        sched_count = await session.execute(
            select(sqlfunc.count(ScheduledTask.id)).where(
                ScheduledTask.user_id == user.id,
                ScheduledTask.is_active == True,
            )
        )
        active_schedules = sched_count.scalar() or 0

    today_cost = float(daily.total_cost_usd) if daily else 0.0
    today_requests = daily.request_count if daily else 0

    # Memory count
    try:
        from src.memory.mem0_client import get_all_memories
        memories = await get_all_memories(user_id=str(message.from_user.id))
        memory_count = len(memories)
    except Exception:
        memory_count = 0

    cost_pct = (today_cost / settings.daily_cost_cap_usd * 100) if settings.daily_cost_cap_usd else 0
    bar_len = 10
    filled = int(cost_pct / 100 * bar_len)
    cost_bar = "\u2588" * filled + "\u2591" * (bar_len - filled)

    await message.answer(
        f"**Dashboard**\n\n"
        f"**Cost today:** ${today_cost:.4f} / ${settings.daily_cost_cap_usd:.2f}\n"
        f"[{cost_bar}] {cost_pct:.0f}%\n\n"
        f"**Requests today:** {today_requests}\n"
        f"**Total interactions:** {total_interactions}\n"
        f"**Active tools:** {active_tools}\n"
        f"**Active schedules:** {active_schedules}\n"
        f"**Stored memories:** {memory_count}\n",
        parse_mode="Markdown",
    )


@router.message(Command("tools"))
async def cmd_tools(message: Message) -> None:
    """Handle /tools — list all registered tools."""
    if not await is_allowed(message.from_user.id):
        return

    from sqlalchemy import select
    from src.db.session import async_session
    from src.db.models import Tool

    async with async_session() as session:
        result = await session.execute(select(Tool).where(Tool.is_active == True))
        tools = result.scalars().all()

    if not tools:
        await message.answer(
            "No custom tools registered yet.\n\n"
            "Ask me to create one: \"Create a tool that converts CSV to JSON\""
        )
        return

    lines = [f"**Available Tools ({len(tools)}):**\n"]
    for t in tools:
        lines.append(
            f"\u2022 **{t.name}** ({t.tool_type}) — {t.description}\n"
            f"  Used {t.use_count}x | By: {t.created_by}"
        )
    await message.answer("\n".join(lines), parse_mode="Markdown")


@router.message(Command("schedules"))
async def cmd_schedules(message: Message) -> None:
    """Handle /schedules — list all active scheduled tasks."""
    if not await is_allowed(message.from_user.id):
        return

    from sqlalchemy import select
    from src.db.session import async_session
    from src.db.models import ScheduledTask, User

    async with async_session() as session:
        user_result = await session.execute(
            select(User).where(User.telegram_id == message.from_user.id)
        )
        user = user_result.scalar_one_or_none()
        if not user:
            await message.answer("No scheduled tasks yet.")
            return

        result = await session.execute(
            select(ScheduledTask).where(
                ScheduledTask.user_id == user.id,
                ScheduledTask.is_active == True,
            )
        )
        tasks = result.scalars().all()

    if not tasks:
        await message.answer(
            "No active scheduled tasks.\n\n"
            "Try: \"Remind me every Monday at 9am to review my goals\""
        )
        return

    lines = [f"**Active Schedules ({len(tasks)}):**\n"]
    for t in tasks:
        lines.append(
            f"\u2022 **{t.description}**\n"
            f"  Type: {t.trigger_type} | ID: `{t.apscheduler_id}`"
        )
    lines.append("\nCancel with: `/cancel <job_id>`")
    await message.answer("\n".join(lines), parse_mode="Markdown")


@router.message(Command("connect"))
async def cmd_connect(message: Message) -> None:
    """Handle /connect google — start OAuth flow."""
    if not await is_allowed(message.from_user.id):
        return

    from src.integrations.workspace_mcp import is_google_configured, get_oauth_url

    args = message.text.split() if message.text else []

    if len(args) < 2 or args[1].lower() != "google":
        await message.answer(
            "Usage: `/connect google`\n\n"
            "This connects your Google Workspace (Gmail, Calendar, Drive).",
            parse_mode="Markdown",
        )
        return

    if not is_google_configured():
        await message.answer(
            "Google Workspace is not configured yet.\n\n"
            "The server admin needs to set `GOOGLE_OAUTH_CLIENT_ID` and "
            "`GOOGLE_OAUTH_CLIENT_SECRET` in the `.env` file.",
        )
        return

    oauth_url = get_oauth_url(message.from_user.id)
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Connect Google Workspace", url=oauth_url)]
        ]
    )
    await message.answer(
        "Click the button below to authorize access to your Google Workspace:\n\n"
        "This will allow me to manage your Gmail, Calendar, and Drive.",
        reply_markup=keyboard,
    )


@router.message(Command("cancel"))
async def cmd_cancel(message: Message) -> None:
    """Handle /cancel — cancel current operation or a scheduled task by ID."""
    if not await is_allowed(message.from_user.id):
        return

    args = message.text.split(maxsplit=1) if message.text else []

    if len(args) >= 2:
        job_id = args[1].strip()
        # Cancel a specific scheduled task
        from src.scheduler.engine import remove_job
        from sqlalchemy import select, update
        from src.db.session import async_session
        from src.db.models import ScheduledTask, User

        async with async_session() as session:
            user_result = await session.execute(
                select(User).where(User.telegram_id == message.from_user.id)
            )
            user = user_result.scalar_one_or_none()
            if user:
                await session.execute(
                    update(ScheduledTask)
                    .where(
                        ScheduledTask.apscheduler_id == job_id,
                        ScheduledTask.user_id == user.id,
                    )
                    .values(is_active=False)
                )
                await session.commit()

        success = await remove_job(job_id)
        if success:
            await message.answer(f"Schedule `{job_id}` cancelled.", parse_mode="Markdown")
        else:
            await message.answer(
                f"Removed `{job_id}` from records. "
                "It may not have been active in the scheduler.",
                parse_mode="Markdown",
            )
    else:
        await message.answer("Current operation cancelled.")


@router.message()
async def handle_message(message: Message) -> None:
    """Handle all non-command messages — route to orchestrator (text + voice)."""
    if not await is_allowed(message.from_user.id):
        return

    if await is_cost_capped(message.from_user.id):
        await message.answer("Daily usage limit reached. Resets at midnight.")
        return

    # Phase 6: Voice message support
    if message.voice:
        await message.answer("🎤 Transcribing voice message...")
        from src.bot.voice import transcribe_voice
        text = await transcribe_voice(message.voice.file_id, message.bot)
        if text.startswith("("):
            await message.answer(text)
            return
        # Process transcribed text as a regular message
        queue = await _get_user_queue(message.from_user.id)
        await queue.put((message, _run_orchestrator_with_text(message, text)))
        return

    if not message.text:
        return

    queue = await _get_user_queue(message.from_user.id)
    await queue.put((message, _run_orchestrator(message)))


async def _run_orchestrator(message: Message) -> None:
    """Run the orchestrator agent on the user's message."""
    await _run_orchestrator_with_text(message, message.text)


async def _run_orchestrator_with_text(message: Message, text: str) -> None:
    """Run the orchestrator agent with given text (supports voice transcription)."""
    from src.agents.orchestrator import run_orchestrator

    try:
        response_text = await run_orchestrator(
            user_telegram_id=message.from_user.id,
            user_message=text,
        )
        await message.answer(response_text, parse_mode="Markdown")
    except Exception as e:
        logger.exception("Orchestrator error: %s", e)
        await message.answer("Something went wrong. I've logged the error. Please try again.")
