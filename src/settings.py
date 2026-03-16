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

    # ── Models ──
    model_orchestrator: str = Field(default="gpt-5.4-mine")
    model_code_gen: str = Field(default="gpt-5.3-codex")
    model_fast: str = Field(default="gpt-4.1-nano")
    model_general: str = Field(default="gpt-5.4")

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

    # ── Google Workspace (optional, Phase 2+) ──
    google_oauth_client_id: str = Field(default="")
    google_oauth_client_secret: str = Field(default="")
    workspace_mcp_url: str = Field(default="http://workspace-mcp:8080/mcp")

    # ── Web Search (optional) ──
    tavily_api_key: str = Field(default="")

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
