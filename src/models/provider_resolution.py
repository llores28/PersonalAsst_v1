"""Provider resolution — maps (provider, model) → (api_mode, api_key, base_url).

This module enables multi-LLM support for Atlas, allowing users to switch
between OpenAI, Anthropic, OpenRouter, Google, and local LLM providers.

Usage:
    from src.models.provider_resolution import ProviderResolver, ProviderConfig
    resolver = ProviderResolver()
    config = resolver.resolve("anthropic", "claude-sonnet-4-6")
    # config.api_mode = "anthropic"
    # config.api_key = settings.anthropic_api_key
    # config.base_url = None

Feature flag: MULTI_LLM_ENABLED (default: false)
When disabled, Atlas uses legacy OpenAI-only behavior.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Literal, Optional
from pathlib import Path

import yaml

from src.settings import settings


# ── Types ──────────────────────────────────────────────────────────────

ApiMode = Literal["openai", "anthropic", "google"]


# ── Configuration Dataclass ─────────────────────────────────────────────

@dataclass(frozen=True)
class ProviderConfig:
    """Configuration for a specific LLM provider.
    
    Attributes:
        name: Provider identifier (e.g., "openai", "anthropic")
        api_mode: API compatibility mode (openai/anthropic/google)
        base_url: Optional custom endpoint URL (for local/proxy)
        supports_tools: Whether the provider supports function calling
        supports_streaming: Whether the provider supports streaming responses
        default_model: Fallback model if none specified
        cost_per_1k_input: Cost per 1k input tokens in USD
        cost_per_1k_output: Cost per 1k output tokens in USD
        api_key_env_var: Environment variable name for the API key
    """
    name: str
    api_mode: ApiMode
    base_url: Optional[str] = None
    supports_tools: bool = True
    supports_streaming: bool = True
    default_model: str = ""
    cost_per_1k_input: float = 0.0
    cost_per_1k_output: float = 0.0
    api_key_env_var: str = ""

    @property
    def api_key(self) -> Optional[str]:
        """Get the API key from environment variables."""
        if not self.api_key_env_var:
            return None
        return os.getenv(self.api_key_env_var)

    @property
    def is_configured(self) -> bool:
        """Check if this provider has a valid API key configured."""
        if self.api_key_env_var:
            return bool(self.api_key)
        # Local LLMs don't need API keys
        return True


# ── Built-in Provider Presets ────────────────────────────────────────

_PROVIDER_PRESETS: dict[str, ProviderConfig] = {
    "openai": ProviderConfig(
        name="openai",
        api_mode="openai",
        base_url="https://api.openai.com/v1",
        supports_tools=True,
        supports_streaming=True,
        default_model="gpt-5.4-mini",
        cost_per_1k_input=0.0005,  # gpt-5.4-mini
        cost_per_1k_output=0.0015,
        api_key_env_var="OPENAI_API_KEY",
    ),
    "anthropic": ProviderConfig(
        name="anthropic",
        api_mode="anthropic",
        base_url="https://api.anthropic.com",
        supports_tools=True,
        supports_streaming=True,
        default_model="claude-sonnet-4-6",
        cost_per_1k_input=0.003,
        cost_per_1k_output=0.015,
        api_key_env_var="ANTHROPIC_API_KEY",
    ),
    "openrouter": ProviderConfig(
        name="openrouter",
        api_mode="openai",  # OpenRouter uses OpenAI-compatible API
        base_url="https://openrouter.ai/api/v1",
        supports_tools=True,
        supports_streaming=True,
        default_model="anthropic/claude-sonnet-4",
        cost_per_1k_input=0.003,  # Fallback only — per-model pricing via estimate_cost_from_model()
        cost_per_1k_output=0.015,
        api_key_env_var="OPENROUTER_API_KEY",
    ),
    "google": ProviderConfig(
        name="google",
        api_mode="google",
        base_url="https://generativelanguage.googleapis.com",
        supports_tools=True,
        supports_streaming=True,
        default_model="gemini-2.0-flash",
        cost_per_1k_input=0.0001,  # Gemini Flash
        cost_per_1k_output=0.0004,
        api_key_env_var="GOOGLE_API_KEY",
    ),
    "local": ProviderConfig(
        name="local",
        api_mode="openai",  # Local LLMs typically use OpenAI-compatible API
        base_url="${LOCAL_LLM_BASE_URL}",  # Must be set by user
        supports_tools=True,  # Assumes Ollama/vLLM with tool support
        supports_streaming=True,
        default_model="llama3.1",
        cost_per_1k_input=0.0,  # Free (running locally)
        cost_per_1k_output=0.0,
        api_key_env_var="",  # Local LLMs typically don't need API keys
    ),
}


# ── Provider Resolver ─────────────────────────────────────────────────

class ProviderResolver:
    """Resolves provider/model tuples to configuration.
    
    Supports loading additional providers from YAML configuration files.
    Falls back to built-in presets for common providers.
    
    Usage:
        resolver = ProviderResolver()
        
        # Resolve by provider name (uses default model)
        config = resolver.resolve("anthropic")
        
        # Resolve with specific model
        config = resolver.resolve("openrouter", "anthropic/claude-3-opus")
        
        # List all available providers
        available = resolver.list_available()
        
        # Check if multi-LLM is enabled
        if not resolver.is_enabled:
            # Fall back to legacy OpenAI-only behavior
            config = resolver.resolve("openai")
    """

    def __init__(self, config_path: Optional[Path] = None):
        """Initialize the resolver.
        
        Args:
            config_path: Optional path to YAML provider configuration file.
                        If not provided, uses src/config/providers.yaml if it exists.
        """
        self._providers: dict[str, ProviderConfig] = dict(_PROVIDER_PRESETS)
        self._load_config(config_path)

    @property
    def is_enabled(self) -> bool:
        """Check if multi-LLM support is enabled via feature flag."""
        # Check for MULTI_LLM_ENABLED in settings (will be added to settings.py)
        return getattr(settings, 'multi_llm_enabled', False)

    def _load_config(self, config_path: Optional[Path] = None) -> None:
        """Load additional provider configurations from YAML file."""
        if config_path is None:
            # Try default location
            default_path = Path(__file__).parent.parent / "config" / "providers.yaml"
            if default_path.exists():
                config_path = default_path
        
        if config_path is None or not config_path.exists():
            return

        try:
            with open(config_path, 'r') as f:
                config_data = yaml.safe_load(f)
            
            if not config_data or 'providers' not in config_data:
                return

            for provider_data in config_data['providers']:
                name = provider_data.get('name')
                if not name:
                    continue
                
                # Resolve base_url template (e.g., ${LOCAL_LLM_BASE_URL})
                base_url = provider_data.get('base_url', '')
                if base_url and '${' in base_url:
                    base_url = self._resolve_template(base_url)

                config = ProviderConfig(
                    name=name,
                    api_mode=provider_data.get('api_mode', 'openai'),
                    base_url=base_url or None,
                    supports_tools=provider_data.get('supports_tools', True),
                    supports_streaming=provider_data.get('supports_streaming', True),
                    default_model=provider_data.get('default_model', ''),
                    cost_per_1k_input=provider_data.get('cost_per_1k_input', 0.0),
                    cost_per_1k_output=provider_data.get('cost_per_1k_output', 0.0),
                    api_key_env_var=provider_data.get('api_key_env_var', ''),
                )
                self._providers[name] = config

        except Exception as e:
            # Log but don't fail — built-in presets still work
            import logging
            logging.getLogger(__name__).warning(f"Failed to load provider config from {config_path}: {e}")

    def _resolve_template(self, template: str) -> str:
        """Resolve environment variable templates in strings."""
        import re
        
        def replace_var(match: re.Match) -> str:
            var_name = match.group(1)
            return os.getenv(var_name, match.group(0))  # Keep original if not found
        
        return re.sub(r'\$\{(\w+)\}', replace_var, template)

    def resolve(
        self,
        provider: str,
        model: Optional[str] = None,
    ) -> ProviderConfig:
        """Resolve a provider and optional model to configuration.
        
        Args:
            provider: Provider name (e.g., "openai", "anthropic", "openrouter")
            model: Optional specific model ID. If not provided, uses provider default.
        
        Returns:
            ProviderConfig with all necessary connection details.
        
        Raises:
            ValueError: If provider is not recognized or not configured.
        """
        if provider not in self._providers:
            available = ', '.join(self._providers.keys())
            raise ValueError(f"Unknown provider: '{provider}'. Available: {available}")

        config = self._providers[provider]
        
        # Check if provider is properly configured (has API key if required)
        if not config.is_configured:
            raise ValueError(
                f"Provider '{provider}' is not configured. "
                f"Set {config.api_key_env_var} environment variable."
            )

        return config

    def list_available(self, configured_only: bool = True) -> list[ProviderConfig]:
        """List all available providers.
        
        Args:
            configured_only: If True, only return providers with valid API keys.
        
        Returns:
            List of ProviderConfig objects.
        """
        providers = list(self._providers.values())
        if configured_only:
            providers = [p for p in providers if p.is_configured]
        return providers

    def list_provider_names(self, configured_only: bool = True) -> list[str]:
        """List names of all available providers."""
        return [p.name for p in self.list_available(configured_only)]

    def get_default_provider(self) -> str:
        """Get the default provider name from settings."""
        return getattr(settings, 'default_llm_provider', 'openai')

    def estimate_cost(
        self,
        provider: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
    ) -> float:
        """Estimate API cost for a request.
        
        Args:
            provider: Provider name
            model: Model ID
            input_tokens: Number of input tokens
            output_tokens: Number of output tokens
        
        Returns:
            Estimated cost in USD.
        """
        config = self.resolve(provider, model)
        input_cost = (input_tokens / 1000) * config.cost_per_1k_input
        output_cost = (output_tokens / 1000) * config.cost_per_1k_output
        return input_cost + output_cost


def validate_provider_setup() -> dict[str, bool]:
    """Validate that all configured providers have proper API keys.

    Returns:
        Dictionary mapping provider names to boolean (True if configured).
    """
    resolver = ProviderResolver()
    result = {}
    for name in resolver.list_provider_names(configured_only=False):
        try:
            config = resolver.resolve(name)
            result[name] = config.is_configured
        except ValueError:
            # Provider not configured (missing API key)
            result[name] = False
    return result


def get_provider_status_message() -> str:
    """Generate a human-readable status message of provider configurations."""
    resolver = ProviderResolver()
    providers = validate_provider_setup()
    
    lines = ["🔌 LLM Provider Status:"]
    for name, is_configured in providers.items():
        status = "✅" if is_configured else "❌"
        lines.append(f"  {status} {name}")
    
    enabled = resolver.is_enabled
    lines.append(f"\nMulti-LLM support: {'🟢 enabled' if enabled else '🔴 disabled'}")
    
    if enabled:
        default = resolver.get_default_provider()
        lines.append(f"Default provider: {default}")
    else:
        lines.append("Set MULTI_LLM_ENABLED=true to enable provider switching")
    
    return "\n".join(lines)


# ── Legacy Compatibility ───────────────────────────────────────────────

def resolve_provider_for_legacy(
    provider: Optional[str] = None,
    model: Optional[str] = None,
) -> tuple[str, Optional[str]]:
    """Resolve provider for legacy OpenAI-only code.
    
    When multi-LLM is disabled, this always returns ("openai", None).
    When enabled, it resolves the requested provider or default.
    
    Returns:
        Tuple of (provider_name, api_key). api_key is None if not needed.
    """
    resolver = ProviderResolver()
    
    if not resolver.is_enabled:
        return ("openai", os.getenv("OPENAI_API_KEY"))
    
    provider_name = provider or resolver.get_default_provider()
    config = resolver.resolve(provider_name, model)
    
    return (provider_name, config.api_key)
