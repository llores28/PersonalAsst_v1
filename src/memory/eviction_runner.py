"""Run the memory eviction policy against a real user's Mem0 store.

Entry point: `prune_user_memories(user_id)`. Designed to be called by the
nightly cron at `scripts/prune_memories.py`, or for ad-hoc cleanup.
Idempotent — safe to re-run; under-cap users are a no-op.

Two-phase pipeline ordered to never lose data on partial failure:

  Phase 1: write all consolidation summaries (fail-fast on first error;
           we abort BEFORE deleting anything if we can't summarize).
  Phase 2: delete the evicted originals.

If Phase 1 partially writes and then fails, the report surfaces the partial
state and Phase 2 is skipped — operator can clean up the duplicate summaries
on the next successful run.

Summarization gracefully degrades: if the OpenAI Agents SDK is unavailable
or the LLM call fails, a deterministic stub summary is written so the
pipeline still makes progress (storage still freed, just less useful
summaries).
"""

import logging
from typing import Any, Optional

from src.memory.eviction import (
    DEFAULT_CAP,
    DEFAULT_TARGET_AFTER_EVICT,
    chunk_for_summary,
    select_for_eviction,
)
from src.memory.mem0_client import (
    add_memory,
    delete_memory,
    get_all_memories,
)

logger = logging.getLogger(__name__)


async def _summarize_chunk(chunk: list[dict]) -> str:
    """Produce a 2-3 sentence dense summary of a chunk of evicted memories.

    Falls back to a deterministic stub if the Agents SDK or LLM call fails,
    so eviction still makes progress in degraded conditions (we'd rather
    have an "summarization-failed" stub in place of N raw memories than
    block the eviction and let storage keep growing).
    """
    if not chunk:
        return ""

    facts_text = "\n".join(f"- {m.get('memory', '')}" for m in chunk[:50])

    try:
        from agents import Agent, Runner

        from src.settings import settings

        summarizer = Agent(
            name="MemoryConsolidator",
            instructions=(
                "Summarize these user-related facts into 2-3 dense sentences. "
                "Preserve names, dates, places, preferences, and decisions — "
                "round only when a specific value clearly doesn't matter."
            ),
            model=settings.model_fast,
        )
        result = await Runner.run(summarizer, facts_text)
        return f"[consolidated, {len(chunk)} sources] {result.final_output}"
    except Exception as e:
        logger.warning("Summarization LLM unavailable, using stub: %s", e)
        return f"[consolidated, {len(chunk)} sources, summarization-failed]"


async def prune_user_memories(
    user_id: str,
    *,
    cap: int = DEFAULT_CAP,
    target_after: int = DEFAULT_TARGET_AFTER_EVICT,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Evict bottom-scored memories above `cap`, consolidating into summaries.

    Returns a structured report dict:
        {
          "total":             int,        # memories before eviction
          "evicted":           int,        # actually deleted
          "summaries_added":   int,        # consolidation summaries written
          "dry_run":           bool,
          "reason":            str|absent, # e.g. "under_cap"
          "would_evict":       int|absent, # dry_run only
          "would_create_summaries": int|absent,  # dry_run only
          "error":             str|absent,
        }
    """
    memories = await get_all_memories(user_id)
    total = len(memories)

    to_evict = select_for_eviction(memories, cap=cap, target_after=target_after)
    if not to_evict:
        return {
            "total": total,
            "evicted": 0,
            "summaries_added": 0,
            "dry_run": dry_run,
            "reason": "under_cap",
        }

    chunks = chunk_for_summary(to_evict)

    if dry_run:
        return {
            "total": total,
            "evicted": 0,
            "summaries_added": 0,
            "dry_run": True,
            "would_evict": len(to_evict),
            "would_create_summaries": len(chunks),
        }

    # Phase 1: write all summaries before deleting any originals.
    summaries_added = 0
    for chunk in chunks:
        try:
            summary_text = await _summarize_chunk(chunk)
            if not summary_text:
                continue
            await add_memory(
                summary_text,
                user_id=user_id,
                metadata={"atlas": {
                    "is_summary": True,
                    "importance": 0.7,  # consolidated content kept higher
                    "access_count": 0,
                    "consolidated_count": len(chunk),
                }},
            )
            summaries_added += 1
        except Exception as e:
            logger.error(
                "Summary write failed for user %s mid-eviction; "
                "ABORTING before deletion. Wrote %d/%d summaries. Error: %s",
                user_id, summaries_added, len(chunks), e,
            )
            return {
                "total": total,
                "evicted": 0,
                "summaries_added": summaries_added,
                "dry_run": False,
                "error": "summary_write_failed",
                "detail": str(e),
            }

    # Phase 2: delete originals only after all summaries are durably stored.
    evicted = 0
    for memory in to_evict:
        memory_id = memory.get("id")
        if not memory_id:
            continue
        if await delete_memory(memory_id):
            evicted += 1

    logger.info(
        "Pruned user %s: %d evicted, %d summaries added (cap=%d, was=%d)",
        user_id, evicted, summaries_added, cap, total,
    )
    return {
        "total": total,
        "evicted": evicted,
        "summaries_added": summaries_added,
        "dry_run": False,
    }
