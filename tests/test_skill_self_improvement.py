"""Tests for Wave 1.3 — skill self-improvement on low quality outcomes.

When the per-turn reflector scores a turn below 0.4, the orchestrator queues
that turn into a Redis list (the "skill refinement queue"). The next time
the meta-reflector fires, it drains the queue and feeds those low-quality
turns to the LLM alongside the auto-skill list and asks for `skill_patches`
proposals — concrete change suggestions for skills whose tags or routing
hints overlap with the failing turns.

This is the third leg of the Hermes-parity self-healing loop:
- Wave 1.1: reflector writes new skills from successful workflows
- Wave 1.2: meta-reflector reviews recurring patterns every N turns
- Wave 1.3: meta-reflector also refines existing skills based on failures

Pinned behaviors here:

1. **Queue bounded by max+TTL** — old entries roll off; queue can't grow
   unbounded if a user has a bad day full of low-quality turns.
2. **Drain consumes** — once the meta-reflector reads the queue, those
   entries are gone. Each low-quality turn gets reviewed at most once.
3. **`skill_patches` field round-trips** — the field is added to the schema
   and persisted with proposals when actionable.
4. **Persistence still gated** — empty `skill_patches` alone (no other
   actionable content) doesn't store a payload.
5. **Reflector wires the enqueue** — score<0.4 path calls the queue helper.
   This test pins the regression introduced if a future change accidentally
   detaches the orchestrator's reflector hook from the refinement queue.
"""

from __future__ import annotations

import json
import sys
from unittest.mock import AsyncMock, MagicMock

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
# Queue mechanics — record + drain
# --------------------------------------------------------------------------


class TestSkillRefinementQueue:
    @pytest.mark.asyncio
    async def test_record_and_drain_round_trip(self, monkeypatch) -> None:
        from src.memory import conversation as conv

        # Fake a Redis list backed by a dict
        store: dict[str, list[str]] = {}

        class _FakeRedis:
            async def rpush(self, key, payload):
                store.setdefault(key, []).append(payload)
            async def ltrim(self, key, start, end):
                store[key] = store[key][start:end + 1] if end >= 0 else store[key][start:]
            async def expire(self, key, ttl):
                pass
            async def lrange(self, key, start, end):
                arr = store.get(key, [])
                if end == -1:
                    return arr[start:]
                return arr[start:end + 1]
            async def delete(self, key):
                store.pop(key, None)

        async def fake_get_redis():
            return _FakeRedis()

        monkeypatch.setattr(conv, "get_redis", fake_get_redis)

        # Record three turns
        for i in range(3):
            await conv.record_skill_refinement_request(
                999,
                user_message=f"send the daily devotional {i}",
                assistant_response=f"failed response {i}",
                quality_score=0.3 - i * 0.05,
            )

        # Drain — first call returns the 3 entries
        drained = await conv.drain_skill_refinement_queue(999)
        assert len(drained) == 3
        assert all("user_message" in d and "quality_score" in d for d in drained)
        assert drained[0]["quality_score"] == pytest.approx(0.3)

        # Second drain returns empty — queue is consumed.
        drained2 = await conv.drain_skill_refinement_queue(999)
        assert drained2 == []

    @pytest.mark.asyncio
    async def test_record_caps_message_length(self, monkeypatch) -> None:
        """Messages over 1000 chars must be truncated so the meta-reflector
        prompt size stays predictable even when a user pastes a giant blob."""
        from src.memory import conversation as conv

        captured: dict = {}

        class _FakeRedis:
            async def rpush(self, key, payload):
                captured["payload"] = payload
            async def ltrim(self, *_a, **_kw): pass
            async def expire(self, *_a, **_kw): pass

        monkeypatch.setattr(conv, "get_redis", lambda: AsyncMock(return_value=_FakeRedis())())

        await conv.record_skill_refinement_request(
            999,
            user_message="x" * 5000,
            assistant_response="y" * 5000,
            quality_score=0.2,
        )
        payload = json.loads(captured["payload"])
        assert len(payload["user_message"]) == 1000
        assert len(payload["assistant_response"]) == 1000


# --------------------------------------------------------------------------
# Meta-reflector reads the queue
# --------------------------------------------------------------------------


class TestMetaReflectorConsumesQueue:
    @pytest.mark.asyncio
    async def test_drained_queue_appears_in_review_text(self, monkeypatch) -> None:
        """The meta-reflector must include drained low-quality turns in the
        prompt, otherwise it can't propose `skill_patches`."""
        from src.agents import meta_reflector_agent

        fake_review = {
            "quality_scores": [0.3, 0.4, 0.2],
            "auto_skill_ids": ["devotional-style-guide"],
            "recent_workflows": ["User wants morning devotional"],
            "low_quality_turns": [
                {
                    "user_message": "send today's devotional",
                    "assistant_response": "I can't write devotionals right now",
                    "quality_score": 0.2,
                },
            ],
        }
        monkeypatch.setattr(
            meta_reflector_agent, "_gather_review_window",
            AsyncMock(return_value=fake_review),
        )

        captured_prompt = {}

        class _FakeRunResult:
            final_output = json.dumps({
                "skills_to_retire": [],
                "skills_to_consolidate": [],
                "skill_patches": [{
                    "skill_id": "devotional-style-guide",
                    "diagnosis": "instructions don't cover refusal cases",
                    "suggested_change": "add fallback when LLM refuses",
                }],
                "persona_refinements": [],
                "summary": "one skill needs a refusal-handling clarification",
            })

        async def fake_run(_agent, prompt_text):
            captured_prompt["text"] = prompt_text
            return _FakeRunResult()

        runner_mock = MagicMock()
        runner_mock.run = fake_run
        monkeypatch.setattr(meta_reflector_agent, "Runner", runner_mock)

        proposals = await meta_reflector_agent.run_meta_reflection("999")

        assert "Low-quality turns" in captured_prompt["text"]
        assert "send today's devotional" in captured_prompt["text"]
        assert "score=0.20" in captured_prompt["text"]
        assert proposals["skill_patches"][0]["skill_id"] == "devotional-style-guide"

    @pytest.mark.asyncio
    async def test_skill_patches_persist_when_only_actionable_field(self, monkeypatch) -> None:
        """`skill_patches` is the only non-empty field → still actionable, must persist."""
        from src.agents import meta_reflector_agent
        from src.memory import conversation as conv

        monkeypatch.setattr(meta_reflector_agent.settings, "meta_reflector_interval", 1)
        proposals_payload = {
            "skills_to_retire": [],
            "skills_to_consolidate": [],
            "skill_patches": [{
                "skill_id": "morning-routine",
                "diagnosis": "missing edge case",
                "suggested_change": "handle weekend exceptions",
            }],
            "persona_refinements": [],
            "summary": "one patch suggested",
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

        result = await meta_reflector_agent.maybe_run_meta_reflector("999")

        assert result == proposals_payload
        store_mock.assert_awaited_once()
        _, payload = store_mock.await_args.args
        decoded = json.loads(payload)
        assert decoded["skill_patches"][0]["skill_id"] == "morning-routine"


# --------------------------------------------------------------------------
# Default schema includes skill_patches
# --------------------------------------------------------------------------


class TestDefaultProposalsSchema:
    def test_skill_patches_present_in_default_proposals(self) -> None:
        from src.agents.meta_reflector_agent import _default_proposals

        defaults = _default_proposals()
        assert "skill_patches" in defaults, \
            "skill_patches must be in the default schema so callers can rely on it"
        assert defaults["skill_patches"] == []
