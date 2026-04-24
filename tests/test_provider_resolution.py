"""Tests for provider resolution module (Phase 1 Option B Upgrade)."""

# ruff: noqa: E402

from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch
import pytest

# Mock redis before importing
sys.modules["redis"] = MagicMock()
sys.modules["redis.asyncio"] = MagicMock()


class TestProviderConfig:
    """Test the ProviderConfig dataclass."""

    def test_provider_config_basic(self):
        """Test basic ProviderConfig creation."""
        from src.models.provider_resolution import ProviderConfig
        
        config = ProviderConfig(
            name="test_provider",
            api_mode="openai",
            base_url="https://api.test.com/v1",
            supports_tools=True,
            supports_streaming=True,
            default_model="test-model",
            cost_per_1k_input=0.001,
            cost_per_1k_output=0.002,
            api_key_env_var="TEST_API_KEY",
        )
        
        assert config.name == "test_provider"
        assert config.api_mode == "openai"
        assert config.base_url == "https://api.test.com/v1"
        assert config.supports_tools is True
        assert config.cost_per_1k_input == 0.001

    def test_provider_config_api_key_from_env(self):
        """Test that API key is read from environment variable."""
        from src.models.provider_resolution import ProviderConfig
        
        with patch.dict("os.environ", {"TEST_KEY": "secret123"}):
            config = ProviderConfig(
                name="test",
                api_mode="openai",
                api_key_env_var="TEST_KEY",
            )
            assert config.api_key == "secret123"

    def test_provider_config_no_api_key_needed(self):
        """Test that local providers don't need API keys."""
        from src.models.provider_resolution import ProviderConfig
        
        config = ProviderConfig(
            name="local",
            api_mode="openai",
            api_key_env_var="",  # No API key needed
        )
        assert config.api_key is None
        assert config.is_configured is True  # Local LLMs always configured

    def test_provider_config_not_configured(self):
        """Test that missing API key makes provider not configured."""
        from src.models.provider_resolution import ProviderConfig
        
        with patch.dict("os.environ", {}, clear=True):
            config = ProviderConfig(
                name="test",
                api_mode="openai",
                api_key_env_var="MISSING_KEY",
            )
            assert config.api_key is None
            assert config.is_configured is False


class TestProviderResolver:
    """Test the ProviderResolver class."""

    def test_resolver_starts_with_builtin_providers(self):
        """Test that resolver initializes with built-in providers."""
        from src.models.provider_resolution import ProviderResolver
        
        resolver = ProviderResolver()
        providers = resolver.list_provider_names(configured_only=False)
        
        assert "openai" in providers
        assert "anthropic" in providers
        assert "openrouter" in providers
        assert "google" in providers
        assert "local" in providers

    def test_resolve_openai_provider(self):
        """Test resolving OpenAI provider."""
        from src.models.provider_resolution import ProviderResolver
        
        with patch.dict("os.environ", {"OPENAI_API_KEY": "sk-test123"}):
            resolver = ProviderResolver()
            config = resolver.resolve("openai")
            
            assert config.name == "openai"
            assert config.api_mode == "openai"
            assert config.base_url == "https://api.openai.com/v1"
            assert config.supports_tools is True
            assert config.api_key == "sk-test123"

    def test_resolve_anthropic_provider(self):
        """Test resolving Anthropic provider."""
        from src.models.provider_resolution import ProviderResolver
        
        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "sk-ant-test"}):
            resolver = ProviderResolver()
            config = resolver.resolve("anthropic")
            
            assert config.name == "anthropic"
            assert config.api_mode == "anthropic"
            assert config.api_key == "sk-ant-test"

    def test_resolve_openrouter_provider(self):
        """Test resolving OpenRouter provider."""
        from src.models.provider_resolution import ProviderResolver
        
        with patch.dict("os.environ", {"OPENROUTER_API_KEY": "sk-or-test"}):
            resolver = ProviderResolver()
            config = resolver.resolve("openrouter")
            
            assert config.name == "openrouter"
            assert config.api_mode == "openai"  # OpenRouter uses OpenAI-compatible API
            assert config.base_url == "https://openrouter.ai/api/v1"

    def test_resolve_google_provider(self):
        """Test resolving Google provider."""
        from src.models.provider_resolution import ProviderResolver
        
        with patch.dict("os.environ", {"GOOGLE_API_KEY": "google-test"}):
            resolver = ProviderResolver()
            config = resolver.resolve("google")
            
            assert config.name == "google"
            assert config.api_mode == "google"

    def test_resolve_local_provider(self):
        """Test resolving local LLM provider."""
        from src.models.provider_resolution import ProviderResolver
        
        # Local provider doesn't need API key
        resolver = ProviderResolver()
        config = resolver.resolve("local")
        
        assert config.name == "local"
        assert config.api_mode == "openai"
        assert config.api_key is None
        assert config.is_configured is True

    def test_resolve_unknown_provider_raises_error(self):
        """Test that resolving unknown provider raises ValueError."""
        from src.models.provider_resolution import ProviderResolver
        
        resolver = ProviderResolver()
        
        with pytest.raises(ValueError) as exc_info:
            resolver.resolve("unknown_provider")
        
        assert "Unknown provider" in str(exc_info.value)
        assert "unknown_provider" in str(exc_info.value)

    def test_resolve_unconfigured_provider_raises_error(self):
        """Test that resolving unconfigured provider raises ValueError."""
        from src.models.provider_resolution import ProviderResolver
        
        with patch.dict("os.environ", {}, clear=True):
            resolver = ProviderResolver()
            
            with pytest.raises(ValueError) as exc_info:
                resolver.resolve("anthropic")
            
            assert "not configured" in str(exc_info.value)
            assert "ANTHROPIC_API_KEY" in str(exc_info.value)

    def test_list_available_configured_only(self):
        """Test listing only configured providers."""
        from src.models.provider_resolution import ProviderResolver
        
        with patch.dict("os.environ", {"OPENAI_API_KEY": "test"}):
            resolver = ProviderResolver()
            configured = resolver.list_available(configured_only=True)
            
            # Only OpenAI is configured
            assert len(configured) >= 1
            assert any(p.name == "openai" for p in configured)

    def test_estimate_cost(self):
        """Test cost estimation."""
        from src.models.provider_resolution import ProviderResolver
        
        with patch.dict("os.environ", {"OPENAI_API_KEY": "test"}):
            resolver = ProviderResolver()
            cost = resolver.estimate_cost("openai", "gpt-5.4-mini", 1000, 500)
            
            # gpt-5.4-mini: input $0.0005/1k, output $0.0015/1k
            expected = (1000 / 1000 * 0.0005) + (500 / 1000 * 0.0015)
            assert cost == pytest.approx(expected, rel=1e-9)

    def test_get_default_provider(self):
        """Test getting default provider from settings."""
        from src.models.provider_resolution import ProviderResolver
        
        with patch("src.models.provider_resolution.settings") as mock_settings:
            mock_settings.default_llm_provider = "anthropic"
            
            resolver = ProviderResolver()
            default = resolver.get_default_provider()
            
            assert default == "anthropic"


class TestFeatureFlag:
    """Test multi-LLM feature flag behavior."""

    def test_multi_llm_disabled_by_default(self):
        """Test that multi-LLM is disabled by default."""
        from src.models.provider_resolution import ProviderResolver
        
        with patch("src.models.provider_resolution.settings") as mock_settings:
            # Simulate default settings (no multi_llm_enabled attribute)
            delattr(mock_settings, "multi_llm_enabled")
            
            resolver = ProviderResolver()
            assert resolver.is_enabled is False

    def test_multi_llm_enabled_when_set(self):
        """Test that multi-LLM can be enabled via settings."""
        from src.models.provider_resolution import ProviderResolver
        
        with patch("src.models.provider_resolution.settings") as mock_settings:
            mock_settings.multi_llm_enabled = True
            
            resolver = ProviderResolver()
            assert resolver.is_enabled is True


class TestLegacyCompatibility:
    """Test legacy compatibility functions."""

    def test_resolve_provider_for_legacy_when_disabled(self):
        """Test legacy resolver when multi-LLM is disabled."""
        from src.models.provider_resolution import (
            resolve_provider_for_legacy,
            ProviderResolver,
        )
        
        with patch.object(ProviderResolver, "is_enabled", False):
            with patch.dict("os.environ", {"OPENAI_API_KEY": "legacy-key"}):
                provider, api_key = resolve_provider_for_legacy()
                
                assert provider == "openai"
                assert api_key == "legacy-key"

    def test_resolve_provider_for_legacy_when_enabled(self):
        """Test legacy resolver when multi-LLM is enabled."""
        from src.models.provider_resolution import (
            resolve_provider_for_legacy,
            ProviderResolver,
        )
        
        with patch.object(ProviderResolver, "is_enabled", True):
            with patch("src.models.provider_resolution.settings") as mock_settings:
                mock_settings.default_llm_provider = "anthropic"
                
                with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "anthropic-key"}):
                    provider, api_key = resolve_provider_for_legacy()
                    
                    assert provider == "anthropic"
                    assert api_key == "anthropic-key"


class TestValidation:
    """Test validation functions."""

    def test_validate_provider_setup(self):
        """Test provider setup validation."""
        from src.models.provider_resolution import validate_provider_setup
        
        with patch.dict("os.environ", {"OPENAI_API_KEY": "test"}):
            providers = validate_provider_setup()
            
            assert "openai" in providers
            assert providers["openai"] is True
            # Anthropic not configured (no API key)
            assert providers.get("anthropic") is False

    def test_get_provider_status_message(self):
        """Test provider status message generation."""
        from src.models.provider_resolution import get_provider_status_message
        
        with patch.dict("os.environ", {"OPENAI_API_KEY": "test"}):
            message = get_provider_status_message()
            
            assert "🔌 LLM Provider Status:" in message
            assert "openai" in message
            assert "Multi-LLM support:" in message


class TestYAMLConfigLoading:
    """Test YAML configuration file loading."""

    def test_loads_custom_providers_from_yaml(self, tmp_path):
        """Test loading custom providers from YAML file."""
        from src.models.provider_resolution import ProviderResolver
        
        # Create a temporary YAML config file
        config_content = """
providers:
  - name: custom_provider
    api_mode: openai
    base_url: https://custom.api.com/v1
    supports_tools: true
    default_model: custom-model
    cost_per_1k_input: 0.01
    cost_per_1k_output: 0.02
    api_key_env_var: CUSTOM_API_KEY
"""
        config_file = tmp_path / "providers.yaml"
        config_file.write_text(config_content)
        
        with patch.dict("os.environ", {"CUSTOM_API_KEY": "custom-test-key"}):
            resolver = ProviderResolver(config_path=config_file)
            config = resolver.resolve("custom_provider")
            
            assert config.name == "custom_provider"
            assert config.base_url == "https://custom.api.com/v1"

    def test_yaml_env_var_substitution(self, tmp_path):
        """Test environment variable substitution in YAML config."""
        from src.models.provider_resolution import ProviderResolver
        
        config_content = """
providers:
  - name: dynamic_provider
    api_mode: openai
    base_url: ${TEST_BASE_URL}/v1
    api_key_env_var: TEST_API_KEY
"""
        config_file = tmp_path / "providers.yaml"
        config_file.write_text(config_content)
        
        with patch.dict("os.environ", {
            "TEST_BASE_URL": "https://dynamic.example.com",
            "TEST_API_KEY": "dynamic-key",
        }):
            resolver = ProviderResolver(config_path=config_file)
            config = resolver.resolve("dynamic_provider")
            
            assert config.base_url == "https://dynamic.example.com/v1"

    def test_handles_missing_yaml_gracefully(self):
        """Test that missing YAML file doesn't break resolver."""
        from pathlib import Path
        from src.models.provider_resolution import ProviderResolver
        
        # Pass non-existent path
        resolver = ProviderResolver(config_path=Path("/nonexistent/path.yaml"))
        
        # Should still have built-in providers
        providers = resolver.list_provider_names(configured_only=False)
        assert "openai" in providers
