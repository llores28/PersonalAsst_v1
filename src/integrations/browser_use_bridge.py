"""Bridge between browser-use and Atlas's existing capabilities.

Three responsibilities:

1. **Custom Tools registry** (`build_atlas_browser_tools`): exposes a small
   set of Atlas helpers as `@tools.action(...)` callables the browser-use
   Agent can invoke mid-task. Without this, the browser agent is an
   isolated automaton; with it, it can read the user's memory, log to
   the dashboard, and stash results back into Drive.

2. **Sensitive-data dictionary** (`collect_sensitive_data`): builds a
   `{label: real_value}` mapping that browser-use uses to keep PII out
   of the LLM context. The LLM sees `<owner_email>`; only Chromium
   substitutes the real value into form fields. Matches browser-use's
   documented `sensitive_data=` parameter.

3. **System-message extension** (`build_atlas_system_extension`): a
   short, Atlas-flavoured prompt fragment passed via
   `extend_system_message=` so the browser agent shares Atlas's voice
   and safety posture (always describe what you're about to do; never
   submit anything that wasn't explicitly requested).

All three functions are pure-data builders so the runner can call them
once per `browse_web` invocation without holding state.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)


# ─── Tools registry ─────────────────────────────────────────────────────


def build_atlas_browser_tools(*, user_telegram_id: int) -> Any:
    """Return a `browser_use.Tools` instance with Atlas helpers registered.

    The browser agent can call these mid-task. They are intentionally
    narrow: read-only access to memory + Drive (pre-tailored output
    only), plus an activity logger so the dashboard reflects browser
    progress in real time.

    All tools fail-soft: an exception inside one returns a string
    explaining the failure rather than aborting the browser run.

    Args:
        user_telegram_id: Owner ID — used to scope memory reads, log
            ownership, and resolve the connected Google email for the
            Drive helper.
    """
    try:
        from browser_use import Tools  # type: ignore[import-not-found]
    except ImportError as exc:
        logger.warning("browser-use Tools registry unavailable: %s", exc)
        return None

    tools = Tools()

    @tools.action(
        description=(
            "Search Atlas's long-term memory for facts about the user that "
            "would help complete this browser task. Use sparingly — only "
            "when you genuinely need a name, preference, address, or other "
            "stored fact you can't see on the page. Returns up to 5 hits."
        ),
    )
    async def read_user_memory(query: str) -> str:
        try:
            from src.memory.mem0_client import search_memories
            hits = await search_memories(query, str(user_telegram_id), limit=5)
            if not hits:
                return "No matching memories found."
            return "\n".join(
                f"- {(h.get('memory') or '')[:200]}" for h in hits[:5]
            )
        except Exception as exc:
            logger.warning("browser-use read_user_memory failed: %s", exc)
            return f"(memory search failed: {exc})"

    @tools.action(
        description=(
            "Append a one-line update to Atlas's activity log so the "
            "dashboard shows live browser progress. Use this for major "
            "milestones (logged in, found results, ready to submit) — "
            "NOT for every click."
        ),
    )
    async def log_browse_activity(message: str) -> str:
        try:
            from src.db.models import AuditLog
            from src.db.session import async_session
            from sqlalchemy import select
            from src.db.models import User
            async with async_session() as session:
                user_row = await session.execute(
                    select(User).where(User.telegram_id == user_telegram_id)
                )
                user = user_row.scalar_one_or_none()
                session.add(AuditLog(
                    user_id=user.id if user else None,
                    direction="outbound",
                    platform="browser",
                    agent_name="browser_use",
                    message_text=message[:500],
                    tools_used={"source": "browse_web", "kind": "milestone"},
                ))
                await session.commit()
            return "logged"
        except Exception as exc:
            logger.warning("browser-use log_browse_activity failed: %s", exc)
            return f"(log failed: {exc})"

    @tools.action(
        description=(
            "Save a small text artifact (markdown, JSON, plain text — NOT "
            "binary files) to the user's Google Drive root folder. Returns "
            "the Drive file ID and a shareable link. Use ONLY when the "
            "user explicitly asked you to save something — never as a "
            "side effect."
        ),
    )
    async def save_text_to_drive(content: str, filename: str) -> str:
        try:
            from src.integrations.workspace_mcp import call_workspace_tool
            args = {
                "filename": filename[:200],
                "content": content[:200_000],  # 200 KB cap — Drive uploads should be small here
                "mime_type": "text/plain",
            }
            result = await call_workspace_tool("upload_to_drive", args)
            return f"saved: {result[:300]}"
        except Exception as exc:
            logger.warning("browser-use save_text_to_drive failed: %s", exc)
            return f"(save failed: {exc})"

    return tools


# ─── Sensitive-data dictionary ──────────────────────────────────────────


async def collect_sensitive_data(*, user_telegram_id: int) -> dict[str, str]:
    """Build the `{label: real_value}` map browser-use's `sensitive_data=`
    parameter expects.

    The LLM only sees the labels (`<owner_email>`); the real values are
    substituted by Chromium when typing into form fields. Keeps PII out
    of every LLM call's prompt.

    What we put in (best-effort — any failure returns a partial map
    rather than crashing):
      - `owner_email`: the user's connected Google email (from Redis)
      - `owner_telegram_id`: their numeric Telegram ID
      - `owner_name`: from the User row if set
      - any memory entry tagged `secret:*` or `credential:*`

    Never includes raw passwords, OAuth tokens, or session cookies —
    those live in the persistent profile or in the workspace-mcp vault
    and never round-trip through this code.
    """
    sensitive: dict[str, str] = {
        "owner_telegram_id": str(user_telegram_id),
    }

    try:
        from src.memory.conversation import get_redis
        redis = await get_redis()
        raw = await redis.get(f"google_email:{user_telegram_id}")
        if raw:
            email = raw if isinstance(raw, str) else raw.decode("utf-8", errors="ignore")
            sensitive["owner_email"] = email.strip()
    except Exception as exc:
        logger.debug("collect_sensitive_data: no google_email in Redis: %s", exc)

    try:
        from sqlalchemy import select
        from src.db.session import async_session
        from src.db.models import User
        async with async_session() as session:
            row = (await session.execute(
                select(User).where(User.telegram_id == user_telegram_id)
            )).scalar_one_or_none()
            if row and getattr(row, "name", None):
                sensitive["owner_name"] = str(row.name)
    except Exception as exc:
        logger.debug("collect_sensitive_data: User lookup failed: %s", exc)

    # Pull entries from Mem0 tagged as secrets the user has explicitly
    # marked for browser-use auto-fill. Format: any memory whose
    # metadata.label starts with 'secret:' becomes a labelled entry.
    try:
        from src.memory.mem0_client import search_memories
        secrets_hits = await search_memories("secret credential password", str(user_telegram_id), limit=20)
        for hit in secrets_hits or []:
            meta = (hit.get("metadata") or {}) if isinstance(hit, dict) else {}
            label = meta.get("label") or meta.get("key")
            value = hit.get("memory") or meta.get("value")
            if isinstance(label, str) and label.startswith("secret:") and value:
                # 'secret:bank_pin' → label key 'bank_pin'
                key = label.split(":", 1)[1].strip().replace(" ", "_") or None
                if key and key not in sensitive:
                    sensitive[key] = str(value)
    except Exception as exc:
        logger.debug("collect_sensitive_data: secrets scan failed: %s", exc)

    return sensitive


# ─── System-message extension ───────────────────────────────────────────


def build_atlas_system_extension() -> str:
    """Short prompt fragment appended via browser-use's
    `extend_system_message=` so the agent inherits Atlas's voice and
    safety posture.

    Kept terse on purpose — every line ships in every step's LLM call
    and burns tokens.
    """
    return (
        "You are operating as Atlas's browser hand. Atlas is a single-user "
        "assistant — its owner is the same person whose accounts you may "
        "be touching. Rules:\n"
        "1. Describe what you're about to do BEFORE any click that submits, "
        "purchases, sends, posts, or applies. The runtime will pause for "
        "human approval; describe so the human can decide quickly.\n"
        "2. Never invent credentials. If a login page asks for a password "
        "you don't see in your sensitive-data map, stop and report "
        "'login required' rather than guessing.\n"
        "3. Prefer reading + summarising over interacting. The cheapest "
        "win is usually 'navigate, extract, return text' — leave clicks "
        "for when they're necessary.\n"
        "4. If a page redirects you to a login screen mid-task, "
        "immediately stop and surface 'login required: <site>' so the "
        "owner can re-seed the profile.\n"
    )
