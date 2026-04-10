"""Persona CRUD — versioned persona management backed by PostgreSQL + Mem0.

Resolves PRD gaps A3 (persona storage) and E2 (phase-aware loading).

The canonical persona template and prompt-assembly helpers live in
``src.agents.persona_mode`` (single source of truth).  This module
re-exports them so existing imports keep working.
"""

import logging
from typing import Optional

from sqlalchemy import select, update

from src.settings import settings

# ── Re-exports from the canonical module ──────────────────────────────
from src.agents.persona_mode import (          # noqa: F401
    PersonaMode,
    PERSONA_TEMPLATE,
    assemble_persona_prompt,
    build_persona_mode_addendum,
)

logger = logging.getLogger(__name__)


async def get_active_persona(user_id: int) -> Optional[dict]:
    """Get the active persona version for a user from PostgreSQL."""
    from src.db.session import async_session
    from src.db.models import PersonaVersion

    async with async_session() as session:
        result = await session.execute(
            select(PersonaVersion).where(
                PersonaVersion.user_id == user_id,
                PersonaVersion.is_active == True,  # noqa: E712 — SQLAlchemy filter
            )
        )
        pv = result.scalar_one_or_none()
        if pv:
            return {
                "id": pv.id,
                "version": pv.version,
                "assistant_name": pv.assistant_name,
                "personality": pv.personality,
            }
    return None


async def create_persona_version(
    user_id: int,
    assistant_name: str,
    personality: dict,
    change_reason: str,
) -> int:
    """Create a new persona version, deactivating the previous one."""
    from src.db.session import async_session
    from src.db.models import PersonaVersion

    async with async_session() as session:
        # Deactivate all existing versions for this user
        await session.execute(
            update(PersonaVersion)
            .where(PersonaVersion.user_id == user_id, PersonaVersion.is_active == True)  # noqa: E712
            .values(is_active=False)
        )

        # Get next version number
        result = await session.execute(
            select(PersonaVersion.version)
            .where(PersonaVersion.user_id == user_id)
            .order_by(PersonaVersion.version.desc())
            .limit(1)
        )
        last_version = result.scalar()
        next_version = (last_version or 0) + 1

        new_pv = PersonaVersion(
            user_id=user_id,
            version=next_version,
            assistant_name=assistant_name,
            personality=personality,
            is_active=True,
            change_reason=change_reason,
        )
        session.add(new_pv)
        await session.commit()
        logger.info("Persona v%d created for user %d: %s", next_version, user_id, change_reason)
        return next_version


async def update_persona_field(user_id: int, field: str, value: str) -> str:
    """Update a single persona field (name or style). Returns confirmation message."""
    current = await get_active_persona(user_id)

    if current is None:
        # Initialize from defaults
        personality = {
            "traits": ["helpful", "proactive", "concise"],
            "style": settings.default_persona_style,
        }
        name = settings.default_assistant_name
    else:
        personality = dict(current["personality"])
        name = current["assistant_name"]

    if field == "name":
        name = value
        reason = f"User changed assistant name to '{value}'"
    elif field == "style":
        valid_styles = ["casual", "friendly", "professional", "brief"]
        if value.lower() not in valid_styles:
            return f"Invalid style. Choose from: {', '.join(valid_styles)}"
        personality["style"] = value.lower()
        reason = f"User changed communication style to '{value}'"
    elif field == "traits":
        personality["traits"] = [t.strip() for t in value.split(",")]
        reason = f"User changed personality traits to '{value}'"
    else:
        return f"Unknown persona field: {field}. Use 'name', 'style', or 'traits'."

    version = await create_persona_version(user_id, name, personality, reason)
    return f"Updated! Persona v{version} — {reason}"


async def _get_db_user_id_from_telegram_id(telegram_id: int) -> Optional[int]:
    """Resolve the internal users.id for a Telegram user ID."""
    from src.db.session import async_session
    from src.db.models import User

    async with async_session() as session:
        result = await session.execute(
            select(User.id).where(User.telegram_id == telegram_id)
        )
        return result.scalar_one_or_none()


def _filter_stale_memories(
    memories: list[dict],
    *,
    workspace_connected: bool = False,
) -> list[dict]:
    """Remove memories that contradict the current system state.

    When Google Workspace tools are connected and operational, memories
    claiming they are broken or need fixing would poison the persona prompt
    and cause the LLM to avoid calling working tools.
    """
    if not workspace_connected:
        return memories

    # Phrases that indicate stale "tools are broken" memories.
    # These were learned during past debugging sessions and are no longer true.
    _STALE_WORKSPACE_PHRASES = [
        "needs authenticated",
        "needs connected",
        "tool access to be fixed",
        "tool access needs",
        "tools need fixing",
        "tools are broken",
        "tools aren't working",
        "drive tool fix",
        "drive session",
        "session issue",
        "connector issue",
        "can't access drive",
        "cannot access drive",
        "drive access to be fixed",
        "before continuing other tasks",
        "require re-authorization",
        "may require re-",
        "re-authorize",
        "reauthorize",
        "authenticated inventory",
        "authenticated listing",
        "not available in this turn",
        "isn't available in this turn",
        "connector path",
        "drive inventory",
        "drive connector",
    ]

    filtered = []
    for mem in memories:
        text = mem.get("memory", mem.get("text", "")).lower()
        if any(phrase in text for phrase in _STALE_WORKSPACE_PHRASES):
            logger.debug("Filtered stale memory: %s", text[:80])
            continue
        filtered.append(mem)
    return filtered


async def build_dynamic_persona_prompt(
    user_id: int,
    user_name: str = "there",
    task_context: str = "(No task-local context yet)",
    recent_context_override: str | None = None,
) -> str:
    """Build persona prompt with Mem0 memories and conversation context.

    This is the Phase 3+ version that replaces the static YAML-based prompt.
    """
    from src.memory.mem0_client import search_memories
    from src.memory.conversation import get_session_context
    from src.integrations.workspace_mcp import is_google_configured, get_connected_google_email

    # user_id here is the Telegram ID used by memory/session systems; persona_versions uses users.id
    db_user_id = await _get_db_user_id_from_telegram_id(user_id)

    # Check current workspace state for stale-memory filtering
    workspace_connected = False
    try:
        connected_email = await get_connected_google_email(user_id)
        workspace_connected = is_google_configured() and connected_email is not None
    except Exception:
        pass

    # Load persona from DB (or fall back to defaults)
    persona = await get_active_persona(db_user_id) if db_user_id is not None else None
    if persona:
        name = persona["assistant_name"]
        traits = persona["personality"].get("traits", ["helpful"])
        style = persona["personality"].get("style", "friendly")
    else:
        name = settings.default_assistant_name
        traits = ["helpful", "proactive", "concise"]
        style = settings.default_persona_style

    # Load user preferences from Mem0
    try:
        preference_memories = await search_memories(
            "user preferences communication style likes dislikes",
            user_id=str(user_id),
            limit=10,
        )
        preference_memories = _filter_stale_memories(
            preference_memories, workspace_connected=workspace_connected,
        )
        if preference_memories:
            preferences_text = "\n".join(
                f"- {m.get('memory', m.get('text', str(m)))}"
                for m in preference_memories
            )
        else:
            preferences_text = "(Still learning your preferences — tell me what you like!)"
    except Exception as e:
        logger.warning("Failed to load preference memories: %s", e)
        preferences_text = "(Memory system initializing...)"

    # Load procedural memories
    try:
        procedural_memories = await search_memories(
            "workflow process steps how to procedure",
            user_id=str(user_id),
            limit=5,
        )
        procedural_memories = _filter_stale_memories(
            procedural_memories, workspace_connected=workspace_connected,
        )
        if procedural_memories:
            procedures_text = "\n".join(
                f"- {m.get('memory', m.get('text', str(m)))}"
                for m in procedural_memories
            )
        else:
            procedures_text = "(No learned workflows yet)"
    except Exception:
        procedures_text = "(No learned workflows yet)"

    # Load recent conversation context from Redis
    if recent_context_override is not None:
        recent_context = recent_context_override
    else:
        try:
            recent_context = await get_session_context(user_id)
            if not recent_context:
                recent_context = "(New conversation)"
        except Exception:
            recent_context = "(New conversation)"

    # Pass full personality dict for deep profile rendering (OCEAN, communication, etc.)
    personality_data = persona["personality"] if persona else None

    return assemble_persona_prompt(
        name=name,
        user_name=user_name,
        personality_traits=", ".join(traits) if isinstance(traits, list) else str(traits),
        communication_style=style,
        user_preferences=preferences_text,
        procedural_memories=procedures_text,
        recent_context=recent_context,
        task_context=task_context,
        personality_data=personality_data,
    )
