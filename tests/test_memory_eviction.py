"""Tests for the memory cap + LRU-style eviction policy.

100% mocked — does not require Mem0, Qdrant, OpenAI, or any running infra.
Covers: scoring formula, selection, chunking, and the two-phase runner
including the "summary write failed → abort before deletion" guarantee.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

from src.memory.eviction import (
    DEFAULT_CAP,
    LEGACY_IMPORTANCE_DEFAULT,
    chunk_for_summary,
    compute_score,
    select_for_eviction,
)


# --------------------------------------------------------------------------
# Fixture factory
# --------------------------------------------------------------------------

def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


def _mem(*, id: str = "m1", text: str = "fact", importance: float = 0.5,
         access: int = 0, age_hours: float = 0.0,
         is_summary: bool = False, with_atlas_meta: bool = True) -> dict:
    """Build a memory dict matching Mem0's get_all schema."""
    when = _iso(_now() - timedelta(hours=age_hours))
    metadata: dict = {}
    if with_atlas_meta:
        metadata["atlas"] = {
            "importance": importance,
            "access_count": access,
            "is_summary": is_summary,
        }
    return {
        "id": id,
        "memory": text,
        "metadata": metadata,
        "created_at": when,
        "updated_at": when,
    }


# --------------------------------------------------------------------------
# compute_score
# --------------------------------------------------------------------------

class TestComputeScore:
    def test_recent_high_importance_high_access_scores_near_one(self):
        m = _mem(importance=1.0, access=10, age_hours=0)
        s = compute_score(m, max_access=10)
        # 0.45·~1.0 + 0.25·1.0 + 0.30·1.0 = 1.0
        assert s > 0.95, f"expected near-1, got {s}"

    def test_old_low_importance_unaccessed_scores_near_zero(self):
        m = _mem(importance=0.0, access=0, age_hours=24 * 365)  # 1 year
        s = compute_score(m, max_access=10)
        # recency ≈ exp(-8760/720) ≈ 5e-6 → 0; access=0 → 0; importance=0 → 0
        assert s < 0.05, f"expected near-0, got {s}"

    def test_legacy_memory_without_atlas_meta_uses_neutral_default(self):
        m = _mem(with_atlas_meta=False, age_hours=0)  # very recent, no atlas block
        s = compute_score(m, max_access=10)
        # 0.45·~1.0 + 0.25·0 + 0.30·0.5 = 0.60
        expected = 0.45 + 0.30 * LEGACY_IMPORTANCE_DEFAULT
        assert abs(s - expected) < 0.05

    def test_invalid_timestamps_default_to_neutral_recency(self):
        # Bad ISO strings — recency falls back to 0.5.
        m = {"id": "broken", "memory": "x", "metadata": {},
             "created_at": "not-a-date", "updated_at": "also-broken"}
        s = compute_score(m, max_access=1)
        # 0.45·0.5 + 0 + 0.30·0.5 = 0.375
        assert 0.30 < s < 0.45

    def test_missing_max_access_no_div_by_zero(self):
        # max_access=0 means nobody has accessed anything — access_norm should be 0.
        m = _mem(importance=0.0, access=0, age_hours=0)
        s = compute_score(m, max_access=0)
        # 0.45·~1.0 + 0 + 0 = 0.45
        assert 0.40 < s < 0.50


# --------------------------------------------------------------------------
# select_for_eviction
# --------------------------------------------------------------------------

class TestSelectForEviction:
    def test_empty_returns_empty(self):
        assert select_for_eviction([], cap=10, target_after=8) == []

    def test_under_cap_returns_empty(self):
        memories = [_mem(id=f"m{i}") for i in range(50)]
        assert select_for_eviction(memories, cap=100, target_after=80) == []

    def test_at_cap_returns_empty(self):
        memories = [_mem(id=f"m{i}") for i in range(10)]
        assert select_for_eviction(memories, cap=10, target_after=8) == []

    def test_over_cap_evicts_to_target(self):
        # 12 memories, cap=10, target=8 — must evict exactly 4 (12-8).
        memories = [_mem(id=f"m{i}", access=i, age_hours=0) for i in range(12)]
        evicted = select_for_eviction(memories, cap=10, target_after=8)
        assert len(evicted) == 4
        # Lowest score = lowest access in this fixture → m0..m3 evicted first.
        evicted_ids = {m["id"] for m in evicted}
        assert "m0" in evicted_ids
        assert "m11" not in evicted_ids

    def test_summary_memories_protected(self):
        # 12 entries: 4 summaries + 8 regular. Cap=10, target=8 → evict 4.
        # All 4 evicted must come from non-summaries.
        memories = (
            [_mem(id=f"s{i}", is_summary=True) for i in range(4)]
            + [_mem(id=f"m{i}", access=i, age_hours=0) for i in range(8)]
        )
        evicted = select_for_eviction(memories, cap=10, target_after=8)
        assert len(evicted) == 4
        for e in evicted:
            assert e["metadata"]["atlas"]["is_summary"] is False

    def test_no_eviction_when_only_summaries(self):
        # If everything is a summary, refuse to evict (no obvious recovery path).
        memories = [_mem(id=f"s{i}", is_summary=True) for i in range(20)]
        assert select_for_eviction(memories, cap=10, target_after=8) == []

    def test_importance_protects_within_half_life(self):
        # Within the recency half-life (30d), max importance OUTSCORES a fresh
        # zero-importance entry. The score formula intentionally lets recency
        # dominate after long ages (60+ days) — that's the "fade unless
        # re-accessed" behavior. This test guards the inner-window contract.
        # 7 days: recency = exp(-168/720) ≈ 0.79 → score = 0.45·0.79 + 0.30·1.0 ≈ 0.66
        #   vs fresh no-importance: score = 0.45·1.0 + 0 + 0 = 0.45
        old_important = _mem(id="keeper", importance=1.0, age_hours=24 * 7)
        new_trivial = _mem(id="trash", importance=0.0, age_hours=0)
        memories = [old_important, new_trivial] + [_mem(id=f"m{i}") for i in range(9)]
        evicted = select_for_eviction(memories, cap=10, target_after=10)
        assert len(evicted) == 1
        assert evicted[0]["id"] == "trash"

    def test_recency_dominates_after_long_age(self):
        # Beyond ~60 days even max importance can't save you. This is intentional:
        # truly stale facts that never get re-accessed should age out, otherwise
        # the cap can never reclaim space.
        very_old_important = _mem(id="ancient", importance=1.0, age_hours=24 * 60)
        new_trivial = _mem(id="fresh", importance=0.0, age_hours=0)
        memories = [very_old_important, new_trivial] + [_mem(id=f"m{i}") for i in range(9)]
        evicted = select_for_eviction(memories, cap=10, target_after=10)
        assert len(evicted) == 1
        assert evicted[0]["id"] == "ancient"


# --------------------------------------------------------------------------
# chunk_for_summary
# --------------------------------------------------------------------------

class TestChunkForSummary:
    def test_empty_returns_empty(self):
        assert chunk_for_summary([]) == []

    def test_few_memories_one_chunk(self):
        memories = [_mem(id=f"m{i}") for i in range(3)]
        chunks = chunk_for_summary(memories, batches=8)
        assert len(chunks) == 1
        assert len(chunks[0]) == 3

    def test_many_memories_split_evenly(self):
        # 800 memories / 8 batches = 100 each.
        memories = [_mem(id=f"m{i}", age_hours=i) for i in range(800)]
        chunks = chunk_for_summary(memories, batches=8)
        assert len(chunks) == 8
        assert all(len(c) == 100 for c in chunks)
        # Total preserved.
        assert sum(len(c) for c in chunks) == 800

    def test_chronological_order_preserved(self):
        # Older first within each chunk.
        memories = [_mem(id=f"m{i}", age_hours=100 - i) for i in range(20)]  # m0 oldest
        chunks = chunk_for_summary(memories, batches=4)
        # Within first chunk, IDs should be in order of created_at ascending.
        first_chunk = chunks[0]
        timestamps = [m["created_at"] for m in first_chunk]
        assert timestamps == sorted(timestamps)


# --------------------------------------------------------------------------
# prune_user_memories — runner integration with mocked Mem0 + LLM
# --------------------------------------------------------------------------

class TestPruneRunner:
    """End-to-end coverage of the two-phase pipeline."""

    async def test_under_cap_no_op(self):
        memories = [_mem(id=f"m{i}") for i in range(5)]
        with (
            patch("src.memory.eviction_runner.get_all_memories",
                  AsyncMock(return_value=memories)),
            patch("src.memory.eviction_runner.delete_memory",
                  AsyncMock()) as del_mock,
            patch("src.memory.eviction_runner.add_memory",
                  AsyncMock()) as add_mock,
        ):
            from src.memory.eviction_runner import prune_user_memories
            report = await prune_user_memories("u1", cap=10)
        assert report["evicted"] == 0
        assert report["summaries_added"] == 0
        assert report["reason"] == "under_cap"
        del_mock.assert_not_called()
        add_mock.assert_not_called()

    async def test_dry_run_reports_without_mutating(self):
        memories = [_mem(id=f"m{i}", access=i) for i in range(12)]
        with (
            patch("src.memory.eviction_runner.get_all_memories",
                  AsyncMock(return_value=memories)),
            patch("src.memory.eviction_runner.delete_memory",
                  AsyncMock(return_value=True)) as del_mock,
            patch("src.memory.eviction_runner.add_memory",
                  AsyncMock()) as add_mock,
        ):
            from src.memory.eviction_runner import prune_user_memories
            report = await prune_user_memories("u1", cap=10, target_after=8,
                                               dry_run=True)
        assert report["dry_run"] is True
        assert report["would_evict"] == 4
        assert report["would_create_summaries"] >= 1
        del_mock.assert_not_called()
        add_mock.assert_not_called()

    async def test_full_pipeline_writes_summaries_then_deletes(self, monkeypatch):
        # 12 memories, cap=10, target=8 → evict 4 → 1 summary chunk.
        memories = [_mem(id=f"m{i}", access=i, age_hours=i) for i in range(12)]
        order: list[str] = []  # observe phase ordering across mocks

        async def fake_add(text, user_id, metadata=None):
            order.append("add")
            return {"id": "summary-id"}

        async def fake_delete(memory_id):
            order.append(f"delete:{memory_id}")
            return True

        async def fake_summarize(chunk):
            return f"[stub summary of {len(chunk)} memories]"

        with (
            patch("src.memory.eviction_runner.get_all_memories",
                  AsyncMock(return_value=memories)),
            patch("src.memory.eviction_runner.delete_memory", fake_delete),
            patch("src.memory.eviction_runner.add_memory", fake_add),
            patch("src.memory.eviction_runner._summarize_chunk", fake_summarize),
        ):
            from src.memory.eviction_runner import prune_user_memories
            report = await prune_user_memories("u1", cap=10, target_after=8)

        assert report["evicted"] == 4
        assert report["summaries_added"] >= 1
        assert report["dry_run"] is False
        # Phase ordering: ALL adds happen before ANY delete.
        first_delete_idx = next((i for i, op in enumerate(order)
                                 if op.startswith("delete:")), -1)
        last_add_idx = max((i for i, op in enumerate(order)
                            if op == "add"), default=-1)
        assert first_delete_idx > last_add_idx, \
            f"delete happened before add complete; order={order}"

    async def test_summary_write_failure_aborts_before_delete(self):
        """If summary writes fail mid-flight, NO deletes should happen.

        This is the data-safety contract — operators expect that a failed
        eviction leaves the original memories intact, never half-deleted.
        """
        memories = [_mem(id=f"m{i}", access=i) for i in range(20)]

        # Fail every add_memory call.
        with (
            patch("src.memory.eviction_runner.get_all_memories",
                  AsyncMock(return_value=memories)),
            patch("src.memory.eviction_runner.delete_memory",
                  AsyncMock(return_value=True)) as del_mock,
            patch("src.memory.eviction_runner.add_memory",
                  AsyncMock(side_effect=RuntimeError("Mem0 down"))),
            patch("src.memory.eviction_runner._summarize_chunk",
                  AsyncMock(return_value="stub summary")),
        ):
            from src.memory.eviction_runner import prune_user_memories
            report = await prune_user_memories("u1", cap=10, target_after=8)

        assert report["error"] == "summary_write_failed"
        assert report["evicted"] == 0
        del_mock.assert_not_called()  # the contract — zero deletes on summary failure

    async def test_summarize_falls_back_to_stub_without_llm(self):
        """If the Agents SDK is unavailable, the stub summary still allows
        eviction to make progress (storage gets freed)."""
        from src.memory.eviction_runner import _summarize_chunk
        chunk = [_mem(id=f"m{i}", text=f"fact {i}") for i in range(5)]
        # No Agents SDK patching — let the real import fail or succeed naturally.
        # Worst case the stub kicks in; either way we get a non-empty string.
        result = await _summarize_chunk(chunk)
        assert isinstance(result, str)
        assert "5" in result  # source-count is rendered either way

    async def test_summarize_empty_chunk_returns_empty(self):
        from src.memory.eviction_runner import _summarize_chunk
        assert await _summarize_chunk([]) == ""
