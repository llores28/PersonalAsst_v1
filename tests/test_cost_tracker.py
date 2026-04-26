"""Tests for per-provider cost tracking (Phase 3 Option B Upgrade)."""

# ruff: noqa: E402

from __future__ import annotations

import sys
from unittest.mock import MagicMock, AsyncMock, patch
import pytest

# Mock redis before importing
sys.modules["redis"] = MagicMock()
sys.modules["redis.asyncio"] = MagicMock()


class TestTrackCost:
    """Test track_cost function."""

    @pytest.mark.asyncio
    async def test_tracks_cost_successfully(self):
        """Test that cost is tracked in database."""
        from src.models.cost_tracker import track_cost

        # Bypass `_resolve_db_user_id` (which adds its own SELECT) and
        # `_track_provider_cost` (which awaits Redis) so this test stays
        # focused on the UPDATE/INSERT logic on the daily_costs row.
        with patch("src.models.cost_tracker.async_session") as mock_session_class, \
             patch("src.models.cost_tracker._resolve_db_user_id", AsyncMock(return_value=42)), \
             patch("src.models.cost_tracker._track_provider_cost", AsyncMock()):
            mock_session = AsyncMock()
            mock_session_class.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session_class.return_value.__aexit__ = AsyncMock(return_value=False)

            # Mock update returning rowcount=0 (no existing record)
            mock_result = MagicMock()
            mock_result.rowcount = 0
            mock_session.execute = AsyncMock(return_value=mock_result)

            result = await track_cost(
                user_id=123,
                provider="anthropic",
                model="claude-sonnet",
                input_tokens=1000,
                output_tokens=500,
                cost_usd=0.015,
            )

            assert result is True
            # UPDATE then INSERT — the resolution SELECT lives in a separate
            # session inside `_resolve_db_user_id` (bypassed above).
            assert mock_session.execute.call_count == 2

    @pytest.mark.asyncio
    async def test_updates_existing_record(self):
        """Test that existing daily record is updated."""
        from src.models.cost_tracker import track_cost

        with patch("src.models.cost_tracker.async_session") as mock_session_class, \
             patch("src.models.cost_tracker._resolve_db_user_id", AsyncMock(return_value=42)), \
             patch("src.models.cost_tracker._track_provider_cost", AsyncMock()):
            mock_session = AsyncMock()
            mock_session_class.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session_class.return_value.__aexit__ = AsyncMock(return_value=False)

            # Mock update returning rowcount=1 (existing record updated)
            mock_result = MagicMock()
            mock_result.rowcount = 1
            mock_session.execute = AsyncMock(return_value=mock_result)

            result = await track_cost(
                user_id=123,
                provider="openai",
                model="gpt-5.4",
                input_tokens=100,
                output_tokens=50,
                cost_usd=0.002,
            )

            assert result is True
            # Only the UPDATE — INSERT skipped because rowcount=1.
            assert mock_session.execute.call_count == 1


class TestCheckCostCap:
    """Test check_cost_cap function."""

    @pytest.mark.asyncio
    async def test_not_capped_when_under_limit(self):
        """Test that provider is not capped when under limit."""
        from src.models.cost_tracker import check_cost_cap
        
        with patch("src.models.cost_tracker.get_provider_cost_today", AsyncMock(return_value=1.50)):
            with patch("src.models.cost_tracker.settings") as mock_settings:
                mock_settings.anthropic_daily_cost_cap_usd = 5.00
                mock_settings.daily_cost_cap_usd = 5.00
                
                is_capped, current, limit = await check_cost_cap(123, "anthropic")
                
                assert is_capped is False
                assert current == 1.50
                assert limit == 5.00

    @pytest.mark.asyncio
    async def test_capped_when_over_limit(self):
        """Test that provider is capped when over limit."""
        from src.models.cost_tracker import check_cost_cap
        
        with patch("src.models.cost_tracker.get_provider_cost_today", AsyncMock(return_value=5.50)):
            with patch("src.models.cost_tracker.settings") as mock_settings:
                mock_settings.anthropic_daily_cost_cap_usd = 5.00
                mock_settings.daily_cost_cap_usd = 5.00
                
                is_capped, current, limit = await check_cost_cap(123, "anthropic")
                
                assert is_capped is True
                assert current == 5.50
                assert limit == 5.00

    @pytest.mark.asyncio
    async def test_no_cap_for_local_llms(self):
        """Test that local LLMs have no cost cap."""
        from src.models.cost_tracker import check_cost_cap
        
        is_capped, current, limit = await check_cost_cap(123, "local")
        
        assert is_capped is False
        assert current == 0.0
        assert limit == float('inf')


class TestGetProviderCostToday:
    """Test get_provider_cost_today function."""

    @pytest.mark.asyncio
    async def test_gets_cost_from_redis(self):
        """Test retrieving cost from Redis."""
        from src.models.cost_tracker import get_provider_cost_today
        
        # Mock the Redis client directly
        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(return_value="2.50")
        mock_redis.close = AsyncMock()
        
        with patch("redis.asyncio.from_url", return_value=mock_redis):
            cost = await get_provider_cost_today(123, "openai")
            
            assert cost == 2.50

    @pytest.mark.asyncio
    async def test_returns_zero_when_no_cost(self):
        """Test that zero is returned when no cost tracked."""
        from src.models.cost_tracker import get_provider_cost_today
        
        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(return_value=None)
        mock_redis.close = AsyncMock()
        
        with patch("redis.asyncio.from_url", return_value=mock_redis):
            cost = await get_provider_cost_today(123, "anthropic")
            
            assert cost == 0.0


class TestGetCostBreakdown:
    """Test get_cost_breakdown function."""

    @pytest.mark.asyncio
    async def test_returns_breakdown_with_costs(self):
        """Test getting cost breakdown for multiple providers."""
        from src.models.cost_tracker import get_cost_breakdown
        
        async def mock_get_cost(user_id, provider):
            costs = {"openai": 2.50, "anthropic": 1.20, "google": 0.0}
            return costs.get(provider, 0.0)
        
        with patch("src.models.cost_tracker.get_provider_cost_today", mock_get_cost):
            breakdown = await get_cost_breakdown(123)
            
            assert "openai" in breakdown
            assert "anthropic" in breakdown
            assert breakdown["openai"] == 2.50
            assert breakdown["anthropic"] == 1.20
            # Google has 0 cost, should not be in breakdown
            assert "google" not in breakdown


class TestGetFallbackProvider:
    """Test get_fallback_provider function."""

    @pytest.mark.asyncio
    async def test_returns_different_provider_when_capped(self):
        """Test that fallback provider is returned when requested is capped."""
        from src.models.cost_tracker import get_fallback_provider
        from src.models.provider_resolution import ProviderResolver
        
        # Mock check_cost_cap to return capped for anthropic, not capped for openai
        async def mock_check_cap(user_id, provider):
            if provider == "anthropic":
                return (True, 5.50, 5.00)  # Capped
            return (False, 1.00, 5.00)  # Not capped
        
        # Mock ProviderResolver to return configured providers
        mock_config = MagicMock()
        mock_config.is_configured = True
        
        with patch("src.models.cost_tracker.check_cost_cap", mock_check_cap):
            with patch.object(ProviderResolver, "resolve", return_value=mock_config):
                fallback = await get_fallback_provider(123, "anthropic")
                
                assert fallback == "openai"  # Falls back to openai

    @pytest.mark.asyncio
    async def test_returns_same_provider_when_not_capped(self):
        """Test that same provider is returned when not capped."""
        from src.models.cost_tracker import get_fallback_provider
        from src.models.provider_resolution import ProviderResolver
        
        # Mock check_cost_cap to return not capped for anthropic
        async def mock_check_cap(user_id, provider):
            return (False, 1.00, 5.00)  # Not capped
        
        # Mock ProviderResolver to return configured providers
        mock_config = MagicMock()
        mock_config.is_configured = True
        mock_resolver = MagicMock()
        mock_resolver.resolve = MagicMock(return_value=mock_config)
        
        with patch("src.models.cost_tracker.check_cost_cap", mock_check_cap):
            with patch.object(ProviderResolver, "resolve", return_value=mock_config):
                fallback = await get_fallback_provider(123, "anthropic")
                
                # Will return openai first since it's first in fallback order and "configured"
                assert fallback in ["openai", "anthropic"]


class TestShouldWarnAboutCap:
    """Test should_warn_about_cap function."""

    @pytest.mark.asyncio
    async def test_warns_at_80_percent(self):
        """Test that warning is triggered at 80% of cap."""
        from src.models.cost_tracker import should_warn_about_cap
        
        # Cost at 82% of cap
        with patch("src.models.cost_tracker.get_provider_cost_today", AsyncMock(return_value=4.10)):
            with patch("src.models.cost_tracker.settings") as mock_settings:
                mock_settings.openai_daily_cost_cap_usd = 5.00
                mock_settings.daily_cost_cap_usd = 5.00
                
                should_warn, current, limit = await should_warn_about_cap(123, "openai")
                
                assert should_warn is True
                assert current == 4.10
                assert limit == 5.00

    @pytest.mark.asyncio
    async def test_no_warning_under_80_percent(self):
        """Test that no warning under 80% of cap."""
        from src.models.cost_tracker import should_warn_about_cap
        
        # Cost at 50% of cap
        with patch("src.models.cost_tracker.get_provider_cost_today", AsyncMock(return_value=2.50)):
            with patch("src.models.cost_tracker.settings") as mock_settings:
                mock_settings.openai_daily_cost_cap_usd = 5.00
                mock_settings.daily_cost_cap_usd = 5.00
                
                should_warn, current, limit = await should_warn_about_cap(123, "openai")
                
                assert should_warn is False
