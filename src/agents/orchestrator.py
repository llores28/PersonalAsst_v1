"""Orchestrator agent — the main triage/persona agent."""

import logging
import re
import time as _time
import json
from dataclasses import dataclass as _dataclass
from datetime import datetime, timedelta, timezone as datetime_timezone
from pathlib import Path
from typing import Optional as _Optional
from zoneinfo import ZoneInfo

import yaml
from agents import Agent, Runner, RunConfig, WebSearchTool, InputGuardrail, OutputGuardrail

from src.agents.persona_mode import assemble_persona_prompt
from src.models.router import ModelRole, TaskComplexity, select_model
from src.settings import settings
from src.agents.safety_agent import safety_check_guardrail, pii_check_guardrail
from src.agents.email_agent import _normalize_gmail_subject
from src.agents.tasks_agent import _ensure_google_tasks_tool_success, _format_google_tasks_error
from src.agents.tool_factory_agent import create_tool_factory_agent
from src.skills.registry import SkillRegistry
from src.skills.definition import SkillProfile
from src.skills.loader import SkillLoader
from src.skills.google_workspace import (
    build_gmail_skill,
    build_calendar_skill,
    build_tasks_skill,
    build_drive_skill,
    build_docs_skill,
    build_sheets_skill,
    build_slides_skill,
    build_contacts_skill,
)
from src.skills.internal import build_memory_skill, build_organization_skill, build_scheduler_skill
from src.skills.openrouter import build_openrouter_skill
from src.skills.dynamic import load_dynamic_skills
from src.integrations.workspace_mcp import (
    call_workspace_tool,
    get_connected_google_email,
    is_google_configured,
)
from src.action_policy import append_action_policy_context, build_task_local_context, is_contextual_follow_up_confirmation
from src.clarification import build_needs_input_result
from src.temporal import append_temporal_context, parse_calendar_time_range, parse_temporal_interpretation

logger = logging.getLogger(__name__)


_IMAGE_REQUEST_VERBS = (
    "create image",
    "create an image",
    "generate image",
    "generate an image",
    "make image",
    "make an image",
    "draw",
    "render",
    "illustrate",
)


@_dataclass
class _CachedRegistry:
    registry: SkillRegistry
    persona_base: str
    connected_email: _Optional[str]
    created_at: float


@_dataclass
class ImageAttachment:
    data_base64: str
    mime_type: str
    prompt: str = ""
    caption: str = ""
    model: str = ""


@_dataclass
class OrchestratorResult:
    text: str
    images: list[ImageAttachment]


_registry_cache: dict[int, _CachedRegistry] = {}
_REGISTRY_CACHE_TTL = 30.0  # seconds

# Background task tracking — prevents GC of fire-and-forget tasks
_background_tasks: set = set()


def _fire_and_forget(coro) -> None:
    """Schedule a coroutine as a background task with proper error handling."""
    import asyncio
    task = asyncio.create_task(coro)
    _background_tasks.add(task)
    task.add_done_callback(_on_background_task_done)


def _on_background_task_done(task) -> None:
    """Callback for background tasks — logs exceptions, removes from tracking set."""
    _background_tasks.discard(task)
    if task.cancelled():
        return
    exc = task.exception()
    if exc:
        logger.warning("Background task failed: %s: %s", type(exc).__name__, exc)


async def _notify_owner_error(user_telegram_id: int, user_message: str, response_text: str) -> None:
    """Fire-and-forget: push a proactive Telegram alert when a tool error is detected."""
    try:
        from src.bot.notifications import notify_owner_of_error
        # Use multi-token failure phrases (same set as detector) to pick the summary
        # line — avoids lifting bullet points like "troubleshooting media errors".
        _failure_words = (
            "tool call failed", "tool failed", "tool error", "skill failed",
            "unable to call", "unable to run", "failed to call", "failed to run",
            "api call failed", "api error", "mcp error", "connection error",
            "connection failed", "authentication failed", "permission denied",
            "traceback", "exception occurred", "internal error", "something went wrong",
        )
        lines = [ln.strip() for ln in response_text.splitlines() if ln.strip()]
        summary = next(
            (ln for ln in lines if any(w in ln.lower() for w in _failure_words)),
            "A tool or skill failure was detected."
        )
        await notify_owner_of_error(
            user_telegram_id=user_telegram_id,
            error_summary=summary[:300],
            user_message=user_message,
        )
    except Exception as exc:
        logger.debug("_notify_owner_error failed (non-critical): %s", exc)


_REPAIR_EXPLICIT_PHRASES = (
    "repair agent",
    "debug atlas",
    "diagnose atlas",
    "repair atlas",
    "debug the app",
    "diagnose the app",
    "debug this app",
    "diagnose this app",
    "determine a better verification command",
    "determine the verification command",
    "refine the verification",
    "refine verification",
    "better verification command",
    "fix the verification command",
    "wrong verification command",
)
_REPAIR_DEBUG_VERBS = (
    "fix",
    "debug",
    "diagnose",
    "troubleshoot",
    "investigate",
    "repair",
)
_REPAIR_SYMPTOM_PHRASES = (
    "issue",
    "issues",
    "problem",
    "problems",
    "bug",
    "bugs",
    "broken",
    "failing",
    "failure",
    "not working",
    "isn't working",
    "isnt working",
    "doesn't work",
    "doesnt work",
    "won't work",
    "wont work",
    "can't",
    "cannot",
    "unable",
    "incorrectly",
    "wrong",
    "error",
    "errors",
)
_REPAIR_SYSTEM_TERMS = (
    "atlas",
    "assistant",
    "app",
    "bot",
    "tool",
    "tools",
    "integration",
    "integrations",
    "plugin",
    "plugins",
    "onedrive",
    "one drive",
    "gmail",
    "calendar",
    "google tasks",
    "tasks api",
    "google drive",
    "docs",
    "sheets",
    "slides",
    "contacts",
    "linkedin",
    "browser",
    "scheduler",
    "memory",
    "oauth",
    "credential",
    "credentials",
    "graph",
    "docker",
    "container",
)
_ROUTINE_THIRD_PARTY_ACTIONS = (
    "move",
    "rename",
    "create folder",
    "list",
    "show",
    "find",
    "search",
    "inspect",
    "open",
    "share",
    "upload",
    "download",
    "organize",
)
_FAILED_REPAIR_HANDOFF_PHRASES = (
    "live repair agent",
    "from this chat",
    "can't actually hand",
    "cannot actually hand",
    "turn your description into a precise repair request",
    "turn this into a repair request",
)

# Multi-token phrases that strongly indicate a real tool/skill runtime failure.
# Single words like "error" or "tool" are intentionally excluded — they appear
# in normal planning and advice responses and cause false positives.
_ERROR_RESPONSE_INDICATORS = (
    "tool call failed",
    "tool failed",
    "tool error",
    "skill failed",
    "skill error",
    "unable to call",
    "unable to run",
    "unable to execute",
    "failed to call",
    "failed to run",
    "failed to execute",
    "couldn't call",
    "could not call",
    "could not run",
    "could not execute",
    "api call failed",
    "api error",
    "mcp error",
    "connection error",
    "connection failed",
    "authentication failed",
    "permission denied",
    "traceback",
    "exception occurred",
    "unhandled exception",
    "internal error",
    "something went wrong",
    "miswired",
)
# Suppression: if the response looks like planning/advice/informational,
# do NOT trigger the error detector even if an indicator phrase matches.
_ERROR_SUPPRESSION_PATTERNS = (
    "here's what",
    "here is what",
    "recommended",
    "you could",
    "you can",
    "i can help",
    "i can set up",
    "if you want",
    "best specialist",
    "useful tools",
    "useful skills",
    "core skills",
    "example tasks",
    "recommended agent",
    "here are",
    "here's a",
    "let me know",
    "would you like",
    "want me to",
    # Project/org setup summaries contain validation warnings like
    # "⚠️ not registered" or "⚠️ not installed" for planned-but-missing
    # skills/tools. These are informational, NOT runtime tool errors.
    "project created",
    "plan added to",
    "organization has been created",
    "agents created",
    "tasks created",
    "cli tools",
    "skills (",
    "validation issue",
    "not registered",
    "not installed",
)
_ERROR_TOOL_CONTEXT_TERMS = (
    "tool",
    "drive",
    "gmail",
    "calendar",
    "onedrive",
    "linkedin",
    "browser",
    "api",
    "mcp",
    "workspace",
    "folder",
    "file",
    "call",
)

# User phrases that mean "fix the last error" — route to repair when
# there is a recent error stored in Redis, NOT as a patch approval cue.
_FIX_IT_CUES = (
    "fix it",
    "fix this",
    "fix that",
    "fix the issue",
    "fix the error",
    "fix the problem",
    "fix the bug",
    "can you fix",
    "please fix",
    "go fix",
    "just fix",
)

def _load_persona_config() -> dict:
    """Load persona from config file (fallback when DB persona not yet created)."""
    config_path = Path(__file__).resolve().parents[2] / "src" / "config" / "persona_default.yaml"
    if config_path.exists():
        with open(config_path) as f:
            return yaml.safe_load(f)
    return {
        "assistant_name": settings.default_assistant_name,
        "personality": {
            "traits": ["helpful", "proactive", "concise"],
            "style": settings.default_persona_style,
        },
    }


def build_persona_prompt(user_name: str = "there", mode: str = "conversation") -> str:
    """Build a static persona prompt (Phase 1-2 fallback)."""
    config = _load_persona_config()
    traits = config.get("personality", {}).get("traits", ["helpful"])
    style = config.get("personality", {}).get("style", settings.default_persona_style)

    return assemble_persona_prompt(
        name=config.get("assistant_name", settings.default_assistant_name),
        user_name=user_name,
        personality_traits=", ".join(traits) if isinstance(traits, list) else str(traits),
        communication_style=style,
        user_preferences="(Still learning your preferences)",
        procedural_memories="(No learned workflows yet)",
        recent_context="(New conversation)",
        task_context="(No task-local context yet)",
        mode=mode,
    )


async def build_dynamic_persona_prompt(
    user_id: int,
    user_name: str = "there",
    task_context: str = "(No task-local context yet)",
    recent_context_override: str | None = None,
) -> str:
    from src.memory.persona import build_dynamic_persona_prompt as _build_dynamic_persona_prompt

    return await _build_dynamic_persona_prompt(
        user_id,
        user_name,
        task_context=task_context,
        recent_context_override=recent_context_override,
    )


def _is_simple_connected_gmail_check(user_message: str) -> bool:
    lowered = user_message.strip().lower()
    gmail_verbs = (
        "check my email",
        "check my inbox",
        "check my gmail",
        "check my unread email",
        "check my unread emails",
        "check my last unread email",
        "check my latest unread email",
        "check my most recent unread email",
        "show my email",
        "show my inbox",
        "show my gmail",
        "show my unread email",
        "show my last unread email",
        "read my email",
        "read my inbox",
        "read my unread email",
        "read my last unread email",
        "read my latest unread email",
        "last unread email",
        "latest unread email",
        "most recent unread email",
        "what's in my inbox",
        "whats in my inbox",
    )
    return any(phrase in lowered for phrase in gmail_verbs)


def _gmail_search_query_for_message(user_message: str) -> str:
    lowered = user_message.strip().lower()
    if "unread" in lowered:
        return "in:inbox is:unread"
    if "today" in lowered:
        return "in:inbox newer_than:1d"
    return "in:inbox"


def _is_latest_unread_email_request(user_message: str) -> bool:
    lowered = user_message.strip().lower()
    latest_unread_phrases = (
        "last unread email",
        "latest unread email",
        "most recent unread email",
        "last unread gmail",
        "latest unread gmail",
    )
    return any(phrase in lowered for phrase in latest_unread_phrases)


def _extract_gmail_found_count(search_results: str) -> int | None:
    match = re.search(r"Found\s+(\d+)\s+messages?", search_results, re.IGNORECASE)
    return int(match.group(1)) if match else None


def _extract_gmail_message_ids(search_results: str) -> list[str]:
    message_ids: list[str] = []
    seen: set[str] = set()
    for message_id in re.findall(r"Message ID:\s*([A-Za-z0-9]+)", search_results):
        if message_id not in seen:
            seen.add(message_id)
            message_ids.append(message_id)
    return message_ids


def _sender_display_name(sender_value: str) -> str:
    sender = sender_value.strip()
    match = re.match(r'^"?([^"<]+)"?\s*<[^>]+>$', sender)
    if match:
        return match.group(1).strip()
    return sender


def _normalize_summary_sentence(text: str, max_length: int = 220) -> str:
    normalized = re.sub(r"\s+", " ", text).strip(" -•\"'")
    normalized = normalized.replace("[", "").replace("]", "")
    normalized = normalized.replace("(", "").replace(")", "")
    normalized = re.sub(r"\s+", " ", normalized).strip(" -•\"'")
    if not normalized:
        return ""
    if len(normalized) > max_length:
        normalized = normalized[: max_length - 3].rsplit(" ", 1)[0].rstrip(" ,;:") + "..."
    normalized = normalized.rstrip('"\'')
    normalized = re.sub(r"([.!?]){2,}$", lambda match: match.group(0)[0], normalized)
    if normalized and normalized[-1] not in ".!?":
        normalized += "."
    return normalized


def _parse_gmail_batch_messages(batch_results: str) -> list[dict[str, str]]:
    normalized = re.sub(r"\n-{3,}\n", "\n", batch_results)
    raw_sections = [
        section.strip()
        for section in re.split(r"(?=Message ID:\s*)", normalized)
        if section.strip().startswith("Message ID:")
    ]
    messages: list[dict[str, str]] = []
    header_keys = {
        "message id",
        "subject",
        "from",
        "date",
        "message-id",
        "to",
        "web link",
    }

    for section in raw_sections:
        headers: dict[str, str] = {}
        body_lines: list[str] = []
        in_body = False
        for raw_line in section.splitlines():
            line = raw_line.strip()
            if not line and not in_body:
                continue

            header_match = re.match(r"^([A-Za-z][A-Za-z\- ]+):\s*(.*)$", line)
            if not in_body and header_match and header_match.group(1).lower() in header_keys:
                headers[header_match.group(1).lower()] = header_match.group(2).strip()
                continue

            in_body = True
            body_lines.append(line)

        messages.append(
            {
                "message_id": headers.get("message id", ""),
                "subject": headers.get("subject", ""),
                "from": headers.get("from", ""),
                "body": "\n".join(body_lines).strip(),
            }
        )

    return messages


def _clean_email_body_lines(body: str, subject: str, sender: str) -> list[str]:
    lines: list[str] = []
    seen: set[str] = set()
    subject_lower = subject.lower()
    sender_lower = sender.lower()

    footer_markers = (
        "unsubscribe",
        "manage your job alerts",
        "learn why we included this",
        "powered by",
        "privacy policy",
        "view job:",
        "apply with resume",
        "apply with profile",
        "see all jobs on linkedin",
        "you are receiving job alert emails",
    )
    trim_after_markers = (
        "download invoice",
        "download receipt",
        "questions? visit our support site",
        "powered by",
    )

    for raw_line in body.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if set(line) <= {"-", "_", "=", "*", " ", "•"}:
            continue

        lowered = line.lower()
        if any(lowered.startswith(prefix) for prefix in ("message id:", "subject:", "from:", "date:", "message-id:", "to:", "web link:")):
            continue
        if any(marker in lowered for marker in footer_markers):
            continue

        line = re.sub(r"https?://\S+", "", line)
        line = re.sub(r"\[[^\]]*https?://[^\]]*\]", "", line)
        line = re.sub(r"\([^)]*https?://[^)]*\)", "", line)
        for marker in trim_after_markers:
            marker_index = line.lower().find(marker)
            if marker_index != -1:
                line = line[:marker_index]
                break
        line = line.replace("[", "").replace("]", "")
        line = line.replace("(", "").replace(")", "")
        line = re.sub(r"\s+", " ", line).strip(" -•")
        if not line:
            continue

        normalized = line.lower()
        if normalized in {subject_lower, sender_lower}:
            continue
        if normalized in seen:
            continue

        seen.add(normalized)
        lines.append(line)

    return lines


def _infer_email_reason(subject: str, sender: str, body: str) -> str:
    subject_lower = subject.lower()
    sender_lower = sender.lower()
    body_lower = body.lower()

    if any(keyword in subject_lower for keyword in ("receipt", "invoice")) or "amount paid" in body_lower:
        return "Billing receipt or payment confirmation."
    if "job alert" in subject_lower or "linkedin" in sender_lower or "linkedin" in body_lower:
        return "Job alert or recruiting digest."
    if any(keyword in subject_lower for keyword in ("security", "verification", "verify", "password", "code")):
        return "Security or account verification."
    if any(keyword in subject_lower for keyword in ("meeting", "invite", "invitation", "calendar")):
        return "Calendar invite or scheduling update."
    if any(keyword in subject_lower for keyword in ("newsletter", "digest", "update")):
        return "Newsletter or product update."
    return "General email update."


def _build_email_summary(subject: str, sender: str, body: str) -> tuple[str, str]:
    reason = _infer_email_reason(subject, sender, body)
    cleaned_lines = _clean_email_body_lines(body, subject, sender)
    subject_lower = subject.lower()
    sender_lower = sender.lower()
    sender_name = sender or "The sender"

    if reason == "Billing receipt or payment confirmation.":
        amount_match = re.search(r"\$\d+(?:\.\d{2})?", body)
        service_match = re.search(
            r"Receipt\s+#\S+.*?\d{4}\s+(?P<service>.+?)\s+Qty\s+\d+",
            body,
            re.IGNORECASE | re.DOTALL,
        )
        first_sentence = f"{sender_name} sent a receipt confirming"
        if amount_match:
            first_sentence += f" a payment of {amount_match.group(0)}"
        else:
            first_sentence += " a recent payment"
        first_sentence = _normalize_summary_sentence(first_sentence)

        service_name = ""
        if service_match:
            service_name = _normalize_summary_sentence(service_match.group("service"), max_length=80).rstrip(".")

        if service_name:
            second_sentence = f"It covers your {service_name} plan for the current billing period."
        else:
            detail_line = next(
                (
                    line
                    for line in cleaned_lines
                    if any(keyword in line.lower() for keyword in ("receipt", "invoice", "amount paid", "$"))
                ),
                "",
            )
            second_sentence = _normalize_summary_sentence(detail_line) if detail_line else ""
        summary = " ".join(part for part in (first_sentence, second_sentence) if part)
        return reason, summary or first_sentence

    if reason == "Job alert or recruiting digest.":
        first_sentence = "LinkedIn sent a job alert digest with openings related to your saved search."
        detail_line = next(
            (
                line
                for line in cleaned_lines
                if len(line) > 25 and line.lower() not in {subject_lower, sender_lower}
            ),
            "",
        )
        second_sentence = _normalize_summary_sentence(detail_line) if detail_line else ""
        summary = " ".join(part for part in (first_sentence, second_sentence) if part)
        return reason, summary or first_sentence

    summary_sentences: list[str] = []
    for line in cleaned_lines:
        for sentence in re.split(r"(?<=[.!?])\s+", line):
            normalized = _normalize_summary_sentence(sentence, max_length=180)
            if len(normalized) < 20:
                continue
            summary_sentences.append(normalized)
            if len(summary_sentences) >= 2:
                break
        if len(summary_sentences) >= 2:
            break

    if not summary_sentences:
        summary_sentences.append("I could read the email, but the body preview was too sparse to summarize cleanly.")

    return reason, " ".join(summary_sentences[:2])


def _format_connected_gmail_summary(search_results: str, batch_results: str) -> str:
    found_count = _extract_gmail_found_count(search_results)
    messages = _parse_gmail_batch_messages(batch_results)
    if not messages:
        return search_results

    header = "Here are your latest emails:"
    if found_count is not None and found_count > len(messages):
        header = f"Here are your latest emails (showing {len(messages)} of {found_count}):"

    blocks: list[str] = []
    for index, message in enumerate(messages, start=1):
        sender = _sender_display_name(message.get("from", "") or "Unknown sender")
        subject = message.get("subject", "") or "(No subject)"
        reason, summary = _build_email_summary(subject, sender, message.get("body", ""))
        blocks.append(
            "\n".join(
                [
                    f"{index})",
                    f"From: {sender}",
                    f"Subject: {subject}",
                    f"Why it matters: {reason}",
                    f"Summary: {summary}",
                ]
            )
        )

    footer = ""
    if found_count is not None and found_count > len(messages):
        footer = "\n\nMore matching emails are available if you want me to keep going."

    return f"{header}\n\n" + "\n\n".join(blocks) + footer


def _format_single_connected_gmail_summary(message_results: str) -> str:
    messages = _parse_gmail_batch_messages(message_results)
    if not messages:
        return message_results

    message = messages[0]
    sender = _sender_display_name(message.get("from", "") or "Unknown sender")
    subject = message.get("subject", "") or "(No subject)"
    reason, summary = _build_email_summary(subject, sender, message.get("body", ""))

    return (
        "Here is your latest unread email:\n\n"
        "1)\n"
        f"From: {sender}\n"
        f"Subject: {subject}\n"
        f"Why it matters: {reason}\n"
        f"Summary: {summary}"
    )


async def _maybe_handle_connected_gmail_check(
    user_telegram_id: int,
    user_message: str,
) -> str | None:
    if not _is_simple_connected_gmail_check(user_message):
        return None

    connected_google_email = await get_connected_google_email(user_telegram_id)
    if not connected_google_email:
        return None

    single_unread_request = _is_latest_unread_email_request(user_message)

    try:
        search_results = await call_workspace_tool(
            "search_gmail_messages",
            {
                "query": _gmail_search_query_for_message(user_message),
                "user_google_email": connected_google_email,
                "page_size": 1 if single_unread_request else 10,
            },
        )
    except Exception as exc:
        logger.exception("Connected Gmail inbox check failed: %s", exc)
        return (
            f"I couldn't access Gmail for `{connected_google_email}` right now. "
            f"Google returned: {exc}. "
            f"If you recently reconnected, try `/connect google {connected_google_email}` once more."
        )

    found_count = _extract_gmail_found_count(search_results)
    if found_count == 0:
        return "You don't have any matching emails right now."

    message_ids = _extract_gmail_message_ids(search_results)
    if not message_ids:
        return search_results

    if single_unread_request:
        try:
            message_results = await call_workspace_tool(
                "get_gmail_messages_content_batch",
                {
                    "message_ids": [message_ids[0]],
                    "user_google_email": connected_google_email,
                    "format": "full",
                },
            )
        except Exception as exc:
            logger.exception("Connected Gmail unread message fetch failed: %s", exc)
            return (
                "I found your latest unread email, but I couldn't retrieve its contents right now. "
                f"Google returned: {exc}."
            )

        return _format_single_connected_gmail_summary(message_results)

    try:
        batch_results = await call_workspace_tool(
            "get_gmail_messages_content_batch",
            {
                "message_ids": message_ids[:3],
                "user_google_email": connected_google_email,
                "format": "full",
            },
        )
    except Exception as exc:
        logger.exception("Connected Gmail message summary fetch failed: %s", exc)
        return (
            "I found your latest emails, but I couldn't summarize them cleanly right now. "
            f"Google returned: {exc}.\n\n{search_results}"
        )
    return _format_connected_gmail_summary(search_results, batch_results)


def _is_simple_connected_calendar_check(user_message: str) -> bool:
    return parse_calendar_time_range(
        user_message,
        timezone=settings.default_timezone,
    ) is not None


def _is_calendar_date_follow_up(user_message: str) -> bool:
    lowered = " ".join(user_message.strip().lower().split())
    if not any(keyword in lowered for keyword in ("date", "day")):
        return False
    return any(
        phrase in lowered
        for phrase in (
            "whats the date",
            "what's the date",
            "what is the date",
            "what date",
            "which date",
            "what day",
            "which day",
            "i see time but no date",
            "no date",
            "missing date",
            "date for that",
        )
    )


def _latest_user_calendar_query_from_history(history: list[dict]) -> str | None:
    for turn in reversed(history):
        if turn.get("role") != "user":
            continue
        content = str(turn.get("content", ""))
        if _is_simple_connected_calendar_check(content):
            return content
    return None


def _calendar_time_range_for_message(user_message: str) -> tuple[str, str, str]:
    parsed = parse_calendar_time_range(
        user_message,
        timezone=settings.default_timezone,
    )
    if parsed is not None:
        return parsed

    timezone_name = settings.default_timezone
    tz = ZoneInfo(timezone_name)
    now = datetime.now(tz)
    start = datetime.combine(now.date(), datetime.min.time(), tzinfo=tz)
    end = start + timedelta(days=1)
    return start.isoformat(), end.isoformat(), "today"


def _format_calendar_date(start_value: str, end_value: str) -> str:
    try:
        start = datetime.fromisoformat(start_value).astimezone(ZoneInfo(settings.default_timezone))
        end = datetime.fromisoformat(end_value).astimezone(ZoneInfo(settings.default_timezone))
    except ValueError:
        return start_value

    start_text = f"{start.strftime('%a, %b')} {start.day}, {start.year}"
    end_text = f"{end.strftime('%a, %b')} {end.day}, {end.year}"
    if start.date() == end.date():
        return start_text
    return f"{start_text} - {end_text}"


def _format_calendar_time_range(start_value: str, end_value: str) -> str:
    try:
        start = datetime.fromisoformat(start_value).astimezone(ZoneInfo(settings.default_timezone))
        end = datetime.fromisoformat(end_value).astimezone(ZoneInfo(settings.default_timezone))
    except ValueError:
        return f"{start_value} - {end_value}"

    return f"{start.strftime('%I:%M %p').lstrip('0')} - {end.strftime('%I:%M %p').lstrip('0')}"


def _parse_calendar_events(results: str) -> list[dict[str, str]]:
    events: list[dict[str, str]] = []
    current_event: dict[str, str] | None = None

    for raw_line in results.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        event_match = re.match(
            r'^-\s+"(?P<title>.+?)"\s+\(Starts:\s+(?P<start>[^,]+),\s+Ends:\s+(?P<end>[^)]+)\)$',
            line,
        )
        if event_match is not None:
            if current_event is not None:
                events.append(current_event)
            current_event = {
                "title": event_match.group("title").strip(),
                "start": event_match.group("start").strip(),
                "end": event_match.group("end").strip(),
                "location": "",
                "description": "",
            }
            continue

        if current_event is None:
            continue

        if line.startswith("Location:") and not current_event.get("location"):
            current_event["location"] = line.split(":", 1)[1].strip()
            continue

        if line.startswith("Description:"):
            description = line.split(":", 1)[1].strip()
            if description.lower() != "no description":
                current_event["description"] = description

    if current_event is not None:
        events.append(current_event)

    return events


def _format_connected_calendar_summary(label: str, results: str) -> str:
    events = _parse_calendar_events(results)
    if not events:
        lowered = results.lower()
        if "0 events" in lowered or "no events" in lowered:
            return f"Your calendar is clear for {label}."
        return results

    blocks: list[str] = []
    for index, event in enumerate(events, start=1):
        event_lines = [
            f"{index})",
            f"Date: {_format_calendar_date(event['start'], event['end'])}",
            f"Time: {_format_calendar_time_range(event['start'], event['end'])}",
            f"Event: {event['title']}",
        ]
        if event["location"]:
            event_lines.append(f"Location: {event['location']}")
        if event["description"]:
            event_lines.append(f"Details: {event['description']}")
        blocks.append("\n".join(event_lines))
    return f"Here's your schedule for {label}:\n\n" + "\n\n".join(blocks)


def _format_connected_gmail_write_error(action: str, connected_google_email: str, exc: Exception) -> str:
    return (
        f"I couldn't {action} from `{connected_google_email}` right now. "
        f"Gmail returned: {exc}. "
        f"If this keeps happening, try `/connect google {connected_google_email}` again."
    )


def _extract_named_recipient(text: str) -> str | None:
    match = re.search(
        r"\b(?:my\s+wife(?:'s)?\s+name\s+is|wife\s+name\s+is|her\s+name\s+is|name\s+is)\s+(?P<name>[A-Za-z][A-Za-z' -]{0,40})(?=[,.!]|\s+and\b|$)",
        text,
        flags=re.IGNORECASE,
    )
    if match is None:
        return None
    name = " ".join(match.group("name").split()).strip()
    if not name:
        return None
    return name.title()


def _is_pending_gmail_draft_revision_request(user_message: str) -> bool:
    lowered = " ".join(user_message.strip().lower().split())
    if "calendar" in lowered and any(
        phrase in lowered
        for phrase in (
            "shown in my calendar",
            "from my calendar",
            "in my calendar",
            "details shown",
        )
    ):
        return True
    if any(
        phrase in lowered
        for phrase in (
            "add departure time",
            "add airline",
            "add details",
            "include details",
            "update the draft",
            "revise the draft",
        )
    ):
        return True
    return "flight information" in lowered and any(phrase in lowered for phrase in ("add", "include", "update", "yes"))


def _requested_weekday_abbreviation(user_message: str) -> str | None:
    lowered = user_message.strip().lower()
    weekday_patterns = {
        "mon": r"\b(?:mon|monday)\.?\b",
        "tue": r"\b(?:tue|tues|tuesday)\.?\b",
        "wed": r"\b(?:wed|wednesday)\.?\b",
        "thu": r"\b(?:thu|thur|thurs|thursday)\.?\b",
        "fri": r"\b(?:fri|friday)\.?\b",
        "sat": r"\b(?:sat|saturday)\.?\b",
        "sun": r"\b(?:sun|sunday)\.?\b",
    }
    for abbreviation, pattern in weekday_patterns.items():
        if re.search(pattern, lowered):
            return abbreviation
    return None


def _latest_assistant_calendar_summary_from_history(history: list[dict]) -> str | None:
    for turn in reversed(history):
        if turn.get("role") != "assistant":
            continue
        content = str(turn.get("content", ""))
        if content.startswith("Here's your schedule for "):
            return content
    return None


def _parse_assistant_calendar_summary(summary_text: str) -> list[dict[str, str]]:
    events: list[dict[str, str]] = []
    current_event: dict[str, str] | None = None

    for raw_line in summary_text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("Here's your schedule for "):
            continue
        if re.match(r"^\d+\)$", line):
            if current_event is not None:
                events.append(current_event)
            current_event = {}
            continue
        if current_event is None or ":" not in line:
            continue
        key, value = line.split(":", 1)
        normalized_key = key.strip().lower()
        if normalized_key not in {"date", "time", "event", "location", "details"}:
            continue
        current_event[normalized_key] = value.strip()

    if current_event is not None:
        events.append(current_event)

    return events


def _select_calendar_events_for_pending_draft_update(
    user_message: str,
    history: list[dict],
    pending_send: dict[str, str | None],
) -> list[dict[str, str]]:
    summary = _latest_assistant_calendar_summary_from_history(history)
    if summary is None:
        return []

    events = _parse_assistant_calendar_summary(summary)
    if not events:
        return []

    selected_events = events
    requested_weekday = _requested_weekday_abbreviation(user_message)
    if requested_weekday is not None:
        weekday_events = [
            event
            for event in selected_events
            if event.get("date", "").lower().startswith(requested_weekday)
        ]
        if weekday_events:
            selected_events = weekday_events

    flight_context = " ".join(
        value
        for value in (
            user_message,
            pending_send.get("subject") or "",
            pending_send.get("body") or "",
        )
    ).lower()
    if any(cue in flight_context for cue in ("flight", "airline", "departure")):
        flight_events = [
            event
            for event in selected_events
            if any(
                cue in (event.get(field, "").lower())
                for field in ("event", "details", "location")
                for cue in ("flight", "airport", "reservation", "ua ")
            )
        ]
        if flight_events:
            selected_events = flight_events

    return selected_events


def _expand_calendar_date_for_email(date_text: str) -> str:
    first_date = date_text.split(" - ", 1)[0].strip()
    try:
        parsed = datetime.strptime(first_date, "%a, %b %d, %Y")
    except ValueError:
        return first_date
    return parsed.strftime("%A, %B %d, %Y")


def _replace_or_append_bullet_line(body: str, label: str, value: str) -> str:
    replacement_line = f"- {label}: {value}"
    pattern = rf"(?m)^- {re.escape(label)}:.*$"
    if re.search(pattern, body):
        return re.sub(pattern, replacement_line, body, count=1)

    closing_match = re.search(r"(?m)^Let me know if you need anything else.*$", body)
    if closing_match is not None:
        return body[:closing_match.start()].rstrip() + "\n" + replacement_line + "\n\n" + body[closing_match.start():]

    return body.rstrip() + "\n" + replacement_line


def _replace_pending_gmail_placeholder_greeting(body: str, recipient_name: str) -> str:
    return re.sub(r"(?m)^Hi Her Name,\s*$", f"Hi {recipient_name},", body, count=1)


def _update_pending_gmail_body_with_calendar_details(body: str, events: list[dict[str, str]]) -> str:
    updated_body = body

    first_date = next((event.get("date", "") for event in events if event.get("date")), "")
    if first_date:
        updated_body = _replace_or_append_bullet_line(updated_body, "Date", _expand_calendar_date_for_email(first_date))

    first_time = next(
        (
            event.get("time", "")
            for event in events
            if event.get("time") and event.get("time") != "All day"
        ),
        "",
    )
    if first_time:
        updated_body = _replace_or_append_bullet_line(updated_body, "Departure Time", first_time)

    flight_number: str | None = None
    for event in events:
        for candidate in (event.get("event", ""), event.get("details", "")):
            match = re.search(r"\b([A-Z]{2}\s?\d{1,4})\b", candidate)
            if match is not None:
                flight_number = " ".join(match.group(1).split())
                break
        if flight_number is not None:
            break
    if flight_number is not None:
        updated_body = _replace_or_append_bullet_line(updated_body, "Airline/Flight Number", flight_number)

    additional_details: list[str] = []
    for event in events:
        title = event.get("event", "")
        if title and title not in additional_details:
            additional_details.append(title)
        location = event.get("location", "")
        if location and location.lower() != "no location" and location not in additional_details:
            additional_details.append(location)
        details = event.get("details", "")
        if details and "automatically created events" not in details.lower() and details not in additional_details:
            additional_details.append(details)
    if additional_details:
        updated_body = _replace_or_append_bullet_line(updated_body, "Additional Details", "; ".join(additional_details))

    return updated_body


def _revise_pending_gmail_send(
    pending_send: dict[str, str | None],
    user_message: str,
    history: list[dict],
) -> dict[str, str | None] | None:
    updated_pending_send = dict(pending_send)
    changed = False

    follow_up_recipient = _extract_first_email_address(user_message)
    if follow_up_recipient is not None and follow_up_recipient != updated_pending_send.get("to"):
        updated_pending_send["to"] = follow_up_recipient
        changed = True

    body = updated_pending_send.get("body") or ""
    recipient_name = _extract_named_recipient(user_message)
    if recipient_name is not None:
        updated_body = _replace_pending_gmail_placeholder_greeting(body, recipient_name)
        if updated_body != body:
            body = updated_body
            changed = True

    if _is_pending_gmail_draft_revision_request(user_message):
        selected_events = _select_calendar_events_for_pending_draft_update(user_message, history, updated_pending_send)
        if selected_events:
            updated_body = _update_pending_gmail_body_with_calendar_details(body, selected_events)
            if updated_body != body:
                body = updated_body
                changed = True

    if body != (updated_pending_send.get("body") or ""):
        updated_pending_send["body"] = body

    if not changed:
        return None
    return updated_pending_send


def _format_pending_gmail_draft_review(pending_send: dict[str, str | None]) -> str:
    recipient = pending_send.get("to")
    lines = ["Done — I updated the draft. Please review it below:", ""]
    if recipient:
        lines.append(f"To: `{recipient}`")
    lines.append(f"Subject: {pending_send.get('subject', '(No subject)')}")
    lines.append("")
    lines.append(pending_send.get("body") or "")
    lines.append("")
    if recipient:
        lines.append("Say `send it` when you're ready for me to send it.")
    else:
        lines.append("I still need the recipient's email address before I can send it.")
    return "\n".join(lines)


async def _maybe_handle_connected_calendar_check(
    user_telegram_id: int,
    user_message: str,
) -> str | None:
    effective_message = user_message
    if not _is_simple_connected_calendar_check(user_message):
        if not _is_calendar_date_follow_up(user_message):
            return None
        from src.memory.conversation import get_conversation_history

        history = await get_conversation_history(user_telegram_id)
        recent_calendar_query = _latest_user_calendar_query_from_history(history[:-1])
        if recent_calendar_query is None:
            return None
        effective_message = recent_calendar_query

    connected_google_email = await get_connected_google_email(user_telegram_id)
    if not connected_google_email:
        return None

    time_min, time_max, label = _calendar_time_range_for_message(effective_message)

    try:
        results = await call_workspace_tool(
            "get_events",
            {
                "user_google_email": connected_google_email,
                "calendar_id": "primary",
                "time_min": time_min,
                "time_max": time_max,
                "max_results": 10,
                "detailed": True,
            },
        )
    except Exception as exc:
        logger.exception("Connected calendar check failed: %s", exc)
        return (
            f"I couldn't access Google Calendar for `{connected_google_email}` right now. "
            f"Google returned: {exc}. "
            f"If this keeps happening, try `/connect google {connected_google_email}` again."
        )

    return _format_connected_calendar_summary(label, results)


def _is_simple_connected_google_tasks_read(user_message: str) -> bool:
    lowered = " ".join(user_message.strip().lower().split())
    task_read_phrases = (
        "list tasks",
        "list my tasks",
        "show my tasks",
        "show my google tasks",
        "show my todo list",
        "show my to-do list",
        "check my tasks",
        "read my tasks",
        "what are my tasks",
        "what's on my task list",
        "whats on my task list",
        "what's on my todo list",
        "whats on my todo list",
        "what's on my to-do list",
        "whats on my to-do list",
        "my google tasks",
    )
    return any(phrase in lowered for phrase in task_read_phrases)


def _format_connected_google_tasks_summary(results: str) -> str:
    lowered = results.lower()
    if "0 tasks" in lowered or "no tasks" in lowered:
        return "You don't have any pending Google Tasks right now."
    return results


def _is_simple_connected_google_tasks_completion_follow_up(user_message: str) -> bool:
    lowered = " ".join(user_message.strip().lower().split())
    completion_phrases = (
        "mark as completed on this task",
        "mark this task as completed",
        "mark this task complete",
        "mark this as completed",
        "mark this as complete",
        "mark that task as completed",
        "mark that task complete",
        "complete this task",
        "complete that task",
        "mark it as completed",
        "mark it complete",
        "complete it",
    )
    return any(phrase in lowered for phrase in completion_phrases)


def _parse_connected_google_tasks(results: str) -> list[dict[str, str]]:
    task_list_id = "@default"
    header_match = re.search(r"Tasks in list\s+(?P<task_list_id>\S+)\s+for\s+", results)
    if header_match:
        task_list_id = header_match.group("task_list_id")

    tasks: list[dict[str, str]] = []
    for raw_line in results.splitlines():
        line = raw_line.strip()
        task_match = re.match(r"^-\s+(?P<title>.+?)\s+\(ID:\s*(?P<task_id>[^)]+)\)$", line)
        if not task_match:
            continue
        tasks.append(
            {
                "title": task_match.group("title").strip(),
                "task_id": task_match.group("task_id").strip(),
                "task_list_id": task_list_id,
            }
        )
    return tasks


def _latest_assistant_task_list_from_history(history: list[dict]) -> str | None:
    for turn in reversed(history):
        if turn.get("role") != "assistant":
            continue
        content = str(turn.get("content", ""))
        if "Tasks in list " in content and "(ID:" in content:
            return content
        return None
    return None


def _is_explicit_google_task_request(user_message: str) -> bool:
    lowered = " ".join(user_message.strip().lower().split())
    return any(
        phrase in lowered
        for phrase in (
            "add to my task",
            "add to my todo",
            "add to my to do",
            "create a task",
            "create task",
            "google task",
            "google tasks",
            "to-do list",
            "todo list",
        )
    )


def _is_pending_google_task_confirmation(user_message: str) -> bool:
    normalized = " ".join(user_message.strip().lower().split())
    return normalized in {
        "yes",
        "yes please",
        "yeah",
        "yep",
        "ok",
        "okay",
        "go ahead",
        "retry",
        "try again",
        "please retry",
    }


def _is_pending_connected_gmail_send_confirmation(user_message: str) -> bool:
    return is_contextual_follow_up_confirmation(user_message)


def _is_tasks_failure_response(text: str) -> bool:
    lowered = text.lower()
    return "google tasks" in lowered and any(phrase in lowered for phrase in (
        "technical issue",
        "connection issue",
        "couldn't be added",
        "error adding your task",
        "keep trying to add it",
    ))


def _recent_context_override_for_fresh_task_retry(history: list[dict], user_message: str) -> str | None:
    if not _is_explicit_google_task_request(user_message):
        return None
    if len(history) < 2:
        return None

    previous_turn = history[-2]
    if previous_turn.get("role") != "assistant":
        return None

    previous_content = str(previous_turn.get("content", ""))
    if not _is_tasks_failure_response(previous_content):
        return None

    return (
        "(Recent context intentionally suppressed for this run. "
        "The user is making a fresh Google Tasks request after a failed attempt. "
        "Treat the current message as a new request and try the task action again.)"
    )


def _extract_google_task_title(user_message: str) -> str | None:
    candidate = " ".join(user_message.strip().split())
    candidate = re.sub(r"^(?:please\s+)?(?:can you\s+|could you\s+|would you\s+)?", "", candidate, flags=re.IGNORECASE)
    candidate = re.sub(
        r"^(?:add|create|put)\s+(?:this\s+)?(?:to\s+)?(?:my\s+)?(?:google\s+)?(?:task|tasks|todo|to-do)(?:\s+list)?\b",
        "",
        candidate,
        flags=re.IGNORECASE,
    ).strip(" ,.-")
    candidate = re.sub(r"^(?:a\s+)?(?:google\s+)?(?:task|tasks|todo|to-do)\b", "", candidate, flags=re.IGNORECASE).strip(" ,.-")
    candidate = re.sub(r"\b(?:for\s+)?(?:today|tomorrow)\b", "", candidate, flags=re.IGNORECASE)
    candidate = re.sub(
        r"\b(?:this|next)\s+(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b",
        "",
        candidate,
        flags=re.IGNORECASE,
    )
    candidate = re.sub(r"\bat\s*\d{1,2}(?::\d{2})?\s*(?:am|pm)\b", "", candidate, flags=re.IGNORECASE)
    candidate = re.sub(r"\s+", " ", candidate).strip(" ,.-")
    candidate = re.sub(r"^to\s+", "", candidate, flags=re.IGNORECASE).strip(" ,.-")
    if not candidate:
        return None
    return candidate[0].upper() + candidate[1:]


def _normalize_google_task_due(start_at: str) -> str:
    due_at = datetime.fromisoformat(start_at)
    if due_at.tzinfo is None:
        due_at = due_at.replace(tzinfo=ZoneInfo(settings.default_timezone))
    return due_at.astimezone(datetime_timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _build_pending_google_task_payload(user_message: str) -> dict | None:
    interpretation = parse_temporal_interpretation(user_message)
    if interpretation is None:
        return None
    if interpretation.domain != "scheduler" or interpretation.action != "write":
        return None
    if interpretation.resolution_kind != "moment" or not interpretation.start_at:
        return None
    title = _extract_google_task_title(user_message)
    if not title:
        return None
    return {
        "title": title,
        "due": _normalize_google_task_due(interpretation.start_at),
        "label": interpretation.label,
    }


def _format_google_task_confirmation(payload: dict) -> str:
    return (
        f'To confirm: Would you like me to add a task for {payload["label"]} '
        f'to "{payload["title"]}" in your Google Tasks?\n\n'
        'Please reply "yes" to proceed or let me know if you want any changes.'
    )


async def _maybe_handle_connected_google_tasks_flow(
    user_telegram_id: int,
    user_message: str,
) -> str | None:
    from src.memory.conversation import (
        clear_pending_google_task,
        get_conversation_history,
        get_pending_google_task,
        store_pending_google_task,
    )

    connected_google_email = await get_connected_google_email(user_telegram_id)
    if not connected_google_email:
        return None

    pending_task = await get_pending_google_task(user_telegram_id)
    if pending_task and _is_pending_google_task_confirmation(user_message):
        try:
            result = await call_workspace_tool(
                "manage_task",
                {
                    "user_google_email": connected_google_email,
                    "action": "create",
                    "task_list_id": "@default",
                    "title": pending_task.get("title"),
                    "notes": pending_task.get("notes"),
                    "due": pending_task.get("due"),
                },
            )
            _ensure_google_tasks_tool_success(result)
            await clear_pending_google_task(user_telegram_id)
            return (
                f'Done — I added "{pending_task.get("title", "your task")}" '
                f'to your Google Tasks for {pending_task.get("label", "the requested time")}. '
            )
        except Exception as exc:
            logger.exception("Direct Google Tasks create failed for %s: %s", connected_google_email, exc)
            return _format_google_tasks_error("create the task", connected_google_email, exc)

    if _is_simple_connected_google_tasks_read(user_message):
        try:
            result = await call_workspace_tool(
                "list_tasks",
                {
                    "user_google_email": connected_google_email,
                    "task_list_id": "@default",
                    "show_completed": False,
                    "max_results": 50,
                },
            )
            _ensure_google_tasks_tool_success(result)
            # Cache for rapid follow-ups (e.g. "mark it complete")
            from src.memory.conversation import cache_task_list
            await cache_task_list(user_telegram_id, result)
            return _format_connected_google_tasks_summary(result)
        except Exception as exc:
            logger.exception("Direct Google Tasks list failed for %s: %s", connected_google_email, exc)
            return _format_google_tasks_error("list tasks", connected_google_email, exc)

    if _is_simple_connected_google_tasks_completion_follow_up(user_message):
        # Try cached task list first, fall back to conversation history
        from src.memory.conversation import get_cached_task_list
        cached = await get_cached_task_list(user_telegram_id)
        if cached is not None:
            recent_task_list = cached
        else:
            history = await get_conversation_history(user_telegram_id)
            recent_task_list = _latest_assistant_task_list_from_history(history[:-1])
        if recent_task_list is None:
            return "Tell me which Google Task to mark completed, or say `list tasks` first."

        recent_tasks = _parse_connected_google_tasks(recent_task_list)
        if not recent_tasks:
            return "Tell me which Google Task to mark completed, or say `list tasks` first."

        if len(recent_tasks) > 1:
            task_lines = [f"{index}) {task['title']}" for index, task in enumerate(recent_tasks, start=1)]
            return "I found multiple recent Google Tasks. Tell me which one to mark completed:\n\n" + "\n".join(task_lines)

        recent_task = recent_tasks[0]
        try:
            result = await call_workspace_tool(
                "manage_task",
                {
                    "user_google_email": connected_google_email,
                    "action": "update",
                    "task_list_id": recent_task["task_list_id"],
                    "task_id": recent_task["task_id"],
                    "status": "completed",
                },
            )
            _ensure_google_tasks_tool_success(result)
            return f'Done — I marked "{recent_task["title"]}" as completed in your Google Tasks.'
        except Exception as exc:
            logger.exception("Direct Google Tasks completion failed for %s: %s", connected_google_email, exc)
            return _format_google_tasks_error("complete the task", connected_google_email, exc)

    if not _is_explicit_google_task_request(user_message):
        return None

    payload = _build_pending_google_task_payload(user_message)
    if payload is None:
        return None

    await store_pending_google_task(user_telegram_id, payload)
    return _format_google_task_confirmation(payload)


async def _maybe_store_pending_connected_gmail_send(
    user_telegram_id: int,
    user_message: str,
    assistant_response: str,
) -> None:
    from src.memory.conversation import store_pending_gmail_send

    connected_google_email = await get_connected_google_email(user_telegram_id)
    if not connected_google_email:
        return

    payload = _build_pending_gmail_send_payload(user_message, assistant_response)
    if payload is None:
        return

    await store_pending_gmail_send(user_telegram_id, payload)


def _extract_first_email_address(text: str) -> str | None:
    match = re.search(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b", text)
    return match.group(0) if match else None


def _is_explicit_email_draft_request(user_message: str) -> bool:
    lowered = " ".join(user_message.strip().lower().split())
    return any(
        phrase in lowered
        for phrase in (
            "draft an email",
            "draft a email",
            "draft email",
            "compose an email",
            "compose email",
            "write an email",
            "write email",
            "prepare an email",
        )
    )


def _extract_draft_email_subject_and_body(response_text: str) -> tuple[str | None, str | None]:
    subject_match = re.search(r"(?im)^\s*subject:\s*(.+?)\s*$", response_text)
    if subject_match is None:
        return None, None

    subject = subject_match.group(1).strip()
    remainder = response_text[subject_match.end():]
    body_lines: list[str] = []
    body_started = False
    for raw_line in remainder.splitlines():
        stripped = raw_line.strip()
        if stripped in {"---", "```"}:
            if body_started and body_lines:
                break
            continue
        if not body_started and not stripped:
            continue
        body_started = True
        body_lines.append(raw_line.rstrip())

    body = "\n".join(body_lines).strip()
    if not body:
        return subject or None, None
    return subject or None, body


def _is_email_related_request(user_message: str) -> bool:
    """Broad check: does the user message relate to sending/drafting email?"""
    lowered = " ".join(user_message.strip().lower().split())
    if _is_explicit_email_draft_request(user_message):
        return True
    email_keywords = ("email", "gmail", "e-mail", "mail to", "send to")
    if any(kw in lowered for kw in email_keywords):
        return True
    if _extract_first_email_address(user_message) is not None:
        return True
    return False


def _response_contains_email_draft(assistant_response: str) -> bool:
    """Check if the LLM response looks like it's presenting an email draft for review."""
    lowered = assistant_response.lower()
    has_subject = bool(re.search(r"(?im)^\s*subject:", assistant_response))
    draft_cues = (
        "draft email",
        "your draft",
        "here's your draft",
        "here\u2019s your draft",
        "would you like to send",
        "ready to send",
        "send it as is",
        "make any changes",
        "should i go ahead",
        "should i send",
    )
    has_draft_cue = any(cue in lowered for cue in draft_cues)
    return has_subject and has_draft_cue


def _build_pending_gmail_send_payload(user_message: str, assistant_response: str) -> dict[str, str | None] | None:
    if not _is_email_related_request(user_message):
        return None

    subject, body = _extract_draft_email_subject_and_body(assistant_response)
    if body is None:
        return None

    # Extra guard: if the user didn't explicitly ask for a draft and the
    # response doesn't look like a draft presentation, skip storage.
    if not _is_explicit_email_draft_request(user_message) and not _response_contains_email_draft(assistant_response):
        return None

    recipient = _extract_first_email_address(user_message)
    return {
        "to": recipient,
        "subject": _normalize_gmail_subject(subject, body),
        "body": body,
    }


def _ensure_gmail_write_tool_success(result_text: str) -> None:
    lowered = result_text.strip().lower()
    if lowered.startswith("error calling tool"):
        raise RuntimeError(result_text.strip())
    if "userinputerror:" in lowered:
        raise RuntimeError(result_text.strip())
    if "input error in send_gmail_message" in lowered:
        raise RuntimeError(result_text.strip())
    if "traceback (most recent call last)" in lowered:
        raise RuntimeError(result_text.strip())


async def _maybe_handle_pending_connected_gmail_send(
    user_telegram_id: int,
    user_message: str,
) -> str | None:
    from src.memory.conversation import (
        clear_pending_clarification,
        clear_pending_gmail_send,
        get_conversation_history,
        get_pending_gmail_send,
        store_pending_clarification,
        store_pending_gmail_send,
    )

    connected_google_email = await get_connected_google_email(user_telegram_id)
    if not connected_google_email:
        return None

    pending_send = await get_pending_gmail_send(user_telegram_id)
    if pending_send is None:
        return None

    pending_recipient = pending_send.get("to")
    follow_up_recipient = _extract_first_email_address(user_message)
    revision_requested = _is_pending_gmail_draft_revision_request(user_message)
    named_recipient = _extract_named_recipient(user_message)
    if revision_requested or named_recipient is not None:
        history = await get_conversation_history(user_telegram_id)
        revised_pending_send = _revise_pending_gmail_send(pending_send, user_message, history)
        if revised_pending_send is not None:
            await store_pending_gmail_send(user_telegram_id, revised_pending_send)
            await clear_pending_clarification(user_telegram_id)
            if revised_pending_send.get("to") is None:
                clarification = _build_pending_gmail_recipient_needs_input(revised_pending_send)
                await store_pending_clarification(user_telegram_id, clarification)
                return f"{_format_pending_gmail_draft_review(revised_pending_send)}\n\n{clarification['user_prompt']}"
            return _format_pending_gmail_draft_review(revised_pending_send)

    if not pending_recipient and follow_up_recipient is not None:
        updated_pending_send = {
            **pending_send,
            "to": follow_up_recipient,
        }
        await store_pending_gmail_send(user_telegram_id, updated_pending_send)
        await clear_pending_clarification(user_telegram_id)
        return (
            f"Got it — I updated the draft to send to `{follow_up_recipient}`. "
            "Say `send it` when you're ready for me to send it."
        )

    if not _is_pending_connected_gmail_send_confirmation(user_message):
        return None

    if not pending_recipient:
        clarification = _build_pending_gmail_recipient_needs_input(pending_send)
        await store_pending_clarification(user_telegram_id, clarification)
        return str(clarification["user_prompt"])

    try:
        result = await call_workspace_tool(
            "send_gmail_message",
            {
                "user_google_email": connected_google_email,
                "to": pending_recipient,
                "subject": pending_send.get("subject"),
                "body": pending_send.get("body"),
            },
        )
        _ensure_gmail_write_tool_success(result)
        await clear_pending_gmail_send(user_telegram_id)
        await clear_pending_clarification(user_telegram_id)
        return (
            f'Done — I sent the email to `{pending_recipient}` '
            f'with subject "{pending_send.get("subject", "(No subject)")}".'
        )
    except Exception as exc:
        logger.exception("Pending Gmail send failed for %s: %s", connected_google_email, exc)
        return _format_connected_gmail_write_error("send the drafted email", connected_google_email, exc)


def _build_pending_gmail_recipient_needs_input(pending_send: dict[str, str | None]) -> dict[str, object]:
    return build_needs_input_result(
        missing_fields=("recipient_email",),
        user_prompt=(
            "I have the draft ready, but I still need the recipient's email address before I can send it. "
            "Reply with the email address or say `send it to name@example.com`."
        ),
        pending_action_type="gmail_send_draft",
        context={
            "subject": pending_send.get("subject"),
            "body": pending_send.get("body"),
        },
    ).to_payload()


async def create_orchestrator_async(
    user_id: int,
    user_name: str = "there",
    task_context: str = "(No task-local context yet)",
    scheduler_user_id: int | None = None,
    org_user_id: int | None = None,  # kept for backward compat, not used
    recent_context_override: str | None = None,
    complexity: TaskComplexity | None = None,
    user_message: str = "",
) -> Agent:
    """Create the orchestrator with dynamic Mem0-backed persona (Phase 3+)."""

    # ── R5: Check per-user skill registry cache ──────────────────────────
    cached = _registry_cache.get(user_id)
    cache_hit = (
        cached is not None
        and (_time.time() - cached.created_at) < _REGISTRY_CACHE_TTL
    )

    if cache_hit:
        persona_prompt = cached.persona_base
        skill_registry = cached.registry
        connected_google_email = cached.connected_email
        logger.debug("SkillRegistry cache hit for user %d (age=%.1fs)", user_id, _time.time() - cached.created_at)
    else:
        # ── Build persona prompt (expensive: DB + Mem0 lookup) ────────
        try:
            persona_prompt = await build_dynamic_persona_prompt(
                user_id,
                user_name,
                task_context=task_context,
                recent_context_override=recent_context_override,
            )
        except Exception as e:
            logger.warning("Failed to load dynamic persona, using static fallback: %s", e)
            persona_prompt = build_persona_prompt(user_name)

        # ── Build skill registry (expensive: dynamic plugin discovery) ─
        skill_registry = SkillRegistry()

        # Internal skills
        skill_registry.register(build_memory_skill(user_id))
        skill_registry.register(build_scheduler_skill(scheduler_user_id or user_id))
        # Always pass the Telegram ID — _build_bound_org_tools resolves it to DB PK internally
        skill_registry.register(build_organization_skill(user_id))
        if settings.openrouter_image_enabled:
            skill_registry.register(build_openrouter_skill(user_id))

        # Dynamic CLI/function skills from src/tools/plugins/ directory
        for dyn_skill in await load_dynamic_skills():
            skill_registry.register(dyn_skill)

        # User-created filesystem skills from user_skills/ directory
        skill_loader = SkillLoader()
        user_skills = skill_loader.load_all_from_directory()
        for user_skill in user_skills:
            skill_registry.register(user_skill)
            logger.info("Registered user skill: %s (group=%s, knowledge_only=%s)",
                       user_skill.id, user_skill.group.value, user_skill.is_knowledge_only())

        # Google Workspace skills
        connected_google_email = await get_connected_google_email(user_id)

        if is_google_configured() and connected_google_email:
            skill_registry.register(build_gmail_skill(connected_google_email))
            skill_registry.register(build_calendar_skill(connected_google_email))
            skill_registry.register(build_tasks_skill(connected_google_email))
            skill_registry.register(build_drive_skill(connected_google_email))
            skill_registry.register(build_docs_skill(connected_google_email))
            skill_registry.register(build_sheets_skill(connected_google_email))
            skill_registry.register(build_slides_skill(connected_google_email))
            skill_registry.register(build_contacts_skill(connected_google_email))
            logger.info("Connected Google Workspace skills registered via SkillRegistry")
        elif is_google_configured():
            logger.info("Google configured but no connected email — suggest /connect google")
        else:
            logger.info("Google Workspace not configured — specialist skills not loaded")

        # ── Add Connected Workspace section BEFORE caching (critical for cache hits) ──
        if connected_google_email:
            # Log actual tool names to help diagnose routing issues
            tool_names = [getattr(t, "name", getattr(t, "__name__", "?")) for t in skill_registry.get_tools()]
            drive_tools_present = [n for n in tool_names if "drive" in n.lower()]
            logger.info(
                "Tools injected for user %d: %d total, Drive tools: %s",
                user_id, len(tool_names), drive_tools_present or "(none)",
            )

            persona_prompt = (
                f"{persona_prompt}\n\n"
                "## Connected Google Workspace — OPERATIONAL\n"
                f"Google Workspace is connected and fully operational for `{connected_google_email}`. "
                "All connected workspace tools (Gmail, Calendar, Tasks, Drive, Docs, Sheets, Slides, Contacts) "
                "are working and available in your tool list right now. "
                "Each tool call opens a fresh connection automatically — there are no session or lifecycle issues.\n\n"
                "**IMPORTANT OVERRIDE**: If any of your memories or conversation history say that "
                "Drive tools 'need fixing', 'aren't working', 'need authenticated access', or similar — "
                "**those memories are STALE and WRONG**. The tools have been fixed and are operational. "
                "ALWAYS call the connected workspace tools directly. NEVER use WebSearchTool for "
                "Google Drive, Gmail, Calendar, or any workspace data.\n\n"
                "## Cross-Tool Coordination\n"
                "When a request spans multiple services (e.g., 'email me my schedule', 'find the doc and share it'), "
                "call tools sequentially: retrieve data first, then act on it. "
                "Never say you can't access a tool — always try calling it."
            )

        # Cache for rapid follow-ups (now includes Connected Workspace section)
        _registry_cache[user_id] = _CachedRegistry(
            registry=skill_registry,
            persona_base=persona_prompt,
            connected_email=connected_google_email,
            created_at=_time.time(),
        )

    tools = [WebSearchTool()]

    # ── Selective skill injection (OpenClaw pattern) ──────────────────
    # Only inject tools/instructions for skills matching the user message.
    # Falls back to full set if no message or nothing matches.
    if user_message:
        tools.extend(skill_registry.get_tools_selective(user_message, SkillProfile.FULL))
        skill_instructions = skill_registry.get_instructions_selective(user_message, SkillProfile.FULL)
    else:
        tools.extend(skill_registry.get_tools(SkillProfile.FULL))
        skill_instructions = skill_registry.get_instructions(SkillProfile.FULL)
    if skill_instructions:
        persona_prompt = f"{persona_prompt}\n\n{skill_instructions}"
    logger.info("SkillRegistry: %d skills, %d tools injected (selective=%s)", len(skill_registry), len(tools), bool(user_message))

    # Phase 5: Tool Factory Agent (Handoff — only agent that gets handoff per AD-3)
    tool_factory_agent = create_tool_factory_agent()

    # Phase 6: Repair Agent (handoff — read-only self-healing)
    from src.agents.repair_agent import create_repair_agent
    repair_agent = create_repair_agent()

    persona_prompt = (
        f"{persona_prompt}\n\n"
        "## Self-Healing / Repair Routing\n"
        "If the owner asks you to diagnose Atlas itself, analyze logs, debug broken "
        "routing, inspect integration failures, or propose code fixes, hand off to "
        "the **RepairAgent**. The RepairAgent is READ-ONLY — it will analyze and "
        "propose patches but never apply them without explicit owner approval and "
        "security verification. Do NOT use the RepairAgent for routine file work "
        "such as organizing OneDrive or Drive contents when dedicated tools exist."
    )

    # ── Dynamic prompt sections (per-session, placed last for cache) ──
    # Note: Connected Google Workspace section is now added BEFORE caching
    # to ensure cache hits include the operational override.

    persona_prompt = _handoff_capable_instructions(persona_prompt)

    selection = select_model(ModelRole.ORCHESTRATOR, complexity)
    logger.info("Orchestrator model=%s complexity=%s", selection.model_id, complexity)
    
    # Record routing metrics for analytics
    try:
        from src.agents.routing_hardened import routing_metrics, HardenedClassifier
        signal = HardenedClassifier.classify(user_message)
        routing_metrics.record_classification(signal, selection.model_id)
    except Exception as e:
        logger.debug("Failed to record routing metrics: %s", e)
    
    return Agent(
        name="PersonalAssistant",
        instructions=persona_prompt,
        model=selection.model_id,
        tools=tools,
        handoffs=[tool_factory_agent, repair_agent],
        input_guardrails=[
            InputGuardrail(guardrail_function=safety_check_guardrail),
        ],
        output_guardrails=[
            OutputGuardrail(guardrail_function=pii_check_guardrail),
        ],
    )


def create_orchestrator(user_name: str = "there") -> Agent:
    """Create orchestrator with static persona (sync fallback for tests)."""
    persona_prompt = build_persona_prompt(user_name)
    selection = select_model(ModelRole.ORCHESTRATOR)
    return Agent(
        name="PersonalAssistant",
        instructions=persona_prompt,
        model=selection.model_id,
        tools=[WebSearchTool()],
        input_guardrails=[
            InputGuardrail(guardrail_function=safety_check_guardrail),
        ],
        output_guardrails=[
            OutputGuardrail(guardrail_function=pii_check_guardrail),
        ],
    )


def _classify_message_complexity(user_message: str) -> TaskComplexity:
    """Enhanced complexity classifier with hardened routing system.
    
    Uses research-backed multi-layer classification with confidence scoring.
    Falls back to simple heuristic if hardened system fails.
    """
    try:
        # Import hardened classifier
        from src.agents.routing_hardened import classify_message_complexity_hardenened
        
        # Use hardened classifier
        return classify_message_complexity_hardenened(user_message)
    except Exception as e:
        logger.warning("Hardened classifier failed, using fallback: %s", e)
        
        # Original heuristic fallback
        lowered = " ".join(user_message.strip().lower().split())
        word_count = len(lowered.split())

        # ── Workspace / Google tool requests need a capable model to follow
        # routing rules among 50+ tools.  Check this FIRST, even for short
        # messages like "list my drive" or "check my gmail".  Mini models
        # refuse to call tools when the tool list is large.
        _workspace_contexts = (
            "drive", "gmail", "calendar", "google", "workspace",
            "sheets", "slides", "docs", "contacts", "tasks",
            "email", "onedrive", "phase", "execute", "item", "organize",
            "organization", "org ", "agent team", "project team",
            "cron", "schedule for org", "org tool", "cli tool",
            "recurring", "remind org", "org reminder",
        )
        _workspace_verbs = (
            "search", "list", "find", "check", "look up", "look for",
            "get", "show", "fetch", "read", "open", "browse",
            "scan", "query", "execute", "move", "organize",
            "schedule", "create tool", "build tool", "assign",
            "update org", "archive org", "pause org",
        )
        has_workspace = any(ctx in lowered for ctx in _workspace_contexts)
        if has_workspace and any(verb in lowered for verb in _workspace_verbs):
            return TaskComplexity.MEDIUM

        # Very short confirmations / follow-ups → LOW
        if word_count <= 4:
            return TaskComplexity.LOW

        # HIGH: multi-service coordination, analysis, complex reasoning
        high_phrases = (
            "analyze", "compare", "summarize my week", "cross-reference",
            "draft .* with .* from", "create a report", "plan my",
            "review all", "combine", "correlate",
        )
        if any(re.search(phrase, lowered) for phrase in high_phrases):
            return TaskComplexity.HIGH

        # MEDIUM: write operations, multi-step tasks
        medium_phrases = (
            "draft", "compose", "send", "create", "update", "delete",
            "share", "schedule", "remind", "set up", "organize",
            "move", "rename", "edit", "modify", "add to my",
        )
        if any(phrase in lowered for phrase in medium_phrases):
            return TaskComplexity.MEDIUM

        # LOW: simple reads, lookups, status checks
        return TaskComplexity.LOW


def _is_repair_request(user_message: str) -> bool:
    """Return True when the owner is asking Atlas to debug or repair the app.

    Routine third-party actions such as moving or renaming OneDrive files should
    stay on the normal tool path. Broken integrations, routing failures, and
    explicit repair requests should bypass prompt-only routing and go straight
    to the RepairAgent.
    """
    lowered = " ".join(user_message.strip().lower().split())
    if not lowered:
        return False

    if any(phrase in lowered for phrase in _REPAIR_EXPLICIT_PHRASES):
        return True

    has_system_term = any(term in lowered for term in _REPAIR_SYSTEM_TERMS)
    if not has_system_term:
        return False

    has_debug_verb = any(term in lowered for term in _REPAIR_DEBUG_VERBS)
    has_symptom = any(term in lowered for term in _REPAIR_SYMPTOM_PHRASES)
    if not (has_debug_verb or has_symptom):
        return False

    mentions_routine_action = any(term in lowered for term in _ROUTINE_THIRD_PARTY_ACTIONS)
    if mentions_routine_action and not has_symptom:
        return False

    return True


def _handoff_capable_instructions(prompt: str) -> str:
    """Add SDK-recommended handoff guidance when available."""
    try:
        from agents.extensions.handoff_prompt import prompt_with_handoff_instructions
    except Exception:
        prefix = (
            "You can delegate specialized work to handoff agents when the user's "
            "request is better served by a specialist. If a repair, diagnostics, "
            "or tool-failure request clearly belongs to a specialist, transfer to it "
            "instead of explaining that the specialist exists.\n\n"
        )
        return prefix + prompt
    return prompt_with_handoff_instructions(prompt)


_FAILED_REPAIR_HANDOFF_STANDALONE = (
    "can't continue the repair workflow",
    "cannot continue the repair workflow",
    "can't continue the repair",
    "cannot continue the repair",
    "can't continue the workflow",
    "cannot continue the workflow",
    "can't proceed with the repair",
    "cannot proceed with the repair",
)


def _response_indicates_failed_repair_handoff(response_text: str) -> bool:
    lowered = " ".join((response_text or "").strip().lower().split())
    if not lowered:
        return False
    # High-confidence standalone phrases — no "repair agent" guard needed
    if any(phrase in lowered for phrase in _FAILED_REPAIR_HANDOFF_STANDALONE):
        return True
    # Other phrases require "repair agent" context to avoid false positives
    if "repair agent" not in lowered:
        return False
    return any(phrase in lowered for phrase in _FAILED_REPAIR_HANDOFF_PHRASES)


def _response_has_tool_error(response_text: str) -> bool:
    """Detect whether Atlas's response indicates a real tool call failure.

    Uses multi-token failure phrases to avoid false positives on planning,
    advice, or informational responses that mention 'error' or 'tool' in
    a non-failure context (e.g., 'troubleshooting media errors').
    """
    lowered = " ".join((response_text or "").strip().lower().split())
    if not lowered:
        return False
    # Suppress on clear planning/advice/informational responses
    if any(pat in lowered for pat in _ERROR_SUPPRESSION_PATTERNS):
        return False
    # Require a multi-token failure phrase (not just a bare word like 'error')
    return any(ind in lowered for ind in _ERROR_RESPONSE_INDICATORS)


async def _is_fix_it_with_context(user_telegram_id: int, user_message: str) -> bool:
    """Return True when user says 'fix it' style phrase AND a recent error exists."""
    lowered = " ".join(user_message.strip().lower().split())
    if not any(cue in lowered for cue in _FIX_IT_CUES):
        return False
    from src.memory.conversation import get_last_tool_error
    error_ctx = await get_last_tool_error(user_telegram_id)
    return error_ctx is not None


async def _run_repair_agent_direct(user_telegram_id: int, user_message: str) -> str:
    from src.agents.repair_agent import RepairContext, create_repair_agent
    from src.memory.conversation import get_last_tool_error

    # Prepend stored error context so the repair agent knows what failed.
    # We intentionally do NOT clear the error here — the agent's get_error_context
    # tool will retrieve it directly from Redis during the run, and the tool
    # itself decides when to consume/clear it. Clearing eagerly here caused the
    # agent to see an empty error context when it called get_error_context().
    error_ctx = await get_last_tool_error(user_telegram_id)
    enriched_message = user_message
    if error_ctx:
        failure_kind = error_ctx.get('failure_kind', 'code_failure')
        retry = error_ctx.get('retry_context', False)
        extra = ""
        if retry and failure_kind == "missing_tool":
            extra = (
                "\n**Hint:** The previous verification command was wrong for the file "
                "type (missing_tool). Call `refine_pending_verification` to auto-pick "
                "the correct command, then tell the owner to say `apply patch` again."
            )
        elif retry:
            extra = (
                "\n**Hint:** The patched code failed verification. Analyze the stderr "
                "above and propose a revised diff via `propose_patch`."
            )
        error_block = (
            "## Recent Error Context (auto-captured)\n"
            f"**User request that failed:** {error_ctx.get('user_message', 'N/A')}\n"
            f"**Atlas response:** {error_ctx.get('assistant_response', 'N/A')}\n"
            f"**failure_kind:** {failure_kind}\n"
            f"**Timestamp:** {error_ctx.get('timestamp', 'N/A')}"
            f"{extra}\n\n"
            "---\n\n"
            f"**Owner's current request:** {user_message}"
        )
        enriched_message = error_block

    repair_agent = create_repair_agent()
    sdk_session = await _get_agent_session(user_telegram_id)
    run_config = RunConfig(
        session_input_callback=_keep_recent_session_history,
    )
    repair_result = await Runner.run(
        repair_agent,
        enriched_message,
        session=sdk_session,
        run_config=run_config,
        context=RepairContext(user_telegram_id=user_telegram_id),
    )
    return repair_result.final_output


def _prepare_orchestrator_input(user_message: str) -> str:
    temporal_context = append_temporal_context(user_message)
    action_policy_context = append_action_policy_context(user_message)
    if action_policy_context == user_message:
        return temporal_context
    action_policy_block = action_policy_context[len(user_message):]
    return f"{temporal_context}{action_policy_block}"


def _looks_like_explicit_image_generation_request(user_message: str) -> bool:
    lowered = " ".join((user_message or "").strip().lower().split())
    if not lowered:
        return False
    if not any(verb in lowered for verb in _IMAGE_REQUEST_VERBS):
        return False
    image_terms = ("image", "picture", "photo", "art", "artwork", "illustration", "logo")
    return any(term in lowered for term in image_terms) or "draw" in lowered or "render" in lowered


async def _maybe_handle_direct_image_analysis(user_telegram_id: int, user_message: str) -> str | None:
    """If the session has a pending uploaded image, analyze it directly without LLM routing."""
    if not settings.openrouter_image_enabled or not settings.openrouter_api_key:
        return None

    from src.memory.conversation import get_session_field, delete_session_field

    try:
        raw_payload = await get_session_field(user_telegram_id, "latest_uploaded_image")
    except Exception:
        return None
    if not raw_payload:
        return None

    import base64 as _base64
    payload = json.loads(raw_payload)
    image_base64 = payload.get("data_base64")
    mime_type = payload.get("mime_type") or "image/jpeg"
    if not image_base64:
        return None

    from src.integrations.openrouter import analyze_image

    result = await analyze_image(
        user_id=user_telegram_id,
        prompt=user_message,
        image_bytes=_base64.b64decode(image_base64),
        mime_type=mime_type,
    )
    await delete_session_field(user_telegram_id, "latest_uploaded_image")
    return result.analysis


async def _maybe_handle_direct_image_generation(user_telegram_id: int, user_message: str) -> str | None:
    if not _looks_like_explicit_image_generation_request(user_message):
        return None

    if not settings.openrouter_image_enabled:
        return (
            "Image generation is currently disabled in Atlas. "
            "Enable `OPENROUTER_IMAGE_ENABLED=true` and restart the assistant container."
        )
    if not settings.openrouter_api_key:
        return (
            "Image generation is not configured yet because `OPENROUTER_API_KEY` is missing in the running assistant container."
        )

    from src.integrations.openrouter import generate_image
    from src.memory.conversation import set_session_field

    result = await generate_image(user_id=user_telegram_id, prompt=user_message)
    payload = json.dumps([
        {
            "data_base64": result.data_base64,
            "mime_type": result.mime_type,
            "prompt": result.prompt,
            "caption": result.revised_prompt,
            "model": result.model,
        }
    ])
    await set_session_field(user_telegram_id, "pending_image_attachments", payload)
    return f"Generated your image with `{result.model}`."


def _keep_recent_session_history(history: list, new_input: list) -> list:
    """Session input callback: keep only text messages, strip tool call items.

    Tool call items (function_call, function_call_output) from previous
    Runner.run() calls reference call_ids that don't exist in the current
    API context, causing 400 "No tool call found" errors.  We keep only
    role-based items (user / assistant / system / developer messages).
    """
    safe_items = []
    for item in history:
        if isinstance(item, dict):
            if "role" in item:
                safe_items.append(item)
            # Skip dicts with "type" key only (function_call, function_call_output)
        elif hasattr(item, "role") and getattr(item, "role", None) is not None:
            safe_items.append(item)
        # Skip tool-call objects without a role attribute
    return safe_items[-20:] + new_input


async def _get_agent_session(user_telegram_id: int):
    """Get or create a RedisSession for SDK-managed conversation memory."""
    try:
        from agents.extensions.memory import RedisSession
        return RedisSession.from_url(
            session_id=f"agent_session:{user_telegram_id}",
            url=settings.redis_url,
        )
    except Exception as e:
        logger.warning("Failed to create RedisSession, running without session: %s", e)
        return None


async def _add_direct_response_to_session(
    user_telegram_id: int,
    user_message: str,
    assistant_response: str,
) -> None:
    """Record a direct handler response in the SDK session so the LLM
    sees it as a prior conversation turn on subsequent requests."""
    try:
        sdk_session = await _get_agent_session(user_telegram_id)
        if sdk_session is not None:
            await sdk_session.add_items([
                {"role": "user", "content": user_message},
                {"role": "assistant", "content": assistant_response},
            ])
    except Exception as e:
        logger.debug("Failed to add direct response to SDK session: %s", e)


def _extract_generated_images(result) -> list[ImageAttachment]:
    images: list[ImageAttachment] = []
    call_names: dict[str, str] = {}
    for item in getattr(result, "new_items", None) or []:
        item_type = type(item).__name__
        if item_type in ("FunctionCallItem", "ToolCallItem"):
            call_id = getattr(item, "call_id", None) or getattr(item, "id", "")
            tool_name = getattr(item, "name", None) or getattr(item, "tool_name", None) or ""
            if call_id and tool_name:
                call_names[call_id] = tool_name
            continue

        if item_type not in ("FunctionCallOutputItem", "ToolCallOutputItem"):
            continue

        call_id = getattr(item, "call_id", None) or ""
        if call_names.get(call_id) != "generate_image":
            continue

        raw_output = getattr(item, "output", None)
        if not isinstance(raw_output, str):
            continue

        try:
            payload = json.loads(raw_output)
        except json.JSONDecodeError:
            logger.debug("generate_image tool output was not JSON")
            continue

        if payload.get("kind") != "openrouter_image":
            continue

        data_base64 = payload.get("data_base64")
        mime_type = payload.get("mime_type") or "image/png"
        if not data_base64:
            continue

        images.append(
            ImageAttachment(
                data_base64=data_base64,
                mime_type=mime_type,
                prompt=payload.get("prompt") or "",
                caption=payload.get("revised_prompt") or "",
                model=payload.get("model") or "",
            )
        )

    return images


async def run_orchestrator_result(user_telegram_id: int, user_message: str) -> OrchestratorResult:
    try:
        from src.memory.conversation import delete_session_field

        await delete_session_field(user_telegram_id, "pending_image_attachments")
    except Exception as exc:
        logger.debug("Failed to clear stale image attachments: %s", exc)

    text = await run_orchestrator(user_telegram_id, user_message)
    images: list[ImageAttachment] = []
    try:
        from src.memory.conversation import delete_session_field, get_session_field

        raw_payload = await get_session_field(user_telegram_id, "pending_image_attachments")
        if raw_payload:
            payload = json.loads(raw_payload)
            images = [
                ImageAttachment(
                    data_base64=item.get("data_base64") or "",
                    mime_type=item.get("mime_type") or "image/png",
                    prompt=item.get("prompt") or "",
                    caption=item.get("caption") or "",
                    model=item.get("model") or "",
                )
                for item in payload
                if item.get("data_base64")
            ]
            await delete_session_field(user_telegram_id, "pending_image_attachments")
    except Exception as exc:
        logger.debug("Failed to load pending image attachments: %s", exc)

    return OrchestratorResult(text=text, images=images)


async def run_orchestrator(user_telegram_id: int, user_message: str) -> str:
    """Run the orchestrator agent on a user message and return the response text.

    Phase 3+: Uses dynamic persona, SDK RedisSession for conversation memory,
    Mem0 for long-term memory, and reflector for quality scoring.
    """
    from src.db.session import async_session
    from src.db.models import User
    from src.memory.conversation import add_turn, get_conversation_history
    from src.repair.engine import maybe_handle_pending_repair
    from sqlalchemy import select

    # Look up user
    user_name = "there"
    user_db_id = None
    async with async_session() as session:
        result = await session.execute(
            select(User).where(User.telegram_id == user_telegram_id)
        )
        user = result.scalar_one_or_none()
        if user:
            user_name = user.display_name or "there"
            user_db_id = user.id

    # ── Log inbound message to AuditLog for Activity tab visibility ────
    _inbound_audit_id: int | None = None
    try:
        from src.db.models import AuditLog as _AuditLog
        _inbound_entry = _AuditLog(
            user_id=user_db_id or None,
            direction="inbound",
            platform="telegram",
            agent_name=None,
            message_text=user_message,
        )
        async with async_session() as _asess:
            _asess.add(_inbound_entry)
            await _asess.commit()
            _inbound_audit_id = _inbound_entry.id
    except Exception as _ae:
        logger.debug("Inbound audit log failed (non-critical): %s", _ae)

    # Record user message in conversation session (state management)
    await add_turn(user_telegram_id, "user", user_message)
    history = await get_conversation_history(user_telegram_id)
    recent_context_override = _recent_context_override_for_fresh_task_retry(history, user_message)

    direct_pending_repair_response = await maybe_handle_pending_repair(
        user_telegram_id,
        user_message,
    )
    if direct_pending_repair_response is not None:
        await add_turn(user_telegram_id, "assistant", direct_pending_repair_response)
        await _add_direct_response_to_session(user_telegram_id, user_message, direct_pending_repair_response)
        return direct_pending_repair_response

    # Context-aware repair: "fix it" / "fix this" with a stored error → repair agent
    if await _is_fix_it_with_context(user_telegram_id, user_message):
        logger.info("Routing to RepairAgent via context-aware 'fix it' cue")
        response_text = await _run_repair_agent_direct(user_telegram_id, user_message)
        await add_turn(user_telegram_id, "assistant", response_text)
        await _add_direct_response_to_session(user_telegram_id, user_message, response_text)

        _fire_and_forget(
            _run_reflector_background(
                user_message, response_text, str(user_telegram_id)
            )
        )
        return response_text

    if _is_repair_request(user_message):
        response_text = await _run_repair_agent_direct(user_telegram_id, user_message)
        await add_turn(user_telegram_id, "assistant", response_text)
        await _add_direct_response_to_session(user_telegram_id, user_message, response_text)

        _fire_and_forget(
            _run_reflector_background(
                user_message, response_text, str(user_telegram_id)
            )
        )
        return response_text

    direct_pending_gmail_send_response = await _maybe_handle_pending_connected_gmail_send(
        user_telegram_id,
        user_message,
    )
    if direct_pending_gmail_send_response is not None:
        await add_turn(user_telegram_id, "assistant", direct_pending_gmail_send_response)
        await _add_direct_response_to_session(user_telegram_id, user_message, direct_pending_gmail_send_response)
        return direct_pending_gmail_send_response

    direct_gmail_response = await _maybe_handle_connected_gmail_check(
        user_telegram_id,
        user_message,
    )
    if direct_gmail_response is not None:
        await add_turn(user_telegram_id, "assistant", direct_gmail_response)
        await _add_direct_response_to_session(user_telegram_id, user_message, direct_gmail_response)
        return direct_gmail_response

    direct_calendar_response = await _maybe_handle_connected_calendar_check(
        user_telegram_id,
        user_message,
    )
    if direct_calendar_response is not None:
        await add_turn(user_telegram_id, "assistant", direct_calendar_response)
        await _add_direct_response_to_session(user_telegram_id, user_message, direct_calendar_response)
        return direct_calendar_response

    direct_google_tasks_response = await _maybe_handle_connected_google_tasks_flow(
        user_telegram_id,
        user_message,
    )
    if direct_google_tasks_response is not None:
        await add_turn(user_telegram_id, "assistant", direct_google_tasks_response)
        await _add_direct_response_to_session(user_telegram_id, user_message, direct_google_tasks_response)
        return direct_google_tasks_response

    direct_image_analysis_response = await _maybe_handle_direct_image_analysis(
        user_telegram_id,
        user_message,
    )
    if direct_image_analysis_response is not None:
        await add_turn(user_telegram_id, "assistant", direct_image_analysis_response)
        await _add_direct_response_to_session(user_telegram_id, user_message, direct_image_analysis_response)
        return direct_image_analysis_response

    direct_image_response = await _maybe_handle_direct_image_generation(
        user_telegram_id,
        user_message,
    )
    if direct_image_response is not None:
        await add_turn(user_telegram_id, "assistant", direct_image_response)
        await _add_direct_response_to_session(user_telegram_id, user_message, direct_image_response)
        return direct_image_response

    # ── M2: Autonomous background job (monitor/watch requests) ────────
    try:
        from src.agents.background_job import is_background_job_request, create_background_job
        if is_background_job_request(user_message) and user_db_id:
            _interval = 600
            _max_iter = 48
            _done_cond = None
            _lowered = user_message.lower()
            if "every minute" in _lowered:
                _interval = 60
            elif "every 5 min" in _lowered:
                _interval = 300
            elif "every hour" in _lowered or "hourly" in _lowered:
                _interval = 3600
            if "until " in _lowered:
                _idx = _lowered.index("until ")
                _done_cond = user_message[_idx + 6:].strip()
            job_info = await create_background_job(
                user_telegram_id=user_telegram_id,
                user_db_id=user_db_id,
                goal=user_message,
                done_condition=_done_cond,
                check_interval_seconds=_interval,
                max_iterations=_max_iter,
            )
            _interval_label = f"{_interval // 60} min" if _interval >= 60 else f"{_interval}s"
            response_text = (
                f"✅ Background job started (ID #{job_info['id']}).\n\n"
                f"**Goal:** {user_message}\n"
                + (f"**Stop when:** {_done_cond}\n" if _done_cond else "")
                + f"**Check interval:** every {_interval_label}\n"
                f"**Max iterations:** {_max_iter}\n\n"
                "I'll run this in the background and notify you when done or if anything important comes up. "
                "You can cancel it from the Dashboard → Background Jobs tab or say `/cancel`."
            )
            await add_turn(user_telegram_id, "assistant", response_text)
            await _add_direct_response_to_session(user_telegram_id, user_message, response_text)
            return response_text
    except Exception as _bge:
        logger.warning("Background job detection failed, continuing normally: %s", _bge)

    # ── M1: Parallel multi-domain fan-out ─────────────────────────────
    try:
        from src.agents.routing_hardened import detect_parallel_domains
        _parallel_domains = detect_parallel_domains(user_message)
        if _parallel_domains:
            from src.agents.parallel_runner import ParallelTask, run_parallel_tasks
            _tasks = [
                ParallelTask(domain=d["domain"], prompt=d["prompt"])
                for d in _parallel_domains
            ]
            logger.info(
                "Parallel fan-out: %d branches detected for user %d — domains: %s",
                len(_tasks), user_telegram_id,
                [t.domain for t in _tasks],
            )
            response_text = await run_parallel_tasks(_tasks, user_telegram_id, user_name)
            await add_turn(user_telegram_id, "assistant", response_text)
            await _add_direct_response_to_session(user_telegram_id, user_message, response_text)
            _fire_and_forget(
                _run_reflector_background(user_message, response_text, str(user_telegram_id))
            )
            return response_text
    except Exception as _pe:
        logger.warning("Parallel fan-out detection failed, falling through to single agent: %s", _pe)

    # Create orchestrator with dynamic persona (Mem0 + conversation context)
    complexity = _classify_message_complexity(user_message)
    agent = await create_orchestrator_async(
        user_telegram_id,
        user_name,
        task_context=build_task_local_context(user_message),
        scheduler_user_id=user_db_id,
        org_user_id=user_db_id,
        recent_context_override=recent_context_override,
        complexity=complexity,
        user_message=user_message,
    )
    orchestrator_input = _prepare_orchestrator_input(user_message)

    # SDK RedisSession for proper conversation memory — the LLM sees actual
    # message turns (including tool calls/results) instead of a summary.
    sdk_session = await _get_agent_session(user_telegram_id)

    # Pass user_message through RunConfig context so guardrails can make
    # context-aware decisions (e.g., allow email addresses in email workflows).
    run_config = RunConfig(
        session_input_callback=_keep_recent_session_history,
    )

    try:
        result = await Runner.run(
            agent,
            orchestrator_input,
            session=sdk_session,
            run_config=run_config,
            context={"user_message": user_message},
        )
        response_text = result.final_output
        image_attachments = _extract_generated_images(result)

        # ── Record cost for this turn (shared helper) ─────────────────
        from src.models.cost_tracker import record_llm_cost
        await record_llm_cost(
            result=result,
            agent=agent,
            user_db_id=user_db_id,
            user_telegram_id=user_telegram_id,
        )

        # ── Log outbound response to AuditLog (every turn) ────────────
        _outbound_audit_id: int | None = None
        try:
            import time as _t
            from src.db.session import async_session as _async_session
            from src.db.models import AuditLog as _AuditLog
            _usage = getattr(result, "usage", None)
            _tokens = (getattr(_usage, "total_tokens", None) if _usage else None)
            _last_ag = getattr(result, "last_agent", None)
            _ag_name = getattr(_last_ag, "name", None) or "orchestrator"
            _model_name: str | None = None
            try:
                _model_name = getattr(agent, "model", None) or None
            except Exception:
                pass
            _outbound_entry = _AuditLog(
                user_id=user_db_id or None,
                direction="outbound",
                platform="telegram",
                agent_name=_ag_name,
                message_text=response_text,
                token_count=_tokens,
                model_used=_model_name,
            )
            async with _async_session() as _osess:
                _osess.add(_outbound_entry)
                await _osess.commit()
                _outbound_audit_id = _outbound_entry.id
        except Exception as _oe:
            logger.debug("Outbound audit log failed (non-critical): %s", _oe)

        # ── Record step-by-step thought trace (M3 observability) ──────
        try:
            _new_items = getattr(result, "new_items", None) or []
            if _new_items:
                from src.db.session import async_session as _async_session
                from src.db.models import AgentTrace as _AgentTrace
                import time as _time_mod
                _session_key = f"agent_session:{user_telegram_id}"
                _call_timestamps: dict[str, float] = {}
                _trace_rows: list[_AgentTrace] = []
                _step = 0
                for _item in _new_items:
                    _item_type = type(_item).__name__
                    if _item_type in ("FunctionCallItem", "ToolCallItem"):
                        _call_id = getattr(_item, "call_id", None) or getattr(_item, "id", "")
                        _call_timestamps[_call_id] = _time_mod.time()
                        _tool = (
                            getattr(_item, "name", None)
                            or getattr(_item, "tool_name", None)
                            or _item_type
                        )
                        _raw_args = getattr(_item, "arguments", None) or getattr(_item, "input", None)
                        _args_dict: dict | None = None
                        if isinstance(_raw_args, dict):
                            _args_dict = _raw_args
                        elif isinstance(_raw_args, str):
                            try:
                                import json as _json
                                _args_dict = _json.loads(_raw_args)
                            except Exception:
                                _args_dict = {"raw": _raw_args[:500]}
                        _agent_name_trace = (
                            getattr(getattr(_item, "agent", None), "name", None)
                            or (agent.name if hasattr(agent, "name") else None)
                        )
                        _trace_rows.append(_AgentTrace(
                            audit_log_id=_outbound_audit_id,
                            session_key=_session_key,
                            step_index=_step,
                            agent_name=_agent_name_trace,
                            tool_name=_tool,
                            tool_args=_args_dict,
                        ))
                        _step += 1
                    elif _item_type in ("FunctionCallOutputItem", "ToolCallOutputItem"):
                        _call_id = getattr(_item, "call_id", None) or ""
                        _start_ts = _call_timestamps.pop(_call_id, None)
                        _dur = int((_time_mod.time() - _start_ts) * 1000) if _start_ts else None
                        _output = getattr(_item, "output", None) or ""
                        _preview = str(_output)[:200] if _output else None
                        if _trace_rows:
                            _trace_rows[-1].tool_result_preview = _preview
                            _trace_rows[-1].duration_ms = _dur

                if _trace_rows:
                    async with _async_session() as _ts:
                        _ts.add_all(_trace_rows)
                        await _ts.commit()
                    logger.debug("Agent trace: %d steps recorded for session %s", len(_trace_rows), _session_key)
        except Exception as _te:
            logger.debug("Trace recording failed (non-critical): %s", _te)

        last_agent = getattr(result, "last_agent", None)
        last_agent_name = getattr(last_agent, "name", "")
        if (
            last_agent_name != "RepairAgent"
            and _response_indicates_failed_repair_handoff(response_text)
        ):
            logger.warning(
                "Detected failed repair handoff reply; rerouting directly to RepairAgent"
            )
            response_text = await _run_repair_agent_direct(user_telegram_id, user_message)

        await _maybe_store_pending_connected_gmail_send(user_telegram_id, user_message, response_text)

        # Capture tool/skill/agent errors for repair routing and durable audit
        if _response_has_tool_error(response_text):
            import time as _time
            from src.memory.conversation import store_last_tool_error
            await store_last_tool_error(user_telegram_id, {
                "user_message": user_message,
                "assistant_response": response_text[:2000],
                "timestamp": _time.strftime("%Y-%m-%dT%H:%M:%S"),
            })
            logger.info("Stored tool error context for user %s (repair agent can access it)", user_telegram_id)

            # Proactively notify owner via Telegram so they don't have to discover errors manually
            _fire_and_forget(
                _notify_owner_error(user_telegram_id, user_message, response_text)
            )

            # Also persist to Postgres for durable debugging via dashboard/agents
            try:
                from src.db.session import async_session as _async_session
                from src.db.models import AuditLog as _AuditLog
                async with _async_session() as _session:
                    _session.add(_AuditLog(
                        user_id=user_db_id or None,
                        direction="outbound",
                        platform="telegram",
                        agent_name="orchestrator",
                        message_text="Tool/skill/agent error detected in assistant response",
                        tools_used={
                            "error": True,
                            "user_message": user_message,
                            "assistant_response": response_text[:1000],
                        },
                    ))
                    await _session.commit()
            except Exception as _e:
                logger.debug("Failed to persist error audit log (non-critical): %s", _e)

        # Record assistant response in conversation session (state management)
        await add_turn(user_telegram_id, "assistant", response_text)

        # Run reflector asynchronously (don't block the response)
        _fire_and_forget(
            _run_reflector_background(
                user_message, response_text, str(user_telegram_id)
            )
        )

        if image_attachments:
            try:
                from src.memory.conversation import set_session_field
                payload = json.dumps([
                    {
                        "data_base64": img.data_base64,
                        "mime_type": img.mime_type,
                        "prompt": img.prompt,
                        "caption": img.caption,
                        "model": img.model,
                    }
                    for img in image_attachments
                ])
                await set_session_field(user_telegram_id, "pending_image_attachments", payload)
            except Exception as exc:
                logger.debug("Failed to persist image attachments: %s", exc)

        return response_text
    except Exception as e:
        logger.exception("Orchestrator run failed: %s", e)
        raise


async def _run_reflector_background(
    user_message: str, assistant_response: str, user_id: str
) -> None:
    """Run the reflector in the background without blocking the main response."""
    try:
        from src.agents.reflector_agent import reflect_on_interaction
        from src.memory.conversation import record_quality_score, get_quality_trend

        reflection = await reflect_on_interaction(user_message, assistant_response, user_id)
        score = reflection.get("quality_score", 0.5)

        # Track quality scores for trend analysis
        try:
            await record_quality_score(int(user_id), score)
        except (ValueError, TypeError):
            pass

        if score < 0.4:
            logger.warning("Low quality interaction for user %s (score: %.1f)", user_id, score)

        # Check quality trend — warn on degradation
        try:
            trend = await get_quality_trend(int(user_id), window=5)
            if trend is not None and trend < 0.5:
                logger.warning(
                    "Quality trend degrading for user %s (avg=%.2f over last 5)",
                    user_id, trend,
                )
        except (ValueError, TypeError):
            pass

    except Exception as e:
        logger.debug("Reflector background task failed (non-critical): %s", e)
