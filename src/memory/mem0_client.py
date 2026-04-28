"""Mem0 memory client — self-hosted, connects to Docker Qdrant + PostgreSQL."""

import logging
from typing import Optional

from mem0 import Memory

from src.settings import settings

logger = logging.getLogger(__name__)

DEDUP_THRESHOLD = 0.85  # Cosine similarity threshold for memory deduplication

_memory_instance: Optional[Memory] = None


def get_memory() -> Memory:
    """Get or create the singleton Mem0 memory instance.

    All backends are self-hosted Docker containers — zero SaaS calls (HC-1).
    """
    global _memory_instance
    if _memory_instance is not None:
        return _memory_instance

    qdrant_host = settings.qdrant_url.replace("http://", "").split(":")[0]
    qdrant_port = int(settings.qdrant_url.replace("http://", "").split(":")[1])

    config = {
        "llm": {
            "provider": "openai",
            "config": {
                "model": settings.model_fast,
                "api_key": settings.openai_api_key,
            },
        },
        "embedder": {
            "provider": "openai",
            "config": {
                "model": "text-embedding-3-small",
                "api_key": settings.openai_api_key,
            },
        },
        "vector_store": {
            "provider": "qdrant",
            "config": {
                "host": qdrant_host,
                "port": qdrant_port,
                "collection_name": "personal_assistant_memories",
            },
        },
    }

    _memory_instance = Memory.from_config(config)
    logger.info("Mem0 memory client initialized (Qdrant: %s:%d)", qdrant_host, qdrant_port)
    return _memory_instance


async def add_memory(text: str, user_id: str, metadata: Optional[dict] = None) -> dict:
    """Add a memory for a user, with dedup check.

    Before storing, searches for semantically similar memories.  If a
    near-duplicate is found (score >= ``DEDUP_THRESHOLD``), the existing
    memory is updated instead of creating a new entry.
    """
    mem = get_memory()
    meta = metadata or {}

    # Dedup: check for near-duplicate before inserting.
    # Mem0 v2.x dropped the top-level user_id/limit kwargs from search() and
    # get_all() in favor of `filters={"user_id": ...}` and `top_k`. The add()
    # and delete_all() APIs still accept user_id directly. See the v1 -> v2
    # migration warning: "Top-level entity parameters frozenset({'user_id'})
    # are not supported in search(). Use filters={'user_id': '...'} instead."
    try:
        existing = mem.search(text, filters={"user_id": user_id}, top_k=3)
        hits = existing.get("results", []) if isinstance(existing, dict) else existing
        for hit in hits:
            score = hit.get("score", 0)
            if score >= DEDUP_THRESHOLD:
                hit_id = hit.get("id")
                if hit_id:
                    mem.update(hit_id, text)
                    logger.info(
                        "Memory deduped (score=%.2f) for user %s — updated %s",
                        score, user_id, hit_id,
                    )
                    return {"deduplicated": True, "id": hit_id, "score": score}
    except Exception as exc:
        logger.debug("Dedup search failed, storing fresh: %s", exc)

    result = mem.add(text, user_id=user_id, metadata=meta)
    logger.debug("Memory added for user %s: %s", user_id, text[:80])
    return result


async def search_memories(query: str, user_id: str, limit: int = 10) -> list[dict]:
    """Search memories for a user by semantic similarity.

    Mem0 v2.x: pass `filters={"user_id": ...}` and `top_k=` (was top-level
    `user_id=` and `limit=` in v1).
    """
    mem = get_memory()
    results = mem.search(query, filters={"user_id": user_id}, top_k=limit)
    hits = results.get("results", []) if isinstance(results, dict) else results

    # Track access count on each returned memory
    for hit in hits:
        hit_id = hit.get("id")
        if hit_id:
            try:
                current_meta = hit.get("metadata") or {}
                current_meta["access_count"] = current_meta.get("access_count", 0) + 1
                mem.update(hit_id, hit.get("memory", ""), metadata=current_meta)
            except Exception:
                pass  # non-critical — don't break search for tracking
    return hits


async def get_all_memories(user_id: str) -> list[dict]:
    """Get all memories for a user.

    Mem0 v2.x: pass `filters={"user_id": ...}` (was top-level `user_id=` in v1).
    """
    mem = get_memory()
    results = mem.get_all(filters={"user_id": user_id})
    return results.get("results", []) if isinstance(results, dict) else results


async def delete_memory(memory_id: str) -> bool:
    """Delete a specific memory by ID."""
    mem = get_memory()
    try:
        mem.delete(memory_id)
        logger.info("Memory deleted: %s", memory_id)
        return True
    except Exception as e:
        logger.error("Failed to delete memory %s: %s", memory_id, e)
        return False


async def delete_all_memories(user_id: str) -> bool:
    """Delete all memories for a user."""
    mem = get_memory()
    try:
        mem.delete_all(user_id=user_id)
        logger.info("All memories deleted for user %s", user_id)
        return True
    except Exception as e:
        logger.error("Failed to delete all memories for %s: %s", user_id, e)
        return False
