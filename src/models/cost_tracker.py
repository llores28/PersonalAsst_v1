"""Per-provider cost tracking for multi-LLM support.

Tracks API costs separately for each LLM provider (OpenAI, Anthropic, etc.)
and enforces per-provider daily cost caps.

Usage:
    from src.models.cost_tracker import track_cost, check_cost_cap
    
    # Track a request
    await track_cost(
        user_id=123,
        provider="anthropic",
        model="claude-sonnet-4-6",
        input_tokens=1000,
        output_tokens=500,
        cost_usd=0.015,
    )
    
    # Check if provider is capped
    is_capped = await check_cost_cap(user_id=123, provider="anthropic")
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Optional

from sqlalchemy import update, insert, select

from src.db.session import async_session
from src.db.models import DailyCost, User
from src.settings import settings

logger = logging.getLogger(__name__)

# Provider-specific cost cap settings from settings
_PROVIDER_COST_CAP_SETTINGS = {
    "openai": "daily_cost_cap_usd",
    "anthropic": "anthropic_daily_cost_cap_usd",
    "openrouter": "openrouter_daily_cost_cap_usd",
    "google": "google_daily_cost_cap_usd",
    "local": None,  # Local LLMs have no cost
}


async def _resolve_db_user_id(telegram_id: int) -> Optional[int]:
    """Resolve a Telegram user ID to the internal users.id (auto-increment PK).

    daily_costs.user_id is a FK to users.id (INTEGER), so we must look up
    the internal PK — not pass the Telegram ID directly.
    Returns None if no matching user row exists yet.
    """
    try:
        async with async_session() as session:
            row = await session.execute(
                select(User.id).where(User.telegram_id == telegram_id)
            )
            result = row.scalar_one_or_none()
            return result
    except Exception as exc:
        logger.debug("Could not resolve DB user id for telegram_id=%s: %s", telegram_id, exc)
        return None


async def track_cost(
    user_id: int,
    provider: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
    cost_usd: float,
) -> bool:
    """Track API call cost for a specific provider.
    
    Args:
        user_id: Telegram user ID (will be resolved to internal DB PK).
        provider: Provider name (e.g., "openai", "anthropic")
        model: Model ID used
        input_tokens: Number of input tokens
        output_tokens: Number of output tokens
        cost_usd: Estimated cost in USD
    
    Returns:
        True if tracked successfully, False otherwise.
    """
    # Always persist Redis per-provider cost (works with large Telegram IDs)
    await _track_provider_cost(user_id, provider, cost_usd)

    # Resolve Telegram ID → internal DB PK for the daily_costs FK
    db_user_id = await _resolve_db_user_id(user_id)
    if db_user_id is None:
        logger.debug("Skipping daily_costs write — no DB user row for telegram_id=%s", user_id)
        return True

    try:
        async with async_session() as session:
            today = date.today()
            
            # Try to update existing record
            result = await session.execute(
                update(DailyCost)
                .where(
                    DailyCost.user_id == db_user_id,
                    DailyCost.date == today,
                )
                .values(
                    total_tokens=DailyCost.total_tokens + input_tokens + output_tokens,
                    total_cost_usd=DailyCost.total_cost_usd + cost_usd,
                    request_count=DailyCost.request_count + 1,
                )
            )
            
            # If no row updated, insert new record
            if result.rowcount == 0:
                await session.execute(
                    insert(DailyCost).values(
                        user_id=db_user_id,
                        date=today,
                        total_tokens=input_tokens + output_tokens,
                        total_cost_usd=cost_usd,
                        request_count=1,
                    )
                )
            
            await session.commit()
            
            logger.debug(
                "Tracked cost for user %s (db_id=%s), provider %s: $%.4f",
                user_id, db_user_id, provider, cost_usd
            )
            return True
            
    except Exception as e:
        logger.error("Failed to track cost for user %s: %s", user_id, e)
        return False


async def _track_provider_cost(
    user_id: int,
    provider: str,
    cost_usd: float,
) -> None:
    """Track per-provider cost in Redis for fast lookup.
    
    This allows quick cost cap checks without database queries.
    """
    try:
        from redis.asyncio import from_url
        
        redis = from_url(settings.redis_url, decode_responses=True)
        key = f"costs:{date.today().isoformat()}:{user_id}:{provider}"
        
        # Increment the provider-specific cost
        await redis.incrbyfloat(key, cost_usd)
        # Set expiry to end of day + 1 hour
        await redis.expire(key, 25 * 60 * 60)
        await redis.close()
        
    except Exception as e:
        # Redis failure shouldn't break cost tracking
        logger.warning("Failed to track provider cost in Redis: %s", e)


async def get_provider_cost_today(user_id: int, provider: str) -> float:
    """Get today's cost for a specific provider.
    
    Args:
        user_id: User ID
        provider: Provider name
    
    Returns:
        Total cost in USD for today.
    """
    try:
        from redis.asyncio import from_url
        
        redis = from_url(settings.redis_url, decode_responses=True)
        key = f"costs:{date.today().isoformat()}:{user_id}:{provider}"
        
        cost = await redis.get(key)
        await redis.close()
        
        return float(cost) if cost else 0.0
        
    except Exception:
        # Fallback to 0 if Redis unavailable
        return 0.0


async def check_cost_cap(user_id: int, provider: str) -> tuple[bool, float, float]:
    """Check if user has exceeded daily cost cap for a provider.
    
    Args:
        user_id: User ID
        provider: Provider name
    
    Returns:
        Tuple of (is_capped, current_cost, cap_limit)
        - is_capped: True if cost cap exceeded
        - current_cost: Current day's cost for provider
        - cap_limit: The cost cap for this provider
    """
    # Get the cost cap for this provider
    cap_setting = _PROVIDER_COST_CAP_SETTINGS.get(provider)
    
    if cap_setting is None:
        # No cap for this provider (e.g., local LLMs)
        return (False, 0.0, float('inf'))
    
    cap_limit = getattr(settings, cap_setting, settings.daily_cost_cap_usd)
    current_cost = await get_provider_cost_today(user_id, provider)
    
    is_capped = current_cost >= cap_limit
    
    if is_capped:
        logger.warning(
            "User %s exceeded cost cap for %s: $%.2f / $%.2f",
            user_id, provider, current_cost, cap_limit
        )
    
    return (is_capped, current_cost, cap_limit)


async def get_cost_breakdown(user_id: int) -> dict[str, float]:
    """Get cost breakdown by provider for today.
    
    Args:
        user_id: User ID
    
    Returns:
        Dictionary mapping provider names to costs.
    """
    breakdown = {}
    
    # Get costs for all known providers
    providers = ["openai", "anthropic", "openrouter", "google", "local"]
    
    for provider in providers:
        cost = await get_provider_cost_today(user_id, provider)
        if cost > 0:
            breakdown[provider] = cost
    
    return breakdown


def get_provider_cap_setting(provider: str) -> Optional[float]:
    """Get the cost cap setting for a provider.
    
    Args:
        provider: Provider name
    
    Returns:
        Cost cap in USD, or None if no cap.
    """
    cap_setting = _PROVIDER_COST_CAP_SETTINGS.get(provider)
    if cap_setting is None:
        return None
    return getattr(settings, cap_setting, settings.daily_cost_cap_usd)


async def get_fallback_provider(user_id: int, requested_provider: str) -> str:
    """Get a fallback provider when the requested one is capped.
    
    Tries providers in order: openai -> anthropic -> google -> openrouter
    
    Args:
        user_id: User ID
        requested_provider: The provider that was requested (and may be capped)
    
    Returns:
        Name of fallback provider to use.
    """
    from src.models.provider_resolution import ProviderResolver
    
    # Priority order for fallbacks
    fallback_order = ["openai", "anthropic", "google", "openrouter"]
    
    # Remove the requested provider from consideration
    if requested_provider in fallback_order:
        fallback_order.remove(requested_provider)
    
    # Add requested provider at the end as last resort
    fallback_order.append(requested_provider)
    
    resolver = ProviderResolver()
    
    for provider in fallback_order:
        # Check if provider is configured
        try:
            config = resolver.resolve(provider)
            if not config.is_configured:
                continue
        except ValueError:
            continue
        
        # Check if provider is capped
        is_capped, _, _ = await check_cost_cap(user_id, provider)
        if not is_capped:
            logger.info(
                "Using fallback provider %s for user %s (requested %s was capped)",
                provider, user_id, requested_provider
            )
            return provider
    
    # All providers capped - return openai as last resort
    # (it will fail gracefully with cost cap error)
    return "openai"


async def should_warn_about_cap(user_id: int, provider: str) -> tuple[bool, float, float]:
    """Check if user should be warned about approaching cost cap.
    
    Returns:
        Tuple of (should_warn, current_cost, cap_limit)
        - should_warn: True if cost is at 80% or more of cap
    """
    is_capped, current_cost, cap_limit = await check_cost_cap(user_id, provider)
    
    if is_capped:
        return (True, current_cost, cap_limit)  # Already capped
    
    # Warn at 80% of cap
    warning_threshold = cap_limit * 0.8
    should_warn = current_cost >= warning_threshold
    
    return (should_warn, current_cost, cap_limit)


# ---------------------------------------------------------------------------
# Unified pricing table & record_llm_cost helper
# ---------------------------------------------------------------------------
# Per-1M-token pricing (input, output). Update when models change.
# Source: https://developers.openai.com/api/docs/pricing
#
# Ordering matters: the first key whose substring appears in model_id wins.
# Put the most-specific suffixes first so e.g. "gpt-5-mini" matches before
# "gpt-5".
OPENAI_MODEL_PRICING: dict[str, tuple[float, float]] = {
    # --- GPT-5.4 family (internal Atlas naming) ---
    "gpt-5.4-pro":       (3.75, 15.00),
    "gpt-5.4-mini":      (0.40,  1.60),
    "gpt-5.4-nano":      (0.10,  0.40),
    "gpt-5.4":           (3.00, 12.00),
    # --- GPT-5 family ---
    "gpt-5-pro":         (3.75, 15.00),
    "gpt-5-mini":        (0.40,  1.60),
    "gpt-5-nano":        (0.10,  0.40),
    "gpt-5":             (3.00, 12.00),
    # --- GPT-4.1 family ---
    "gpt-4.1-mini":      (0.40,  1.60),
    "gpt-4.1-nano":      (0.10,  0.40),
    "gpt-4.1":           (2.00,  8.00),
    # --- GPT-4o family ---
    "gpt-4o-mini":       (0.15,  0.60),
    "gpt-4o":            (2.50, 10.00),
    # --- Reasoning models ---
    "o3-mini":           (1.10,  4.40),
    "o1-preview":        (15.00, 60.00),
    "o1-mini":           (1.10,  4.40),
    "o1":                (15.00, 60.00),
    # --- Anthropic (via OpenRouter) ---
    "claude-opus-4":     (15.00, 75.00),
    "claude-sonnet-4-6": (3.00, 15.00),
    "claude-sonnet-4":   (3.00, 15.00),
    "claude-haiku-4":    (0.80,  4.00),
    # --- Windsurf SWE-1.5 (free tier) ---
    "swe-1.5":           (0.00,  0.00),
    "swe-1":             (0.00,  0.00),
    # --- OpenRouter-routed models (prefix includes provider namespace) ---
    # Google Gemini via OpenRouter
    "google/gemini-2.5-pro":          (1.25, 10.00),
    "google/gemini-2.5-flash":        (0.15,  0.60),
    "google/gemini-3.1-flash":        (0.15,  0.60),
    "google/gemini-2.0-flash":        (0.10,  0.40),
    "google/gemma-2-9b-it":           (0.05,  0.10),
    # Anthropic via OpenRouter
    "anthropic/claude-sonnet-4":      (3.00, 15.00),
    "anthropic/claude-3.5-sonnet":    (3.00, 15.00),
    "anthropic/claude-3-opus":        (15.00, 75.00),
    "anthropic/claude-3-haiku":       (0.25,  1.25),
    # OpenAI via OpenRouter
    "openai/gpt-4o-mini":             (0.15,  0.60),
    "openai/gpt-4o":                  (2.50, 10.00),
    "openai/o3-mini":                 (1.10,  4.40),
    # Black Forest Labs / Flux (image-only, billed per image — no token cost)
    "black-forest-labs/flux":         (0.00,  0.00),
}
OPENAI_MODEL_PRICING_DEFAULT: tuple[float, float] = (3.00, 12.00)


def estimate_cost_from_model(
    model_id: str,
    input_tokens: int,
    output_tokens: int,
) -> tuple[float, str | None]:
    """Return (cost_usd, matched_pricing_key) using the pricing table.

    If no key matches, uses the fallback default rate and returns
    matched_key=None.
    """
    matched_key = next(
        (k for k in OPENAI_MODEL_PRICING if k in model_id),
        None,
    )
    if matched_key is None:
        in_rate, out_rate = OPENAI_MODEL_PRICING_DEFAULT
    else:
        in_rate, out_rate = OPENAI_MODEL_PRICING[matched_key]
    cost = (input_tokens * in_rate + output_tokens * out_rate) / 1_000_000
    return cost, matched_key


async def record_llm_cost(
    *,
    result: object,
    agent: object,
    user_db_id: int | None,
    user_telegram_id: int,
    provider: str = "openai",
) -> None:
    """Best-effort recording of LLM cost from a Runner result.

    Extracts usage from *result*, looks up pricing for *agent.model*,
    then persists via both the PostgreSQL ``daily_costs`` table (upsert)
    and the Redis per-provider tracker.

    This is the **single call-site** that all agents should use instead
    of inlining their own cost-tracking block.

    Args:
        result: The ``RunResult`` returned by ``Runner.run()``.
        agent: The agent instance (used to read ``agent.model``).
        user_db_id: Internal ``users.id`` PK (may be ``None`` if unknown).
        user_telegram_id: Telegram user ID (used for Redis tracking).
        provider: LLM provider name (default ``"openai"``).
    """
    model_id = str(getattr(agent, "model", "")) if agent else ""
    usage = getattr(result, "usage", None)

    if not user_db_id:
        logger.warning(
            "Cost tracking skipped: no user_db_id for telegram_id=%s model=%s",
            user_telegram_id, model_id,
        )
        return
    if usage is None:
        logger.warning(
            "Cost tracking skipped: result.usage is None for user=%s model=%s "
            "(provider may not report usage for this model)",
            user_db_id, model_id,
        )
        return

    try:
        in_tok = int(getattr(usage, "input_tokens", 0) or 0)
        out_tok = int(getattr(usage, "output_tokens", 0) or 0)
        total_tok = in_tok + out_tok
        cost, matched_key = estimate_cost_from_model(model_id, in_tok, out_tok)

        if matched_key is None:
            logger.warning(
                "Cost tracking: model '%s' not in pricing table — using "
                "default rate %s. Add it to OPENAI_MODEL_PRICING in "
                "src/models/cost_tracker.py to report accurate spend.",
                model_id, OPENAI_MODEL_PRICING_DEFAULT,
            )

        # Persist to PostgreSQL (upsert daily_costs row)
        from sqlalchemy.dialects.postgresql import insert as pg_insert

        async with async_session() as sess:
            stmt = pg_insert(DailyCost).values(
                date=date.today(),
                user_id=user_db_id,
                total_tokens=total_tok,
                total_cost_usd=cost,
                request_count=1,
            ).on_conflict_do_update(
                index_elements=["date", "user_id"],
                set_={
                    "total_tokens": DailyCost.total_tokens + total_tok,
                    "total_cost_usd": DailyCost.total_cost_usd + cost,
                    "request_count": DailyCost.request_count + 1,
                },
            )
            await sess.execute(stmt)
            await sess.commit()

        # Also persist to Redis per-provider tracker
        await _track_provider_cost(user_telegram_id, provider, cost)

        logger.info(
            "Cost recorded: $%.6f (%d tokens, model=%s matched=%s) for user %s",
            cost, total_tok, model_id, matched_key or "DEFAULT", user_db_id,
        )
    except Exception as exc:
        logger.warning(
            "Cost tracking failed for user=%s model=%s: %s",
            user_db_id, model_id, exc, exc_info=True,
        )
