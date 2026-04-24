"""Telegram message and command handlers."""

import asyncio
import logging
import base64

import json
import uuid

from aiogram import Router, F
from aiogram.filters import Command, CommandStart
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

from src.settings import settings
from src.bot.handler_utils import (
    _extract_embedded_command,
    _handle_connect_request,
    _run_orchestrator_with_text,
    is_allowed,
)

logger = logging.getLogger(__name__)

router = Router()

# Per-user message queues (AD-6: sequential processing)
_user_queues: dict[int, asyncio.Queue] = {}
_user_workers: dict[int, asyncio.Task] = {}

QUEUE_IDLE_TIMEOUT_S = 1800  # 30 minutes — worker self-terminates after this idle period

# Per-user rate limiting: max messages per window
_USER_RATE_LIMIT = 10       # max messages
_USER_RATE_WINDOW_S = 60    # per this many seconds
_user_message_times: dict[int, list[float]] = {}


def _is_user_rate_limited(user_id: int) -> bool:
    """Return True if user has exceeded the per-user message rate limit."""
    import time
    now = time.monotonic()
    window_start = now - _USER_RATE_WINDOW_S

    timestamps = _user_message_times.get(user_id, [])
    # Prune old timestamps
    timestamps = [t for t in timestamps if t > window_start]
    _user_message_times[user_id] = timestamps

    if len(timestamps) >= _USER_RATE_LIMIT:
        return True

    timestamps.append(now)
    return False


async def _get_user_queue(user_id: int) -> asyncio.Queue:
    existing_worker = _user_workers.get(user_id)
    if user_id not in _user_queues or (existing_worker is not None and existing_worker.done()):
        _user_queues[user_id] = asyncio.Queue()
        _user_workers[user_id] = asyncio.create_task(_process_user_queue(user_id))
    return _user_queues[user_id]


async def _process_user_queue(user_id: int) -> None:
    """Process messages sequentially per user (AD-6).

    Exits after QUEUE_IDLE_TIMEOUT_S of inactivity and removes itself from
    the tracking dicts to prevent unbounded memory growth.
    """
    queue = _user_queues[user_id]
    while True:
        try:
            message, handler_coro = await asyncio.wait_for(
                queue.get(), timeout=QUEUE_IDLE_TIMEOUT_S
            )
        except asyncio.TimeoutError:
            # Idle timeout — clean up and exit
            _user_queues.pop(user_id, None)
            _user_workers.pop(user_id, None)
            logger.debug("User queue for %d idle for %ds — cleaned up", user_id, QUEUE_IDLE_TIMEOUT_S)
            return
        try:
            await asyncio.wait_for(handler_coro, timeout=settings.agent_timeout_seconds)
        except asyncio.TimeoutError:
            logger.error("Handler timeout for user %d", user_id)
            try:
                await message.answer("That took too long. Please try again.")
            except Exception as notify_error:
                logger.warning(
                    "Failed to send timeout notice for user %d: %s",
                    user_id,
                    notify_error,
                )
        except Exception as e:
            logger.exception("Handler error for user %d: %s", user_id, e)
            try:
                await message.answer("Something unexpected happened. I've logged it.")
            except Exception as notify_error:
                logger.warning(
                    "Failed to send error notice for user %d: %s",
                    user_id,
                    notify_error,
                )
        finally:
            queue.task_done()


# _answer_with_markdown_fallback, is_allowed imported from handler_utils


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
        "Available Commands:\n\n"
        "/start — Initial setup\n"
        "/help — Show this help\n"
        "/connect google — Connect Google Workspace\n"
        "/persona — View/edit assistant personality\n"
        "/persona interview — Build your personality profile (3 sessions)\n"
        "/memory — See what I remember about you\n"
        "/forget <topic> — Ask me to forget something\n"
        "/tools — List available tools\n"
        "/tools credentials — Manage tool API keys/passwords\n"
        "/schedules — List scheduled tasks\n"
        "/stats — View usage statistics\n"
        "/cancel <job_id> — Cancel a scheduled task\n"
        "/neworg <goal> — AI-powered organization creation\n"
        "/security — Configure security PIN/questions for repair approvals\n\n"
        "Scheduling:\n"
        "• \"Remind me every Monday at 9am to review goals\"\n"
        "• \"Set up a morning brief at 8am\"\n"
        "• \"Check my email every 30 minutes\"\n\n"
        "Memory:\n"
        "• \"Remember that I prefer mornings\"\n"
        "• \"What do you know about me?\"\n"
        "• \"Forget my coffee preference\"\n\n"
        "Google Workspace (after /connect google):\n"
        "• \"Read my latest emails\"\n"
        "• \"What's on my calendar today?\"\n"
        "• \"Find the budget spreadsheet on Drive\"\n\n"
        "Or just chat naturally! Ask me anything.",
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

    # Handle /persona interview
    if len(args) >= 2 and args[1].lower() == "interview":
        await _handle_persona_interview(message, user, args)
        return

    if len(args) == 1:
        # Show current persona
        persona = await get_active_persona(user.id)
        if persona:
            name = persona["assistant_name"]
            style = persona["personality"].get("style", "friendly")
            traits = persona["personality"].get("traits", [])
            version = persona["version"]
            sessions_done = persona["personality"].get(
                "interview_sessions_completed", 0
            )
            has_ocean = "ocean" in persona["personality"]
        else:
            name = settings.default_assistant_name
            style = settings.default_persona_style
            traits = ["helpful", "proactive", "concise"]
            version = 0
            sessions_done = 0
            has_ocean = False

        traits_str = ", ".join(traits) if isinstance(traits, list) else str(traits)
        interview_status = (
            f"\n\n🧬 **Personality Profile:** {'Active' if has_ocean else 'Not started'}\n"
            f"Interview sessions: {sessions_done}/3"
        )
        if not has_ocean:
            interview_status += "\n💡 Run `/persona interview` to build your personality profile!"

        await message.answer(
            f"**Current Persona (v{version}):**\n\n"
            f"Name: {name}\n"
            f"Style: {style}\n"
            f"Traits: {traits_str}"
            f"{interview_status}\n\n"
            "Change with:\n"
            "`/persona name <name>`\n"
            "`/persona style <casual|friendly|professional|brief>`\n"
            "`/persona traits <trait1, trait2, ...>`\n"
            "`/persona interview` — start personality profiling interview",
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
            "`/persona traits helpful, witty, concise` — change traits\n"
            "`/persona interview` — start personality profiling interview\n"
            "`/persona interview reset` — restart interview from scratch",
            parse_mode="Markdown",
        )


async def _handle_persona_interview(message: Message, user, args: list[str]) -> None:
    """Handle /persona interview [reset] subcommands."""
    from src.agents.persona_interview_agent import (
        get_interview_state,
        handle_interview_message,
        reset_interview,
    )
    from src.memory.conversation import set_session_field

    telegram_id = message.from_user.id

    # Handle reset
    if len(args) >= 3 and args[2].lower() == "reset":
        result_msg = await reset_interview(telegram_id)
        await message.answer(result_msg)
        return

    # Start or resume interview
    state = await get_interview_state(telegram_id)

    if state.get("all_complete"):
        await message.answer(
            "You've completed all 3 interview sessions! 🎉\n\n"
            "Your personality profile is active.\n"
            "To redo the interview, use `/persona interview reset`.",
            parse_mode="Markdown",
        )
        return

    # Set interview mode in Redis so regular messages route to interview handler
    try:
        await set_session_field(telegram_id, "interview_active", "true")
        await set_session_field(
            telegram_id, "interview_session", str(state["current_session"])
        )
    except Exception as e:
        logger.warning(
            "Failed to set interview session fields for user %d: %s",
            telegram_id,
            e,
        )

    # Get the first question of the session
    response = await handle_interview_message(telegram_id, "__START__")
    await message.answer(response, parse_mode="Markdown")


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
            select(sqlfunc.count(Tool.id)).where(Tool.is_active == True)  # noqa: E712
        )
        active_tools = tool_count.scalar() or 0

        sched_count = await session.execute(
            select(sqlfunc.count(ScheduledTask.id)).where(
                ScheduledTask.user_id == user.id,
                ScheduledTask.is_active == True,  # noqa: E712
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

    # Build message lines
    lines = [
        "**Dashboard**\n",
        f"**Cost today:** ${today_cost:.4f} / ${settings.daily_cost_cap_usd:.2f}",
        f"[{cost_bar}] {cost_pct:.0f}%\n",
    ]

    # Add per-provider cost breakdown if multi-LLM is enabled
    if getattr(settings, 'multi_llm_enabled', False):
        from src.models.cost_tracker import get_cost_breakdown
        
        breakdown = await get_cost_breakdown(user.id)
        if breakdown:
            lines.append("**By provider:**")
            for provider, cost in sorted(breakdown.items(), key=lambda x: x[1], reverse=True):
                cap_setting = f"{provider}_daily_cost_cap_usd"
                cap = getattr(settings, cap_setting, settings.daily_cost_cap_usd)
                pct = (cost / cap * 100) if cap else 0
                status = "🟢" if pct < 80 else "🟡" if pct < 100 else "🔴"
                lines.append(f"  {status} {provider}: ${cost:.4f} / ${cap:.2f}")
            lines.append("")  # Empty line after breakdown

    lines.extend([
        f"**Requests today:** {today_requests}",
        f"**Total interactions:** {total_interactions}",
        f"**Active tools:** {active_tools}",
        f"**Active schedules:** {active_schedules}",
        f"**Stored memories:** {memory_count}",
    ])

    await message.answer("\n".join(lines), parse_mode="Markdown")


@router.message(Command("model"))
async def cmd_model(message: Message) -> None:
    """Handle /model — switch LLM provider and model.

    Subcommands:
        /model                              — Show current model and available providers
        /model list                         — List available providers
        /model <provider>                   — Switch to provider's default model
        /model <provider>:<model>           — Switch to specific model
        /model reset                        — Reset to system default

    Examples:
        /model anthropic                    — Use Claude default
        /model openrouter:anthropic/claude-3-opus  — Use specific OpenRouter model
        /model openai:gpt-5.4-mini          — Use specific OpenAI model
    """
    if not await is_allowed(message.from_user.id):
        return

    # Check if multi-LLM is enabled
    if not getattr(settings, 'multi_llm_enabled', False):
        await message.answer(
            "🔒 *Multi-LLM support is disabled.*\n\n"
            "To enable provider switching, set `MULTI_LLM_ENABLED=true` in your `.env` file and restart.",
            parse_mode="Markdown",
        )
        return

    from src.models.provider_resolution import ProviderResolver
    from src.models.user_preferences import (
        set_user_model,
        clear_user_model,
        format_provider_list,
        list_available_models_for_user,
        get_user_model_display,
    )

    text = (message.text or "").strip()
    parts = text.split(maxsplit=2)

    # No arguments - show current and available
    if len(parts) == 1:
        current_display = await get_user_model_display(message.from_user.id)
        providers = await list_available_models_for_user(message.from_user.id)

        lines = [
            f"🤖 *Current Model:* `{current_display}`\n",
            format_provider_list(providers),
        ]
        await message.answer("\n".join(lines), parse_mode="Markdown")
        return

    subcommand = parts[1].lower()

    # /model list - show available providers
    if subcommand == "list":
        providers = await list_available_models_for_user(message.from_user.id)
        await message.answer(
            format_provider_list(providers),
            parse_mode="Markdown",
        )
        return

    # /model status - show detailed provider status
    if subcommand == "status":
        from src.models.cost_tracker import check_cost_cap
        
        resolver = ProviderResolver()
        providers = resolver.list_available(configured_only=False)
        
        lines = ["🔌 *Provider Status*\n"]
        
        for provider in providers:
            # Check configuration
            is_configured = provider.is_configured
            status_icon = "✅" if is_configured else "❌"
            
            # Check cost cap if configured
            cap_info = ""
            if is_configured and getattr(settings, 'multi_llm_enabled', False):
                try:
                    is_capped, current, limit = await check_cost_cap(
                        message.from_user.id, provider.name
                    )
                    if is_capped:
                        cap_info = " 🔴 *CAPPED*"
                    elif current > 0:
                        cap_info = f" (${current:.2f}/${limit:.2f})"
                except Exception:
                    pass
            
            lines.append(
                f"{status_icon} *{provider.name}* — {provider.api_mode}{cap_info}\n"
                f"   Default: `{provider.default_model}`\n"
                f"   Tools: {'✅' if provider.supports_tools else '❌'} "
                f"Streaming: {'✅' if provider.supports_streaming else '❌'}\n"
            )
        
        # Show current preference
        current = await get_user_model_display(message.from_user.id)
        lines.append(f"\n🤖 *Your current:* `{current}`")
        
        await message.answer("\n".join(lines), parse_mode="Markdown")
        return

    # /model reset - clear user preference
    if subcommand == "reset":
        success = await clear_user_model(message.from_user.id)
        if success:
            default = settings.default_llm_provider
            await message.answer(
                f"✅ Reset to system default: `{default}`",
                parse_mode="Markdown",
            )
        else:
            await message.answer(
                "❌ Failed to reset. Try again or check logs.",
                parse_mode="Markdown",
            )
        return

    # Parse provider:model format
    provider_input = subcommand
    model_id = None

    if ":" in provider_input:
        provider_name, model_id = provider_input.split(":", 1)
    else:
        provider_name = provider_input

    # Validate provider exists and is configured
    resolver = ProviderResolver()
    try:
        config = resolver.resolve(provider_name)
    except ValueError as e:
        available = resolver.list_provider_names(configured_only=True)
        await message.answer(
            f"❌ *Error:* `{e}`\n\n"
            f"*Available providers:* {', '.join(f'`{p}`' for p in available)}",
            parse_mode="Markdown",
        )
        return

    # Check cost cap before switching
    from src.models.cost_tracker import check_cost_cap, should_warn_about_cap
    
    is_capped, current_cost, cap_limit = await check_cost_cap(
        message.from_user.id, provider_name
    )
    
    if is_capped:
        await message.answer(
            f"🔴 *Cannot switch to {provider_name}*\n\n"
            f"Daily cost cap reached: `${current_cost:.2f} / ${cap_limit:.2f}`\n\n"
            f"Try another provider or wait until tomorrow.",
            parse_mode="Markdown",
        )
        return
    
    # Warn if approaching cap (80%)
    should_warn, _, _ = await should_warn_about_cap(message.from_user.id, provider_name)
    if should_warn:
        await message.answer(
            f"⚠️ *Warning:* {provider_name} is at {current_cost/cap_limit*100:.0f}% of daily cap.\n"
            f"Cost: `${current_cost:.2f} / ${cap_limit:.2f}`\n\n"
            f"Switching anyway...",
            parse_mode="Markdown",
        )

    # Save the preference
    success = await set_user_model(message.from_user.id, provider_name, model_id)

    if success:
        model_display = model_id or config.default_model
        await message.answer(
            f"✅ *Model switched successfully*\n\n"
            f"Provider: `{provider_name}`\n"
            f"Model: `{model_display}`\n\n"
            f"Your next message will use this model.",
            parse_mode="Markdown",
        )
    else:
        await message.answer(
            "❌ Failed to save model preference. Please try again.",
            parse_mode="Markdown",
        )


@router.message(Command("tools"))
async def cmd_tools(message: Message) -> None:
    """Handle /tools — list tools, manage credentials.

    Subcommands:
        /tools                              — List all registered tools
        /tools credentials set <tool> <key> <value>  — Store a credential
        /tools credentials list [tool]      — List credential keys
        /tools credentials delete <tool> <key>       — Delete a credential
    """
    if not await is_allowed(message.from_user.id):
        return

    # Owner-only for credential management
    is_owner = message.from_user.id == settings.owner_telegram_id

    text = (message.text or "").strip()
    parts = text.split(maxsplit=4)  # /tools credentials set tool key value

    # Subcommand routing
    if len(parts) >= 2 and parts[1].lower() == "credentials":
        if not is_owner:
            await message.answer("Only the owner can manage tool credentials.")
            return
        await _handle_tools_credentials(message, parts)
        return

    # Default: list tools
    from sqlalchemy import select
    from src.db.session import async_session
    from src.db.models import Tool

    async with async_session() as session:
        result = await session.execute(select(Tool).where(Tool.is_active == True))  # noqa: E712
        db_tools = result.scalars().all()

    # Also show dynamic tools from the registry
    from src.tools.registry import get_registry
    try:
        registry = await get_registry()
        dynamic_names = list(registry._manifests.keys())
    except Exception:
        dynamic_names = []

    lines = []
    if db_tools:
        lines.append(f"**DB Tools ({len(db_tools)}):**\n")
        for t in db_tools:
            lines.append(
                f"\u2022 **{t.name}** ({t.tool_type}) — {t.description}\n"
                f"  Used {t.use_count}x | By: {t.created_by}"
            )
    if dynamic_names:
        lines.append(f"\n**Dynamic Tools ({len(dynamic_names)}):**\n")
        for name in dynamic_names:
            m = registry.get_manifest(name)
            desc = m.description if m else ""
            cred_count = len(m.credentials) if m and m.credentials else 0
            cred_label = f" | 🔑 {cred_count} creds" if cred_count else ""
            lines.append(f"\u2022 **{name}** ({m.type if m else '?'}) — {desc}{cred_label}")

    if not lines:
        await message.answer(
            "No tools registered yet.\n\n"
            "Ask me to create one, or see `/tools credentials` for credential management."
        )
        return

    lines.append(
        "\n_Tip: Use_ `/tools credentials set <tool>` "
        "_to see what credentials a tool needs._"
    )
    await message.answer("\n".join(lines), parse_mode="Markdown")


async def _handle_tools_credentials(message: Message, parts: list[str]) -> None:
    """Handle /tools credentials subcommands."""
    from src.tools.credentials import (
        store_credential,
        list_credential_keys,
        delete_credential,
    )

    if len(parts) < 3:
        await message.answer(
            "**Tool Credentials Management:**\n\n"
            "`/tools credentials set <tool> <key> <value>`\n"
            "  Store a credential for a tool\n\n"
            "`/tools credentials list [tool]`\n"
            "  List credential keys (no values shown)\n\n"
            "`/tools credentials delete <tool> <key>`\n"
            "  Delete a credential\n\n"
            "**Example:**\n"
            "`/tools credentials set linkedin linkedin_email me@example.com`\n"
            "`/tools credentials set linkedin linkedin_password mypassword`\n"
            "`/tools credentials list linkedin`",
            parse_mode="Markdown",
        )
        return

    action = parts[2].lower()

    if action == "set":
        # /tools credentials set <tool> <key> <value>
        full_parts = (message.text or "").strip().split()

        # User gave just the tool name — show what credentials it needs
        if len(full_parts) == 4:
            tool_name = full_parts[3]
            hint = await _get_credential_hints(tool_name)
            if hint:
                await message.answer(hint, parse_mode="Markdown")
            else:
                await message.answer(
                    f"Usage: `/tools credentials set {tool_name} <key> <value>`",
                    parse_mode="Markdown",
                )
            return

        if len(full_parts) < 6:
            await message.answer(
                "Usage: `/tools credentials set <tool> <key> <value>`\n\n"
                "_Tip: Type_ `/tools credentials set <tool>` "
                "_to see which credentials it needs._",
                parse_mode="Markdown",
            )
            return

        tool_name = full_parts[3]
        cred_key = full_parts[4]
        cred_value = " ".join(full_parts[5:])

        await store_credential(tool_name, cred_key, cred_value)
        await message.answer(
            f"\u2705 Credential `{cred_key}` stored for tool `{tool_name}`.",
            parse_mode="Markdown",
        )

    elif action == "list":
        if len(parts) >= 4:
            tool_name = parts[3]
            keys = await list_credential_keys(tool_name)
            if keys:
                key_list = "\n".join(f"  \u2022 `{k}`" for k in keys)
                await message.answer(
                    f"**Credentials for `{tool_name}`:**\n{key_list}",
                    parse_mode="Markdown",
                )
            else:
                await message.answer(
                    f"No credentials stored for `{tool_name}`.",
                    parse_mode="Markdown",
                )
        else:
            # List all tools that have credentials
            from src.tools.registry import get_registry
            try:
                registry = await get_registry()
                manifests = registry._manifests
            except Exception:
                manifests = {}

            lines = ["**Tools with declared credentials:**\n"]
            for name, m in manifests.items():
                if m.credentials:
                    stored = await list_credential_keys(name)
                    declared = list(m.credentials.keys())
                    missing = [k for k in declared if k not in stored]
                    status = "✅ All set" if not missing else f"⚠️ Missing: {', '.join(missing)}"
                    lines.append(f"\u2022 **{name}** — {status}")

            if len(lines) == 1:
                lines.append("  No tools with declared credentials found.")
            await message.answer("\n".join(lines), parse_mode="Markdown")

    elif action == "delete":
        if len(parts) < 5:
            remaining = (message.text or "").strip().split()
            if len(remaining) >= 5:
                tool_name = remaining[3]
                cred_key = remaining[4]
            else:
                await message.answer(
                    "Usage: `/tools credentials delete <tool> <key>`",
                    parse_mode="Markdown",
                )
                return
        else:
            remaining = (message.text or "").strip().split()
            tool_name = remaining[3]
            cred_key = remaining[4]

        await delete_credential(tool_name, cred_key)
        await message.answer(
            f"Credential `{cred_key}` deleted from tool `{tool_name}`.",
            parse_mode="Markdown",
        )

    else:
        await message.answer(
            f"Unknown subcommand: `{action}`. Use `set`, `list`, or `delete`.",
            parse_mode="Markdown",
        )


async def _get_credential_hints(tool_name: str) -> str | None:
    """Build a tool-specific credential setup guide from its manifest.

    Returns a formatted Markdown string showing which credentials the tool
    needs, their descriptions, and which are already configured.
    Returns None if the tool has no manifest or no credentials defined.
    """
    from src.tools.registry import get_registry
    from src.tools.credentials import list_credential_keys

    try:
        registry = await get_registry()
        manifest = registry.get_manifest(tool_name)
    except Exception:
        manifest = None

    if not manifest or not manifest.credentials:
        return None

    # Check which keys are already stored
    stored_keys = set(await list_credential_keys(tool_name))

    lines = [f"**Setup credentials for `{tool_name}`:**\n"]
    for key, cred_info in manifest.credentials.items():
        desc = cred_info.get("description", key)
        required = cred_info.get("required", False)
        status = "\u2705" if key in stored_keys else ("\u274c required" if required else "\u26a0\ufe0f optional")
        lines.append(f"{status}  `{key}` — {desc}")

    lines.append("\n**Commands to run:**")
    for key, cred_info in manifest.credentials.items():
        if key not in stored_keys:
            desc = cred_info.get("description", "value")
            lines.append(f"`/tools credentials set {tool_name} {key} <{desc}>`")

    if stored_keys >= set(manifest.credentials.keys()):
        lines.append("\n\u2705 All credentials are configured!")

    return "\n".join(lines)


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
                ScheduledTask.is_active == True,  # noqa: E712
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


@router.message(Command("ticket"))
async def cmd_ticket(message: Message) -> None:
    """Handle /ticket approve <id> — approve a verified repair ticket for deploy."""
    if not await is_allowed(message.from_user.id):
        return

    parts = (message.text or "").split(maxsplit=2)
    sub = parts[1].lower() if len(parts) > 1 else None
    arg = parts[2] if len(parts) > 2 else None

    if sub == "approve" and arg:
        try:
            ticket_id = int(arg)
        except ValueError:
            await message.answer("Ticket ID must be a number.")
            return
        from src.repair.engine import approve_ticket_deploy
        resp = await approve_ticket_deploy(ticket_id, message.from_user.id)
        await message.answer(resp, parse_mode="Markdown")
        return

    await message.answer(
        "Usage:\n\n"
        "• `/ticket approve <id>` — Merge verified ticket branch and deploy",
        parse_mode="Markdown",
    )


@router.message(Command("connect"))
async def cmd_connect(message: Message) -> None:
    """Handle /connect google — start OAuth flow."""
    await _handle_connect_request(message)


@router.message(Command("security"))
async def cmd_security(message: Message) -> None:
    """Handle /security — configure owner security PIN or security questions.

    Usage:
        /security pin <4-digit-pin>       — Set or update your security PIN
        /security question <Q> | <A>      — Add a security question and answer
        /security status                  — Show current config
    """
    if not await is_allowed(message.from_user.id):
        return

    if message.from_user.id != settings.owner_telegram_id:
        await message.answer("Only the owner can configure security settings.")
        return

    args = message.text.split(maxsplit=2) if message.text else []
    if len(args) < 2:
        await message.answer(
            "🔐 **Security Configuration**\n\n"
            "Set up a PIN or security questions for approving repair patches.\n\n"
            "**Commands:**\n"
            "`/security pin 1234` — Set your 4-digit PIN\n"
            "`/security question What is your pet? | Fluffy` — Add a security Q&A\n"
            "`/security status` — View current config",
            parse_mode="Markdown",
        )
        return

    subcommand = args[1].lower()

    # Shorthand: /security 1234 → treat as /security pin 1234
    if subcommand.isdigit() and len(subcommand) == 4:
        args = [args[0], "pin", subcommand]
        subcommand = "pin"

    from sqlalchemy import select
    from src.db.session import async_session
    from src.db.models import User, OwnerSecurityConfig
    from src.security.challenge import hash_pin, hash_security_answer

    async with async_session() as session:
        user_result = await session.execute(
            select(User).where(User.telegram_id == message.from_user.id)
        )
        user = user_result.scalar_one_or_none()
        if not user:
            await message.answer("User not found. Send any message first to register.")
            return

        config_result = await session.execute(
            select(OwnerSecurityConfig).where(OwnerSecurityConfig.user_id == user.id)
        )
        config = config_result.scalar_one_or_none()

        if subcommand == "pin":
            pin_value = args[2].strip() if len(args) > 2 else ""
            if not pin_value.isdigit() or len(pin_value) != 4:
                await message.answer("PIN must be exactly 4 digits. Example: `/security pin 1234`", parse_mode="Markdown")
                return

            hashed = hash_pin(pin_value)
            if config:
                config.pin_hash = hashed
            else:
                session.add(OwnerSecurityConfig(user_id=user.id, pin_hash=hashed))
            await session.commit()
            await message.answer("✅ Security PIN updated. You'll use this to approve repair patches.")

        elif subcommand == "question":
            qa_text = args[2].strip() if len(args) > 2 else ""
            if "|" not in qa_text:
                await message.answer(
                    "Format: `/security question Your question? | Your answer`",
                    parse_mode="Markdown",
                )
                return

            question, answer = qa_text.split("|", 1)
            question = question.strip()
            answer = answer.strip()
            if not question or not answer:
                await message.answer("Both question and answer must be non-empty.")
                return

            qa_entry = {"q": question, "a_hash": hash_security_answer(answer)}
            if config:
                existing_qa = config.security_qa or []
                existing_qa.append(qa_entry)
                config.security_qa = existing_qa
            else:
                session.add(OwnerSecurityConfig(
                    user_id=user.id,
                    security_qa=[qa_entry],
                ))
            await session.commit()
            await message.answer(f"✅ Security question added: **{question}**", parse_mode="Markdown")

        elif subcommand == "status":
            if not config:
                await message.answer("🔐 No security config set. Use `/security pin` or `/security question` to set one up.", parse_mode="Markdown")
                return

            lines = ["🔐 **Security Config**\n"]
            if config.pin_hash:
                lines.append("- **PIN:** ✅ Set")
            else:
                lines.append("- **PIN:** ❌ Not set")
            qa_count = len(config.security_qa) if config.security_qa else 0
            lines.append(f"- **Security questions:** {qa_count}")
            lines.append(f"- **Challenge TTL:** {config.challenge_ttl}s")
            await message.answer("\n".join(lines), parse_mode="Markdown")

        else:
            await message.answer(
                "Unknown subcommand.\n\n"
                "**Usage:**\n"
                "`/security pin 1234` — Set your 4-digit PIN\n"
                "`/security 1234` — Shorthand for setting PIN\n"
                "`/security question What is your pet? | Fluffy` — Add a Q&A\n"
                "`/security status` — View current config",
                parse_mode="Markdown",
            )


@router.message(Command("voice"))
async def cmd_voice(message: Message) -> None:
    """Handle /voice — view or set the TTS voice used for audio replies."""
    if not await is_allowed(message.from_user.id):
        return

    from src.bot.voice import TTS_VOICES, get_user_tts_voice, set_user_tts_voice

    args = message.text.split(maxsplit=1) if message.text else []

    if len(args) < 2:
        current = await get_user_tts_voice(message.from_user.id)
        voices_list = "  ".join(f"`{v}`" for v in TTS_VOICES)
        await message.answer(
            f"🎙 Current TTS voice: *{current}*\n\n"
            f"Available voices: {voices_list}\n\n"
            f"Change with: `/voice <name>` — e.g. `/voice nova`",
            parse_mode="Markdown",
        )
        return

    requested = args[1].strip().lower()
    if requested not in TTS_VOICES:
        voices_list = ", ".join(f"`{v}`" for v in TTS_VOICES)
        await message.answer(
            f"Unknown voice `{requested}`. Choose from: {voices_list}",
            parse_mode="Markdown",
        )
        return

    ok = await set_user_tts_voice(message.from_user.id, requested)
    if ok:
        await message.answer(
            f"✅ TTS voice set to *{requested}*. Your next audio reply will use this voice.",
            parse_mode="Markdown",
        )
    else:
        await message.answer("Failed to save voice preference. Please try again.")


# ── Repair Ticket Commands ───────────────────────────────────────────────

@router.message(Command("tickets"))
async def cmd_tickets(message: Message) -> None:
    """Handle /tickets — list all open repair tickets."""
    if not await is_allowed(message.from_user.id):
        return

    from sqlalchemy import select
    from src.db.session import async_session
    from src.db.models import RepairTicket, User

    try:
        async with async_session() as session:
            user_result = await session.execute(
                select(User).where(User.telegram_id == message.from_user.id)
            )
            user = user_result.scalar_one_or_none()
            if user is None:
                await message.answer("No user record found.")
                return

            result = await session.execute(
                select(RepairTicket)
                .where(
                    RepairTicket.user_id == user.id,
                    RepairTicket.status.notin_(["deployed", "closed"]),
                )
                .order_by(RepairTicket.created_at.desc())
                .limit(20)
            )
            tickets = result.scalars().all()

        if not tickets:
            await message.answer(
                "✅ No open repair tickets — everything looks clean!",
                parse_mode="Markdown",
            )
            return

        lines = ["🎫 *Open Repair Tickets*\n"]
        for t in tickets:
            status_emoji = {
                "open": "🔴",
                "debug_analysis_ready": "🟡",
                "plan_ready": "🟠",
                "verifying": "🔵",
                "verification_failed": "❌",
                "ready_for_deploy": "✅",
            }.get(t.status, "⚪")
            lines.append(
                f"{status_emoji} *#{t.id}* — {t.title[:60]}\n"
                f"   Status: `{t.status}` | Priority: `{t.priority}`\n"
                f"   Created: {t.created_at.strftime('%Y-%m-%d %H:%M') if t.created_at else 'unknown'}"
            )

        lines.append("\nUse `/ticket approve <id>` to deploy a ready fix.")
        lines.append("Use `/ticket close <id>` to dismiss a ticket.")
        await message.answer("\n\n".join(lines), parse_mode="Markdown")

    except Exception as exc:
        logger.exception("cmd_tickets error: %s", exc)
        await message.answer("Failed to load tickets. Please try again.")


@router.message(Command("ticket"))
async def cmd_ticket(message: Message) -> None:
    """Handle /ticket approve <id> and /ticket close <id>."""
    if not await is_allowed(message.from_user.id):
        return

    if message.from_user.id != settings.owner_telegram_id:
        await message.answer("Only the owner can manage repair tickets.")
        return

    args = (message.text or "").split(maxsplit=2)
    if len(args) < 3:
        await message.answer(
            "Usage:\n"
            "  `/ticket approve <id>` — deploy a verified fix\n"
            "  `/ticket close <id>` — dismiss a ticket without deploying",
            parse_mode="Markdown",
        )
        return

    action = args[1].strip().lower()
    try:
        ticket_id = int(args[2].strip())
    except ValueError:
        await message.answer("Invalid ticket ID. Use a number, e.g. `/ticket approve 5`", parse_mode="Markdown")
        return

    if action == "approve":
        await message.answer(f"🔐 Approving ticket #{ticket_id}... verifying security.")
        from src.repair.engine import approve_ticket_deploy
        result = await approve_ticket_deploy(ticket_id, message.from_user.id)
        await message.answer(result, parse_mode="Markdown")

    elif action == "close":
        from sqlalchemy import select
        from src.db.session import async_session
        from src.db.models import RepairTicket
        try:
            async with async_session() as session:
                ticket = await session.get(RepairTicket, ticket_id)
                if ticket is None:
                    await message.answer(f"Ticket #{ticket_id} not found.")
                    return
                ticket.status = "closed"
                await session.commit()
            await message.answer(f"✅ Ticket #{ticket_id} closed.")
        except Exception as exc:
            logger.exception("Failed to close ticket %s: %s", ticket_id, exc)
            await message.answer("Failed to close ticket. Please try again.")
    else:
        await message.answer(
            "Unknown action. Use `approve` or `close`.",
            parse_mode="Markdown",
        )


@router.callback_query(F.data.startswith("repair_approve:"))
async def cb_repair_approve(callback: CallbackQuery) -> None:
    """Inline button: owner taps '✅ Apply fix now'."""
    if not await is_allowed(callback.from_user.id):
        await callback.answer("Not authorized.", show_alert=True)
        return

    if callback.from_user.id != settings.owner_telegram_id:
        await callback.answer("Only the owner can approve repairs.", show_alert=True)
        return

    try:
        ticket_id = int(callback.data.split(":")[1])
    except (IndexError, ValueError):
        await callback.answer("Invalid ticket ID.", show_alert=True)
        return

    await callback.answer("Applying fix…", show_alert=False)
    await callback.message.edit_text(
        f"🔧 Applying fix for ticket #{ticket_id}…",
        parse_mode="Markdown",
    )

    from src.repair.engine import approve_ticket_deploy
    result = await approve_ticket_deploy(ticket_id, callback.from_user.id)
    await callback.message.answer(result, parse_mode="Markdown")


@router.callback_query(F.data.startswith("repair_skip:"))
async def cb_repair_skip(callback: CallbackQuery) -> None:
    """Inline button: owner taps '❌ Skip for now'."""
    if not await is_allowed(callback.from_user.id):
        await callback.answer("Not authorized.", show_alert=True)
        return

    try:
        ticket_id = int(callback.data.split(":")[1])
    except (IndexError, ValueError):
        await callback.answer("Invalid ticket ID.", show_alert=True)
        return

    await callback.answer("Skipped.", show_alert=False)
    await callback.message.edit_text(
        f"⏸ Fix for ticket #{ticket_id} skipped. Use `/ticket approve {ticket_id}` whenever you're ready.",
        parse_mode="Markdown",
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


@router.message(Command("skills"))
async def cmd_skills(message: Message) -> None:
    """Handle /skills — manage AI assistant skills.

    Commands:
    /skills                    - List installed skills
    /skills list               - Same as above
    /skills create             - Start AI-guided skill creation
    /skills info <id>          - Show skill details
    /skills activate <id>      - Enable a skill
    /skills deactivate <id>    - Disable a skill
    /skills delete <id>        - Remove a user-created skill
    /skills reload             - Hot-reload filesystem skills
    """
    if not await is_allowed(message.from_user.id):
        return

    args = message.text.split() if message.text else []
    subcommand = args[1] if len(args) > 1 else "list"
    arg = args[2] if len(args) > 2 else None

    if subcommand == "create":
        # Start AI-guided skill creation - delegate to orchestrator
        await message.answer(
            "🎨 **Skill Creation Wizard**\n\n"
            "I'll help you create a custom skill. Describe what you want:\n\n"
            "• What should this skill help with?\n"
            "• Any specific format or style preferences?\n\n"
            "Example: *\"A skill for writing weekly status reports that pulls from my calendar\"*",
            parse_mode="Markdown"
        )
        # Set a session flag so the next message goes to skill factory
        from src.memory.conversation import set_session_field
        await set_session_field(message.from_user.id, "skill_creation_active", "true")
        return

    if subcommand == "reload":
        # Hot-reload filesystem skills
        from src.skills.loader import SkillLoader
        from src.agents.orchestrator import _registry_cache

        loader = SkillLoader()
        new_skills = loader.load_all_from_directory()

        # Invalidate cache to force re-registration
        user_id = message.from_user.id
        if user_id in _registry_cache:
            del _registry_cache[user_id]

        await message.answer(
            f"🔄 Reloaded {len(new_skills)} skills from filesystem.\n"
            "New skills are now available.",
            parse_mode="Markdown"
        )
        return

    if subcommand in ("activate", "deactivate"):
        if not arg:
            await message.answer(
                f"Usage: `/skills {subcommand} <skill_id>`", parse_mode="Markdown"
            )
            return

        # Get user's registry and toggle skill
        # Note: This requires access to the orchestrator's registry
        # For now, provide informational response
        action = "activated" if subcommand == "activate" else "deactivated"
        await message.answer(
            f"Skill `{arg}` will be {action} on next conversation.\n\n"
            "Note: Full activation/deactivation coming in next update.",
            parse_mode="Markdown"
        )
        return

    if subcommand == "delete":
        if not arg:
            await message.answer("Usage: `/skills delete <skill_id>`", parse_mode="Markdown")
            return

        from pathlib import Path
        skill_path = Path(f"src/user_skills/{arg}")

        if not skill_path.exists():
            await message.answer(f"Skill `{arg}` not found.", parse_mode="Markdown")
            return

        # Remove the skill directory
        import shutil
        try:
            shutil.rmtree(skill_path)
            await message.answer(
                f"✅ Skill `{arg}` deleted.\n"
                "Run `/skills reload` to apply changes.",
                parse_mode="Markdown"
            )
        except Exception as e:
            logger.error("Failed to delete skill %s: %s", arg, e)
            await message.answer(f"Failed to delete skill: {e}")
        return

    if subcommand == "info":
        if not arg:
            await message.answer("Usage: `/skills info <skill_id>`", parse_mode="Markdown")
            return

        # Try to load and display skill info
        from src.skills.loader import SkillLoader
        from pathlib import Path

        skill_path = Path(f"src/user_skills/{arg}")
        if not skill_path.exists():
            await message.answer(f"Skill `{arg}` not found.", parse_mode="Markdown")
            return

        try:
            loader = SkillLoader()
            skill = loader.load_from_path(skill_path)

            info_lines = [
                f"📦 **{skill.name}**",
                f"",
                f"**ID:** `{skill.id}`",
                f"**Group:** {skill.group.value}",
                f"**Version:** {skill.version}",
                f"**Author:** {skill.author}",
                f"**Tags:** {', '.join(skill.tags) or 'None'}",
                f"**Type:** {'Knowledge-only' if skill.is_knowledge_only() else 'Tool-enabled'}",
                f"**Status:** {'Active' if skill.is_active else 'Inactive'}",
                f"",
                f"**Description:** {skill.description}",
            ]

            if skill.routing_hints:
                info_lines.append(f"")
                info_lines.append("**Triggers:**")
                for hint in skill.routing_hints:
                    info_lines.append(f"  • {hint}")

            if skill.requires_skills:
                info_lines.append(f"")
                info_lines.append(f"**Dependencies:** {', '.join(skill.requires_skills)}")

            await message.answer("\n".join(info_lines), parse_mode="Markdown")
        except Exception as e:
            logger.error("Failed to load skill info for %s: %s", arg, e)
            await message.answer(f"Error loading skill info: {e}")
        return

    # Default: list skills
    from src.skills.loader import SkillLoader
    from pathlib import Path

    skills_dir = Path("src/user_skills")
    if not skills_dir.exists():
        await message.answer(
            "No user skills directory found.\n"
            "Create skills in the `src/user_skills/` folder.",
            parse_mode="Markdown"
        )
        return

    loader = SkillLoader()
    skills = loader.load_all_from_directory()

    if not skills:
        await message.answer(
            "📚 **Skills**\n\n"
            "No user-created skills found.\n\n"
            "**Create a skill:**\n"
            "• `/skills create` - AI-guided creation\n"
            "• Or add SKILL.md files to `src/user_skills/` folder",
            parse_mode="Markdown"
        )
        return

    lines = ["📚 **Your Skills**\n"]
    for skill in skills:
        status = "🟢" if skill.is_active else "⚪"
        type_icon = "📖" if skill.is_knowledge_only() else "🔧"
        lines.append(f"{status} {type_icon} `{skill.id}` - {skill.name}")

    lines.append(f"")
    lines.append(f"**Total:** {len(skills)} skills")
    lines.append(f"")
    lines.append("**Commands:**")
    lines.append("• `/skills info <id>` - Details")
    lines.append("• `/skills create` - New skill")
    lines.append("• `/skills reload` - Refresh")

    await message.answer("\n".join(lines), parse_mode="Markdown")


@router.message(Command("orgs"))
async def cmd_orgs(message: Message) -> None:
    """Handle /orgs — manage organizations and launch creation wizard."""
    if not await is_allowed(message.from_user.id):
        return

    args = message.text.split(maxsplit=2) if message.text else []
    subcommand = args[1].lower() if len(args) > 1 else "list"
    arg = args[2].strip() if len(args) > 2 else None

    from sqlalchemy import select
    from src.db.session import async_session
    from src.db.models import User, AuditLog
    from src.orchestration.agent_registry import Organization, OrgActivity
    from src.memory.conversation import set_session_field

    async with async_session() as session:
        user_result = await session.execute(
            select(User).where(User.telegram_id == message.from_user.id)
        )
        user = user_result.scalar_one_or_none()

        if not user:
            await message.answer("I couldn't find your local user record yet.")
            return

        if subcommand == "create":
            await message.answer(
                "🧠 **AI-Powered Organization Wizard**\n\n"
                "Use `/neworg <your goal>` to let Atlas AI design your team, tasks, and tools automatically.\n\n"
                "Example:\n"
                "`/neworg Find a new job as a senior software engineer in 90 days`",
                parse_mode="Markdown",
            )
            return

        if subcommand in ("pause", "resume", "delete", "info") and not arg:
            await message.answer(f"Usage: `/orgs {subcommand} <org_id>`", parse_mode="Markdown")
            return

        if subcommand in ("pause", "resume", "delete", "info"):
            try:
                org_id = int(arg)
            except (TypeError, ValueError):
                await message.answer("Organization ID must be a number.")
                return

            org_result = await session.execute(
                select(Organization).where(
                    Organization.id == org_id,
                    Organization.owner_user_id == user.id,
                )
            )
            org = org_result.scalar_one_or_none()
            if not org:
                await message.answer(f"Organization `{org_id}` not found.", parse_mode="Markdown")
                return

            if subcommand == "info":
                await message.answer(
                    "\n".join([
                        f"🏢 **{org.name}**",
                        f"",
                        f"**ID:** `{org.id}`",
                        f"**Status:** {org.status}",
                        f"**Goal:** {org.goal or 'None'}",
                        f"**Description:** {org.description or 'None'}",
                    ]),
                    parse_mode="Markdown",
                )
                return

            if subcommand == "pause":
                org.status = "paused"
                session.add(OrgActivity(
                    org_id=org.id,
                    action="org_paused",
                    details="Organization paused via Telegram",
                    source="telegram",
                ))
                await session.commit()
                await message.answer(f"⏸️ Organization `{org.name}` paused.", parse_mode="Markdown")
                return

            if subcommand == "resume":
                org.status = "active"
                session.add(OrgActivity(
                    org_id=org.id,
                    action="org_resumed",
                    details="Organization resumed via Telegram",
                    source="telegram",
                ))
                await session.commit()
                await message.answer(f"▶️ Organization `{org.name}` resumed.", parse_mode="Markdown")
                return

            if subcommand == "delete":
                org_name = org.name
                session.add(AuditLog(
                    user_id=user.id,
                    direction="outbound",
                    platform="telegram",
                    message_text=f"Organization deleted: {org_name} ({org.id})",
                    agent_name="org_telegram",
                    tools_used={"action": "org_deleted", "org_id": org.id, "org_name": org_name},
                ))
                await session.delete(org)
                await session.commit()
                await message.answer(f"🗑️ Organization `{org_name}` deleted.", parse_mode="Markdown")
                return

        orgs_result = await session.execute(
            select(Organization)
            .where(Organization.owner_user_id == user.id)
            .order_by(Organization.created_at.desc())
        )
        orgs = orgs_result.scalars().all()

    if not orgs:
        await message.answer(
            "🏢 **Organizations**\n\n"
            "You don't have any organizations yet.\n\n"
            "Use `/orgs create` to start the wizard.",
            parse_mode="Markdown",
        )
        return

    lines = ["🏢 **Your Organizations**\n"]
    for org in orgs:
        status_icon = "🟢" if org.status == "active" else "⏸️"
        lines.append(f"{status_icon} `{org.id}` - {org.name}")
        if org.goal:
            lines.append(f"   Goal: {org.goal}")

    lines.extend([
        "",
        "**Commands:**",
        "• `/neworg <goal>` - AI-powered new organization",
        "• `/orgs info <id>` - Details",
        "• `/orgs pause <id>` - Deactivate",
        "• `/orgs resume <id>` - Reactivate",
        "• `/orgs delete <id>` - Delete",
    ])
    await message.answer("\n".join(lines), parse_mode="Markdown")


# ── /neworg AI-powered organization creation ──────────────────────────

async def _call_org_setup(goal: str, org_name: str, plan: dict, telegram_id: int) -> dict:
    """Call /api/orgs/setup with a pre-built plan from Telegram context."""
    import httpx
    api_base = "http://localhost:8100"
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(
            f"{api_base}/api/orgs/setup",
            json={"goal": goal, "org_name": org_name, "plan": plan},
            headers={"X-Telegram-Id": str(telegram_id)},
        )
        resp.raise_for_status()
        return resp.json()


async def _call_org_validate(org_id: int, telegram_id: int) -> dict:
    """Call /api/orgs/{id}/validate to get cohesion report."""
    import httpx
    api_base = "http://localhost:8100"
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            f"{api_base}/api/orgs/{org_id}/validate",
            headers={"X-Telegram-Id": str(telegram_id)},
        )
        resp.raise_for_status()
        return resp.json()


def _format_plan_summary(plan: dict) -> str:
    """Format the AI plan as a readable Telegram message."""
    lines = [
        f"🏢 *{plan.get('org_name', 'New Org')}*",
        f"_{plan.get('org_goal', '')}_",
        "",
    ]
    agents = plan.get("agents") or []
    if agents:
        lines.append(f"👥 *Agents ({len(agents)}):*")
        for a in agents:
            skills = ", ".join(a.get("skills") or [])
            tools = ", ".join((a.get("allowed_tools") or [])[:3])
            tier = a.get("model_tier", "general")
            lines.append(f"  • *{a.get('name')}* [{tier}]")
            if skills:
                lines.append(f"    Skills: {skills}")
            if tools:
                lines.append(f"    Tools: {tools}")
        lines.append("")

    tasks = plan.get("tasks") or []
    if tasks:
        lines.append(f"📋 *Tasks ({len(tasks)}):*")
        for t in tasks[:8]:
            p = t.get("priority", "medium")
            icon = {"high": "🔴", "medium": "🟡", "low": "⚪"}.get(p, "🟡")
            lines.append(f"  {icon} {t.get('title')} → _{t.get('agent_name')}_")
        if len(tasks) > 8:
            lines.append(f"  ...and {len(tasks) - 8} more")
        lines.append("")

    budget = plan.get("budget_cap_usd")
    if budget:
        lines.append(f"💰 Budget suggestion: ${budget}/month")
        lines.append("")

    lines.append("Confirm creation?")
    return "\n".join(lines)


@router.message(Command("neworg"))
async def cmd_neworg(message: Message) -> None:
    """Handle /neworg <goal> — AI-powered organization creation with live plan."""
    if not await is_allowed(message.from_user.id):
        return

    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        await message.answer(
            "🧠 *AI Organization Wizard*\n\n"
            "Usage: `/neworg <goal>`\n\n"
            "Example:\n"
            "`/neworg Land a senior SWE role in 90 days`\n"
            "`/neworg Build a content marketing pipeline for my startup`",
            parse_mode="Markdown",
        )
        return

    goal = parts[1].strip()
    thinking_msg = await message.answer("🧠 Planning your organization… (this takes ~10 seconds)", parse_mode="Markdown")

    try:
        from src.orchestration.api import _build_org_plan
        plan = await _build_org_plan(goal)
    except Exception as exc:
        await thinking_msg.edit_text(f"❌ Planning failed: {exc}")
        return

    org_name = plan.get("org_name") or goal[:40]

    # Store plan in Redis keyed by a short UUID
    plan_key = str(uuid.uuid4())[:8]
    try:
        from src.memory.conversation import set_session_field
        await set_session_field(
            message.from_user.id,
            f"neworg_plan_{plan_key}",
            json.dumps({"goal": goal, "org_name": org_name, "plan": plan}),
        )
    except Exception as exc:
        logger.warning("Failed to store neworg plan in Redis: %s", exc)
        await thinking_msg.edit_text("❌ Could not store plan. Please try again.")
        return

    summary = _format_plan_summary(plan)
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[[
            InlineKeyboardButton(text="✅ Confirm", callback_data=f"neworg_confirm:{plan_key}:{message.from_user.id}"),
            InlineKeyboardButton(text="❌ Cancel", callback_data=f"neworg_cancel:{plan_key}:{message.from_user.id}"),
        ]]
    )

    await thinking_msg.edit_text(summary, parse_mode="Markdown", reply_markup=keyboard)


@router.callback_query(F.data.startswith("neworg_confirm:"))
async def cb_neworg_confirm(callback: CallbackQuery) -> None:
    """Handle Confirm button for /neworg — create org atomically."""
    _, plan_key, orig_user_id = callback.data.split(":", 2)
    telegram_id = callback.from_user.id

    if str(telegram_id) != orig_user_id:
        await callback.answer("This confirmation is not for you.", show_alert=True)
        return

    await callback.answer("Creating your organization…")
    await callback.message.edit_reply_markup(reply_markup=None)

    try:
        from src.memory.conversation import get_session_field, delete_session_field
        raw = await get_session_field(telegram_id, f"neworg_plan_{plan_key}")
        if not raw:
            await callback.message.answer("❌ Plan expired. Run `/neworg` again.", parse_mode="Markdown")
            return
        data = json.loads(raw)
    except Exception as exc:
        await callback.message.answer(f"❌ Could not retrieve plan: {exc}")
        return

    creating_msg = await callback.message.answer("⚙️ Creating organization…")
    try:
        result = await _call_org_setup(
            goal=data["goal"],
            org_name=data["org_name"],
            plan=data["plan"],
            telegram_id=telegram_id,
        )
        org_id = result["org_id"]
        org_name = result["org_name"]
        agent_count = len(result.get("agents", []))
        task_count = len(result.get("tasks", []))
    except Exception as exc:
        await creating_msg.edit_text(f"❌ Creation failed: {exc}")
        return

    # Run cohesion validation
    cohesion_text = ""
    try:
        val = await _call_org_validate(org_id, telegram_id)
        score = val.get("score", 0)
        warnings = val.get("warnings") or []
        errors = val.get("errors") or []
        score_icon = "✅" if score >= 80 else "⚠️" if score >= 60 else "❌"
        cohesion_text = (
            f"\n\n{score_icon} *Cohesion Score: {score}/100*"
        )
        if errors:
            cohesion_text += "\n" + "\n".join(f"  ❌ {e}" for e in errors[:3])
        if warnings:
            cohesion_text += "\n" + "\n".join(f"  ⚠️ {w}" for w in warnings[:5])
        if score >= 80 and not warnings:
            cohesion_text += "\n  All agents, skills & tools validated."
    except Exception:
        pass

    # Clean up Redis key
    try:
        from src.memory.conversation import delete_session_field
        await delete_session_field(telegram_id, f"neworg_plan_{plan_key}")
    except Exception:
        pass

    await creating_msg.edit_text(
        f"✅ *Organization created!*\n\n"
        f"*Name:* {org_name}\n"
        f"*ID:* `{org_id}`\n"
        f"*Agents:* {agent_count}\n"
        f"*Tasks:* {task_count}"
        f"{cohesion_text}",
        parse_mode="Markdown",
    )


@router.callback_query(F.data.startswith("neworg_cancel:"))
async def cb_neworg_cancel(callback: CallbackQuery) -> None:
    """Handle Cancel button for /neworg."""
    _, plan_key, orig_user_id = callback.data.split(":", 2)
    telegram_id = callback.from_user.id

    if str(telegram_id) != orig_user_id:
        await callback.answer("This is not your confirmation.", show_alert=True)
        return

    await callback.answer("Cancelled.")
    await callback.message.edit_reply_markup(reply_markup=None)

    try:
        from src.memory.conversation import delete_session_field
        await delete_session_field(telegram_id, f"neworg_plan_{plan_key}")
    except Exception:
        pass

    await callback.message.answer(
        "❌ Organization creation cancelled.\n"
        "Run `/neworg <goal>` again whenever you're ready.",
        parse_mode="Markdown",
    )


@router.message()
async def handle_message(message: Message) -> None:
    """Handle all non-command messages — route to orchestrator (text + voice)."""
    if not await is_allowed(message.from_user.id):
        return

    if await is_cost_capped(message.from_user.id):
        await message.answer("Daily usage limit reached. Resets at midnight.")
        return

    if _is_user_rate_limited(message.from_user.id):
        await message.answer("You're sending messages too fast. Please wait a moment.")
        return

    # Phase 6: Voice message support
    if message.voice:
        try:
            await message.answer_chat_action(action="typing")
        except Exception:
            pass
        from src.bot.voice import transcribe_voice
        text = await transcribe_voice(message.voice.file_id, message.bot)
        if text.startswith("("):
            await message.answer(text)
            return
        await message.answer(f'🎤 _"{text}"_', parse_mode="Markdown")
        try:
            from src.memory.conversation import set_session_field
            await set_session_field(message.from_user.id, "wants_audio_reply", "true")
        except Exception:
            pass
        queue = await _get_user_queue(message.from_user.id)
        await queue.put((message, _run_orchestrator_with_text(message, text)))
        return

    if message.photo:
        try:
            from src.memory.conversation import set_session_field

            photo = message.photo[-1]
            file_info = await message.bot.get_file(photo.file_id)
            file_url = f"https://api.telegram.org/file/bot{settings.telegram_bot_token}/{file_info.file_path}"

            import httpx

            async with httpx.AsyncClient() as client:
                response = await client.get(file_url, timeout=30)
                response.raise_for_status()
                photo_bytes = response.content

            suffix = file_info.file_path.rsplit(".", 1)[-1].lower() if "." in file_info.file_path else "jpg"
            mime_type = "image/png" if suffix == "png" else "image/jpeg"
            await set_session_field(
                message.from_user.id,
                "latest_uploaded_image",
                json.dumps(
                    {
                        "mime_type": mime_type,
                        "data_base64": base64.b64encode(photo_bytes).decode("utf-8"),
                    }
                ),
            )
        except Exception as exc:
            logger.exception("Photo upload handling failed: %s", exc)
            await message.answer("I couldn't read that photo. Please try sending it again.")
            return

        prompt = message.caption or "Please describe this image in detail."
        queue = await _get_user_queue(message.from_user.id)
        await queue.put((message, _run_orchestrator_with_text(message, prompt)))
        return

    if not message.text:
        return

    # Check if user is in an active interview session
    try:
        from src.memory.conversation import get_session_field, delete_session_field
        interview_active = await get_session_field(message.from_user.id, "interview_active")
        if interview_active == "true":
            from src.agents.persona_interview_agent import handle_interview_message
            response = await handle_interview_message(
                message.from_user.id, message.text
            )
            await message.answer(response, parse_mode="Markdown")

            # Check if interview session just completed — clear interview mode
            from src.agents.persona_interview_agent import get_interview_state
            state = await get_interview_state(message.from_user.id)
            if not state.get("interview_id"):
                await delete_session_field(message.from_user.id, "interview_active")
                await delete_session_field(message.from_user.id, "interview_session")
            return
    except Exception as e:
        logger.warning(
            "Interview session routing failed for user %d; falling back to normal routing: %s",
            message.from_user.id,
            e,
        )

    # Check if user is in organization creation mode
    try:
        from src.memory.conversation import get_session_field, delete_session_field, set_session_field
        from sqlalchemy import select
        from src.db.session import async_session
        from src.db.models import User
        from src.orchestration.agent_registry import Organization, OrgActivity

        org_creation_active = await get_session_field(message.from_user.id, "org_creation_active")
        if org_creation_active == "true":
            step = await get_session_field(message.from_user.id, "org_creation_step") or "name"

            if step == "name":
                await set_session_field(message.from_user.id, "org_creation_name", message.text.strip())
                await set_session_field(message.from_user.id, "org_creation_step", "goal")
                await message.answer(
                    "Great. Step 2 of 3: What is this organization trying to accomplish?",
                    parse_mode="Markdown",
                )
                return

            if step == "goal":
                await set_session_field(message.from_user.id, "org_creation_goal", message.text.strip())
                await set_session_field(message.from_user.id, "org_creation_step", "description")
                await message.answer(
                    "Step 3 of 3: Add an optional description, or reply `skip`.",
                    parse_mode="Markdown",
                )
                return

            if step == "description":
                name = await get_session_field(message.from_user.id, "org_creation_name")
                goal = await get_session_field(message.from_user.id, "org_creation_goal")
                description = None if message.text.strip().lower() == "skip" else message.text.strip()

                async with async_session() as session:
                    user_result = await session.execute(
                        select(User).where(User.telegram_id == message.from_user.id)
                    )
                    user = user_result.scalar_one_or_none()
                    if not user:
                        await message.answer("I couldn't find your local user record yet.")
                    else:
                        org = Organization(
                            name=name or "Untitled Organization",
                            goal=goal,
                            description=description,
                            owner_user_id=user.id,
                            status="active",
                        )
                        session.add(org)
                        await session.flush()
                        session.add(OrgActivity(
                            org_id=org.id,
                            action="org_created",
                            details=f"Organization '{org.name}' created via Telegram wizard",
                            source="telegram",
                        ))
                        await session.commit()
                        await session.refresh(org)
                        await message.answer(
                            "\n".join([
                                "✅ **Organization created**",
                                "",
                                f"**Name:** {org.name}",
                                f"**ID:** `{org.id}`",
                                f"**Goal:** {org.goal or 'None'}",
                            ]),
                            parse_mode="Markdown",
                        )

                await delete_session_field(message.from_user.id, "org_creation_active")
                await delete_session_field(message.from_user.id, "org_creation_step")
                await delete_session_field(message.from_user.id, "org_creation_name")
                await delete_session_field(message.from_user.id, "org_creation_goal")
                return
    except Exception as e:
        logger.warning(
            "Organization creation routing failed for user %d; falling back to normal routing: %s",
            message.from_user.id,
            e,
        )

    # Check if user is in skill creation mode
    try:
        from src.memory.conversation import get_session_field, delete_session_field
        skill_creation_active = await get_session_field(message.from_user.id, "skill_creation_active")
        if skill_creation_active == "true":
            from src.agents.skill_factory_agent import handle_skill_creation_message
            response = await handle_skill_creation_message(
                message.from_user.id, message.text
            )
            await message.answer(response, parse_mode="Markdown")

            # Check if skill creation completed
            state = await get_session_field(message.from_user.id, "skill_creation_state")
            if state == "completed":
                await delete_session_field(message.from_user.id, "skill_creation_active")
                await delete_session_field(message.from_user.id, "skill_creation_state")
            return
    except Exception as e:
        logger.warning(
            "Skill creation routing failed for user %d; falling back to normal routing: %s",
            message.from_user.id,
            e,
        )

    embedded_command = _extract_embedded_command(message.text)
    if embedded_command:
        if embedded_command.lower().startswith("/connect"):
            await _handle_connect_request(message, embedded_command)
            return

    # Detect explicit audio/voice response requests and set a session flag
    try:
        from src.memory.conversation import set_session_field, delete_session_field
        _lowered_text = message.text.lower()
        _audio_cues = (
            "reply with audio", "respond with audio", "answer with audio",
            "reply in audio", "respond in audio",
            "reply with voice", "respond with voice", "answer with voice",
            "reply in voice", "respond in voice",
            "send audio", "send voice", "voice reply", "audio reply",
            "speak your answer", "speak your response", "read it out",
            "read that out", "say it", "say that",
        )
        if any(cue in _lowered_text for cue in _audio_cues):
            await set_session_field(message.from_user.id, "wants_audio_reply", "true")
        else:
            await delete_session_field(message.from_user.id, "wants_audio_reply")
    except Exception:
        pass

    queue = await _get_user_queue(message.from_user.id)
    await queue.put((message, _run_orchestrator(message)))


async def _run_orchestrator(message: Message) -> None:
    """Run the orchestrator agent on the user's message."""
    await _run_orchestrator_with_text(message, message.text)


    # _run_orchestrator_with_text imported from handler_utils (canonical source)
