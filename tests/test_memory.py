"""Tests for Phase 3 memory system."""

import pytest
from unittest.mock import patch, MagicMock, AsyncMock

from src.memory.persona import PERSONA_TEMPLATE


class TestPersonaTemplate:
    """Test the dynamic persona template."""

    def test_template_has_all_placeholders(self) -> None:
        placeholders = [
            "{name}", "{user_name}", "{personality_traits}",
            "{communication_style}", "{user_preferences}",
            "{procedural_memories}", "{recent_context}",
        ]
        for p in placeholders:
            assert p in PERSONA_TEMPLATE, f"Missing placeholder: {p}"

    def test_template_mentions_specialists(self) -> None:
        assert "Email" in PERSONA_TEMPLATE
        assert "Calendar" in PERSONA_TEMPLATE
        assert "Drive" in PERSONA_TEMPLATE
        assert "Memory" in PERSONA_TEMPLATE

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
        )
        assert "Atlas" in result
        assert "TestUser" in result
        assert "prefers mornings" in result


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

    def test_create_orchestrator_sync_fallback(self) -> None:
        from src.agents.orchestrator import create_orchestrator
        agent = create_orchestrator("TestUser")
        assert agent.name == "PersonalAssistant"
