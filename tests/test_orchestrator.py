"""Tests for the orchestrator agent."""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from src.agents.orchestrator import build_persona_prompt, _load_persona_config


class TestPersonaPrompt:
    """Test persona prompt building."""

    def test_default_persona_prompt_contains_name(self) -> None:
        prompt = build_persona_prompt("Alex")
        assert "Atlas" in prompt or "assistant" in prompt.lower()
        assert "Alex" in prompt

    def test_persona_prompt_contains_style(self) -> None:
        prompt = build_persona_prompt("Alex")
        assert "friendly" in prompt.lower() or "helpful" in prompt.lower()

    def test_persona_prompt_contains_rules(self) -> None:
        prompt = build_persona_prompt()
        assert "confirm" in prompt.lower()
        assert "destructive" in prompt.lower()

    def test_persona_prompt_default_user(self) -> None:
        prompt = build_persona_prompt()
        assert "there" in prompt


class TestPersonaConfig:
    """Test persona config loading."""

    def test_load_config_returns_dict(self) -> None:
        config = _load_persona_config()
        assert isinstance(config, dict)
        assert "assistant_name" in config or "personality" in config

    def test_config_has_personality(self) -> None:
        config = _load_persona_config()
        assert "personality" in config
        personality = config["personality"]
        assert "traits" in personality
        assert "style" in personality
