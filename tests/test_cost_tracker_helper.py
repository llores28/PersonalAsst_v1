"""Tests for the shared record_llm_cost helper and estimate_cost_from_model."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from src.models.cost_tracker import (
    OPENAI_MODEL_PRICING,
    OPENAI_MODEL_PRICING_DEFAULT,
    estimate_cost_from_model,
    record_llm_cost,
)


# ---------------------------------------------------------------------------
# estimate_cost_from_model (pure function, no I/O)
# ---------------------------------------------------------------------------

class TestEstimateCostFromModel:
    """Tests for the pricing-table lookup."""

    def test_exact_match_gpt5(self):
        cost, key = estimate_cost_from_model("gpt-5", 1_000_000, 1_000_000)
        in_rate, out_rate = OPENAI_MODEL_PRICING["gpt-5"]
        assert key == "gpt-5"
        assert cost == pytest.approx(in_rate + out_rate)

    def test_substring_match_gpt5_mini(self):
        cost, key = estimate_cost_from_model("gpt-5-mini-2026-06-01", 500_000, 500_000)
        assert key == "gpt-5-mini"

    def test_unknown_model_uses_default(self):
        cost, key = estimate_cost_from_model("some-future-model-x", 1_000_000, 1_000_000)
        assert key is None
        in_rate, out_rate = OPENAI_MODEL_PRICING_DEFAULT
        assert cost == pytest.approx(in_rate + out_rate)

    def test_zero_tokens(self):
        cost, key = estimate_cost_from_model("gpt-4o", 0, 0)
        assert cost == 0.0
        assert key == "gpt-4o"

    def test_specificity_order_gpt5_mini_before_gpt5(self):
        """gpt-5-mini should match before gpt-5."""
        _, key = estimate_cost_from_model("gpt-5-mini", 100, 100)
        assert key == "gpt-5-mini"

    def test_claude_sonnet(self):
        cost, key = estimate_cost_from_model("claude-sonnet-4-6", 2_000_000, 1_000_000)
        assert key == "claude-sonnet-4-6"
        in_rate, out_rate = OPENAI_MODEL_PRICING["claude-sonnet-4-6"]
        expected = (2_000_000 * in_rate + 1_000_000 * out_rate) / 1_000_000
        assert cost == pytest.approx(expected)


# ---------------------------------------------------------------------------
# OpenRouter model pricing accuracy (regression for GAP 1 / GAP 7)
# ---------------------------------------------------------------------------

class TestOpenRouterPricing:
    """Verify OpenRouter-prefixed model IDs resolve to correct pricing entries."""

    @pytest.mark.parametrize("model_id, expected_key", [
        # Google Gemini via OpenRouter
        ("google/gemini-2.5-flash-image", "google/gemini-2.5-flash"),
        ("google/gemini-3.1-flash-image-preview", "google/gemini-3.1-flash"),
        ("google/gemini-2.5-pro-preview", "google/gemini-2.5-pro"),
        ("google/gemini-2.0-flash", "google/gemini-2.0-flash"),
        ("google/gemma-2-9b-it", "google/gemma-2-9b-it"),
        # Anthropic via OpenRouter
        ("anthropic/claude-3.5-sonnet", "anthropic/claude-3.5-sonnet"),
        ("anthropic/claude-3-opus", "anthropic/claude-3-opus"),
        ("anthropic/claude-3-haiku", "anthropic/claude-3-haiku"),
        ("anthropic/claude-sonnet-4", "claude-sonnet-4"),  # bare key matches first
        # OpenAI via OpenRouter (bare key matches first via substring)
        ("openai/gpt-4o-mini", "gpt-4o-mini"),
        ("openai/gpt-4o", "gpt-4o"),
        ("openai/o3-mini", "o3-mini"),
        # Black Forest Labs Flux (image-only, zero token cost)
        ("black-forest-labs/flux.2-flex", "black-forest-labs/flux"),
        ("black-forest-labs/flux-1.1-pro", "black-forest-labs/flux"),
    ])
    def test_openrouter_model_hits_pricing_table(self, model_id: str, expected_key: str):
        """Each OpenRouter model ID should match a pricing entry (not fall through to default)."""
        cost, matched_key = estimate_cost_from_model(model_id, 1_000, 500)
        assert matched_key == expected_key, (
            f"Model '{model_id}' matched '{matched_key}', expected '{expected_key}'. "
            "Add or fix entry in OPENAI_MODEL_PRICING."
        )

    def test_flux_has_zero_token_cost(self):
        """Flux is billed per image, not per token — both rates should be 0."""
        cost, key = estimate_cost_from_model("black-forest-labs/flux.2-flex", 0, 0)
        assert cost == 0.0
        assert key == "black-forest-labs/flux"

    def test_gemini_25_flash_cheaper_than_gemini_25_pro(self):
        """Sanity: flash tier should be cheaper than pro for same token count."""
        cost_flash, _ = estimate_cost_from_model("google/gemini-2.5-flash", 100_000, 50_000)
        cost_pro, _ = estimate_cost_from_model("google/gemini-2.5-pro", 100_000, 50_000)
        assert cost_flash < cost_pro

    def test_unknown_openrouter_model_uses_default_and_not_zero(self):
        """A new/unknown OpenRouter model should fall back to default, not silently return 0."""
        cost, matched_key = estimate_cost_from_model("google/gemini-future-model-xyz", 1_000_000, 500_000)
        assert matched_key is None
        in_rate, out_rate = OPENAI_MODEL_PRICING_DEFAULT
        expected = (1_000_000 * in_rate + 500_000 * out_rate) / 1_000_000
        assert cost == pytest.approx(expected)
        assert cost > 0.0  # Default rate is non-zero


# ---------------------------------------------------------------------------
# record_llm_cost (async, needs mocking for DB/Redis)
# ---------------------------------------------------------------------------

class TestRecordLlmCost:
    """Tests for the record_llm_cost async helper."""

    @pytest.mark.asyncio
    async def test_skips_when_no_user_db_id(self):
        """Should log warning and return early when user_db_id is None."""
        result = SimpleNamespace(usage=SimpleNamespace(input_tokens=10, output_tokens=5))
        agent = SimpleNamespace(model="gpt-5")

        with patch("src.models.cost_tracker.logger") as mock_logger:
            await record_llm_cost(
                result=result,
                agent=agent,
                user_db_id=None,
                user_telegram_id=12345,
            )
            mock_logger.warning.assert_called()
            assert "no user_db_id" in str(mock_logger.warning.call_args)

    @pytest.mark.asyncio
    async def test_skips_when_usage_is_none(self):
        """Should log warning when result has no usage data."""
        result = SimpleNamespace(usage=None)
        agent = SimpleNamespace(model="gpt-5")

        with patch("src.models.cost_tracker.logger") as mock_logger:
            await record_llm_cost(
                result=result,
                agent=agent,
                user_db_id=1,
                user_telegram_id=12345,
            )
            mock_logger.warning.assert_called()
            assert "result.usage is None" in str(mock_logger.warning.call_args)

    @pytest.mark.asyncio
    async def test_records_cost_successfully(self):
        """Happy path: usage present, DB upsert + Redis tracking called."""
        result = SimpleNamespace(
            usage=SimpleNamespace(input_tokens=1000, output_tokens=500),
        )
        agent = SimpleNamespace(model="gpt-4o-mini")

        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with (
            patch("src.models.cost_tracker.async_session", return_value=mock_session),
            patch("src.models.cost_tracker._track_provider_cost", new_callable=AsyncMock) as mock_redis,
            patch("src.models.cost_tracker.logger") as mock_logger,
        ):
            await record_llm_cost(
                result=result,
                agent=agent,
                user_db_id=42,
                user_telegram_id=12345,
            )
            # DB session should have been used
            mock_session.execute.assert_called_once()
            mock_session.commit.assert_called_once()
            # Redis tracking should have been called
            mock_redis.assert_called_once()
            # Info log with cost recorded
            mock_logger.info.assert_called()
            assert "Cost recorded" in str(mock_logger.info.call_args)

    @pytest.mark.asyncio
    async def test_handles_db_failure_gracefully(self):
        """DB error should be caught and logged, not raised."""
        result = SimpleNamespace(
            usage=SimpleNamespace(input_tokens=100, output_tokens=50),
        )
        agent = SimpleNamespace(model="gpt-5")

        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_session.execute.side_effect = RuntimeError("DB down")

        with (
            patch("src.models.cost_tracker.async_session", return_value=mock_session),
            patch("src.models.cost_tracker.logger") as mock_logger,
        ):
            # Should NOT raise
            await record_llm_cost(
                result=result,
                agent=agent,
                user_db_id=1,
                user_telegram_id=12345,
            )
            mock_logger.warning.assert_called()
            assert "Cost tracking failed" in str(mock_logger.warning.call_args)

    @pytest.mark.asyncio
    async def test_unknown_model_logs_warning(self):
        """Unknown model should still record cost but warn about default rate."""
        result = SimpleNamespace(
            usage=SimpleNamespace(input_tokens=100, output_tokens=50),
        )
        agent = SimpleNamespace(model="totally-unknown-model")

        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with (
            patch("src.models.cost_tracker.async_session", return_value=mock_session),
            patch("src.models.cost_tracker._track_provider_cost", new_callable=AsyncMock),
            patch("src.models.cost_tracker.logger") as mock_logger,
        ):
            await record_llm_cost(
                result=result,
                agent=agent,
                user_db_id=1,
                user_telegram_id=12345,
            )
            # Should have a warning about unknown model
            warning_calls = [str(c) for c in mock_logger.warning.call_args_list]
            assert any("not in pricing table" in w for w in warning_calls)
