"""Tests for Phase 3 memory system."""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.memory.persona import PERSONA_TEMPLATE


class TestPersonaTemplate:
    """Test the dynamic persona template."""

    def test_template_has_all_placeholders(self) -> None:
        placeholders = [
            "{name}", "{user_name}", "{personality_traits}",
            "{communication_style}", "{user_preferences}",
            "{procedural_memories}", "{recent_context}", "{task_context}",
        ]
        for p in placeholders:
            assert p in PERSONA_TEMPLATE, f"Missing placeholder: {p}"

    def test_template_mentions_specialists(self) -> None:
        assert "Gmail" in PERSONA_TEMPLATE
        assert "Calendar" in PERSONA_TEMPLATE
        assert "Drive" in PERSONA_TEMPLATE
        assert "Memory" in PERSONA_TEMPLATE
        assert "Tasks" in PERSONA_TEMPLATE
        assert "Docs" in PERSONA_TEMPLATE
        assert "Sheets" in PERSONA_TEMPLATE
        assert "Slides" in PERSONA_TEMPLATE
        assert "Contacts" in PERSONA_TEMPLATE
        assert "Scheduler" in PERSONA_TEMPLATE

    def test_template_mentions_connect(self) -> None:
        assert "/connect google" in PERSONA_TEMPLATE

    def test_template_can_be_formatted(self) -> None:
        result = PERSONA_TEMPLATE.format(
            name="Atlas",
            user_name="TestUser",
            personality_traits="helpful, concise",
            communication_style="friendly",
            user_preferences="prefers mornings",
            procedural_memories="none yet",
            recent_context="new conversation",
            task_context="current request",
            deep_profile="",
        )
        assert "Atlas" in result
        assert "TestUser" in result
        assert "prefers mornings" in result
        assert "current request" in result


class TestConversation:
    """Test Redis conversation session management."""

    def test_conv_key_format(self) -> None:
        from src.memory.conversation import _conv_key, _meta_key
        assert _conv_key(123) == "conv:123"
        assert _meta_key(123) == "conv:123:meta"

    def test_session_ttl_constant(self) -> None:
        from src.memory.conversation import SESSION_TTL
        assert SESSION_TTL == 1800  # 30 minutes

    def test_max_turns_constant(self) -> None:
        from src.memory.conversation import MAX_TURNS
        assert MAX_TURNS == 20


class TestMemoryAgent:
    """Test memory agent creation."""

    def test_create_memory_agent(self) -> None:
        from src.agents.memory_agent import create_memory_agent
        agent = create_memory_agent()
        assert agent.name == "MemoryAgent"
        assert len(agent.tools) == 5  # recall, store, list_all, forget, forget_all

    def test_memory_instructions_contain_capabilities(self) -> None:
        from src.agents.memory_agent import MEMORY_INSTRUCTIONS
        assert "Recall" in MEMORY_INSTRUCTIONS
        assert "Search" in MEMORY_INSTRUCTIONS
        assert "Forget" in MEMORY_INSTRUCTIONS


class TestReflectorAgent:
    """Test reflector agent."""

    def test_reflector_instructions_contain_json_schema(self) -> None:
        from src.agents.reflector_agent import REFLECTOR_INSTRUCTIONS
        assert "task_completed" in REFLECTOR_INSTRUCTIONS
        assert "quality_score" in REFLECTOR_INSTRUCTIONS
        assert "preference_learned" in REFLECTOR_INSTRUCTIONS
        assert "workflow_learned" in REFLECTOR_INSTRUCTIONS


class TestOrchestratorPhase3:
    """Test orchestrator Phase 3 integration."""

    def test_static_persona_still_works(self) -> None:
        from src.agents.orchestrator import build_persona_prompt
        prompt = build_persona_prompt("TestUser")
        assert "TestUser" in prompt
        assert "Still learning" in prompt
        assert "## Current Task" in prompt
        assert "## Memory Strata" in prompt
        assert "internal_write" in prompt

    def test_create_orchestrator_sync_fallback(self) -> None:
        from src.agents.orchestrator import create_orchestrator
        agent = create_orchestrator("TestUser")
        assert agent.name == "PersonalAssistant"


# --------------------------------------------------------------------------
# Behavior tests — Redis-backed conversation, Mem0 client, persona filters
# --------------------------------------------------------------------------
#
# These tests are 100% mocked — they do NOT require a running Redis, Postgres,
# or Qdrant. The point is to catch logic regressions in the memory subsystem
# (dedup thresholds, compaction triggers, key formatting, stale-memory filters)
# without infra. Integration tests against real services live elsewhere.


@pytest.fixture
def fake_redis():
    """An AsyncMock pre-wired with the redis methods the conversation module calls."""
    r = AsyncMock()
    r.rpush.return_value = 1
    r.expire.return_value = True
    r.hincrby.return_value = 1
    r.hexists.return_value = False
    r.hset.return_value = 1
    r.lrange.return_value = []
    r.llen.return_value = 1
    r.ltrim.return_value = True
    r.delete.return_value = 1
    r.get.return_value = None
    r.set.return_value = True
    return r


def _patch_redis(monkeypatch, fake_redis):
    """Inject the fake redis into conversation.get_redis as a one-liner."""
    from src.memory import conversation as conv
    monkeypatch.setattr(conv, "get_redis", AsyncMock(return_value=fake_redis))


class TestConversationAddTurn:
    """add_turn — write path into Redis."""

    async def test_persists_turn_and_metadata(self, fake_redis, monkeypatch):
        _patch_redis(monkeypatch, fake_redis)
        from src.memory.conversation import add_turn

        await add_turn(123, "user", "hello world")

        fake_redis.rpush.assert_called_once()
        key, payload = fake_redis.rpush.call_args[0]
        assert key == "conv:123"
        # Payload is a JSON-encoded turn dict — content survives round-trip.
        import json
        parsed = json.loads(payload)
        assert parsed["role"] == "user"
        assert parsed["content"] == "hello world"
        # TTL applied to both list and meta hash.
        assert fake_redis.expire.call_count >= 2
        # Metadata turn_count incremented.
        fake_redis.hincrby.assert_called_with("conv:123:meta", "turn_count", 1)

    async def test_writes_started_at_on_first_turn(self, fake_redis, monkeypatch):
        fake_redis.hexists.return_value = False  # no started_at yet
        _patch_redis(monkeypatch, fake_redis)
        from src.memory.conversation import add_turn

        await add_turn(7, "user", "first")
        fake_redis.hset.assert_called_once()
        args = fake_redis.hset.call_args[0]
        assert args[0] == "conv:7:meta"
        assert args[1] == "started_at"

    async def test_skips_started_at_when_already_set(self, fake_redis, monkeypatch):
        fake_redis.hexists.return_value = True  # started_at already there
        _patch_redis(monkeypatch, fake_redis)
        from src.memory.conversation import add_turn

        await add_turn(7, "user", "second")
        fake_redis.hset.assert_not_called()

    async def test_no_compaction_under_max_turns(self, fake_redis, monkeypatch):
        fake_redis.llen.return_value = 5  # well under MAX_TURNS=20
        _patch_redis(monkeypatch, fake_redis)
        from src.memory import conversation as conv
        compact = AsyncMock()
        monkeypatch.setattr(conv, "_compact_turns_to_memory", compact)

        await conv.add_turn(1, "user", "hi")
        # No overflow → no compaction → no trim.
        fake_redis.ltrim.assert_not_called()

    async def test_compacts_when_over_max_turns(self, fake_redis, monkeypatch):
        from src.memory.conversation import MAX_TURNS

        # Pretend Redis has MAX_TURNS+3 turns already.
        fake_redis.llen.return_value = MAX_TURNS + 3
        old_turns_raw = [
            f'{{"role": "user", "content": "msg-{i}", "timestamp": 1.0}}'
            for i in range(3)
        ]
        fake_redis.lrange.return_value = old_turns_raw
        _patch_redis(monkeypatch, fake_redis)

        # Patch asyncio.create_task to capture the compaction coroutine without
        # actually scheduling it. The conversation module imports asyncio inline
        # inside add_turn, so patching the global asyncio.create_task is enough.
        from src.memory import conversation as conv
        import asyncio
        captured = []

        def fake_create_task(coro):
            captured.append(coro)
            coro.close()  # prevent "coroutine never awaited" warning
            return MagicMock()

        monkeypatch.setattr(asyncio, "create_task", fake_create_task)

        await conv.add_turn(42, "user", "newest")

        # Trim called with overflow=3 → drop first 3.
        fake_redis.ltrim.assert_called_once()
        trim_args = fake_redis.ltrim.call_args[0]
        assert trim_args[1] == 3  # overflow
        # Compaction was scheduled.
        assert len(captured) == 1


class TestConversationRead:
    """get_conversation_history / get_session_context — read paths."""

    async def test_history_parses_json(self, fake_redis, monkeypatch):
        fake_redis.lrange.return_value = [
            '{"role": "user", "content": "a", "timestamp": 1.0}',
            '{"role": "assistant", "content": "b", "timestamp": 2.0}',
        ]
        _patch_redis(monkeypatch, fake_redis)
        from src.memory.conversation import get_conversation_history

        history = await get_conversation_history(99)
        assert len(history) == 2
        assert history[0]["role"] == "user"
        assert history[1]["content"] == "b"

    async def test_session_context_empty_returns_blank(self, fake_redis, monkeypatch):
        fake_redis.lrange.return_value = []
        _patch_redis(monkeypatch, fake_redis)
        from src.memory.conversation import get_session_context

        assert await get_session_context(1) == ""

    async def test_session_context_truncates_long_content(self, fake_redis, monkeypatch):
        long_content = "x" * 500
        fake_redis.lrange.return_value = [
            f'{{"role": "user", "content": "{long_content}", "timestamp": 1.0}}'
        ]
        _patch_redis(monkeypatch, fake_redis)
        from src.memory.conversation import get_session_context

        out = await get_session_context(1)
        # Must contain truncation marker, not full content.
        assert "..." in out
        assert "x" * 500 not in out

    async def test_session_context_uses_last_ten(self, fake_redis, monkeypatch):
        # 15 turns; only the last 10 should appear.
        fake_redis.lrange.return_value = [
            f'{{"role": "user", "content": "msg-{i}", "timestamp": 1.0}}'
            for i in range(15)
        ]
        _patch_redis(monkeypatch, fake_redis)
        from src.memory.conversation import get_session_context

        out = await get_session_context(1)
        assert "msg-14" in out  # newest
        assert "msg-5" in out   # 10th from end
        assert "msg-4" not in out  # outside window

    async def test_clear_session_deletes_both_keys(self, fake_redis, monkeypatch):
        _patch_redis(monkeypatch, fake_redis)
        from src.memory.conversation import clear_session

        await clear_session(77)
        fake_redis.delete.assert_called_once_with("conv:77", "conv:77:meta")

    async def test_per_user_isolation(self, fake_redis, monkeypatch):
        """Redis keys must include user_id so user A and user B never collide."""
        _patch_redis(monkeypatch, fake_redis)
        from src.memory.conversation import add_turn, _conv_key

        await add_turn(1, "user", "hi from 1")
        await add_turn(2, "user", "hi from 2")
        used_keys = {call.args[0] for call in fake_redis.rpush.call_args_list}
        assert _conv_key(1) in used_keys
        assert _conv_key(2) in used_keys
        assert _conv_key(1) != _conv_key(2)


class TestMem0Dedup:
    """add_memory — dedup vs insert decision based on cosine score."""

    def _patch_mem0(self, monkeypatch, mem):
        from src.memory import mem0_client as mc
        monkeypatch.setattr(mc, "get_memory", lambda: mem)
        return mem

    async def test_dedup_when_score_above_threshold(self, monkeypatch):
        from src.memory.mem0_client import DEDUP_THRESHOLD, add_memory
        mem = MagicMock()
        # Search returns a near-duplicate.
        mem.search.return_value = {
            "results": [{"id": "abc", "score": DEDUP_THRESHOLD + 0.05}]
        }
        self._patch_mem0(monkeypatch, mem)

        result = await add_memory("the user likes coffee", user_id="u1")

        # Wave 1.1 added crystallize_count metadata propagation on dedup so
        # the meta-reflector can crystallize repeated workflows into SKILL.md.
        mem.update.assert_called_once()
        update_args, update_kwargs = mem.update.call_args
        assert update_args[0] == "abc"
        assert update_args[1] == "the user likes coffee"
        assert update_kwargs.get("metadata", {}).get("crystallize_count") == 2
        mem.add.assert_not_called()
        assert result["deduplicated"] is True
        assert result["id"] == "abc"
        assert result["score"] == DEDUP_THRESHOLD + 0.05
        assert result.get("metadata", {}).get("crystallize_count") == 2

    async def test_inserts_when_score_below_threshold(self, monkeypatch):
        from src.memory.mem0_client import DEDUP_THRESHOLD, add_memory
        mem = MagicMock()
        mem.search.return_value = {
            "results": [{"id": "abc", "score": DEDUP_THRESHOLD - 0.10}]
        }
        mem.add.return_value = {"id": "new"}
        self._patch_mem0(monkeypatch, mem)

        await add_memory("a totally new fact", user_id="u1")

        mem.update.assert_not_called()
        mem.add.assert_called_once()

    async def test_inserts_when_search_returns_no_hits(self, monkeypatch):
        from src.memory.mem0_client import add_memory
        mem = MagicMock()
        mem.search.return_value = {"results": []}
        mem.add.return_value = {"id": "new"}
        self._patch_mem0(monkeypatch, mem)

        await add_memory("brand new", user_id="u1")
        mem.add.assert_called_once()
        mem.update.assert_not_called()

    async def test_inserts_when_search_raises(self, monkeypatch):
        """Dedup-search failure is non-fatal — must fall through to add()."""
        from src.memory.mem0_client import add_memory
        mem = MagicMock()
        mem.search.side_effect = RuntimeError("qdrant unreachable")
        mem.add.return_value = {"id": "new"}
        self._patch_mem0(monkeypatch, mem)

        await add_memory("survives search failure", user_id="u1")
        mem.add.assert_called_once()


class TestMem0DeleteAndSearch:
    """search_memories / delete_memory — exception swallowing + access tracking."""

    def _patch_mem0(self, monkeypatch, mem):
        from src.memory import mem0_client as mc
        monkeypatch.setattr(mc, "get_memory", lambda: mem)

    async def test_search_increments_access_count(self, monkeypatch):
        from src.memory.mem0_client import search_memories
        mem = MagicMock()
        mem.search.return_value = {
            "results": [
                {"id": "a", "memory": "fact a",
                 "metadata": {"access_count": 3}},
                {"id": "b", "memory": "fact b", "metadata": None},
            ]
        }
        self._patch_mem0(monkeypatch, mem)

        hits = await search_memories("fact", user_id="u1")
        assert len(hits) == 2
        # Each hit gets an update() with bumped access_count.
        assert mem.update.call_count == 2
        # Find the call for memory "a" — access_count should be 4.
        for call in mem.update.call_args_list:
            args, kwargs = call
            if args[0] == "a":
                assert kwargs["metadata"]["access_count"] == 4

    async def test_delete_returns_true_on_success(self, monkeypatch):
        from src.memory.mem0_client import delete_memory
        mem = MagicMock()
        mem.delete.return_value = None
        self._patch_mem0(monkeypatch, mem)

        assert await delete_memory("xyz") is True
        mem.delete.assert_called_once_with("xyz")

    async def test_delete_returns_false_on_exception(self, monkeypatch):
        from src.memory.mem0_client import delete_memory
        mem = MagicMock()
        mem.delete.side_effect = RuntimeError("nope")
        self._patch_mem0(monkeypatch, mem)

        assert await delete_memory("xyz") is False

    async def test_delete_all_returns_false_on_exception(self, monkeypatch):
        from src.memory.mem0_client import delete_all_memories
        mem = MagicMock()
        mem.delete_all.side_effect = RuntimeError("kaboom")
        self._patch_mem0(monkeypatch, mem)

        assert await delete_all_memories("u1") is False


class TestCompactionResilience:
    """_compact_turns_to_memory — retry on transient failure, dead-letter on exhaustion.

    Regression guard for the previous fire-and-forget design that silently
    discarded turns when Mem0 or the summarizer LLM was momentarily down.
    """

    @pytest.fixture
    def fast_compact_retry(self):
        """Skip exponential-backoff sleeps so tests run instantly."""
        from tenacity import wait_none
        from src.memory import conversation as conv
        original = conv._compact_turns_to_memory_with_retry.retry.wait
        conv._compact_turns_to_memory_with_retry.retry.wait = wait_none()
        yield
        conv._compact_turns_to_memory_with_retry.retry.wait = original

    @pytest.fixture
    def mock_agents_module(self, monkeypatch):
        """Inject a fake `agents` module so the inline import inside
        `_compact_turns_to_memory_with_retry` resolves without the real SDK."""
        import sys
        runner = MagicMock()
        runner.run = AsyncMock(return_value=MagicMock(final_output="summary"))
        agents_mod = MagicMock(Agent=MagicMock(), Runner=runner)
        monkeypatch.setitem(sys.modules, "agents", agents_mod)
        return runner

    async def test_returns_immediately_when_too_few_turns(self, fake_redis, monkeypatch):
        """0 or 1 turns must not invoke the LLM, write to Mem0, or touch DLQ."""
        _patch_redis(monkeypatch, fake_redis)
        from src.memory.conversation import _compact_turns_to_memory

        await _compact_turns_to_memory(1, [{"role": "user", "content": "alone"}])
        # No DLQ write, no Mem0 call.
        fake_redis.rpush.assert_not_called()

    async def test_succeeds_on_happy_path(self, fake_redis, monkeypatch,
                                          fast_compact_retry, mock_agents_module):
        """Successful compaction must not write to DLQ."""
        _patch_redis(monkeypatch, fake_redis)
        # Stub add_memory so we don't need a real Mem0.
        from src.memory import mem0_client as mc
        monkeypatch.setattr(mc, "add_memory", AsyncMock())
        # And ensure the conversation module sees the patched function.
        from src.memory import conversation as conv
        old_turns = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello"},
        ]
        await conv._compact_turns_to_memory(7, old_turns)
        # No DLQ rpush — happy path.
        fake_redis.rpush.assert_not_called()

    async def test_retries_on_transient_failure_then_succeeds(
        self, fake_redis, monkeypatch, fast_compact_retry, mock_agents_module
    ):
        """One transient LLM failure → retry → success → no DLQ write."""
        _patch_redis(monkeypatch, fake_redis)
        # First call raises, second succeeds.
        mock_agents_module.run = AsyncMock(side_effect=[
            RuntimeError("transient blip"),
            MagicMock(final_output="ok-summary"),
        ])
        from src.memory import mem0_client as mc
        monkeypatch.setattr(mc, "add_memory", AsyncMock())
        from src.memory.conversation import _compact_turns_to_memory

        old_turns = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello"},
        ]
        await _compact_turns_to_memory(7, old_turns)
        # Runner.run was called twice — first failed, second succeeded.
        assert mock_agents_module.run.await_count == 2
        # No DLQ.
        fake_redis.rpush.assert_not_called()

    async def test_dead_letters_when_retries_exhausted(
        self, fake_redis, monkeypatch, fast_compact_retry, mock_agents_module
    ):
        """All 3 attempts fail → raw turns written to compaction_dlq:{user_id}."""
        _patch_redis(monkeypatch, fake_redis)
        mock_agents_module.run = AsyncMock(side_effect=RuntimeError("LLM down"))
        from src.memory.conversation import _compact_turns_to_memory

        old_turns = [
            {"role": "user", "content": "lost-1"},
            {"role": "assistant", "content": "lost-2"},
        ]
        await _compact_turns_to_memory(42, old_turns)
        # 3 attempts (tenacity stop_after_attempt(3)).
        assert mock_agents_module.run.await_count == 3
        # DLQ rpush was called with the user's key.
        fake_redis.rpush.assert_called_once()
        key, payload = fake_redis.rpush.call_args.args
        assert key == "compaction_dlq:42"
        # Payload is JSON with the raw turns + error.
        record = json.loads(payload)
        assert record["old_turns"] == old_turns
        assert "LLM down" in record["error"]
        # And TTL applied.
        fake_redis.expire.assert_called_with("compaction_dlq:42", 7 * 86400)


class TestArchiveSession:
    """archive_session — null-history short-circuit + Mem0 write path."""

    async def test_returns_none_when_history_too_short(self, fake_redis, monkeypatch):
        fake_redis.lrange.return_value = [
            '{"role": "user", "content": "single", "timestamp": 1.0}'
        ]
        _patch_redis(monkeypatch, fake_redis)
        from src.memory.conversation import archive_session

        assert await archive_session(1) is None

    async def test_returns_none_on_empty_history(self, fake_redis, monkeypatch):
        fake_redis.lrange.return_value = []
        _patch_redis(monkeypatch, fake_redis)
        from src.memory.conversation import archive_session

        assert await archive_session(1) is None


class TestStaleMemoryFilter:
    """_filter_stale_memories — sync helper, no infra."""

    def test_returns_unchanged_when_workspace_disconnected(self):
        from src.memory.persona import _filter_stale_memories
        memories = [
            {"memory": "tools are broken"},
            {"memory": "user likes coffee"},
        ]
        # Disconnected: leave everything (we don't know the ground truth).
        result = _filter_stale_memories(memories, workspace_connected=False)
        assert result == memories

    def test_removes_broken_tools_phrase_when_connected(self):
        from src.memory.persona import _filter_stale_memories
        memories = [
            {"memory": "drive tools need fixing before continuing other tasks"},
            {"memory": "user prefers brief responses"},
        ]
        result = _filter_stale_memories(memories, workspace_connected=True)
        # Only the clean memory survives.
        assert len(result) == 1
        assert result[0]["memory"] == "user prefers brief responses"

    def test_keeps_clean_memories_when_connected(self):
        from src.memory.persona import _filter_stale_memories
        memories = [
            {"memory": "user lives in Texas"},
            {"memory": "user works as a data scientist"},
        ]
        result = _filter_stale_memories(memories, workspace_connected=True)
        assert result == memories

    def test_handles_text_field_fallback(self):
        """Memory dicts can use 'memory' or 'text' field — both must be checked."""
        from src.memory.persona import _filter_stale_memories
        memories = [
            {"text": "tools are broken"},  # 'text' not 'memory'
            {"text": "user prefers Python"},
        ]
        result = _filter_stale_memories(memories, workspace_connected=True)
        assert len(result) == 1
        assert result[0]["text"] == "user prefers Python"

    def test_phrase_match_is_case_insensitive(self):
        from src.memory.persona import _filter_stale_memories
        memories = [{"memory": "Drive Connector ISSUE remains unresolved"}]
        result = _filter_stale_memories(memories, workspace_connected=True)
        assert result == []  # filtered despite mixed case
