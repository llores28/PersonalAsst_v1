"""User LLM provider/model preferences persistence.

Stores per-user provider and model selections in Redis for fast access.
Falls back to system defaults when no preference set.

Usage:
    from src.models.user_preferences import get_user_model, set_user_model
    
    # Get user's preferred model
    provider, model = await get_user_model(telegram_id=12345)
    
    # Set user's preferred model
    await set_user_model(telegram_id=12345, provider="anthropic", model="claude-sonnet")
"""

from __future__ import annotations

import json
import logging
from typing import Optional, Tuple

from src.settings import settings

logger = logging.getLogger(__name__)

# Redis key prefix for user model preferences
_USER_MODEL_KEY_PREFIX = "user:model_preference:"

# Cache TTL (30 days)
_CACHE_TTL_SECONDS = 30 * 24 * 60 * 60


async def _get_redis():
    """Get Redis connection."""
    try:
        from redis.asyncio import from_url
        return from_url(settings.redis_url, decode_responses=True)
    except Exception as e:
        logger.warning("Failed to connect to Redis: %s", e)
        return None


async def get_user_model(telegram_id: int) -> Tuple[str, Optional[str]]:
    """Get user's preferred provider and model.
    
    Returns:
        Tuple of (provider, model). If no preference set, returns
        (default_provider, None) where model=None means "use provider default".
    
    Example:
        >>> provider, model = await get_user_model(12345)
        >>> print(f"Using {provider}:{model or 'default'}")
        Using anthropic:claude-sonnet
    """
    # If multi-LLM is disabled, always use OpenAI
    if not getattr(settings, 'multi_llm_enabled', False):
        return ("openai", None)
    
    try:
        redis = await _get_redis()
        if not redis:
            # Fallback to default if Redis unavailable
            return (settings.default_llm_provider, None)
        
        key = f"{_USER_MODEL_KEY_PREFIX}{telegram_id}"
        data = await redis.get(key)
        
        if data:
            try:
                pref = json.loads(data)
                return (pref.get("provider", settings.default_llm_provider),
                       pref.get("model"))
            except json.JSONDecodeError:
                logger.warning("Invalid model preference JSON for user %s", telegram_id)
        
        # No preference set - return default
        return (settings.default_llm_provider, None)
        
    except Exception as e:
        logger.warning("Error getting user model preference for %s: %s", telegram_id, e)
        return (settings.default_llm_provider, None)


async def set_user_model(
    telegram_id: int,
    provider: str,
    model: Optional[str] = None,
) -> bool:
    """Set user's preferred provider and model.
    
    Args:
        telegram_id: User's Telegram ID
        provider: Provider name (e.g., "openai", "anthropic", "openrouter")
        model: Optional specific model ID. If None, uses provider default.
    
    Returns:
        True if saved successfully, False otherwise.
    
    Example:
        >>> await set_user_model(12345, "anthropic", "claude-sonnet")
        True
    """
    # If multi-LLM is disabled, reject the change
    if not getattr(settings, 'multi_llm_enabled', False):
        logger.warning("Attempted to set model preference but multi_llm_enabled=false")
        return False
    
    try:
        redis = await _get_redis()
        if not redis:
            logger.error("Cannot save model preference - Redis unavailable")
            return False
        
        key = f"{_USER_MODEL_KEY_PREFIX}{telegram_id}"
        data = json.dumps({
            "provider": provider,
            "model": model,
        })
        
        await redis.setex(key, _CACHE_TTL_SECONDS, data)
        logger.info("Saved model preference for user %s: %s:%s", telegram_id, provider, model or "default")
        return True
        
    except Exception as e:
        logger.error("Error saving user model preference for %s: %s", telegram_id, e)
        return False


async def clear_user_model(telegram_id: int) -> bool:
    """Clear user's model preference (reset to default).
    
    Args:
        telegram_id: User's Telegram ID
    
    Returns:
        True if cleared successfully, False otherwise.
    """
    try:
        redis = await _get_redis()
        if not redis:
            return False
        
        key = f"{_USER_MODEL_KEY_PREFIX}{telegram_id}"
        result = await redis.delete(key)
        logger.info("Cleared model preference for user %s", telegram_id)
        return result > 0
        
    except Exception as e:
        logger.error("Error clearing user model preference for %s: %s", telegram_id, e)
        return False


async def list_available_models_for_user(telegram_id: int) -> list[dict]:
    """List all available models for a user, organized by provider.
    
    Returns:
        List of provider dictionaries with available models.
    """
    from src.models.provider_resolution import ProviderResolver
    
    resolver = ProviderResolver()
    providers = resolver.list_available(configured_only=True)
    
    result = []
    for provider in providers:
        provider_info = {
            "name": provider.name,
            "api_mode": provider.api_mode,
            "default_model": provider.default_model,
            "supports_tools": provider.supports_tools,
            "supports_streaming": provider.supports_streaming,
        }
        result.append(provider_info)
    
    return result


async def get_user_model_display(telegram_id: int) -> str:
    """Get a human-readable string of user's current model selection.
    
    Returns:
        String like "openai:gpt-5.4-mini" or "anthropic:claude-sonnet (default)"
    """
    provider, model = await get_user_model(telegram_id)
    
    if model:
        return f"{provider}:{model}"
    else:
        # Get the default model for this provider
        from src.models.provider_resolution import ProviderResolver
        try:
            resolver = ProviderResolver()
            config = resolver.resolve(provider)
            return f"{provider}:{config.default_model} (default)"
        except Exception:
            return f"{provider}:default"


def format_provider_list(providers: list) -> str:
    """Format provider list for Telegram display.
    
    Args:
        providers: List of provider info dictionaries
    
    Returns:
        Formatted markdown string.
    """
    lines = ["🔌 *Available LLM Providers*\n"]
    
    for i, provider in enumerate(providers, 1):
        name = provider["name"]
        default = provider["default_model"]
        tools = "✅ tools" if provider["supports_tools"] else "❌ no tools"
        stream = "✅ stream" if provider["supports_streaming"] else "❌ no stream"
        
        lines.append(
            f"*{i}. {name}*\n"
            f"   Default: `{default}`\n"
            f"   {tools} | {stream}\n"
        )
    
    lines.append("\n*Usage:* `/model <provider>:<model>`")
    lines.append("*Example:* `/model anthropic:claude-sonnet-4-6`")
    
    return "\n".join(lines)
