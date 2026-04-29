"""Tests for Wave 2.5 — versioned checkpoint/resume for the repair pipeline.

Atlas's repair pipeline (Debugger → Programmer → QualityControl → Sandbox)
can run for 30+ seconds across multiple LLM calls. If the container restarts
mid-flight, the previous design lost all audit context — the user couldn't
tell whether the LLM crashed at the patch generation step or the QA step.

Wave 2.5 closes that by piggybacking on Wave 2.4's FSM: every transition
serializes the state into Redis under ``repair_checkpoint:{user_id}`` with
a 24-hour TTL, and terminal phases (DONE/FAILED) clear the checkpoint
eagerly so the next repair starts clean.

Pinned behaviors:

1. **Round-trip** — a snapshot saved with ``save_repair_checkpoint`` returns
   identically (within JSON's lossy date handling) from
   ``get_repair_checkpoint``.
2. **TTL** — the SET call uses a 24-hour expiry; without it, abandoned
   checkpoints would accumulate forever.
3. **Eager terminal clear** — terminal-phase transitions in the FSM trigger
   a delete, not a re-save.
4. **Best-effort** — Redis exceptions don't propagate; the pipeline must
   keep running even if the checkpoint is unavailable.
5. **Resume discovery** — ``FSMState.from_dict`` rehydrates the saved
   snapshot into a runner that can keep transitioning. This is the contract
   that lets future code resume an interrupted repair if we choose to.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest


# --------------------------------------------------------------------------
# Round-trip + TTL
# --------------------------------------------------------------------------


class TestCheckpointRoundTrip:
    @pytest.mark.asyncio
    async def test_save_and_get_returns_same_snapshot(self, monkeypatch) -> None:
        from src.memory import conversation as conv

        store: dict[str, str] = {}

        class _FakeRedis:
            async def set(self, key, value, ex=None):
                store[key] = value
            async def get(self, key):
                return store.get(key)
            async def delete(self, key):
                store.pop(key, None)

        async def fake_get_redis():
            return _FakeRedis()

        monkeypatch.setattr(conv, "get_redis", fake_get_redis)

        snapshot = {
            "flow_id": "repair-999-1234",
            "phase": "act",
            "step_id": 3,
            "payload": {"ticket_id": 42, "confidence": 0.91},
            "history": [],
            "started_at": 1727800000.0,
        }
        await conv.save_repair_checkpoint(999, snapshot)
        loaded = await conv.get_repair_checkpoint(999)
        assert loaded == snapshot

    @pytest.mark.asyncio
    async def test_save_uses_24h_ttl(self, monkeypatch) -> None:
        """Without a TTL, abandoned checkpoints would accumulate forever."""
        from src.memory import conversation as conv

        captured = {}

        class _FakeRedis:
            async def set(self, key, value, ex=None):
                captured["key"] = key
                captured["ex"] = ex

        monkeypatch.setattr(conv, "get_redis", lambda: AsyncMock(return_value=_FakeRedis())())

        await conv.save_repair_checkpoint(999, {"flow_id": "x", "phase": "plan"})
        assert captured["ex"] == 86400, "TTL must be 24 hours (86400 seconds)"
        assert captured["key"] == "repair_checkpoint:999"

    @pytest.mark.asyncio
    async def test_get_returns_none_for_missing_user(self, monkeypatch) -> None:
        from src.memory import conversation as conv

        class _FakeRedis:
            async def get(self, key):
                return None

        monkeypatch.setattr(conv, "get_redis", lambda: AsyncMock(return_value=_FakeRedis())())

        loaded = await conv.get_repair_checkpoint(404)
        assert loaded is None

    @pytest.mark.asyncio
    async def test_get_returns_none_on_corrupt_json(self, monkeypatch) -> None:
        """A corrupt checkpoint must not crash callers — return None."""
        from src.memory import conversation as conv

        class _FakeRedis:
            async def get(self, key):
                return "not-json{{"

        monkeypatch.setattr(conv, "get_redis", lambda: AsyncMock(return_value=_FakeRedis())())

        loaded = await conv.get_repair_checkpoint(999)
        assert loaded is None


# --------------------------------------------------------------------------
# Eager terminal clear
# --------------------------------------------------------------------------


class TestTerminalClear:
    @pytest.mark.asyncio
    async def test_clear_deletes_the_key(self, monkeypatch) -> None:
        from src.memory import conversation as conv

        deleted = []

        class _FakeRedis:
            async def delete(self, key):
                deleted.append(key)

        monkeypatch.setattr(conv, "get_redis", lambda: AsyncMock(return_value=_FakeRedis())())

        await conv.clear_repair_checkpoint(999)
        assert deleted == ["repair_checkpoint:999"]

    @pytest.mark.asyncio
    async def test_clear_is_idempotent(self, monkeypatch) -> None:
        """Calling clear when no checkpoint exists must not raise."""
        from src.memory import conversation as conv

        class _FakeRedis:
            async def delete(self, key):
                return None  # Redis DEL on missing key returns 0, never raises

        monkeypatch.setattr(conv, "get_redis", lambda: AsyncMock(return_value=_FakeRedis())())

        # Should not raise
        await conv.clear_repair_checkpoint(123)
        await conv.clear_repair_checkpoint(123)


# --------------------------------------------------------------------------
# Best-effort failure handling
# --------------------------------------------------------------------------


class TestBestEffortFailure:
    @pytest.mark.asyncio
    async def test_save_swallows_redis_exceptions(self, monkeypatch) -> None:
        """A Redis outage must not abort the repair pipeline. The save call
        catches and logs; the pipeline keeps running."""
        from src.memory import conversation as conv

        class _BrokenRedis:
            async def set(self, *_a, **_kw):
                raise RuntimeError("simulated Redis outage")

        monkeypatch.setattr(conv, "get_redis", lambda: AsyncMock(return_value=_BrokenRedis())())

        # Should not raise
        await conv.save_repair_checkpoint(999, {"phase": "plan"})

    @pytest.mark.asyncio
    async def test_get_swallows_redis_exceptions(self, monkeypatch) -> None:
        from src.memory import conversation as conv

        class _BrokenRedis:
            async def get(self, *_a, **_kw):
                raise RuntimeError("simulated Redis outage")

        monkeypatch.setattr(conv, "get_redis", lambda: AsyncMock(return_value=_BrokenRedis())())

        result = await conv.get_repair_checkpoint(999)
        assert result is None


# --------------------------------------------------------------------------
# Resume discovery — FSMState.from_dict round-trips a saved checkpoint
# --------------------------------------------------------------------------


class TestResumeDiscovery:
    def test_saved_snapshot_round_trips_through_fsm_state(self) -> None:
        """The whole point of checkpointing: a saved snapshot must rehydrate
        into an FSMState we can resume. This is the contract that any
        future "actually resume the partial repair" feature builds on."""
        from src.agents.fsm import new_runner, FSMState, Phase, resume_runner

        runner = new_runner("repair-999-T", initial_payload={"ticket": 42})
        runner.transition(Phase.ACT, reason="generate patch")
        runner.transition(Phase.OBSERVE, reason="qa pending")

        # Simulate save: this is what save_repair_checkpoint persists
        snapshot_dict = runner.state.to_dict()
        json_blob = json.dumps(snapshot_dict)

        # Simulate load: this is what get_repair_checkpoint returns
        rehydrated = FSMState.from_dict(json.loads(json_blob))
        runner2 = resume_runner(rehydrated)

        assert runner2.phase == Phase.OBSERVE
        assert runner2.state.flow_id == "repair-999-T"
        assert runner2.state.payload == {"ticket": 42}
        # Resumed runner can keep transitioning
        runner2.transition(Phase.REVISE, reason="qa rejected")
        assert runner2.phase == Phase.REVISE
