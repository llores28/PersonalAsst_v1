"""API client factory for multi-LLM support.

Creates appropriate API clients for OpenAI, Anthropic, Google, and other providers.
Handles authentication, base URLs, and provider-specific initialization.

Usage:
    from src.models.api_clients import get_client_for_provider
    
    client = await get_client_for_provider("anthropic")
    response = await client.chat.completions.create(...)
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any, Optional

from src.models.provider_resolution import ProviderResolver, ProviderConfig

logger = logging.getLogger(__name__)

# Cache for API clients (by provider name)
_client_cache: dict[str, Any] = {}


async def get_client_for_provider(
    provider: str,
    model: Optional[str] = None,
) -> Any:
    """Get or create an API client for the specified provider.
    
    Args:
        provider: Provider name (e.g., "openai", "anthropic", "openrouter")
        model: Optional specific model (used for some provider-specific initialization)
    
    Returns:
        Initialized API client appropriate for the provider.
    
    Raises:
        ValueError: If provider is not supported or not configured.
        ImportError: If required provider library is not installed.
    
    Example:
        >>> client = await get_client_for_provider("anthropic")
        >>> print(type(client))
        <class 'anthropic.AsyncAnthropic'>
    """
    # Check cache first
    cache_key = f"{provider}:{model or 'default'}"
    if cache_key in _client_cache:
        return _client_cache[cache_key]
    
    # Resolve provider configuration
    resolver = ProviderResolver()
    config = resolver.resolve(provider, model)
    
    # Create client based on API mode
    client = await _create_client_for_api_mode(config)
    
    # Cache the client
    _client_cache[cache_key] = client
    logger.debug("Created and cached %s client for model %s", provider, model or "default")
    
    return client


async def _create_client_for_api_mode(config: ProviderConfig) -> Any:
    """Create the appropriate client based on API mode.
    
    Args:
        config: Provider configuration with api_mode
    
    Returns:
        Initialized client instance.
    """
    if config.api_mode == "openai":
        return await _create_openai_compatible_client(config)
    elif config.api_mode == "anthropic":
        return await _create_anthropic_client(config)
    elif config.api_mode == "google":
        return await _create_google_client(config)
    else:
        raise ValueError(f"Unsupported API mode: {config.api_mode}")


async def _create_openai_compatible_client(config: ProviderConfig) -> Any:
    """Create an OpenAI-compatible client.
    
    This works for:
    - OpenAI (native)
    - OpenRouter (OpenAI-compatible)
    - Local LLMs (Ollama, vLLM with OpenAI-compatible API)
    - Any other OpenAI-compatible endpoint
    """
    try:
        from openai import AsyncOpenAI
    except ImportError:
        raise ImportError(
            "OpenAI library not installed. "
            "Install with: pip install openai"
        )
    
    client_kwargs = {
        "api_key": config.api_key or "not-needed-for-local",
    }
    
    # Use custom base URL if provided
    if config.base_url:
        client_kwargs["base_url"] = config.base_url
        logger.debug("Using custom base URL: %s", config.base_url)
    
    # Special handling for OpenRouter
    if config.name == "openrouter":
        client_kwargs["default_headers"] = {
            "HTTP-Referer": "https://github.com/llores28/PersonalAsst",
            "X-Title": "Atlas Personal Assistant",
        }
    
    client = AsyncOpenAI(**client_kwargs)
    return client


async def _create_anthropic_client(config: ProviderConfig) -> Any:
    """Create an Anthropic client for Claude models."""
    try:
        from anthropic import AsyncAnthropic
    except ImportError:
        raise ImportError(
            "Anthropic library not installed. "
            "Install with: pip install anthropic"
        )
    
    if not config.api_key:
        raise ValueError("Anthropic API key is required")
    
    client = AsyncAnthropic(
        api_key=config.api_key,
        base_url=config.base_url,
    )
    return client


async def _create_google_client(config: ProviderConfig) -> Any:
    """Create a Google Gemini client."""
    try:
        from google import genai
    except ImportError:
        raise ImportError(
            "Google GenAI library not installed. "
            "Install with: pip install google-genai"
        )
    
    if not config.api_key:
        raise ValueError("Google API key is required")
    
    client = genai.Client(api_key=config.api_key)
    return client


def clear_client_cache(provider: Optional[str] = None) -> None:
    """Clear the API client cache.
    
    Args:
        provider: If specified, only clear cache for this provider.
                 If None, clear all cached clients.
    """
    global _client_cache
    
    if provider is None:
        _client_cache.clear()
        logger.info("Cleared all API client caches")
    else:
        # Clear all entries for this provider
        keys_to_remove = [k for k in _client_cache.keys() if k.startswith(f"{provider}:")]
        for key in keys_to_remove:
            del _client_cache[key]
        logger.info("Cleared API client cache for %s", provider)


async def get_client_for_user(telegram_id: int) -> tuple[Any, str, Optional[str]]:
    """Get the appropriate API client for a user based on their preferences.
    
    Args:
        telegram_id: User's Telegram ID
    
    Returns:
        Tuple of (client, provider, model)
    
    Example:
        >>> client, provider, model = await get_client_for_user(12345)
        >>> print(f"Using {provider}:{model or 'default'}")
    """
    from src.models.user_preferences import get_user_model
    
    provider, model = await get_user_model(telegram_id)
    client = await get_client_for_provider(provider, model)
    
    return client, provider, model


# Provider-specific utility functions

async def call_anthropic_with_tools(
    client: Any,
    model: str,
    messages: list[dict],
    tools: Optional[list[dict]] = None,
    temperature: float = 0.7,
    max_tokens: int = 4096,
) -> dict:
    """Call Anthropic API with tool support.
    
    Anthropic has a different tool format than OpenAI, so we need to transform.
    """
    from anthropic.types import MessageParam, ToolParam
    
    # Convert OpenAI-style messages to Anthropic format
    anthropic_messages = []
    for msg in messages:
        role = msg.get("role", "user")
        content = msg.get("content", "")
        
        if role == "system":
            # System messages go in a separate parameter for Anthropic
            continue
        
        anthropic_messages.append(MessageParam(role=role, content=content))
    
    # Extract system message if present
    system_message = None
    for msg in messages:
        if msg.get("role") == "system":
            system_message = msg.get("content")
            break
    
    # Convert tools to Anthropic format
    anthropic_tools = None
    if tools:
        anthropic_tools = [
            ToolParam(
                name=tool.get("function", {}).get("name", "unknown"),
                description=tool.get("function", {}).get("description", ""),
                input_schema=tool.get("function", {}).get("parameters", {}),
            )
            for tool in tools
        ]
    
    kwargs = {
        "model": model,
        "messages": anthropic_messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    
    if system_message:
        kwargs["system"] = system_message
    
    if anthropic_tools:
        kwargs["tools"] = anthropic_tools
    
    response = await client.messages.create(**kwargs)

    # Convert Anthropic response to OpenAI-compatible format
    # Handle both text and tool_use content blocks
    content_text = ""
    tool_calls = []

    for block in response.content:
        if block.type == "text":
            content_text += block.text
        elif block.type == "tool_use":
            # Convert Anthropic tool_use to OpenAI tool_calls format
            tool_calls.append({
                "id": block.id,
                "type": "function",
                "function": {
                    "name": block.name,
                    "arguments": json.dumps(block.input) if block.input else "{}",
                },
            })

    # Determine finish reason
    finish_reason = "stop"
    if response.stop_reason == "tool_use":
        finish_reason = "tool_calls"
    elif response.stop_reason:
        finish_reason = response.stop_reason

    return {
        "choices": [{
            "message": {
                "content": content_text,
                "role": "assistant",
                "tool_calls": tool_calls if tool_calls else None,
            },
            "finish_reason": finish_reason,
        }],
        "model": model,
        "usage": {
            "prompt_tokens": response.usage.input_tokens if response.usage else 0,
            "completion_tokens": response.usage.output_tokens if response.usage else 0,
            "total_tokens": (
                response.usage.input_tokens + response.usage.output_tokens
                if response.usage else 0
            ),
        },
    }


async def call_google_with_tools(
    client: Any,
    model: str,
    messages: list[dict],
    tools: Optional[list[dict]] = None,
    temperature: float = 0.7,
    max_tokens: int = 4096,
) -> dict:
    """Call Google Gemini API with tool support."""
    try:
        from google.genai import types
    except ImportError:
        raise ImportError("Google GenAI library not installed")
    
    # Convert OpenAI-style messages to Gemini format
    contents = []
    system_instruction = None
    
    for msg in messages:
        role = msg.get("role", "user")
        content = msg.get("content", "")
        
        if role == "system":
            system_instruction = content
            continue
        
        gemini_role = "model" if role == "assistant" else role
        contents.append(types.Content(role=gemini_role, parts=[types.Part(text=content)]))
    
    # Convert tools to Gemini format
    gemini_tools = None
    if tools:
        gemini_tools = [
            types.Tool(
                function_declarations=[
                    types.FunctionDeclaration(
                        name=tool.get("function", {}).get("name", "unknown"),
                        description=tool.get("function", {}).get("description", ""),
                        parameters=tool.get("function", {}).get("parameters", {}),
                    )
                    for tool in tools
                ]
            )
        ]
    
    # Create config
    config = types.GenerateContentConfig(
        temperature=temperature,
        max_output_tokens=max_tokens,
    )
    
    if system_instruction:
        config.system_instruction = system_instruction
    
    if gemini_tools:
        config.tools = gemini_tools
    
    # Call the API
    response = await client.aio.models.generate_content(
        model=model,
        contents=contents,
        config=config,
    )

    # Convert to OpenAI-compatible format
    # Handle function calls from Gemini response
    content_text = ""
    tool_calls = []
    finish_reason = "stop"

    # Extract content and function calls from response
    if response.candidates and response.candidates[0].content:
        candidate = response.candidates[0]
        parts = candidate.content.parts or []

        for part in parts:
            if hasattr(part, 'text') and part.text:
                content_text += part.text
            elif hasattr(part, 'function_call') and part.function_call:
                # Convert Gemini function call to OpenAI tool_calls format
                func_call = part.function_call
                tool_calls.append({
                    "id": f"call_{uuid.uuid4().hex[:24]}",
                    "type": "function",
                    "function": {
                        "name": func_call.name,
                        "arguments": json.dumps(dict(func_call.args)) if func_call.args else "{}",
                    },
                })

        # Determine finish reason from candidate
        if candidate.finish_reason:
            if candidate.finish_reason.name == "STOP":
                finish_reason = "stop"
            elif candidate.finish_reason.name in ["MAX_TOKENS", "OTHER"]:
                finish_reason = "length"
            elif tool_calls:
                finish_reason = "tool_calls"

    # Get token counts if available
    usage_metadata = response.usage_metadata if hasattr(response, 'usage_metadata') else None
    prompt_tokens = usage_metadata.prompt_token_count if usage_metadata and hasattr(usage_metadata, 'prompt_token_count') else 0
    completion_tokens = usage_metadata.candidates_token_count if usage_metadata and hasattr(usage_metadata, 'candidates_token_count') else 0

    return {
        "choices": [{
            "message": {
                "content": content_text or "",
                "role": "assistant",
                "tool_calls": tool_calls if tool_calls else None,
            },
            "finish_reason": finish_reason,
        }],
        "model": model,
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        },
    }
