"""Application settings loaded from environment variables via Pydantic."""

from pydantic_settings import BaseSettings
from pydantic import Field


class Settings(BaseSettings):
    """All settings from .env — app fails fast if required vars are missing."""

    # ── Required ──
    openai_api_key: str = Field(..., description="OpenAI API key")
    telegram_bot_token: str = Field(..., description="Telegram bot token from @BotFather")
    owner_telegram_id: int = Field(..., description="Owner's Telegram numeric user ID")
    database_url: str = Field(..., description="PostgreSQL async connection string")
    redis_url: str = Field(default="redis://redis:6379/0", description="Redis connection URL")
    qdrant_url: str = Field(default="http://qdrant:6333", description="Qdrant connection URL")

    # ── Multi-LLM Support (Option B Upgrade) ─────────────────────────────
    multi_llm_enabled: bool = Field(
        default=False,
        description="Enable multi-LLM provider support. When false, uses legacy OpenAI-only behavior."
    )
    default_llm_provider: str = Field(
        default="openai",
        description="Default provider when multi_llm_enabled is true (openai|anthropic|openrouter|google|local)"
    )

    # ── Provider API Keys (only needed when multi_llm_enabled=true) ───────
    anthropic_api_key: str = Field(default="", description="Anthropic API key for Claude models")
    openrouter_api_key: str = Field(default="", description="OpenRouter API key for 200+ models")
    google_api_key: str = Field(default="", description="Google Gemini API key")
    local_llm_base_url: str = Field(
        default="http://localhost:11434/v1",
        description="Base URL for local LLM (Ollama, vLLM, etc.)"
    )
    openrouter_image_enabled: bool = Field(
        default=False,
        description="Enable OpenRouter image generation skill and Telegram image replies"
    )

    # ── Per-Provider Cost Caps ────────────────────────────────────────────
    anthropic_daily_cost_cap_usd: float = Field(default=5.00)
    openrouter_daily_cost_cap_usd: float = Field(default=5.00)
    google_daily_cost_cap_usd: float = Field(default=5.00)

    # ── Models (role-based, GPT-5.4 family defaults) ──
    model_orchestrator: str = Field(default="gpt-5.4")
    model_code_gen: str = Field(default="gpt-5.4-mini")  # deprecated alias for model_coding
    model_coding: str = Field(default="gpt-5.4-mini")
    model_fast: str = Field(default="gpt-5.4-nano")
    model_general: str = Field(default="gpt-5.4-mini")
    model_safety: str = Field(default="gpt-5.4-nano")
    model_reflector: str = Field(default="gpt-5.4-nano")
    model_repair: str = Field(default="gpt-5.4-mini")
    model_routing: str = Field(default="gpt-5.4-nano")
    default_reasoning_effort: str = Field(default="medium")

    # ── Cost Control ──
    daily_cost_cap_usd: float = Field(default=5.00)
    monthly_cost_cap_usd: float = Field(default=100.00)

    # ── Persona ──
    default_assistant_name: str = Field(default="Atlas")
    default_persona_style: str = Field(default="friendly")

    # ── Timezone ──
    default_timezone: str = Field(default="America/New_York")

    # ── Security ──
    max_tool_calls_per_request: int = Field(default=20)
    agent_timeout_seconds: int = Field(default=120)
    tool_subprocess_timeout: int = Field(default=30)
    startup_migrations_enabled: bool = Field(default=False)

    # ── Agent-Managed Skills (Option B Upgrade) ──────────────────────────
    agent_managed_skills: bool = Field(
        default=False,
        description="Enable agent-proposed skill creation from successful workflows"
    )
    skill_auto_approve: bool = Field(
        default=False,
        description="Skip approval for agent-proposed skills (DANGER: use with caution)"
    )
    skill_confidence_threshold: float = Field(
        default=0.8,
        description="Minimum agent confidence to propose skill creation (0-1)"
    )
    skill_nudge_cooldown_hours: int = Field(
        default=24,
        description="Minimum hours between skill creation nudges"
    )

    # ── User Skills ──
    user_skills_dir: str = Field(default="src/user_skills", description="Directory for user-installed SKILL.md skills")

    # ── Meta-reflector (Wave 1.2) ──
    # Every N turns we run a holistic review that looks at recent reflector
    # outputs + the current auto-skill set and proposes consolidations,
    # retirements, or persona refinements. Mirrors Hermes's "every 15 tasks"
    # nudge. Set to 0 to disable.
    meta_reflector_interval: int = Field(
        default=15,
        description="Turn cadence for meta-reflector (0 disables).",
    )
    meta_reflector_window: int = Field(
        default=15,
        description="How many recent reflector outputs the meta-reflector reviews.",
    )

    # ── Skill FTS5 hybrid retrieval (Wave A.2) ──
    # When enabled, SkillRegistry.match_skills unions the existing keyword-tag
    # matcher with SQLite FTS5 BM25 results so fuzzy phrasings still surface
    # the right skill. Off by default to preserve current routing behavior;
    # opt-in once the catalog grows past a handful of skills.
    skill_fts_enabled: bool = Field(
        default=False,
        description="Enable hybrid FTS5+keyword skill retrieval in SkillRegistry.",
    )
    skill_fts_top_k: int = Field(
        default=5,
        description="How many FTS5 hits to merge per match_skills call.",
    )

    # ── Google Workspace (optional, Phase 2+) ──
    google_oauth_client_id: str = Field(default="")
    google_oauth_client_secret: str = Field(default="")
    workspace_mcp_url: str = Field(default="http://workspace-mcp:8000/mcp")
    # Token-encryption key for the workspace-mcp sidecar's persistent store
    # (FASTMCP_SERVER_AUTH_GOOGLE_JWT_SIGNING_KEY in the container's env).
    # Empty default is permitted so .env-less dev environments still load,
    # but main.py emits a startup warning when this is empty AND
    # google_oauth_client_id is set — the heartbeat would then false-positive
    # on every container rebuild. See ADR-2026-04-26-workspace-mcp-token-persistence.
    workspace_mcp_signing_key: str = Field(default="")

    # ── Web Search (optional) ──
    tavily_api_key: str = Field(default="")

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}


settings = Settings()
