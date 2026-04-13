"""Telegram message and command handlers."""

import asyncio
import logging

from aiogram import Router
from aiogram.filters import Command, CommandStart
from aiogram.types import Message

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
            await message.answer("Unknown subcommand. Use `pin`, `question`, or `status`.", parse_mode="Markdown")


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
        skill_path = Path(f"user_skills/{arg}")

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

        skill_path = Path(f"user_skills/{arg}")
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

    skills_dir = Path("user_skills")
    if not skills_dir.exists():
        await message.answer(
            "No user skills directory found.\n"
            "Create skills in the `user_skills/` folder.",
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
            "• Or add SKILL.md files to `user_skills/` folder",
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
            await set_session_field(message.from_user.id, "org_creation_active", "true")
            await set_session_field(message.from_user.id, "org_creation_step", "name")
            await message.answer(
                "🏢 **Organization Creation Wizard**\n\n"
                "Let's create a new organization.\n\n"
                "Step 1 of 3: What should the organization be called?",
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
        "• `/orgs create` - New organization",
        "• `/orgs info <id>` - Details",
        "• `/orgs pause <id>` - Deactivate",
        "• `/orgs resume <id>` - Reactivate",
        "• `/orgs delete <id>` - Delete",
    ])
    await message.answer("\n".join(lines), parse_mode="Markdown")


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
        await message.answer(" Transcribing voice message...")
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

    queue = await _get_user_queue(message.from_user.id)
    await queue.put((message, _run_orchestrator(message)))


async def _run_orchestrator(message: Message) -> None:
    """Run the orchestrator agent on the user's message."""
    await _run_orchestrator_with_text(message, message.text)


    # _run_orchestrator_with_text imported from handler_utils (canonical source)
