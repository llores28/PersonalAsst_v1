"""Tests for Wave 1.2 — every-N-turns meta-reflector.

The meta-reflector closes the gap to Hermes Agent's "every 15 tasks" nudge.
Where the per-turn reflector evaluates one interaction in isolation, the
meta-reflector reviews the last N reflector outputs holistically and proposes
skill consolidations / retirements / persona refinements. Proposals are
**owner-gated** — they land in Redis under ``meta_reflector_pending:{user_id}``
with a 7-day TTL and never auto-modify the filesystem.

Pinned behaviors:

1. **Cadence gating** — only fires when the turn counter is a multiple of
   ``settings.meta_reflector_interval``. A turn count of 14 must NOT fire,
   15 must.
2. **Disabled state** — ``meta_reflector_interval=0`` short-circuits the
   counter entirely (no Redis writes).
3. **Empty review window** — when there are no quality scores AND no recent
   workflows, the LLM is NOT called (saves tokens, avoids hallucinated
   proposals on empty input).
4. **Schema normalization** — the LLM output is coerced into the expected
   shape; missing fields fall back to empty arrays + a default summary.
5. **Persistence gate** — empty-payload proposals are NOT stored. Only
   actionable ones (≥1 retire/consolidate/persona entry) reach Redis.
"""

from __future__ import annotations

import json
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

if "agents" not in sys.modules:
    fake_agents = MagicMock()
    fake_agents.Agent = MagicMock
    fake_agents.function_tool = lambda *a, **kw: (lambda f: f) if (a and not callable(a[0])) else (a[0] if a else (lambda f: f))
    fake_agents.Runner = MagicMock()
    fake_agents.WebSearchTool = MagicMock
    sys.modules["agents"] = fake_agents
    sys.modules["agents.mcp"] = MagicMock()


# --------------------------------------------------------------------------
# Cadence gating
# --------------------------------------------------------------------------


class TestCadenceGating:
    @pytest.mark.asyncio
    async def test_fires_only_on_multiple_of_interval(self, monkeypatch) -> None:
        from src.agents import meta_reflector_agent
        from src.memory import conversation as conv

        monkeypatch.setattr(meta_reflector_agent.settings, "meta_reflector_interval", 5)
        # Make the underlying review path a no-op so we can isolate cadence.
        run_mock = AsyncMock(return_value={
            "skills_to_retire": [], "skills_to_consolidate": [],
            "persona_refinements": [], "summary": "noop",
        })
        monkeypatch.setattr(meta_reflector_agent, "run_meta_reflection", run_mock)

        # Patch counter to walk 1, 2, 3, 4, 5, 6
        counter = {"n": 0}

        async def fake_inc(_uid: int) -> int:
            counter["n"] += 1
            return counter["n"]

        monkeypatch.setattr(conv, "increment_meta_reflector_count", fake_inc)
        monkeypatch.setattr(conv, "store_meta_reflector_proposals", AsyncMock())

        # Turns 1-4 must NOT fire
        for _ in range(4):
            assert await meta_reflector_agent.maybe_run_meta_reflector("123") is None
        run_mock.assert_not_awaited()

        # Turn 5 fires (5 % 5 == 0)
        result = await meta_reflector_agent.maybe_run_meta_reflector("123")
        assert result is not None
        run_mock.assert_awaited_once()

        # Turn 6 doesn't fire again
        assert await meta_reflector_agent.maybe_run_meta_reflector("123") is None
        run_mock.assert_awaited_once()  # still just 1

    @pytest.mark.asyncio
    async def test_disabled_when_interval_zero(self, monkeypatch) -> None:
        from src.agents import meta_reflector_agent
        from src.memory import conversation as conv

        monkeypatch.setattr(meta_reflector_agent.settings, "meta_reflector_interval", 0)
        # Counter MUST NOT be touched when disabled.
        inc_mock = AsyncMock()
        monkeypatch.setattr(conv, "increment_meta_reflector_count", inc_mock)

        result = await meta_reflector_agent.maybe_run_meta_reflector("123")
        assert result is None
        inc_mock.assert_not_awaited()


# --------------------------------------------------------------------------
# Empty review window
# --------------------------------------------------------------------------


class TestEmptyWindow:
    @pytest.mark.asyncio
    async def test_no_scores_and_no_workflows_skips_llm(self, monkeypatch) -> None:
        from src.agents import meta_reflector_agent

        gather_mock = AsyncMock(return_value={
            "quality_scores": [], "auto_skill_ids": [],
            "recent_workflows": [], "low_quality_turns": [],
        })
        monkeypatch.setattr(meta_reflector_agent, "_gather_review_window", gather_mock)
        # If the LLM gets called, this Runner mock will raise.
        runner_mock = MagicMock()
        runner_mock.run = AsyncMock(side_effect=AssertionError("LLM should not run on empty window"))
        monkeypatch.setattr(meta_reflector_agent, "Runner", runner_mock)

        proposals = await meta_reflector_agent.run_meta_reflection("123", window=15)

        assert proposals["summary"] == "no recurring patterns detected"
        assert proposals["skills_to_retire"] == []
        runner_mock.run.assert_not_awaited()


# --------------------------------------------------------------------------
# Schema normalization
# --------------------------------------------------------------------------


class TestSchemaNormalization:
    @pytest.mark.asyncio
    async def test_partial_llm_output_is_padded(self, monkeypatch) -> None:
        from src.agents import meta_reflector_agent

        monkeypatch.setattr(
            meta_reflector_agent, "_gather_review_window",
            AsyncMock(return_value={
                "quality_scores": [0.3, 0.4, 0.5],
                "auto_skill_ids": ["foo", "bar"],
                "recent_workflows": ["A", "B", "C"],
                "low_quality_turns": [],
            }),
        )
        # LLM returns ONLY the summary — every other field should default empty.
        result = MagicMock()
        result.final_output = json.dumps({"summary": "two skills overlap"})
        runner_mock = MagicMock()
        runner_mock.run = AsyncMock(return_value=result)
        monkeypatch.setattr(meta_reflector_agent, "Runner", runner_mock)

        proposals = await meta_reflector_agent.run_meta_reflection("123")
        assert proposals["summary"] == "two skills overlap"
        assert proposals["skills_to_retire"] == []
        assert proposals["skills_to_consolidate"] == []
        assert proposals["persona_refinements"] == []

    @pytest.mark.asyncio
    async def test_invalid_json_falls_back_to_defaults(self, monkeypatch) -> None:
        from src.agents import meta_reflector_agent

        monkeypatch.setattr(
            meta_reflector_agent, "_gather_review_window",
            AsyncMock(return_value={
                "quality_scores": [0.5], "auto_skill_ids": [],
                "recent_workflows": ["w"], "low_quality_turns": [],
            }),
        )
        result = MagicMock()
        result.final_output = "not json at all{{"
        runner_mock = MagicMock()
        runner_mock.run = AsyncMock(return_value=result)
        monkeypatch.setattr(meta_reflector_agent, "Runner", runner_mock)

        proposals = await meta_reflector_agent.run_meta_reflection("123")
        assert proposals["summary"] == "no recurring patterns detected"


# --------------------------------------------------------------------------
# Persistence gate
# --------------------------------------------------------------------------


class TestPersistenceGate:
    @pytest.mark.asyncio
    async def test_empty_proposals_are_not_stored(self, monkeypatch) -> None:
        from src.agents import meta_reflector_agent
        from src.memory import conversation as conv

        monkeypatch.setattr(meta_reflector_agent.settings, "meta_reflector_interval", 1)
        monkeypatch.setattr(
            meta_reflector_agent, "run_meta_reflection",
            AsyncMock(return_value={
                "skills_to_retire": [], "skills_to_consolidate": [],
                "persona_refinements": [], "summary": "all good",
            }),
        )
        async def fake_inc(_uid: int) -> int:
            return 1
        monkeypatch.setattr(conv, "increment_meta_reflector_count", fake_inc)
        store_mock = AsyncMock()
        monkeypatch.setattr(conv, "store_meta_reflector_proposals", store_mock)

        result = await meta_reflector_agent.maybe_run_meta_reflector("123")
        assert result is not None  # ran
        store_mock.assert_not_awaited()  # but didn't persist empty payload

    @pytest.mark.asyncio
    async def test_actionable_proposals_are_stored(self, monkeypatch) -> None:
        from src.agents import meta_reflector_agent
        from src.memory import conversation as conv

        monkeypatch.setattr(meta_reflector_agent.settings, "meta_reflector_interval", 1)
        proposals_payload = {
            "skills_to_retire": [{"skill_id": "stale", "reason": "low quality"}],
            "skills_to_consolidate": [],
            "persona_refinements": [],
            "summary": "one skill should retire",
        }
        monkeypatch.setattr(
            meta_reflector_agent, "run_meta_reflection",
            AsyncMock(return_value=proposals_payload),
        )
        async def fake_inc(_uid: int) -> int:
            return 1
        monkeypatch.setattr(conv, "increment_meta_reflector_count", fake_inc)
        store_mock = AsyncMock()
        monkeypatch.setattr(conv, "store_meta_reflector_proposals", store_mock)

        result = await meta_reflector_agent.maybe_run_meta_reflector("123")

        assert result == proposals_payload
        store_mock.assert_awaited_once()
        stored_user, stored_payload = store_mock.await_args.args
        assert stored_user == 123
        assert json.loads(stored_payload) == proposals_payload
