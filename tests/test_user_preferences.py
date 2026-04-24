"""Tests for user model preferences (Phase 2 Option B Upgrade)."""

# ruff: noqa: E402

from __future__ import annotations

import sys
from unittest.mock import MagicMock, AsyncMock, patch
import pytest

# Mock redis before importing
sys.modules["redis"] = MagicMock()
sys.modules["redis.asyncio"] = MagicMock()


class TestGetUserModel:
    """Test get_user_model function."""

    @pytest.mark.asyncio
    async def test_returns_openai_when_multi_llm_disabled(self):
        """Test that OpenAI is returned when multi-LLM is disabled."""
        from src.models.user_preferences import get_user_model
        
        with patch("src.models.user_preferences.settings") as mock_settings:
            mock_settings.multi_llm_enabled = False
            
            provider, model = await get_user_model(12345)
            
            assert provider == "openai"
            assert model is None

    @pytest.mark.asyncio
    async def test_returns_default_when_no_preference_set(self):
        """Test default provider when no preference in Redis."""
        from src.models.user_preferences import get_user_model
        
        with patch("src.models.user_preferences.settings") as mock_settings:
            mock_settings.multi_llm_enabled = True
            mock_settings.default_llm_provider = "anthropic"
            
            # Mock Redis returning None (no preference)
            mock_redis = AsyncMock()
            mock_redis.get = AsyncMock(return_value=None)
            
            with patch("src.models.user_preferences._get_redis", AsyncMock(return_value=mock_redis)):
                provider, model = await get_user_model(12345)
                
                assert provider == "anthropic"
                assert model is None

    @pytest.mark.asyncio
    async def test_returns_saved_preference(self):
        """Test retrieving saved model preference."""
        from src.models.user_preferences import get_user_model
        import json
        
        with patch("src.models.user_preferences.settings") as mock_settings:
            mock_settings.multi_llm_enabled = True
            
            # Mock Redis returning saved preference
            saved_pref = json.dumps({"provider": "openrouter", "model": "anthropic/claude-3-opus"})
            mock_redis = AsyncMock()
            mock_redis.get = AsyncMock(return_value=saved_pref)
            
            with patch("src.models.user_preferences._get_redis", AsyncMock(return_value=mock_redis)):
                provider, model = await get_user_model(12345)
                
                assert provider == "openrouter"
                assert model == "anthropic/claude-3-opus"

    @pytest.mark.asyncio
    async def test_handles_invalid_json_gracefully(self):
        """Test graceful handling of invalid JSON in Redis."""
        from src.models.user_preferences import get_user_model
        
        with patch("src.models.user_preferences.settings") as mock_settings:
            mock_settings.multi_llm_enabled = True
            mock_settings.default_llm_provider = "openai"
            
            # Mock Redis returning invalid JSON
            mock_redis = AsyncMock()
            mock_redis.get = AsyncMock(return_value="invalid json{{")
            
            with patch("src.models.user_preferences._get_redis", AsyncMock(return_value=mock_redis)):
                provider, model = await get_user_model(12345)
                
                # Should fall back to default
                assert provider == "openai"


class TestSetUserModel:
    """Test set_user_model function."""

    @pytest.mark.asyncio
    async def test_saves_preference_successfully(self):
        """Test saving model preference to Redis."""
        from src.models.user_preferences import set_user_model
        
        with patch("src.models.user_preferences.settings") as mock_settings:
            mock_settings.multi_llm_enabled = True
            
            mock_redis = AsyncMock()
            mock_redis.setex = AsyncMock(return_value=True)
            
            with patch("src.models.user_preferences._get_redis", AsyncMock(return_value=mock_redis)):
                result = await set_user_model(12345, "anthropic", "claude-sonnet")
                
                assert result is True
                mock_redis.setex.assert_called_once()

    @pytest.mark.asyncio
    async def test_rejects_when_multi_llm_disabled(self):
        """Test that setting model is rejected when multi-LLM disabled."""
        from src.models.user_preferences import set_user_model
        
        with patch("src.models.user_preferences.settings") as mock_settings:
            mock_settings.multi_llm_enabled = False
            
            result = await set_user_model(12345, "anthropic", "claude-sonnet")
            
            assert result is False

    @pytest.mark.asyncio
    async def test_handles_redis_failure(self):
        """Test handling when Redis is unavailable."""
        from src.models.user_preferences import set_user_model
        
        with patch("src.models.user_preferences.settings") as mock_settings:
            mock_settings.multi_llm_enabled = True
            
            # Redis unavailable
            with patch("src.models.user_preferences._get_redis", AsyncMock(return_value=None)):
                result = await set_user_model(12345, "anthropic", "claude-sonnet")
                
                assert result is False


class TestClearUserModel:
    """Test clear_user_model function."""

    @pytest.mark.asyncio
    async def test_clears_preference_successfully(self):
        """Test clearing model preference from Redis."""
        from src.models.user_preferences import clear_user_model
        
        mock_redis = AsyncMock()
        mock_redis.delete = AsyncMock(return_value=1)
        
        with patch("src.models.user_preferences._get_redis", AsyncMock(return_value=mock_redis)):
            result = await clear_user_model(12345)
            
            assert result is True
            mock_redis.delete.assert_called_once_with("user:model_preference:12345")


class TestListAvailableModels:
    """Test list_available_models_for_user function."""

    @pytest.mark.asyncio
    async def test_returns_configured_providers(self):
        """Test listing only configured providers."""
        from src.models.user_preferences import list_available_models_for_user
        
        with patch.dict("os.environ", {"OPENAI_API_KEY": "test-key"}):
            providers = await list_available_models_for_user(12345)
            
            # Should return list of dicts
            assert isinstance(providers, list)
            # OpenAI should be in the list (configured)
            provider_names = [p["name"] for p in providers]
            assert "openai" in provider_names


class TestGetUserModelDisplay:
    """Test get_user_model_display function."""

    @pytest.mark.asyncio
    async def test_returns_formatted_string_with_model(self):
        """Test display format when model is set."""
        from src.models.user_preferences import get_user_model_display
        import json
        
        with patch("src.models.user_preferences.settings") as mock_settings:
            mock_settings.multi_llm_enabled = True
            
            saved_pref = json.dumps({"provider": "anthropic", "model": "claude-sonnet"})
            mock_redis = AsyncMock()
            mock_redis.get = AsyncMock(return_value=saved_pref)
            
            with patch("src.models.user_preferences._get_redis", AsyncMock(return_value=mock_redis)):
                display = await get_user_model_display(12345)
                
                assert display == "anthropic:claude-sonnet"

    @pytest.mark.asyncio
    async def test_returns_formatted_string_with_default(self):
        """Test display format when using provider default."""
        from src.models.user_preferences import get_user_model_display
        
        with patch("src.models.user_preferences.settings") as mock_settings:
            mock_settings.multi_llm_enabled = True
            mock_settings.default_llm_provider = "openai"
            
            mock_redis = AsyncMock()
            mock_redis.get = AsyncMock(return_value=None)  # No preference
            
            with patch("src.models.user_preferences._get_redis", AsyncMock(return_value=mock_redis)):
                with patch("src.models.provider_resolution.ProviderResolver") as mock_resolver_class:
                    mock_resolver = MagicMock()
                    mock_config = MagicMock()
                    mock_config.default_model = "gpt-5.4-mini"
                    mock_resolver.resolve = MagicMock(return_value=mock_config)
                    mock_resolver_class.return_value = mock_resolver
                    
                    display = await get_user_model_display(12345)
                    
                    assert "openai:gpt-5.4-mini" in display


class TestFormatProviderList:
    """Test format_provider_list function."""

    def test_includes_provider_names(self):
        """Test that output includes provider names."""
        from src.models.user_preferences import format_provider_list
        
        providers = [
            {"name": "openai", "default_model": "gpt-5.4", "supports_tools": True, "supports_streaming": True},
            {"name": "anthropic", "default_model": "claude-sonnet", "supports_tools": True, "supports_streaming": True},
        ]
        
        output = format_provider_list(providers)
        
        assert "openai" in output
        assert "anthropic" in output
        assert "gpt-5.4" in output
        assert "claude-sonnet" in output
        assert "Available LLM Providers" in output
