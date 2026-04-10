"""SQLAlchemy models — resolves PRD gap A1 (database schema)."""

from datetime import datetime, date
from typing import Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.sql import func


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False)
    display_name: Mapped[Optional[str]] = mapped_column(String(100))
    timezone: Mapped[str] = mapped_column(String(50), default="UTC")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    is_owner: Mapped[bool] = mapped_column(Boolean, default=False)

    persona_versions: Mapped[list["PersonaVersion"]] = relationship(
        back_populates="user"
    )
    audit_logs: Mapped[list["AuditLog"]] = relationship(back_populates="user")
    persona_interviews: Mapped[list["PersonaInterview"]] = relationship(
        back_populates="user"
    )


class PersonaVersion(Base):
    __tablename__ = "persona_versions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    assistant_name: Mapped[str] = mapped_column(
        String(50), nullable=False, default="Atlas"
    )
    personality: Mapped[dict] = mapped_column(JSONB, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    change_reason: Mapped[Optional[str]] = mapped_column(Text)

    user: Mapped["User"] = relationship(back_populates="persona_versions")


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"))
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    direction: Mapped[str] = mapped_column(String(10), nullable=False)
    platform: Mapped[str] = mapped_column(String(20), nullable=False, default="telegram")
    message_text: Mapped[Optional[str]] = mapped_column(Text)
    agent_name: Mapped[Optional[str]] = mapped_column(String(50))
    tools_used: Mapped[Optional[dict]] = mapped_column(JSONB)
    model_used: Mapped[Optional[str]] = mapped_column(String(50))
    token_count: Mapped[Optional[int]] = mapped_column(Integer)
    cost_usd: Mapped[Optional[float]] = mapped_column(Numeric(10, 6))
    error: Mapped[Optional[str]] = mapped_column(Text)
    duration_ms: Mapped[Optional[int]] = mapped_column(Integer)

    user: Mapped[Optional["User"]] = relationship(back_populates="audit_logs")


class DailyCost(Base):
    __tablename__ = "daily_costs"
    __table_args__ = (UniqueConstraint("date", "user_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    date: Mapped[date] = mapped_column(Date, nullable=False, server_default=func.current_date())
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    total_tokens: Mapped[int] = mapped_column(Integer, default=0)
    total_cost_usd: Mapped[float] = mapped_column(Numeric(10, 4), default=0)
    request_count: Mapped[int] = mapped_column(Integer, default=0)


class Tool(Base):
    __tablename__ = "tools"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    tool_type: Mapped[str] = mapped_column(String(20), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    manifest_path: Mapped[str] = mapped_column(String(500), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    created_by: Mapped[str] = mapped_column(String(50), default="tool_factory")
    last_used_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    use_count: Mapped[int] = mapped_column(Integer, default=0)


class ScheduledTask(Base):
    __tablename__ = "scheduled_tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    apscheduler_id: Mapped[str] = mapped_column(String(200), unique=True, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    natural_lang: Mapped[Optional[str]] = mapped_column(Text)
    trigger_type: Mapped[str] = mapped_column(String(20), nullable=False)
    trigger_config: Mapped[dict] = mapped_column(JSONB, nullable=False)
    job_function: Mapped[str] = mapped_column(String(200), nullable=False)
    job_args: Mapped[Optional[dict]] = mapped_column(JSONB)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    last_run_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    next_run_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))


class AllowedUser(Base):
    __tablename__ = "allowed_users"

    telegram_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    added_by: Mapped[Optional[int]] = mapped_column(BigInteger)
    added_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    role: Mapped[str] = mapped_column(String(20), default="user")


class OpenAIModel(Base):
    __tablename__ = "openai_models"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    model_id: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    display_name: Mapped[str] = mapped_column(String(150), nullable=False)
    family: Mapped[str] = mapped_column(String(50), nullable=False)
    category: Mapped[str] = mapped_column(String(30), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    strengths: Mapped[Optional[dict]] = mapped_column(JSONB)
    use_cases: Mapped[Optional[dict]] = mapped_column(JSONB)
    context_window: Mapped[Optional[int]] = mapped_column(Integer)
    max_output_tokens: Mapped[Optional[int]] = mapped_column(Integer)
    reasoning_effort_levels: Mapped[Optional[dict]] = mapped_column(JSONB)
    pricing_input: Mapped[Optional[float]] = mapped_column(Numeric(10, 4))
    pricing_cached_input: Mapped[Optional[float]] = mapped_column(Numeric(10, 4))
    pricing_output: Mapped[Optional[float]] = mapped_column(Numeric(10, 4))
    pricing_unit: Mapped[str] = mapped_column(
        String(30), nullable=False, default="per_1m_tokens"
    )
    pricing_notes: Mapped[Optional[str]] = mapped_column(Text)
    api_docs_url: Mapped[Optional[str]] = mapped_column(String(500))
    api_endpoint: Mapped[Optional[str]] = mapped_column(String(200))
    is_deprecated: Mapped[bool] = mapped_column(Boolean, default=False)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    atlas_role: Mapped[Optional[str]] = mapped_column(String(50))
    notes: Mapped[Optional[str]] = mapped_column(Text)
    added_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class PersonaInterview(Base):
    """Stores persona interview sessions and their transcripts.

    Each row represents one interview session (1–3) for a user.
    The transcript is a list of {role, content} dicts.
    The synthesis is the LLM-generated personality analysis for that session.
    """
    __tablename__ = "persona_interviews"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    session_number: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="in_progress"
    )
    transcript: Mapped[dict] = mapped_column(JSONB, nullable=False, default=list)
    synthesis: Mapped[Optional[dict]] = mapped_column(JSONB)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    user: Mapped["User"] = relationship(back_populates="persona_interviews")


class OwnerSecurityConfig(Base):
    __tablename__ = "owner_security_config"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    pin_hash: Mapped[Optional[str]] = mapped_column(String(128))
    security_qa: Mapped[Optional[dict]] = mapped_column(JSONB)
    challenge_ttl: Mapped[int] = mapped_column(Integer, default=60)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class MarketplaceSkill(Base):
    """Registry entry for a skill in the marketplace."""
    __tablename__ = "marketplace_skills"
    __table_args__ = (UniqueConstraint("id", "version"),)

    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    author: Mapped[str] = mapped_column(String(100), nullable=False)
    version: Mapped[str] = mapped_column(String(20), nullable=False, default="1.0.0")
    tags: Mapped[list] = mapped_column(JSONB, default=list)

    # Source location
    git_url: Mapped[Optional[str]] = mapped_column(String(500))
    filesystem_path: Mapped[Optional[str]] = mapped_column(String(500))

    # Skill metadata
    skill_group: Mapped[str] = mapped_column(String(50), default="user")
    is_knowledge_only: Mapped[bool] = mapped_column(Boolean, default=False)
    requires_connection: Mapped[bool] = mapped_column(Boolean, default=False)

    # Stats
    install_count: Mapped[int] = mapped_column(Integer, default=0)
    rating_avg: Mapped[Optional[float]] = mapped_column(Numeric(3, 2))
    rating_count: Mapped[int] = mapped_column(Integer, default=0)

    # Metadata
    is_official: Mapped[bool] = mapped_column(Boolean, default=False)
    is_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class InstalledSkill(Base):
    """User's installed skill instances."""
    __tablename__ = "installed_skills"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    marketplace_skill_id: Mapped[str] = mapped_column(String(100), nullable=False)

    # Installation state
    version_installed: Mapped[str] = mapped_column(String(20), nullable=False)
    filesystem_path: Mapped[str] = mapped_column(String(500), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    config: Mapped[dict] = mapped_column(JSONB, default=dict)  # User overrides

    installed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    last_used_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
