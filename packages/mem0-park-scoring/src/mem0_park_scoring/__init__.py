"""Memory eviction policy: composite-score + summarize-then-delete.

Caps per-user Mem0 memory count to prevent unbounded vector storage growth
and embedding-API cost growth. When the cap is exceeded, the lowest-scoring
memories are consolidated into a small number of summary memories (preserving
forensic value) and then deleted.

Scoring follows the **Generative Agents** pattern (Park et al., 2023 — recency
+ importance + usage), with the relevance term replaced by access frequency
since this scoring is performed at eviction time, not at retrieval time:

    score = 0.45·recency + 0.25·access_norm + 0.30·importance

  recency      = exp(-Δhours / 720)                # ~30-day half-life
  access_norm  = log(1 + access) / log(1 + max_access_in_batch)
  importance   = metadata.atlas.importance         # default 0.5 if absent

The default cap (8000) is intentionally a safety net rather than a tight
storage policy — the Curator agent does targeted pruning weekly; this
evictor catches what the Curator misses and prevents catastrophic growth in
worst-case scenarios (curator disabled, runaway add_memory calls, etc.).

Summary memories (`metadata.atlas.is_summary == True`) are protected from
eviction so we never lose consolidated context once it has been written.

References:
- Park et al. (2023) "Generative Agents", arXiv:2304.03442
- Packer et al. (2023) "MemGPT", arXiv:2310.08560 — recursive summarization
"""

from datetime import datetime, timezone
from math import exp, log
from typing import Optional


__all__ = [
    "compute_score",
    "score_memory",
    "select_for_eviction",
    "chunk_for_summary",
    "DEFAULT_CAP",
    "DEFAULT_TARGET_AFTER_EVICT",
    "SUMMARY_BATCH_COUNT",
    "RECENCY_HALF_LIFE_HOURS",
    "LEGACY_IMPORTANCE_DEFAULT",
]


DEFAULT_CAP = 8000
DEFAULT_TARGET_AFTER_EVICT = 7200  # 10% headroom after eviction (hysteresis)
SUMMARY_BATCH_COUNT = 8            # consolidate evicted into ≤8 chronological summaries
RECENCY_HALF_LIFE_HOURS = 720      # 30 days
LEGACY_IMPORTANCE_DEFAULT = 0.5    # neutral — avoid penalizing pre-importance entries


def _parse_iso(ts: Optional[str]) -> Optional[datetime]:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def _atlas_meta(memory: dict) -> dict:
    """Pull the namespaced `metadata.atlas` block, with safe defaults for
    legacy entries that pre-date the importance/access tracking."""
    raw = memory.get("metadata") or {}
    block = raw.get("atlas") or {}
    return {
        "importance": float(block.get("importance", LEGACY_IMPORTANCE_DEFAULT)),
        "access_count": int(block.get("access_count", 0)),
        "is_summary": bool(block.get("is_summary", False)),
    }


def compute_score(
    memory: dict,
    *,
    max_access: int,
    now: Optional[datetime] = None,
) -> float:
    """Composite eviction score in [0, 1]. Higher = more valuable.

    Lowest-scoring entries are evicted first.
    """
    now = now or datetime.now(timezone.utc)
    meta = _atlas_meta(memory)

    updated = (
        _parse_iso(memory.get("updated_at"))
        or _parse_iso(memory.get("created_at"))
    )
    if updated is None:
        # Unknown age — neutral recency rather than 0 (don't punish broken timestamps).
        recency = 0.5
    else:
        delta_hours = max(0.0, (now - updated).total_seconds() / 3600.0)
        recency = exp(-delta_hours / RECENCY_HALF_LIFE_HOURS)

    if max_access > 0:
        access_norm = log(1 + meta["access_count"]) / log(1 + max_access)
    else:
        access_norm = 0.0

    return (
        0.45 * recency
        + 0.25 * access_norm
        + 0.30 * meta["importance"]
    )


def select_for_eviction(
    memories: list[dict],
    *,
    cap: int = DEFAULT_CAP,
    target_after: int = DEFAULT_TARGET_AFTER_EVICT,
    now: Optional[datetime] = None,
) -> list[dict]:
    """Return the slice to evict, lowest-score first. No-op below the cap.

    Summary memories are protected — they hold consolidated state that can't
    be recovered if dropped, and the eviction was already supposed to free
    space by *creating* summaries, so re-evicting them defeats the policy.
    """
    if len(memories) <= cap:
        return []

    candidates = [m for m in memories if not _atlas_meta(m)["is_summary"]]
    if not candidates:
        # Pathological case: every entry is a summary. Don't trigger eviction.
        return []

    max_access = max(
        (_atlas_meta(m)["access_count"] for m in candidates),
        default=0,
    )

    scored = sorted(
        candidates,
        key=lambda m: compute_score(m, max_access=max_access, now=now),
    )

    needed = len(memories) - target_after
    return scored[:needed]


def chunk_for_summary(
    memories: list[dict],
    *,
    batches: int = SUMMARY_BATCH_COUNT,
) -> list[list[dict]]:
    """Split evicted memories into chronological chunks for summarization.

    Chronological (rather than thematic) chunking keeps the implementation
    dependency-free. Each chunk gets one LLM summary call. A future iteration
    can swap this for k-means on text or embeddings for thematic coherence.
    """
    if not memories:
        return []
    sortable = sorted(memories, key=lambda m: m.get("created_at") or "")
    n = len(sortable)
    if n <= batches:
        return [sortable]
    size = (n + batches - 1) // batches  # ceil-divide for even chunks
    return [sortable[i:i + size] for i in range(0, n, size)]


# Convenience alias matching the README. The Atlas-internal name is
# ``compute_score``; ``score_memory`` is exposed for symmetry with
# ``select_for_eviction`` (verb_object naming).
score_memory = compute_score
