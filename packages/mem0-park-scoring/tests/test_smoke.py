"""Smoke tests for mem0-park-scoring as a standalone package."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone


def test_public_api_imports() -> None:
    from mem0_park_scoring import (
        compute_score,
        score_memory,
        select_for_eviction,
        chunk_for_summary,
        DEFAULT_CAP,
    )
    assert callable(compute_score)
    assert score_memory is compute_score  # alias
    assert callable(select_for_eviction)
    assert callable(chunk_for_summary)
    assert DEFAULT_CAP == 8000


def test_recency_dominates_for_recent_memory() -> None:
    """A memory created 1 hour ago with default importance + 0 access
    should still score above 0.3 (recency = ~1.0 contributes 0.45)."""
    from mem0_park_scoring import compute_score

    now = datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc)
    fresh = {
        "id": "m1",
        "memory": "User likes morning meetings",
        "updated_at": (now - timedelta(hours=1)).isoformat().replace("+00:00", "Z"),
        "metadata": {"atlas": {"importance": 0.5, "access_count": 0}},
    }
    assert compute_score(fresh, max_access=10, now=now) > 0.5


def test_old_low_access_low_importance_scores_lowest() -> None:
    """Eviction policy: a 60-day-old, never-accessed, importance-0.1 memory
    must score below a fresh, frequently-accessed, importance-0.9 memory."""
    from mem0_park_scoring import compute_score

    now = datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc)
    old_low = {
        "id": "m_old",
        "updated_at": (now - timedelta(days=60)).isoformat().replace("+00:00", "Z"),
        "metadata": {"atlas": {"importance": 0.1, "access_count": 0}},
    }
    fresh_high = {
        "id": "m_fresh",
        "updated_at": now.isoformat().replace("+00:00", "Z"),
        "metadata": {"atlas": {"importance": 0.9, "access_count": 100}},
    }
    assert compute_score(old_low, max_access=100, now=now) < compute_score(
        fresh_high, max_access=100, now=now
    )


def test_select_for_eviction_below_cap_returns_empty() -> None:
    from mem0_park_scoring import select_for_eviction

    memories = [{"id": str(i), "metadata": {}} for i in range(5)]
    assert select_for_eviction(memories, cap=10) == []


def test_select_for_eviction_protects_summaries() -> None:
    """Summary memories (atlas.is_summary=True) must never be evicted."""
    from mem0_park_scoring import select_for_eviction

    now = datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc)
    # Old summary that would otherwise be evicted first
    summary = {
        "id": "s",
        "updated_at": (now - timedelta(days=365)).isoformat().replace("+00:00", "Z"),
        "metadata": {"atlas": {"is_summary": True, "importance": 0.5}},
    }
    fresh = [
        {
            "id": f"m{i}",
            "updated_at": now.isoformat().replace("+00:00", "Z"),
            "metadata": {"atlas": {"importance": 0.5, "access_count": 0}},
        }
        for i in range(15)
    ]
    memories = [summary, *fresh]
    to_evict = select_for_eviction(memories, cap=10, target_after=8, now=now)
    assert summary not in to_evict, "Summary memory must be eviction-protected"


def test_chunk_for_summary_splits_evenly() -> None:
    from mem0_park_scoring import chunk_for_summary

    memories = [{"id": str(i), "created_at": f"2026-01-{i+1:02d}T00:00:00Z"} for i in range(16)]
    chunks = chunk_for_summary(memories, batches=4)
    assert len(chunks) == 4
    # Roughly even
    sizes = sorted(len(c) for c in chunks)
    assert max(sizes) - min(sizes) <= 1
