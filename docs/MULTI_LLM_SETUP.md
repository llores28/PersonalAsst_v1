# Multi-LLM Setup Guide (Option B Upgrade)

This guide walks you through enabling multi-LLM support in Atlas, allowing you to use models from OpenAI, Anthropic, OpenRouter, Google, and local LLM providers.

## Overview

By default, Atlas uses OpenAI exclusively. With the Option B upgrade, you can:
- Switch between 5+ LLM providers
- Use 200+ models via OpenRouter
- Run local LLMs with Ollama or vLLM
- Set per-provider cost caps
- Automatically route tasks to the best provider

## Quick Start

### 1. Enable Multi-LLM Support

Add to your `.env`:
```bash
MULTI_LLM_ENABLED=true
DEFAULT_LLM_PROVIDER=openai  # or anthropic, openrouter, google, local
```

### 2. Add Provider API Keys

Add keys for providers you want to use:
```bash
# Keep your existing OpenAI key
OPENAI_API_KEY=sk-...

# Add new providers (optional)
ANTHROPIC_API_KEY=sk-ant-...         # For Claude models
OPENROUTER_API_KEY=sk-or-...          # For 200+ models
GOOGLE_API_KEY=...                    # For Gemini models
LOCAL_LLM_BASE_URL=http://localhost:11434/v1  # For Ollama
```

### 3. Restart Atlas
```bash
docker compose restart
```

### 4. Switch Providers in Telegram
```
/model list                    # Show available providers
/model openai:gpt-5.4-mini     # Switch to OpenAI
/model anthropic:claude-sonnet # Switch to Anthropic
```

## Supported Providers

| Provider | API Mode | Best For | Cost |
|----------|----------|----------|------|
| **OpenAI** | Native | Tool use, reliability | $$ |
| **Anthropic** | Native | Long context, reasoning | $$$ |
| **OpenRouter** | OpenAI-compatible | Model variety, fallbacks | $-$$$ |
| **Google** | Native | Cost-effective, fast | $ |
| **Local** | OpenAI-compatible | Privacy, no API costs | Free |

## Configuration Details

### Per-Provider Cost Caps

Prevent runaway costs per provider:
```bash
ANTHROPIC_DAILY_COST_CAP_USD=5.00
OPENROUTER_DAILY_COST_CAP_USD=5.00
GOOGLE_DAILY_COST_CAP_USD=5.00
```

When a provider hits its cap, Atlas falls back to OpenAI (if configured).

### Local LLM Setup

#### Ollama (Recommended)
1. Install Ollama: https://ollama.com
2. Pull a model: `ollama pull llama3.1`
3. Configure Atlas:
   ```bash
   MULTI_LLM_ENABLED=true
   DEFAULT_LLM_PROVIDER=local
   LOCAL_LLM_BASE_URL=http://host.docker.internal:11434/v1
   ```

#### vLLM
```bash
# Start vLLM server
python -m vLLM.entrypoints.openai_api_server --model meta-llama/Meta-Llama-3.1-70B

# Configure Atlas
LOCAL_LLM_BASE_URL=http://your-server:8000/v1
```

### Custom Providers

Add custom providers via `src/config/providers.yaml`:
```yaml
providers:
  - name: groq
    api_mode: openai
    base_url: https://api.groq.com/openai/v1
    supports_tools: true
    default_model: llama-3.1-70b
    api_key_env_var: GROQ_API_KEY
```

## Feature Flags

| Flag | Default | Description |
|------|---------|-------------|
| `MULTI_LLM_ENABLED` | `false` | Master switch for multi-LLM support |
| `DEFAULT_LLM_PROVIDER` | `openai` | Default when multi-LLM is on |
| `ANTHROPIC_API_KEY` | - | Claude API access |
| `OPENROUTER_API_KEY` | - | OpenRouter access (200+ models) |
| `GOOGLE_API_KEY` | - | Gemini API access |
| `LOCAL_LLM_BASE_URL` | `http://localhost:11434/v1` | Ollama/vLLM endpoint |

## Safety & Fallbacks

### Graceful Degradation
- If provider is unavailable → Falls back to OpenAI
- If model doesn't support tools → Warns user
- If cost cap exceeded → Blocks provider, uses default
- If API key missing → Provider not shown in `/model list`

### Validation
Check provider setup:
```python
from src.models.provider_resolution import get_provider_status_message
print(get_provider_status_message())
```

Output:
```
🔌 LLM Provider Status:
  ✅ openai
  ❌ anthropic
  ❌ openrouter
  ✅ local

Multi-LLM support: 🟢 enabled
Default provider: openai
```

## Troubleshooting

### "Provider not configured"
- Check API key is set: `echo $ANTHROPIC_API_KEY`
- Verify `.env` loaded: `docker compose logs atlas | grep "Loaded .env"`

### "Model doesn't support tools"
- Some OpenRouter models don't support function calling
- Switch to a tool-compatible model: `/model openrouter:anthropic/claude-3.5-sonnet`

### Local LLM not connecting
- Check Ollama is running: `ollama list`
- Verify URL is accessible from Docker: `docker compose exec atlas curl http://host.docker.internal:11434`
- Use host IP instead of localhost: `LOCAL_LLM_BASE_URL=http://192.168.1.100:11434/v1`

### Costs higher than expected
- Check per-provider caps are set
- Review cost tracking: `/stats` in Telegram
- Lower cost cap: `ANTHROPIC_DAILY_COST_CAP_USD=2.00`

## Migration from Single-LLM

### Option 1: Gradual (Recommended)
1. Keep `MULTI_LLM_ENABLED=false` (default)
2. Add new provider keys to `.env`
3. Test with `/model` command
4. Enable: `MULTI_LLM_ENABLED=true`

### Option 2: Full Switch
1. Add all provider keys
2. Set `MULTI_LLM_ENABLED=true`
3. Set `DEFAULT_LLM_PROVIDER=your-choice`
4. Restart

### Rollback
```bash
MULTI_LLM_ENABLED=false
# Or remove the line (defaults to false)
docker compose restart atlas
```

## Architecture

```
User Request
    ↓
Orchestrator
    ↓
ProviderResolver.resolve(provider, model)
    ↓
┌─────────────────────────────────────────┐
│ ProviderConfig                          │
│  - api_mode (openai/anthropic/google)  │
│  - api_key                             │
│  - base_url                            │
│  - supports_tools                      │
└─────────────────────────────────────────┘
    ↓
AgentFactory.create_agent(api_mode)
    ↓
LLM API Call
```

## Security Notes

- API keys stored in `.env` only (gitignored)
- Keys never logged or exposed to agents
- Per-provider cost caps prevent runaway spending
- Local LLMs keep data on your machine
- Provider switching requires Telegram auth (no unauthorized changes)

## Roadmap

**Phase 2+ (Coming Soon)**:
- Automatic provider selection based on task type
- Smart fallbacks when providers fail
- Cost-optimized routing
- Provider performance metrics

---

**Version**: Phase 1 (Provider Resolution)
**Last Updated**: April 15, 2026
**Status**: ✅ Ready for Testing
