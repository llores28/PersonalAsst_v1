"""Redis-backed conversation session management (AD-2).

Active conversation context lives in Redis (TTL: 30 min).
On TTL expiry, conversation is summarized and archived to Mem0 episodic memory.
"""

import json
import logging
import time
from typing import Any, Optional

import redis.asyncio as aioredis
from tenacity import retry, stop_after_attempt, wait_exponential

from src.settings import settings

logger = logging.getLogger(__name__)

SESSION_TTL = 1800  # 30 minutes
MAX_TURNS = 20  # Max conversation turns to keep in session

# Dead-letter queue for compaction failures: keep failed payloads for 7 days
# so an operator (or future background worker) can recover and replay them.
_COMPACT_DLQ_TTL = 86400 * 7


def _compaction_dlq_key(user_id: int) -> str:
    return f"compaction_dlq:{user_id}"


_redis: Any = None


async def get_redis() -> Any:
    """Get or create the Redis connection."""
    global _redis
    if _redis is None:
        _redis = aioredis.from_url(settings.redis_url, decode_responses=True)
    return _redis


def _conv_key(user_id: int) -> str:
    return f"conv:{user_id}"


def _meta_key(user_id: int) -> str:
    return f"conv:{user_id}:meta"


def _pending_google_task_key(user_id: int) -> str:
    return f"pending_google_task:{user_id}"


def _pending_gmail_send_key(user_id: int) -> str:
    return f"pending_gmail_send:{user_id}"


def _pending_clarification_key(user_id: int) -> str:
    return f"pending_clarification:{user_id}"


def _pending_repair_key(user_id: int) -> str:
    return f"pending_repair:{user_id}"


def _last_tool_error_key(user_id: int) -> str:
    return f"last_tool_error:{user_id}"


async def get_conversation_history(user_id: int) -> list[dict]:
    """Get the current conversation history from Redis."""
    r = await get_redis()
    raw = await r.lrange(_conv_key(user_id), 0, -1)
    return [json.loads(item) for item in raw]


async def add_turn(user_id: int, role: str, content: str) -> None:
    """Add a conversation turn (user or assistant message) to the session.

    When the session exceeds MAX_TURNS, the oldest turns are summarized
    and flushed to Mem0 long-term memory before being trimmed. This
    follows the OpenClaw session compaction pattern — no context is
    silently discarded.
    """
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

    # Session compaction: summarize → flush to memory → trim
    length = await r.llen(key)
    if length > MAX_TURNS:
        overflow = length - MAX_TURNS
        # Read the turns that are about to be dropped
        raw_old = await r.lrange(key, 0, overflow - 1)
        old_turns = [json.loads(item) for item in raw_old]
        # Fire-and-forget: compact old turns to Mem0 (non-blocking)
        import asyncio
        asyncio.create_task(_compact_turns_to_memory(user_id, old_turns))
        # Trim immediately so the session stays within limits
        await r.ltrim(key, overflow, -1)


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


async def store_pending_google_task(user_id: int, payload: dict) -> None:
    r = await get_redis()
    key = _pending_google_task_key(user_id)
    await r.set(key, json.dumps(payload))
    await r.expire(key, SESSION_TTL)


async def get_pending_google_task(user_id: int) -> Optional[dict]:
    r = await get_redis()
    raw = await r.get(_pending_google_task_key(user_id))
    return json.loads(raw) if raw else None


async def clear_pending_google_task(user_id: int) -> None:
    r = await get_redis()
    await r.delete(_pending_google_task_key(user_id))


async def store_pending_gmail_send(user_id: int, payload: dict) -> None:
    r = await get_redis()
    key = _pending_gmail_send_key(user_id)
    await r.set(key, json.dumps(payload))
    await r.expire(key, SESSION_TTL)


async def get_pending_gmail_send(user_id: int) -> Optional[dict]:
    r = await get_redis()
    raw = await r.get(_pending_gmail_send_key(user_id))
    return json.loads(raw) if raw else None


async def clear_pending_gmail_send(user_id: int) -> None:
    r = await get_redis()
    await r.delete(_pending_gmail_send_key(user_id))


async def store_pending_clarification(user_id: int, payload: dict) -> None:
    r = await get_redis()
    key = _pending_clarification_key(user_id)
    await r.set(key, json.dumps(payload))
    await r.expire(key, SESSION_TTL)


async def get_pending_clarification(user_id: int) -> Optional[dict]:
    r = await get_redis()
    raw = await r.get(_pending_clarification_key(user_id))
    return json.loads(raw) if raw else None


async def clear_pending_clarification(user_id: int) -> None:
    r = await get_redis()
    await r.delete(_pending_clarification_key(user_id))


async def store_pending_repair(user_id: int, payload: dict) -> None:
    r = await get_redis()
    key = _pending_repair_key(user_id)
    await r.set(key, json.dumps(payload))
    await r.expire(key, SESSION_TTL)


async def get_pending_repair(user_id: int) -> Optional[dict]:
    r = await get_redis()
    raw = await r.get(_pending_repair_key(user_id))
    return json.loads(raw) if raw else None


async def clear_pending_repair(user_id: int) -> None:
    r = await get_redis()
    await r.delete(_pending_repair_key(user_id))


async def store_last_tool_error(user_id: int, error_details: dict) -> None:
    """Store the most recent tool error so the repair agent can access it."""
    r = await get_redis()
    key = _last_tool_error_key(user_id)
    await r.set(key, json.dumps(error_details))
    await r.expire(key, SESSION_TTL)


async def get_last_tool_error(user_id: int) -> Optional[dict]:
    """Retrieve the most recent tool error for repair context."""
    r = await get_redis()
    raw = await r.get(_last_tool_error_key(user_id))
    return json.loads(raw) if raw else None


async def clear_last_tool_error(user_id: int) -> None:
    """Clear the stored tool error after repair agent handles it."""
    r = await get_redis()
    await r.delete(_last_tool_error_key(user_id))


def _quality_scores_key(user_id: int) -> str:
    return f"quality_scores:{user_id}"


async def record_quality_score(user_id: int, score: float) -> None:
    """Record a reflector quality score for trend tracking."""
    r = await get_redis()
    key = _quality_scores_key(user_id)
    await r.rpush(key, str(score))
    await r.ltrim(key, -20, -1)  # Keep last 20 scores
    await r.expire(key, 86400 * 7)  # 7-day TTL


async def get_quality_trend(user_id: int, window: int = 5) -> Optional[float]:
    """Get the average quality score over the last N interactions.

    Returns None if fewer than ``window`` scores are recorded.
    """
    r = await get_redis()
    scores_raw = await r.lrange(_quality_scores_key(user_id), -window, -1)
    if len(scores_raw) < window:
        return None
    scores = [float(s) for s in scores_raw]
    return sum(scores) / len(scores)


def _cached_tasks_key(user_id: int) -> str:
    return f"cached_tasks:{user_id}"


TASK_CACHE_TTL = 30  # seconds


async def cache_task_list(user_id: int, task_list_result: str) -> None:
    """Cache a Google Tasks list response for rapid follow-ups."""
    r = await get_redis()
    await r.set(_cached_tasks_key(user_id), task_list_result)
    await r.expire(_cached_tasks_key(user_id), TASK_CACHE_TTL)


async def get_cached_task_list(user_id: int) -> Optional[str]:
    """Get a cached Google Tasks list if still fresh."""
    r = await get_redis()
    return await r.get(_cached_tasks_key(user_id))


async def set_session_field(user_id: int, field: str, value: str) -> None:
    """Set a custom field in the session metadata hash."""
    r = await get_redis()
    await r.hset(_meta_key(user_id), field, value)
    await r.expire(_meta_key(user_id), SESSION_TTL)


async def get_session_field(user_id: int, field: str) -> Optional[str]:
    """Get a custom field from the session metadata hash."""
    r = await get_redis()
    return await r.hget(_meta_key(user_id), field)


async def delete_session_field(user_id: int, field: str) -> None:
    """Delete a custom field from the session metadata hash."""
    r = await get_redis()
    await r.hdel(_meta_key(user_id), field)


async def get_session_metadata(user_id: int) -> Optional[dict]:
    """Get session metadata (turn count, start time)."""
    r = await get_redis()
    meta = await r.hgetall(_meta_key(user_id))
    return meta if meta else None


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=4),
    reraise=True,
)
async def _compact_turns_to_memory_with_retry(user_id: int, old_turns: list[dict]) -> None:
    """Inner: summarize via LLM and write to Mem0. Raises on any failure so
    tenacity retries up to 3 times with exponential backoff. The outer
    `_compact_turns_to_memory` catches the post-retry exception and dead-letters
    so context is never silently lost."""
    conversation_text = "\n".join(
        f"{'User' if t['role'] == 'user' else 'Assistant'}: {t['content'][:500]}"
        for t in old_turns
    )

    from agents import Agent, Runner

    summarizer = Agent(
        name="SessionCompactor",
        instructions=(
            "Summarize this conversation fragment in 1-2 sentences. "
            "Focus on: what the user asked for, key decisions made, "
            "any preferences expressed. Be factual and concise."
        ),
        model=settings.model_fast,
    )
    result = await Runner.run(summarizer, conversation_text)
    summary = result.final_output

    from src.memory.mem0_client import add_memory
    await add_memory(
        f"Session context (compacted): {summary}",
        user_id=str(user_id),
        metadata={"type": "episodic", "source": "session_compaction", "turns": len(old_turns)},
    )


async def _compact_turns_to_memory(user_id: int, old_turns: list[dict]) -> None:
    """Summarize dropped turns and flush to Mem0 long-term memory.

    Called as a fire-and-forget task from `add_turn` when session exceeds
    MAX_TURNS. Retries the LLM+Mem0 work up to 3 times; on exhaustion the raw
    turns are dead-lettered to a Redis list (`compaction_dlq:{user_id}`, 7-day
    TTL) so they can be replayed later — context is never silently discarded
    even when the summarization path is down.
    """
    if len(old_turns) < 2:
        return  # Not enough to summarize

    last_error: Optional[str] = None
    try:
        await _compact_turns_to_memory_with_retry(user_id, old_turns)
        logger.info(
            "Compacted %d turns to memory for user %d",
            len(old_turns), user_id,
        )
        return
    except Exception as e:
        last_error = str(e)
        logger.error(
            "Session compaction exhausted retries for user %d (%d turns); "
            "dead-lettering to Redis. Last error: %s",
            user_id, len(old_turns), last_error,
        )

    # Dead-letter the raw turns so they can be recovered later.
    try:
        r = await get_redis()
        dlq_key = _compaction_dlq_key(user_id)
        await r.rpush(dlq_key, json.dumps({
            "old_turns": old_turns,
            "failed_at": time.time(),
            "error": (last_error or "")[:500],
        }))
        await r.expire(dlq_key, _COMPACT_DLQ_TTL)
    except Exception as dlq_err:  # pragma: no cover — last-ditch
        logger.critical(
            "Compaction DLQ write also failed for user %d: %s — "
            "%d turns LOST",
            user_id, dlq_err, len(old_turns),
        )


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
