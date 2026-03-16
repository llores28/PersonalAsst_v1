"""Redis-backed conversation session management (AD-2).

Active conversation context lives in Redis (TTL: 30 min).
On TTL expiry, conversation is summarized and archived to Mem0 episodic memory.
"""

import json
import logging
import time
from typing import Optional

import redis.asyncio as aioredis

from src.settings import settings

logger = logging.getLogger(__name__)

SESSION_TTL = 1800  # 30 minutes
MAX_TURNS = 20  # Max conversation turns to keep in session

_redis: Optional[aioredis.Redis] = None


async def get_redis() -> aioredis.Redis:
    """Get or create the Redis connection."""
    global _redis
    if _redis is None:
        _redis = aioredis.from_url(settings.redis_url, decode_responses=True)
    return _redis


def _conv_key(user_id: int) -> str:
    return f"conv:{user_id}"


def _meta_key(user_id: int) -> str:
    return f"conv:{user_id}:meta"


async def get_conversation_history(user_id: int) -> list[dict]:
    """Get the current conversation history from Redis."""
    r = await get_redis()
    raw = await r.lrange(_conv_key(user_id), 0, -1)
    return [json.loads(item) for item in raw]


async def add_turn(user_id: int, role: str, content: str) -> None:
    """Add a conversation turn (user or assistant message) to the session."""
    r = await get_redis()
    key = _conv_key(user_id)
    meta_key = _meta_key(user_id)

    turn = json.dumps({
        "role": role,
        "content": content,
        "timestamp": time.time(),
    })

    await r.rpush(key, turn)
    await r.expire(key, SESSION_TTL)

    # Update metadata
    await r.hincrby(meta_key, "turn_count", 1)
    if not await r.hexists(meta_key, "started_at"):
        await r.hset(meta_key, "started_at", str(time.time()))
    await r.expire(meta_key, SESSION_TTL)

    # Trim to max turns (keep most recent)
    length = await r.llen(key)
    if length > MAX_TURNS:
        await r.ltrim(key, length - MAX_TURNS, -1)


async def get_session_context(user_id: int) -> str:
    """Get conversation context formatted for the orchestrator prompt.

    Returns recent conversation as a formatted string, or empty string if no session.
    """
    history = await get_conversation_history(user_id)
    if not history:
        return ""

    lines = ["## Recent Conversation"]
    for turn in history[-10:]:  # Last 10 turns for context
        role = "User" if turn["role"] == "user" else "Assistant"
        content = turn["content"]
        if len(content) > 300:
            content = content[:300] + "..."
        lines.append(f"**{role}:** {content}")

    return "\n".join(lines)


async def clear_session(user_id: int) -> None:
    """Clear the conversation session for a user."""
    r = await get_redis()
    await r.delete(_conv_key(user_id), _meta_key(user_id))


async def get_session_metadata(user_id: int) -> Optional[dict]:
    """Get session metadata (turn count, start time)."""
    r = await get_redis()
    meta = await r.hgetall(_meta_key(user_id))
    return meta if meta else None


async def archive_session(user_id: int) -> Optional[str]:
    """Archive the current session to Mem0 episodic memory.

    Called when session expires or on explicit request.
    Returns the summary text, or None if no session to archive.
    """
    history = await get_conversation_history(user_id)
    if not history or len(history) < 2:
        return None

    from agents import Agent, Runner

    # Summarize the conversation using a fast model
    conversation_text = "\n".join(
        f"{'User' if t['role'] == 'user' else 'Assistant'}: {t['content']}"
        for t in history
    )

    try:
        summarizer = Agent(
            name="ConversationSummarizer",
            instructions=(
                "Summarize this conversation in 2-3 sentences. Focus on: "
                "what the user wanted, what was accomplished, any preferences "
                "or decisions made. Be factual and concise."
            ),
            model=settings.model_fast,
        )
        result = await Runner.run(summarizer, conversation_text)
        summary = result.final_output

        # Store in Mem0 as episodic memory
        from src.memory.mem0_client import add_memory
        await add_memory(
            f"Conversation summary: {summary}",
            user_id=str(user_id),
            metadata={"type": "episodic", "turn_count": len(history)},
        )

        # Clear the Redis session
        await clear_session(user_id)

        logger.info("Session archived for user %d (%d turns)", user_id, len(history))
        return summary

    except Exception as e:
        logger.error("Failed to archive session for user %d: %s", user_id, e)
        return None
