"""End-to-end integration tests for multi-LLM support (Phase 4 Option B Upgrade).

These tests verify the full flow:
1. User switches provider via /model
2. Cost tracking works per provider
3. Cost caps are enforced
4. Fallbacks work when capped
"""

# ruff: noqa: E402

from __future__ import annotations

import sys
from unittest.mock import MagicMock, AsyncMock, patch
import pytest

# Mock redis before importing
sys.modules["redis"] = MagicMock()
sys.modules["redis.asyncio"] = MagicMock()


class TestProviderResolutionIntegration:
    """Integration tests for provider resolution."""

    @pytest.mark.asyncio
    async def test_resolve_all_builtin_providers(self):
        """Test that all built-in providers can be resolved."""
        from src.models.provider_resolution import ProviderResolver
        
        with patch.dict("os.environ", {
            "OPENAI_API_KEY": "test-openai",
            "ANTHROPIC_API_KEY": "test-anthropic",
            "OPENROUTER_API_KEY": "test-openrouter",
            "GOOGLE_API_KEY": "test-google",
        }):
            resolver = ProviderResolver()
            
            # Should resolve all providers without errors
            openai = resolver.resolve("openai")
            assert openai.name == "openai"
            assert openai.api_mode == "openai"
            
            anthropic = resolver.resolve("anthropic")
            assert anthropic.name == "anthropic"
            
            openrouter = resolver.resolve("openrouter")
            assert openrouter.name == "openrouter"
            
            google = resolver.resolve("google")
            assert google.name == "google"
            
            # Local doesn't need API key
            local = resolver.resolve("local")
            assert local.name == "local"

    @pytest.mark.asyncio
    async def test_list_available_respects_configuration(self):
        """Test that only configured providers are listed as available."""
        from src.models.provider_resolution import ProviderResolver
        
        with patch.dict("os.environ", {"OPENAI_API_KEY": "test"}):
            resolver = ProviderResolver()
            available = resolver.list_provider_names(configured_only=True)
            
            assert "openai" in available
            # Anthropic not configured
            assert "anthropic" not in available


class TestUserPreferenceIntegration:
    """Integration tests for user preference flow."""

    @pytest.mark.asyncio
    async def test_full_preference_save_and_retrieve(self):
        """Test saving and retrieving user preferences."""
        from src.models.user_preferences import set_user_model, get_user_model
        
        with patch("src.models.user_preferences.settings") as mock_settings:
            mock_settings.multi_llm_enabled = True
            mock_settings.default_llm_provider = "openai"
            
            mock_redis = AsyncMock()
            mock_redis.setex = AsyncMock(return_value=True)
            mock_redis.get = AsyncMock(return_value='{"provider": "anthropic", "model": "claude-sonnet"}')
            mock_redis.close = AsyncMock()
            
            with patch("redis.asyncio.from_url", return_value=mock_redis):
                # Save preference
                success = await set_user_model(12345, "anthropic", "claude-sonnet")
                assert success is True
                
                # Retrieve preference
                provider, model = await get_user_model(12345)
                assert provider == "anthropic"
                assert model == "claude-sonnet"

    @pytest.mark.asyncio
    async def test_preference_fallback_to_default(self):
        """Test fallback when no preference saved."""
        from src.models.user_preferences import get_user_model
        
        with patch("src.models.user_preferences.settings") as mock_settings:
            mock_settings.multi_llm_enabled = True
            mock_settings.default_llm_provider = "google"
            
            mock_redis = AsyncMock()
            mock_redis.get = AsyncMock(return_value=None)  # No saved preference
            mock_redis.close = AsyncMock()
            
            with patch("redis.asyncio.from_url", return_value=mock_redis):
                provider, model = await get_user_model(12345)
                
                # Should fall back to default
                assert provider == "google"
                assert model is None


class TestCostTrackingIntegration:
    """Integration tests for cost tracking across providers."""

    @pytest.mark.asyncio
    async def test_costs_tracked_separately_per_provider(self):
        """Test that costs are tracked separately for each provider."""
        from src.models.cost_tracker import get_cost_breakdown
        
        # Mock get_provider_cost_today to return test values
        async def mock_get_cost(user_id, provider):
            costs = {"openai": 0.020, "anthropic": 0.015, "google": 0.0}
            return costs.get(provider, 0.0)
        
        with patch("src.models.cost_tracker.get_provider_cost_today", mock_get_cost):
            breakdown = await get_cost_breakdown(123)
            
            assert "openai" in breakdown
            assert "anthropic" in breakdown
            assert breakdown["openai"] == 0.020
            assert breakdown["anthropic"] == 0.015

    @pytest.mark.asyncio
    async def test_cost_cap_enforcement(self):
        """Test that cost caps are enforced per provider."""
        from src.models.cost_tracker import check_cost_cap
        
        with patch("src.models.cost_tracker.settings") as mock_settings:
            mock_settings.anthropic_daily_cost_cap_usd = 0.01  # Very low cap
            mock_settings.daily_cost_cap_usd = 5.00
            
            # Mock get_provider_cost_today to return cost above cap
            with patch("src.models.cost_tracker.get_provider_cost_today", AsyncMock(return_value=0.015)):
                # Check if capped
                is_capped, current, limit = await check_cost_cap(123, "anthropic")
                
                assert is_capped is True
                assert current == 0.015
                assert limit == 0.01

    @pytest.mark.asyncio
    async def test_warning_at_80_percent(self):
        """Test warning when approaching cost cap."""
        from src.models.cost_tracker import should_warn_about_cap
        
        # Mock check_cost_cap to return values at 82% of cap
        async def mock_check_cap(user_id, provider):
            return (False, 0.082, 0.10)  # Not capped, 82% of cap
        
        with patch("src.models.cost_tracker.check_cost_cap", mock_check_cap):
            should_warn, current, limit = await should_warn_about_cap(123, "openai")
            
            assert should_warn is True
            assert current == 0.082
            assert limit == 0.10


class TestFallbackProviderIntegration:
    """Integration tests for provider fallback."""

    @pytest.mark.asyncio
    async def test_fallback_when_provider_capped(self):
        """Test fallback to available provider when requested is capped."""
        from src.models.cost_tracker import get_fallback_provider
        from src.models.provider_resolution import ProviderResolver
        
        # Mock check_cost_cap to return capped for anthropic
        async def mock_check_cap(user_id, provider):
            if provider == "anthropic":
                return (True, 5.50, 5.00)  # Capped
            return (False, 1.00, 5.00)  # Not capped
        
        # Mock ProviderResolver
        mock_config = MagicMock()
        mock_config.is_configured = True
        
        with patch("src.models.cost_tracker.check_cost_cap", mock_check_cap):
            with patch.object(ProviderResolver, "resolve", return_value=mock_config):
                fallback = await get_fallback_provider(123, "anthropic")
                
                # Should not return anthropic since it's capped
                assert fallback != "anthropic"

    @pytest.mark.asyncio
    async def test_same_provider_when_not_capped(self):
        """Test using same provider when not capped."""
        from src.models.cost_tracker import get_fallback_provider
        from src.models.provider_resolution import ProviderResolver
        
        # Mock check_cost_cap to return not capped
        async def mock_check_cap(user_id, provider):
            return (False, 1.00, 5.00)  # Not capped
        
        # Mock ProviderResolver
        mock_config = MagicMock()
        mock_config.is_configured = True
        
        with patch("src.models.cost_tracker.check_cost_cap", mock_check_cap):
            with patch.object(ProviderResolver, "resolve", return_value=mock_config):
                fallback = await get_fallback_provider(123, "anthropic")
                
                # Fallback order prioritizes openai first, then anthropic
                # Since both are "not capped", openai comes first in fallback_order
                assert fallback == "openai"


class TestMultiLLMFeatureFlag:
    """Integration tests for feature flag behavior."""

    @pytest.mark.asyncio
    async def test_multi_llm_disabled_blocks_model_switching(self):
        """Test that model switching is blocked when multi-LLM disabled."""
        from src.models.user_preferences import set_user_model
        
        with patch("src.models.user_preferences.settings") as mock_settings:
            mock_settings.multi_llm_enabled = False
            
            result = await set_user_model(12345, "anthropic", "claude")
            
            # Should be rejected
            assert result is False

    @pytest.mark.asyncio
    async def test_multi_llm_disabled_returns_openai(self):
        """Test that get_user_model returns openai when multi-LLM disabled."""
        from src.models.user_preferences import get_user_model
        
        with patch("src.models.user_preferences.settings") as mock_settings:
            mock_settings.multi_llm_enabled = False
            
            provider, model = await get_user_model(12345)
            
            # Should always return openai
            assert provider == "openai"
            assert model is None


class TestCostEstimationIntegration:
    """Integration tests for cost estimation."""

    def test_provider_cost_estimation(self):
        """Test that cost estimation works for all providers."""
        from src.models.provider_resolution import ProviderResolver
        
        with patch.dict("os.environ", {"OPENAI_API_KEY": "test"}):
            resolver = ProviderResolver()
            
            # Estimate cost for OpenAI
            cost = resolver.estimate_cost("openai", "gpt-5.4-mini", 1000, 500)
            # gpt-5.4-mini: $0.0005/1k input, $0.0015/1k output
            expected = (1000 / 1000 * 0.0005) + (500 / 1000 * 0.0015)
            assert cost == pytest.approx(expected, rel=1e-9)


class TestProviderStatusIntegration:
    """Integration tests for provider status reporting."""

    def test_provider_status_message(self):
        """Test that provider status message is generated correctly."""
        from src.models.provider_resolution import get_provider_status_message
        
        with patch.dict("os.environ", {"OPENAI_API_KEY": "test"}):
            with patch("src.models.provider_resolution.settings") as mock_settings:
                mock_settings.multi_llm_enabled = True
                mock_settings.default_llm_provider = "openai"
                
                message = get_provider_status_message()
                
                assert "openai" in message
                assert "Multi-LLM support:" in message
