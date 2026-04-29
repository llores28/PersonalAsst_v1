"""Atlas Dashboard API — observability + organization management.

Phase A: Observability endpoints (read from bot's real DB tables)
Phase B: Organization CRUD (project containers with agent teams)
"""

import asyncio
import hashlib
import hmac
import logging
import secrets
from datetime import date, datetime, timedelta, timezone
from typing import List, Optional
import os
import importlib

import redis.asyncio as aioredis
from fastapi import Depends, FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi import Header, Query
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from slowapi import Limiter
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings
from sqlalchemy import select, func, desc, text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.db.models import (
    AgentTrace,
    AuditLog,
    BackgroundJob,
    DailyCost,
    PersonaInterview,
    PersonaVersion,
    ScheduledTask,
    Tool,
    User,
    RepairTicket,
    UserSettings,
)
from src.orchestration.agent_registry import (
    Organization,
    OrgAgent,
    OrgApprovalGate,
    OrgSpend,
    OrgTask,
    OrgActivity,
)
from src.orchestration.system_agents import (
    SystemAgentInfo,
    get_system_agents,
    get_system_agent_by_id,
)

logger = logging.getLogger(__name__)


# ── Settings ──────────────────────────────────────────────────────────

class DashboardSettings(BaseSettings):
    database_url: str
    redis_url: str = "redis://redis:6379/0"
    cors_allowed_origins: str = "http://localhost:3001,http://127.0.0.1:3001"
    dashboard_api_key: str = ""

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}


_settings = DashboardSettings()

engine = create_async_engine(_settings.database_url, echo=False, pool_size=5)
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

_redis: Optional[aioredis.Redis] = None


async def _get_redis() -> aioredis.Redis:
    global _redis
    if _redis is None:
        _redis = aioredis.from_url(_settings.redis_url, decode_responses=True)
    return _redis


async def _lookup_user_telegram_id(user_id: int) -> Optional[int]:
    async with async_session() as session:
        result = await session.execute(
            select(User.telegram_id).where(User.id == user_id)
        )
        return result.scalar_one_or_none()


async def _send_telegram_message(user_id: int, message: str) -> None:
    telegram_bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not telegram_bot_token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not configured in orchestration-api")

    telegram_id = await _lookup_user_telegram_id(user_id)
    if not telegram_id:
        raise RuntimeError(f"No telegram_id found for user {user_id}")

    from aiogram import Bot

    bot = Bot(token=telegram_bot_token)
    try:
        await bot.send_message(telegram_id, f"⏰ **Reminder:** {message}", parse_mode="Markdown")
    finally:
        await bot.session.close()


# ── FastAPI app ───────────────────────────────────────────────────────

limiter = Limiter(key_func=get_remote_address, default_limits=["60/minute"])

app = FastAPI(
    title="Atlas Dashboard API",
    description="Observability & organization management for PersonalAsst",
    version="2.0.0",
)
app.state.limiter = limiter
app.add_middleware(SlowAPIMiddleware)


@app.exception_handler(RateLimitExceeded)
async def _rate_limit_handler(request: Request, exc: RateLimitExceeded):
    from starlette.responses import JSONResponse
    return JSONResponse(
        status_code=429,
        content={"detail": f"Rate limit exceeded: {exc.detail}"},
    )


def _parse_allowed_origins(raw_origins: str) -> list[str]:
    origins: list[str] = []
    seen: set[str] = set()
    for value in raw_origins.split(","):
        origin = value.strip()
        if not origin or origin in seen:
            continue
        seen.add(origin)
        origins.append(origin)

    if "*" in origins:
        logger.warning("Ignoring wildcard CORS origin '*' for dashboard API")
        origins = [origin for origin in origins if origin != "*"]

    if origins:
        return origins

    logger.warning(
        "No valid CORS origins configured; falling back to localhost dashboard origins"
    )
    return ["http://localhost:3001", "http://127.0.0.1:3001"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_parse_allowed_origins(_settings.cors_allowed_origins),
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "X-API-Key", "X-Telegram-Id"],
)

# ── API Key Authentication ────────────────────────────────────────────
# Endpoints that don't require auth (health, root, OPTIONS, dashboard
# bootstrap). /api/config is public because the React UI needs to read
# the owner_telegram_id + dashboard_api_key BEFORE it can authenticate
# the rest of its requests. Trust model: this is a single-user dashboard;
# CORS_ALLOWED_ORIGINS already limits which browsers can hit /api/config,
# and exposing the API key to a malicious local-network user gives them
# nothing they couldn't already get by reading .env.
_PUBLIC_PATHS = {"/", "/api/config", "/api/health", "/api/health/scheduler", "/docs", "/openapi.json", "/redoc"}


class APIKeyMiddleware(BaseHTTPMiddleware):
    """Validate X-API-Key header on all non-public endpoints."""

    async def dispatch(self, request: Request, call_next):
        if request.method == "OPTIONS":
            return await call_next(request)
        if request.url.path in _PUBLIC_PATHS:
            return await call_next(request)
        # WebSocket upgrade handled separately
        if request.url.path == "/ws":
            return await call_next(request)

        expected_key = _settings.dashboard_api_key
        if not expected_key:
            # No key configured → allow (dev mode) but log warning
            logger.warning("DASHBOARD_API_KEY not set — API is unprotected")
            return await call_next(request)

        provided_key = request.headers.get("X-API-Key", "")
        if not provided_key:
            return _json_error(401, "Missing X-API-Key header")

        # Constant-time comparison to prevent timing attacks
        if not hmac.compare_digest(provided_key, expected_key):
            return _json_error(403, "Invalid API key")

        return await call_next(request)


def _json_error(status_code: int, detail: str):
    from starlette.responses import JSONResponse
    return JSONResponse(status_code=status_code, content={"detail": detail})


app.add_middleware(APIKeyMiddleware)


# ── Pydantic response models ─────────────────────────────────────────

class HealthResponse(BaseModel):
    status: str
    db_ok: bool
    redis_ok: bool
    timestamp: str


class CostSummary(BaseModel):
    today_usd: float
    month_usd: float
    request_count_today: int
    request_count_month: int


class CostDay(BaseModel):
    date: str
    cost_usd: float
    requests: int
    tokens: int


class ToolItem(BaseModel):
    id: int
    name: str
    tool_type: str
    description: str
    is_active: bool
    use_count: int
    created_by: str
    created_at: Optional[datetime] = None
    last_used_at: Optional[datetime] = None


class RegistryToolItem(BaseModel):
    name: str
    type: str
    description: str
    requires_approval: bool | None = None


class ScheduleItem(BaseModel):
    id: int
    apscheduler_id: str
    description: str
    trigger_type: str
    trigger_config: Optional[dict] = None
    is_active: bool
    created_at: Optional[datetime] = None
    last_run_at: Optional[datetime] = None
    next_run_at: Optional[datetime] = None


class ActivityItem(BaseModel):
    id: int
    timestamp: Optional[datetime] = None
    direction: str
    platform: str
    agent_name: Optional[str] = None
    model_used: Optional[str] = None
    cost_usd: Optional[float] = None
    duration_ms: Optional[int] = None
    error: Optional[str] = None
    message_preview: Optional[str] = None
    user_telegram_id: Optional[int] = None


class PersonaInfo(BaseModel):
    version: int
    assistant_name: str
    personality: Optional[dict] = None
    created_at: Optional[datetime] = None
    interviews_completed: int


class QualityInfo(BaseModel):
    recent_scores: List[float]
    average: Optional[float] = None
    trend: Optional[str] = None


class DashboardSummary(BaseModel):
    costs: CostSummary
    tool_count: int
    active_schedules: int
    interactions_today: int
    quality: QualityInfo
    org_count: int


# ── Organization request/response models ──────────────────────────────

class OrgCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    description: Optional[str] = None
    goal: Optional[str] = None
    config: Optional[dict] = None


class OrgResponse(BaseModel):
    id: int
    name: str
    description: Optional[str] = None
    goal: Optional[str] = None
    status: str
    config: Optional[dict] = None
    budget_cap_usd: float = 0.0
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    agent_count: int = 0
    task_count: int = 0
    completed_tasks: int = 0


class OrgProjectSetupRequest(BaseModel):
    goal: str = Field(..., min_length=5, max_length=2000, description="Plain-English project goal")
    org_name: Optional[str] = Field(default=None, max_length=200)
    org_id: Optional[int] = Field(default=None, description="Reuse an existing org instead of creating one")
    plan: Optional[dict] = Field(
        default=None,
        description="Pre-built plan from /api/orgs/plan-stream. If provided, skips the LLM planning call.",
    )


class OrgProjectSetupResponse(BaseModel):
    org_id: int
    org_name: str
    created_org: bool
    agents: list[dict]
    tasks: list[dict]
    summary: str
    scheduled_jobs: int = 0


class OrgValidationResponse(BaseModel):
    org_id: int
    valid: bool
    score: int
    warnings: list[str]
    errors: list[str]
    agent_summary: list[dict]


class WizardAgentConfig(BaseModel):
    """Agent selected in step 2 of the wizard, with optional overrides."""
    system_agent_id: str = Field(..., description="ID of the system agent to base this org agent on")
    role: Optional[str] = Field(default=None, description="Override the default role. Falls back to system agent name.")
    instructions: Optional[str] = Field(default=None, description="Custom instructions for this agent in the org context.")


class OrgWizardRequest(BaseModel):
    """Single-call wizard: create org + assign agents atomically."""
    name: str = Field(..., min_length=1, max_length=200)
    goal: Optional[str] = Field(default=None, max_length=2000)
    description: Optional[str] = None
    agents: list[WizardAgentConfig] = Field(default_factory=list, description="System agents to assign to this org.")


class OrgWizardResponse(BaseModel):
    org_id: int
    org_name: str
    status: str
    agents_created: int
    agent_names: list[str]


class OrgAgentCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    role: str = Field(..., min_length=1, max_length=100)
    description: Optional[str] = None
    instructions: Optional[str] = None
    tools_config: Optional[dict] = None
    model_tier: str = "general"
    skills: Optional[list[str]] = None
    allowed_tools: Optional[list[str]] = None


class OrgAgentUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=200)
    role: Optional[str] = Field(default=None, min_length=1, max_length=100)
    description: Optional[str] = None
    instructions: Optional[str] = None
    tools_config: Optional[dict] = None
    model_tier: Optional[str] = None
    status: Optional[str] = None
    skills: Optional[list[str]] = None
    allowed_tools: Optional[list[str]] = None


class OrgAgentResponse(BaseModel):
    id: int
    org_id: int
    name: str
    role: str
    description: Optional[str] = None
    instructions: Optional[str] = None
    tools_config: Optional[dict] = None
    model_tier: str
    status: str
    created_at: Optional[datetime] = None
    skills: list[str] = []
    allowed_tools: list[str] = []


class OrgAgentWithOrgInfo(BaseModel):
    """OrgAgent with organization attachment details for the Agents tab."""
    id: int
    org_id: int
    org_name: str
    org_status: str
    name: str
    role: str
    description: Optional[str] = None
    instructions: Optional[str] = None
    model_tier: str
    status: str
    tools_config: Optional[dict] = None
    skills: list[str] = []
    allowed_tools: list[str] = []
    can_delete: bool
    delete_reason: Optional[str] = None
    created_at: Optional[datetime] = None


class AgentDeletionCheck(BaseModel):
    """Response for can-delete check."""
    can_delete: bool
    reason: Optional[str] = None
    attached_org: Optional[str] = None
    attached_org_status: Optional[str] = None


class OrgDeletePreviewAgent(BaseModel):
    id: int
    name: str
    role: str


class OrgDeletePreviewTask(BaseModel):
    id: int
    title: str
    status: str
    agent_name: Optional[str] = None


class OrgDeletePreview(BaseModel):
    """Preview of what will be deleted when an organization is removed."""
    org_id: int
    org_name: str
    agents: list[OrgDeletePreviewAgent] = []
    tasks: list[OrgDeletePreviewTask] = []
    activity_count: int = 0
    exclusive_tools: list[str] = []
    exclusive_skills: list[str] = []


class OrgDeleteRequest(BaseModel):
    """Optional body for selective org deletion.

    IDs listed in retain_agent_ids / retain_task_ids will be detached
    (org_id set to NULL) instead of cascade-deleted.
    """
    retain_agent_ids: list[int] = Field(default_factory=list)
    retain_task_ids: list[int] = Field(default_factory=list)


class OrgTaskCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=500)
    description: Optional[str] = None
    priority: str = "medium"
    agent_id: Optional[int] = None
    due_at: Optional[datetime] = None


class OrgTaskUpdate(BaseModel):
    title: Optional[str] = Field(default=None, min_length=1, max_length=500)
    description: Optional[str] = None
    priority: Optional[str] = None
    status: Optional[str] = None
    agent_id: Optional[int] = None
    due_at: Optional[datetime] = None


class OrgTaskResponse(BaseModel):
    id: int
    org_id: int
    agent_id: Optional[int] = None
    title: str
    description: Optional[str] = None
    priority: str
    status: str
    result: Optional[dict] = None
    source: str
    due_at: Optional[datetime] = None
    created_at: Optional[datetime] = None
    assigned_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None


class OrgActivityResponse(BaseModel):
    id: int
    org_id: int
    agent_id: Optional[int] = None
    task_id: Optional[int] = None
    action: str
    details: Optional[str] = None
    source: str
    created_at: Optional[datetime] = None


# ── Phase 2: Governance ───────────────────────────────────────────────

class OrgBudgetUpdate(BaseModel):
    budget_cap_usd: float = Field(..., ge=0, description="Monthly budget cap in USD. 0 = unlimited.")


class ApprovalGateCreate(BaseModel):
    action: str = Field(..., min_length=1, max_length=200)
    context: Optional[dict] = None
    agent_id: Optional[int] = None


class ApprovalGateDecision(BaseModel):
    decision: str = Field(..., pattern="^(approved|rejected)$")
    note: Optional[str] = None


class ApprovalGateResponse(BaseModel):
    id: int
    org_id: int
    agent_id: Optional[int] = None
    action: str
    context: Optional[dict] = None
    status: str
    decision_note: Optional[str] = None
    created_at: Optional[datetime] = None
    decided_at: Optional[datetime] = None


# ── Phase 3: Task goal ancestry ──────────────────────────────────────

class OrgTaskCreateV2(BaseModel):
    title: str = Field(..., min_length=1, max_length=500)
    description: Optional[str] = None
    priority: str = "medium"
    agent_id: Optional[int] = None
    due_at: Optional[datetime] = None
    goal_ancestry: Optional[list[str]] = Field(
        default=None,
        description="Ordered goal chain, e.g. ['org:sales', 'goal:outreach', 'task:email-draft']",
    )


class OrgTaskUpdateV2(BaseModel):
    title: Optional[str] = Field(default=None, min_length=1, max_length=500)
    description: Optional[str] = None
    priority: Optional[str] = None
    status: Optional[str] = None
    agent_id: Optional[int] = None
    due_at: Optional[datetime] = None
    goal_ancestry: Optional[list[str]] = None


class OrgTaskResponseV2(BaseModel):
    id: int
    org_id: int
    agent_id: Optional[int] = None
    title: str
    description: Optional[str] = None
    priority: str
    status: str
    result: Optional[dict] = None
    goal_ancestry: Optional[list[str]] = None
    source: str
    due_at: Optional[datetime] = None
    created_at: Optional[datetime] = None
    assigned_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None


# ── Phase 4: Spend tracking ───────────────────────────────────────────

class OrgSpendCreate(BaseModel):
    cost_usd: float = Field(..., gt=0)
    agent_id: Optional[int] = None
    model_used: Optional[str] = None
    description: Optional[str] = None


class OrgSpendResponse(BaseModel):
    id: int
    org_id: int
    agent_id: Optional[int] = None
    cost_usd: float
    model_used: Optional[str] = None
    description: Optional[str] = None
    recorded_at: Optional[datetime] = None


class OrgSpendReport(BaseModel):
    org_id: int
    total_usd: float
    budget_cap_usd: float
    pct_used: float
    over_budget: bool
    entries: list[OrgSpendResponse]


# ══════════════════════════════════════════════════════════════════════
#  PHASE A — OBSERVABILITY ENDPOINTS (read from bot tables)
# ══════════════════════════════════════════════════════════════════════


@app.get("/")
async def root():
    return {"name": "Atlas Dashboard API", "version": "2.0.0"}


@app.get("/api/config")
async def get_config():
    """Return dashboard bootstrap configuration. Public so the React UI can
    read owner_telegram_id + dashboard_api_key BEFORE making authenticated
    requests. Trust model documented at the _PUBLIC_PATHS definition.

    The owner_telegram_id is the user the dashboard scopes its queries to —
    looked up from the User table (is_owner=True). The dashboard_api_key is
    what the UI must send as X-API-Key on every other request (or empty
    string in dev mode where auth is disabled).
    """
    owner_telegram_id: Optional[int] = None
    try:
        async with async_session() as session:
            owner_r = await session.execute(
                select(User).where(User.is_owner == True).limit(1)  # noqa: E712
            )
            owner = owner_r.scalar_one_or_none()
            if owner is None:
                # Fallback: first user in the table (single-user dev install)
                fallback_r = await session.execute(select(User).limit(1))
                owner = fallback_r.scalar_one_or_none()
            if owner is not None:
                owner_telegram_id = owner.telegram_id
    except Exception as exc:  # noqa: BLE001
        # /api/config is the dashboard's bootstrap call — never 500 here, the
        # UI gracefully handles a null owner_telegram_id.
        logger.warning("get_config: owner lookup failed: %s", exc)

    return {
        "api_version": "2.0.0",
        "auth_required": bool(_settings.dashboard_api_key),
        "owner_telegram_id": owner_telegram_id,
        "dashboard_api_key": _settings.dashboard_api_key or "",
    }


@app.get("/api/health/scheduler")
async def scheduler_health():
    """Return a snapshot of every internal scheduled job's last-run state.

    Aggregate `status` is "healthy" if every job has zero consecutive
    failures, "degraded" if any job has >= 3 consecutive failures (alert
    threshold), or "unknown" if no health records exist yet (e.g. fresh
    container, no jobs have fired). Public — observability endpoint should
    be reachable without an API key for monitoring tools.
    """
    try:
        from src.scheduler.observability import get_health_snapshot
        return await get_health_snapshot()
    except Exception as e:
        logger.warning("scheduler_health endpoint failed: %s", e)
        return {"status": "unknown", "jobs": [],
                "summary": {"error": str(e)}}


@app.get("/api/health", response_model=HealthResponse)
async def health_check():
    db_ok = False
    redis_ok = False
    try:
        async with async_session() as session:
            await session.execute(select(func.now()))
            db_ok = True
    except Exception as e:
        logger.warning("Health check DB fail: %s", e)
    try:
        r = await _get_redis()
        await r.ping()
        redis_ok = True
    except Exception as e:
        logger.warning("Health check Redis fail: %s", e)
    status = "healthy" if db_ok and redis_ok else "degraded"
    return HealthResponse(
        status=status, db_ok=db_ok, redis_ok=redis_ok,
        timestamp=datetime.now(timezone.utc).isoformat(),
    )


@app.get("/api/dashboard", response_model=DashboardSummary)
async def get_dashboard():
    """Aggregated dashboard summary from bot's real data."""
    today = date.today()
    first_of_month = today.replace(day=1)

    async with async_session() as session:
        # Cost today
        today_r = await session.execute(
            select(
                func.coalesce(func.sum(DailyCost.total_cost_usd), 0),
                func.coalesce(func.sum(DailyCost.request_count), 0),
            ).where(DailyCost.date == today)
        )
        today_cost, today_requests = today_r.one()

        # Cost this month
        month_r = await session.execute(
            select(
                func.coalesce(func.sum(DailyCost.total_cost_usd), 0),
                func.coalesce(func.sum(DailyCost.request_count), 0),
            ).where(DailyCost.date >= first_of_month)
        )
        month_cost, month_requests = month_r.one()

        # Tool count
        tool_r = await session.execute(
            select(func.count(Tool.id)).where(Tool.is_active == True)  # noqa: E712
        )
        tool_count = tool_r.scalar() or 0

        # Active schedules
        sched_r = await session.execute(
            select(func.count(ScheduledTask.id)).where(
                ScheduledTask.is_active == True  # noqa: E712
            )
        )
        active_schedules = sched_r.scalar() or 0

        # Interactions today
        interact_r = await session.execute(
            select(func.count(AuditLog.id)).where(
                func.date(AuditLog.timestamp) == today
            )
        )
        interactions_today = interact_r.scalar() or 0

        # Org count
        org_r = await session.execute(
            select(func.count(Organization.id)).where(
                Organization.status == "active"
            )
        )
        org_count = org_r.scalar() or 0

    # Quality from Redis
    quality = await _get_quality_info()

    return DashboardSummary(
        costs=CostSummary(
            today_usd=float(today_cost),
            month_usd=float(month_cost),
            request_count_today=int(today_requests),
            request_count_month=int(month_requests),
        ),
        tool_count=tool_count,
        active_schedules=active_schedules,
        interactions_today=interactions_today,
        quality=quality,
        org_count=org_count,
    )


@app.get("/api/costs", response_model=List[CostDay])
async def get_costs(days: int = 30):
    """Daily cost data for the last N days."""
    since = date.today() - timedelta(days=days)
    async with async_session() as session:
        result = await session.execute(
            select(DailyCost)
            .where(DailyCost.date >= since)
            .order_by(DailyCost.date)
        )
        rows = result.scalars().all()
    return [
        CostDay(
            date=str(r.date),
            cost_usd=float(r.total_cost_usd or 0),
            requests=r.request_count or 0,
            tokens=r.total_tokens or 0,
        )
        for r in rows
    ]


@app.get("/api/tools", response_model=List[ToolItem])
async def get_tools():
    """All registered tools with usage stats."""
    async with async_session() as session:
        # Some container builds may have an older ORM mapping lacking `use_count` attribute.
        # To avoid AttributeError, order by created_at (always present) instead.
        result = await session.execute(select(Tool).order_by(desc(Tool.created_at)))
        rows = result.scalars().all()
    return [
        ToolItem(
            id=t.id, name=t.name, tool_type=t.tool_type,
            description=t.description, is_active=t.is_active,
            use_count=getattr(t, "use_count", 0), created_by=t.created_by,
            created_at=t.created_at, last_used_at=t.last_used_at,
        )
        for t in rows
    ]


@app.get("/api/tools/available", response_model=List[RegistryToolItem])
async def get_available_tools():
    """List discoverable tools from the on-disk tool registry (plugins).

    These are not DB rows — they are available tools from src/tools/plugins.
    """
    try:
        from src.tools.registry import get_registry
        registry = await get_registry()
        lst = registry.list_tools()
        return [
            RegistryToolItem(
                name=it["name"],
                type=it["type"],
                description=it.get("description", ""),
                requires_approval=it.get("requires_approval"),
            )
            for it in lst
        ]
    except Exception as e:
        logger.error("Failed to enumerate registry tools: %s", e)
        raise HTTPException(status_code=500, detail=f"Failed to list available tools: {e}")


@app.patch("/api/tools/{tool_id}")
async def update_tool(tool_id: int, body: dict):
    """Toggle is_active on a registered tool."""
    async with async_session() as session:
        result = await session.execute(select(Tool).where(Tool.id == tool_id))
        tool = result.scalar_one_or_none()
        if not tool:
            raise HTTPException(status_code=404, detail="Tool not found")
        if "is_active" in body:
            tool.is_active = bool(body["is_active"])
        await session.commit()
    return {"id": tool_id, "is_active": tool.is_active}


@app.delete("/api/tools/{tool_id}")
async def delete_tool(tool_id: int):
    """Delete a registered tool: removes DB row and the plugin directory on disk.

    Built-in plugins (browser, linkedin, onedrive) and the ``_example`` scaffold
    are protected — the user must remove them from source control manually.
    """
    import re
    import shutil
    from pathlib import Path

    PROTECTED = {"browser", "linkedin", "onedrive", "_example"}

    async with async_session() as session:
        result = await session.execute(select(Tool).where(Tool.id == tool_id))
        tool = result.scalar_one_or_none()
        if not tool:
            raise HTTPException(status_code=404, detail="Tool not found")

        tool_name = tool.name
        if tool_name in PROTECTED:
            raise HTTPException(
                status_code=403,
                detail=f"Built-in tool '{tool_name}' cannot be deleted.",
            )

        # Validate name to guard against path traversal before touching the FS.
        if not re.fullmatch(r"[A-Za-z0-9_-]+", tool_name):
            raise HTTPException(status_code=400, detail="Invalid tool name")

        # Remove plugin directory if it exists (only CLI/function tools have one).
        plugin_dir = Path(f"src/tools/plugins/{tool_name}")
        fs_removed = False
        if plugin_dir.exists() and plugin_dir.is_dir():
            try:
                shutil.rmtree(plugin_dir)
                fs_removed = True
            except Exception as e:
                logger.error("Failed to remove plugin dir %s: %s", plugin_dir, e)
                raise HTTPException(
                    status_code=500,
                    detail=f"Failed to remove plugin directory: {e}",
                )

        await session.delete(tool)
        await session.commit()

    # Remove from live registry if already loaded so in-memory state matches disk.
    try:
        from src.tools.registry import get_registry
        registry = await get_registry()
        registry._tools.pop(tool_name, None)
        registry._manifests.pop(tool_name, None)
    except Exception as e:
        logger.warning("Live registry eviction failed for %s: %s", tool_name, e)

    return {"deleted": True, "id": tool_id, "name": tool_name, "fs_removed": fs_removed}


@app.get("/api/schedules", response_model=List[ScheduleItem])
async def get_schedules():
    """All scheduled tasks with live next_run_at from APScheduler."""
    live_jobs = await _get_live_apscheduler_jobs()

    async with async_session() as session:
        result = await session.execute(
            select(ScheduledTask).order_by(desc(ScheduledTask.created_at))
        )
        rows = result.scalars().all()

        # Sync next_run_at into DB where it has changed
        for s in rows:
            live_next = live_jobs.get(s.apscheduler_id)
            if live_next and live_next != "None":
                try:
                    from datetime import datetime
                    parsed = datetime.fromisoformat(live_next)
                    if s.next_run_at != parsed:
                        s.next_run_at = parsed
                except Exception:
                    pass
        await session.commit()

    return [
        ScheduleItem(
            id=s.id,
            apscheduler_id=s.apscheduler_id,
            description=s.description,
            trigger_type=s.trigger_type,
            trigger_config=s.trigger_config,
            is_active=s.is_active,
            created_at=s.created_at,
            last_run_at=s.last_run_at,
            next_run_at=s.next_run_at,
        )
        for s in rows
    ]


@app.delete("/api/schedules/{schedule_id}")
async def delete_schedule(schedule_id: int):
    """Cancel and permanently delete a scheduled task."""
    async with async_session() as session:
        task = await session.get(ScheduledTask, schedule_id)
        if not task:
            raise HTTPException(status_code=404, detail="Schedule not found")
        job_id = task.apscheduler_id
        await session.delete(task)
        await session.commit()
    # Remove from APScheduler's own table (best-effort — may already be gone)
    await _remove_apscheduler_job(job_id)
    return {"message": f"Schedule {schedule_id} deleted"}


@app.post("/api/schedules/{schedule_id}/pause")
async def pause_schedule(schedule_id: int):
    """Mark a scheduled task as paused (does not remove from APScheduler)."""
    async with async_session() as session:
        task = await session.get(ScheduledTask, schedule_id)
        if not task:
            raise HTTPException(status_code=404, detail="Schedule not found")
        task.is_active = False
        await session.commit()
    # Best-effort: remove from APScheduler so it stops firing immediately
    try:
        await _remove_apscheduler_job(task.apscheduler_id)
    except Exception:
        pass
    return {"message": f"Schedule {schedule_id} paused"}


@app.post("/api/schedules/{schedule_id}/resume")
async def resume_schedule(schedule_id: int):
    """Mark a scheduled task as active."""
    async with async_session() as session:
        task = await session.get(ScheduledTask, schedule_id)
        if not task:
            raise HTTPException(status_code=404, detail="Schedule not found")
        task.is_active = True
        await session.commit()
    return {"message": f"Schedule {schedule_id} resumed"}


@app.put("/api/schedules/{schedule_id}")
async def update_schedule(schedule_id: int, request: dict):
    """Update a scheduled task's description or trigger configuration.

    Editable fields:
    - description: New description text
    - trigger_config: New trigger configuration (cron/interval/once)

    Note: Changing trigger_config will recreate the APScheduler job.
    """
    from src.scheduler.engine import add_cron_job, add_interval_job, add_one_shot_job, remove_job

    async with async_session() as session:
        task = await session.get(ScheduledTask, schedule_id)
        if not task:
            raise HTTPException(status_code=404, detail="Schedule not found")

        old_job_id = task.apscheduler_id
        updated = False

        # Update description
        if "description" in request:
            task.description = request["description"]
            updated = True

        # Update trigger (requires recreating the job)
        if "trigger_config" in request:
            new_config = request["trigger_config"]
            trigger_type = new_config.get("trigger_type", task.trigger_type)

            # Remove old job
            await remove_job(old_job_id)
            await _remove_apscheduler_job(old_job_id)

            # Generate new job ID
            import uuid
            new_job_id = f"{task.user_id}_{trigger_type}_{uuid.uuid4().hex[:8]}"

            # Create new job based on trigger type
            try:
                if trigger_type == "cron":
                    cron = new_config.get("cron", {})
                    await add_cron_job(
                        func_path="src.scheduler.jobs:send_reminder",
                        job_id=new_job_id,
                        cron_kwargs={
                            "hour": cron.get("hour", 9),
                            "minute": cron.get("minute", 0),
                            "day_of_week": cron.get("day_of_week", "*"),
                        },
                        kwargs={
                            "user_id": task.user_id,
                            "message": task.description,
                        },
                    )
                elif trigger_type == "interval":
                    interval = new_config.get("interval", {})
                    await add_interval_job(
                        func_path="src.scheduler.jobs:send_reminder",
                        job_id=new_job_id,
                        seconds=interval.get("seconds", 3600),
                        kwargs={
                            "user_id": task.user_id,
                            "message": task.description,
                        },
                    )
                elif trigger_type == "once":
                    once = new_config.get("once", {})
                    from datetime import datetime, timezone
                    run_at = datetime.fromisoformat(once.get("run_at", datetime.now(timezone.utc).isoformat()))
                    await add_one_shot_job(
                        func_path="src.scheduler.jobs:send_reminder",
                        job_id=new_job_id,
                        run_at=run_at,
                        kwargs={
                            "user_id": task.user_id,
                            "message": task.description,
                        },
                    )
                else:
                    raise HTTPException(status_code=400, detail=f"Unknown trigger type: {trigger_type}")

                task.apscheduler_id = new_job_id
                task.trigger_type = trigger_type
                task.trigger_config = new_config.get(trigger_type, {})
                updated = True

            except Exception as e:
                logger.error(f"Failed to recreate schedule job: {e}")
                raise HTTPException(status_code=500, detail=f"Failed to update schedule: {e}")

        if updated:
            await session.commit()
            return {
                "message": f"Schedule {schedule_id} updated",
                "new_job_id": task.apscheduler_id if "trigger_config" in request else None,
            }
        else:
            return {"message": "No changes made"}


@app.post("/api/schedules/{schedule_id}/test")
async def test_schedule(schedule_id: int):
    """Run a scheduled task immediately and return the agent result text.

    For agent tasks (run_agent_task / summarize_new_emails / morning_brief):
      Calls run_orchestrator directly so the result is captured and returned
      to the dashboard — no need to open Telegram to see what happened.
      The result is also still sent to Telegram as normal.

    For other job types (send_reminder):
      Fires the job normally via the safe_job_wrapper.
    """
    async with async_session() as session:
        task = await session.get(ScheduledTask, schedule_id)
        if not task:
            raise HTTPException(status_code=404, detail="Schedule not found")

    try:
        job_path = (task.job_function or "").strip()
        if not job_path or ":" not in job_path:
            raise HTTPException(status_code=400, detail="Job function path is missing or invalid")

        module_path, func_name = job_path.split(":", 1)
        if module_path != "src.scheduler.jobs":
            raise HTTPException(status_code=400, detail="Job function module not allowed for Run now")

        allowed_funcs = {"send_reminder", "run_agent_task", "summarize_new_emails", "morning_brief"}
        if func_name not in allowed_funcs:
            raise HTTPException(status_code=400, detail="Job function not allowed for Run now")

        job_kwargs = dict(task.job_args) if task.job_args else {}
        if not job_kwargs and task.user_id:
            if func_name == "send_reminder":
                job_kwargs = {"user_id": task.user_id, "message": task.description}
            else:
                job_kwargs = {"user_id": task.user_id, "prompt": task.description}

        executed_at = datetime.now(timezone.utc)
        result_text: Optional[str] = None

        agent_funcs = {"run_agent_task", "summarize_new_emails", "morning_brief"}
        if func_name in agent_funcs:
            # Resolve user → telegram_id
            async with async_session() as session:
                user_r = await session.execute(
                    select(User).where(User.id == job_kwargs.get("user_id", 0)).limit(1)
                )
                u = user_r.scalar_one_or_none()

            if not u or not u.telegram_id:
                raise HTTPException(status_code=400, detail="No Telegram user found for this task")

            # Map job function to its effective prompt
            prompt_map = {
                "summarize_new_emails": (
                    "Check for new unread emails since last check. Summarize the important ones briefly."
                ),
                "morning_brief": (
                    "Give me my morning brief: today's calendar events, important unread emails, "
                    "and any pending tasks or reminders."
                ),
            }
            prompt = job_kwargs.get("prompt") or prompt_map.get(func_name) or task.description

            from src.agents.orchestrator import run_orchestrator
            _AGENT_TIMEOUT = int(os.environ.get("AGENT_TIMEOUT_SECONDS", "120"))
            try:
                result_text = await asyncio.wait_for(
                    run_orchestrator(u.telegram_id, prompt),
                    timeout=_AGENT_TIMEOUT,
                )
            except asyncio.TimeoutError:
                raise HTTPException(
                    status_code=504,
                    detail=f"Agent timed out after {_AGENT_TIMEOUT}s — check Telegram for partial results",
                )

            # Also send result to Telegram (normal behaviour)
            if result_text:
                try:
                    from aiogram import Bot
                    from src.settings import settings as _bot_settings
                    bot = Bot(token=_bot_settings.telegram_bot_token)
                    try:
                        await bot.send_message(u.telegram_id, result_text, parse_mode="Markdown")
                    finally:
                        await bot.session.close()
                except Exception as tg_err:
                    logger.warning(f"test_schedule: Telegram send failed (non-fatal): {tg_err}")

        else:
            # Non-agent job (send_reminder etc.): fire normally
            module = importlib.import_module(module_path)
            target = getattr(module, func_name)
            safe_wrapper = getattr(module, "safe_job_wrapper", None)
            if callable(safe_wrapper):
                await safe_wrapper(target, **job_kwargs)
            else:
                await target(**job_kwargs)

        return {
            "message": "Test executed successfully",
            "schedule_id": schedule_id,
            "description": task.description,
            "executed_at": executed_at.isoformat(),
            "result_text": result_text,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Test execution failed for schedule {schedule_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Test execution failed: {e}")


@app.post("/api/schedules/sync")
async def sync_schedules():
    """Sync DB scheduled_tasks with live APScheduler jobs.

    - Marks tasks as paused if their APScheduler job no longer exists
    - Updates next_run_at from live APScheduler data
    - Deletes one-shot (once) tasks that have already fired (no live job)
    Returns a summary of changes made.
    """
    live_jobs = await _get_live_apscheduler_jobs()
    if not live_jobs:
        logger.warning("sync_schedules: APScheduler table may be empty or unreachable")

    now_utc = datetime.now(timezone.utc)
    orphaned = []
    fired_once = []
    stale_once = []
    synced = 0

    async with async_session() as session:
        result = await session.execute(select(ScheduledTask))
        tasks = result.scalars().all()

        for t in tasks:
            if t.apscheduler_id not in live_jobs:
                if t.trigger_type == "once":
                    # One-shot missing from scheduler — already fired or never ran
                    await session.delete(t)
                    fired_once.append(t.description)
                # For recurring jobs, do not flip is_active. It may be waiting for pickup or paused intentionally.
            else:
                # Job is live — sync next_run_at only (do not flip is_active)
                live_next = live_jobs[t.apscheduler_id]
                if live_next and live_next != "None":
                    try:
                        parsed = datetime.fromisoformat(live_next)
                        # If a once-job's fire time is in the past, delete it
                        # from both APScheduler and our DB
                        if t.trigger_type == "once" and parsed < now_utc:
                            await _remove_apscheduler_job(t.apscheduler_id)
                            await session.delete(t)
                            stale_once.append(t.description)
                            continue
                        if t.next_run_at != parsed:
                            t.next_run_at = parsed
                            synced += 1
                    except Exception:
                        pass

        await session.commit()

    # Purge APScheduler jobs that have no matching scheduled_tasks row
    known_ids = {t.apscheduler_id for t in tasks}
    ghost_jobs = []
    for job_id in list(live_jobs.keys()):
        if job_id not in known_ids:
            await _remove_apscheduler_job(job_id)
            ghost_jobs.append(job_id)

    return {
        "message": "Sync complete",
        "orphaned_paused": orphaned,
        "fired_once_deleted": fired_once + stale_once,
        "ghost_apscheduler_jobs_removed": ghost_jobs,
        "next_run_at_synced": synced,
        "live_job_count": len(live_jobs),
    }


@app.get("/api/activity", response_model=List[ActivityItem])
async def get_activity(limit: int = 50, direction: Optional[str] = None):
    """Recent activity from the audit log, with telegram_id for trace lookup."""
    async with async_session() as session:
        q = (
            select(AuditLog, User.telegram_id)
            .outerjoin(User, AuditLog.user_id == User.id)
            .order_by(desc(AuditLog.timestamp))
            .limit(min(limit, 200))
        )
        if direction:
            q = q.where(AuditLog.direction == direction)
        rows = (await session.execute(q)).all()
    return [
        ActivityItem(
            id=a.id, timestamp=a.timestamp, direction=a.direction,
            platform=a.platform, agent_name=a.agent_name,
            model_used=a.model_used, cost_usd=float(a.cost_usd) if a.cost_usd else None,
            duration_ms=a.duration_ms, error=a.error,
            message_preview=(a.message_text[:120] + "…") if a.message_text and len(a.message_text) > 120 else a.message_text,
            user_telegram_id=telegram_id,
        )
        for a, telegram_id in rows
    ]


@app.get("/api/persona", response_model=PersonaInfo)
async def get_persona():
    """Current persona evolution state."""
    async with async_session() as session:
        # Latest active persona version
        pv_r = await session.execute(
            select(PersonaVersion)
            .where(PersonaVersion.is_active == True)  # noqa: E712
            .order_by(desc(PersonaVersion.version))
            .limit(1)
        )
        pv = pv_r.scalar_one_or_none()

        # Count completed interviews
        interview_r = await session.execute(
            select(func.count(PersonaInterview.id)).where(
                PersonaInterview.status == "completed"
            )
        )
        interviews = interview_r.scalar() or 0

    if pv:
        return PersonaInfo(
            version=pv.version,
            assistant_name=pv.assistant_name,
            personality=pv.personality,
            created_at=pv.created_at,
            interviews_completed=interviews,
        )
    return PersonaInfo(
        version=0, assistant_name="Atlas",
        interviews_completed=interviews,
    )


class PersonaUpdateRequest(BaseModel):
    assistant_name: str
    personality: dict


@app.put("/api/persona")
async def update_persona(
    request: PersonaUpdateRequest,
    x_telegram_id: Optional[int] = Header(default=None, alias="X-Telegram-Id"),
):
    """Update Atlas persona (name and personality traits)."""
    if not x_telegram_id:
        raise HTTPException(status_code=401, detail="Authentication required")

    from src.memory.persona import create_persona_version

    async with async_session() as session:
        user_r = await session.execute(
            select(User).where(User.telegram_id == x_telegram_id)
        )
        user = user_r.scalar_one_or_none()
        if not user:
            raise HTTPException(status_code=404, detail="User not found")

        # Create new persona version
        version = await create_persona_version(
            user_id=user.id,
            assistant_name=request.assistant_name,
            personality=request.personality,
            change_reason="Updated via Dashboard",
        )

    return {
        "success": True,
        "version": version,
        "assistant_name": request.assistant_name,
    }


@app.get("/api/quality", response_model=QualityInfo)
async def get_quality():
    """Quality trend from Redis scores."""
    return await _get_quality_info()


class BudgetInfo(BaseModel):
    daily_cap_usd: float
    monthly_cap_usd: float
    today_usd: float
    month_usd: float
    daily_pct: float
    monthly_pct: float
    request_count_today: int
    request_count_month: int


@app.get("/api/budget", response_model=BudgetInfo)
async def get_budget(
    x_telegram_id: Optional[int] = Header(default=None, alias="X-Telegram-Id"),
):
    """Daily and monthly spend vs. caps from DB (fallback to env vars)."""
    today = date.today()
    first_of_month = today.replace(day=1)

    # Default caps from env (fallback)
    daily_cap = float(os.getenv("DAILY_COST_CAP_USD", "5.00"))
    monthly_cap = float(os.getenv("MONTHLY_COST_CAP_USD", "100.00"))

    async with async_session() as session:
        # Resolve user: by header, or fall back to owner
        resolved_user: Optional[User] = None
        if x_telegram_id:
            u_r = await session.execute(
                select(User).where(User.telegram_id == x_telegram_id).limit(1)
            )
            resolved_user = u_r.scalar_one_or_none()
        if resolved_user is None:
            owner_r = await session.execute(
                select(User).where(User.is_owner == True).limit(1)  # noqa: E712
            )
            resolved_user = owner_r.scalar_one_or_none()
        # Load UserSettings directly (avoids async lazy-load pitfall)
        if resolved_user:
            s_r = await session.execute(
                select(UserSettings).where(UserSettings.user_id == resolved_user.id)
            )
            user_settings = s_r.scalar_one_or_none()
            if user_settings:
                daily_cap = float(user_settings.daily_cost_cap_usd)
                monthly_cap = float(user_settings.monthly_cost_cap_usd)

        today_r = await session.execute(
            select(
                func.coalesce(func.sum(DailyCost.total_cost_usd), 0),
                func.coalesce(func.sum(DailyCost.request_count), 0),
            ).where(DailyCost.date == today)
        )
        today_cost, today_requests = today_r.one()

        month_r = await session.execute(
            select(
                func.coalesce(func.sum(DailyCost.total_cost_usd), 0),
                func.coalesce(func.sum(DailyCost.request_count), 0),
            ).where(DailyCost.date >= first_of_month)
        )
        month_cost, month_requests = month_r.one()

    today_usd = float(today_cost)
    month_usd = float(month_cost)
    return BudgetInfo(
        daily_cap_usd=daily_cap,
        monthly_cap_usd=monthly_cap,
        today_usd=today_usd,
        month_usd=month_usd,
        daily_pct=round(today_usd / daily_cap * 100, 1) if daily_cap else 0,
        monthly_pct=round(month_usd / monthly_cap * 100, 1) if monthly_cap else 0,
        request_count_today=int(today_requests),
        request_count_month=int(month_requests),
    )


class BudgetUpdateRequest(BaseModel):
    daily_cap_usd: float
    monthly_cap_usd: float


@app.put("/api/budget")
async def update_budget(
    request: BudgetUpdateRequest,
    x_telegram_id: Optional[int] = Header(default=None, alias="X-Telegram-Id"),
):
    """Update daily and monthly budget caps for the user."""
    if not x_telegram_id:
        raise HTTPException(status_code=401, detail="Authentication required")

    async with async_session() as session:
        user_r = await session.execute(
            select(User).where(User.telegram_id == x_telegram_id)
        )
        user = user_r.scalar_one_or_none()
        if not user:
            raise HTTPException(status_code=404, detail="User not found")

        # Get or create user settings
        settings_r = await session.execute(
            select(UserSettings).where(UserSettings.user_id == user.id)
        )
        settings = settings_r.scalar_one_or_none()

        if not settings:
            settings = UserSettings(
                user_id=user.id,
                daily_cost_cap_usd=request.daily_cap_usd,
                monthly_cost_cap_usd=request.monthly_cap_usd,
            )
            session.add(settings)
        else:
            settings.daily_cost_cap_usd = request.daily_cap_usd
            settings.monthly_cost_cap_usd = request.monthly_cap_usd

        await session.commit()

    return {
        "success": True,
        "daily_cap_usd": request.daily_cap_usd,
        "monthly_cap_usd": request.monthly_cap_usd,
    }


# ── Agent Traces (M3) ────────────────────────────────────────────────

class TraceStep(BaseModel):
    id: int
    step_index: int
    agent_name: Optional[str]
    tool_name: Optional[str]
    tool_args: Optional[dict]
    tool_result_preview: Optional[str]
    duration_ms: Optional[int]
    timestamp: datetime


@app.get("/api/traces", response_model=List[TraceStep])
async def get_traces(
    session_key: Optional[str] = None,
    audit_log_id: Optional[int] = None,
    limit: int = 100,
):
    """Return tool-call trace steps. Filter by session_key or audit_log_id."""
    async with async_session() as session:
        q = select(AgentTrace).order_by(AgentTrace.timestamp.desc(), AgentTrace.step_index)
        if session_key:
            q = q.where(AgentTrace.session_key == session_key)
        if audit_log_id:
            q = q.where(AgentTrace.audit_log_id == audit_log_id)
        q = q.limit(min(limit, 500))
        rows = (await session.execute(q)).scalars().all()
    return [
        TraceStep(
            id=r.id,
            step_index=r.step_index,
            agent_name=r.agent_name,
            tool_name=r.tool_name,
            tool_args=r.tool_args,
            tool_result_preview=r.tool_result_preview,
            duration_ms=r.duration_ms,
            timestamp=r.timestamp,
        )
        for r in rows
    ]


@app.get("/api/traces/sessions")
async def get_trace_sessions(limit: int = 20):
    """Return the most recent distinct session_keys that have traces."""
    async with async_session() as session:
        rows = (await session.execute(
            select(AgentTrace.session_key, func.max(AgentTrace.timestamp).label("last_at"))
            .group_by(AgentTrace.session_key)
            .order_by(desc("last_at"))
            .limit(limit)
        )).all()
    return [{"session_key": r[0], "last_at": r[1].isoformat() if r[1] else None} for r in rows]


# ── Repair Tickets (M4) ───────────────────────────────────────────────

class RepairTicketItem(BaseModel):
    id: int
    title: str
    status: str
    priority: str
    risk_level: str
    auto_applied: bool
    approval_required: bool
    created_at: datetime
    updated_at: datetime
    # error_context lets the UI render the Admin / AI-Agent routing chip
    # in the Repairs tab (Dashboard.js reads error_context.assigned_to).
    # Stripping this field caused the chip to silently never show.
    error_context: Optional[dict] = None


@app.get("/api/repairs", response_model=List[RepairTicketItem])
async def get_repairs(limit: int = 30):
    """Recent repair tickets with risk level and auto-apply status."""
    try:
        async with async_session() as session:
            rows = (await session.execute(
                select(RepairTicket).order_by(desc(RepairTicket.created_at)).limit(limit)
            )).scalars().all()
        return [
            RepairTicketItem(
                id=r.id,
                title=r.title,
                status=r.status,
                priority=r.priority,
                risk_level=getattr(r, "risk_level", "medium"),
                auto_applied=getattr(r, "auto_applied", False),
                approval_required=getattr(r, "approval_required", False),
                created_at=r.created_at,
                updated_at=r.updated_at,
                error_context=getattr(r, "error_context", None) or None,
            )
            for r in rows
        ]
    except Exception as exc:
        logger.warning(f"get_repairs: DB query failed (schema may need migration): {exc}")
        return []


# ── Background Jobs (M2) ──────────────────────────────────────────────

class BackgroundJobItem(BaseModel):
    id: int
    goal: str
    done_condition: Optional[str]
    status: str
    iterations_run: int
    max_iterations: int
    result: Optional[str]
    created_at: datetime
    completed_at: Optional[datetime]


@app.get("/api/background-jobs", response_model=List[BackgroundJobItem])
async def get_background_jobs(limit: int = 20):
    """Current and recent background agent jobs."""
    try:
        async with async_session() as session:
            rows = (await session.execute(
                select(BackgroundJob).order_by(desc(BackgroundJob.created_at)).limit(limit)
            )).scalars().all()
        return [
            BackgroundJobItem(
                id=r.id,
                goal=r.goal,
                done_condition=r.done_condition,
                status=r.status,
                iterations_run=r.iterations_run,
                max_iterations=r.max_iterations,
                result=r.result,
                created_at=r.created_at,
                completed_at=r.completed_at,
            )
            for r in rows
        ]
    except Exception as exc:
        logger.warning(f"get_background_jobs: DB query failed (schema may need migration): {exc}")
        return []


@app.patch("/api/background-jobs/{job_id}/cancel")
async def cancel_background_job(job_id: int):
    """Cancel a running background job."""
    async with async_session() as session:
        result = await session.execute(select(BackgroundJob).where(BackgroundJob.id == job_id))
        job = result.scalar_one_or_none()
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")
        if job.status != "running":
            raise HTTPException(status_code=400, detail=f"Job is already {job.status}")
        job.status = "cancelled"
        from datetime import datetime as _dt, timezone as _tz
        job.completed_at = _dt.now(_tz.utc)
        await session.commit()
    return {"id": job_id, "status": "cancelled"}


async def _get_live_apscheduler_jobs() -> dict[str, str | None]:
    """Query APScheduler's own 'schedules' table directly.

    Returns {job_id: next_fire_time_isoformat_or_None}.
    This avoids importing the apscheduler package in the API container.
    """
    try:
        async with async_session() as session:
            rows = await session.execute(
                text("SELECT id, next_fire_time FROM schedules")
            )
            return {
                row.id: row.next_fire_time.isoformat() if row.next_fire_time else None
                for row in rows
            }
    except Exception as e:
        logger.warning("Could not query APScheduler schedules table: %s", e)
        return {}


async def _remove_apscheduler_job(job_id: str) -> bool:
    """Delete a job from APScheduler's own 'schedules' table directly."""
    try:
        async with async_session() as session:
            await session.execute(
                text("DELETE FROM schedules WHERE id = :job_id"),
                {"job_id": job_id},
            )
            await session.commit()
        return True
    except Exception as e:
        logger.warning("Could not delete APScheduler job %s: %s", job_id, e)
        return False


async def _get_quality_info() -> QualityInfo:
    """Read quality scores from Redis for any user (dashboard-wide)."""
    try:
        r = await _get_redis()
        # Scan for quality_scores:* keys
        scores: List[float] = []
        async for key in r.scan_iter("quality_scores:*"):
            raw = await r.lrange(key, -20, -1)
            scores.extend(float(s) for s in raw)
        scores = scores[-20:]  # keep last 20 across all users
        if not scores:
            return QualityInfo(recent_scores=[], average=None, trend=None)
        avg = sum(scores) / len(scores)
        trend = "stable"
        if len(scores) >= 6:
            first_half = sum(scores[: len(scores) // 2]) / (len(scores) // 2)
            second_half = sum(scores[len(scores) // 2 :]) / (len(scores) - len(scores) // 2)
            if second_half - first_half > 0.1:
                trend = "improving"
            elif first_half - second_half > 0.1:
                trend = "declining"
        return QualityInfo(recent_scores=scores, average=round(avg, 3), trend=trend)
    except Exception as e:
        logger.warning("Failed to read quality from Redis: %s", e)
        return QualityInfo(recent_scores=[], average=None, trend=None)


# ══════════════════════════════════════════════════════════════════════
#  PHASE B — ORGANIZATION ENDPOINTS
# ══════════════════════════════════════════════════════════════════════


async def _log_org_activity(
    session: AsyncSession, org_id: int, action: str,
    details: str = "", agent_id: int | None = None,
    task_id: int | None = None, source: str = "dashboard",
) -> None:
    session.add(OrgActivity(
        org_id=org_id, agent_id=agent_id, task_id=task_id,
        action=action, details=details, source=source,
    ))


async def _resolve_dashboard_user(
    session: AsyncSession,
    x_telegram_id: Optional[int],
) -> User:
    """Resolve dashboard request user (header first, then owner fallback)."""
    if x_telegram_id is not None:
        user_r = await session.execute(
            select(User).where(User.telegram_id == x_telegram_id).limit(1)
        )
        user = user_r.scalar_one_or_none()
        if user:
            return user

    owner_r = await session.execute(
        select(User).where(User.is_owner == True).limit(1)  # noqa: E712
    )
    owner = owner_r.scalar_one_or_none()
    if owner:
        return owner

    user_r = await session.execute(select(User).limit(1))
    fallback = user_r.scalar_one_or_none()
    if fallback:
        return fallback

    raise HTTPException(status_code=400, detail="No users found in database")


async def _get_owned_org_or_404(
    session: AsyncSession,
    org_id: int,
    owner_user_id: int,
) -> Organization:
    org_r = await session.execute(
        select(Organization).where(
            Organization.id == org_id,
            Organization.owner_user_id == owner_user_id,
        )
    )
    org = org_r.scalar_one_or_none()
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")
    return org


@app.get("/api/orgs", response_model=List[OrgResponse])
async def list_orgs(
    status: Optional[str] = None,
    x_telegram_id: Optional[int] = Header(default=None, alias="X-Telegram-Id"),
):
    """List all organizations with counts."""
    async with async_session() as session:
        requester = await _resolve_dashboard_user(session, x_telegram_id)
        stmt = select(Organization).where(
            Organization.owner_user_id == requester.id,
            Organization.name != "__retained__",
        )
        if status:
            stmt = stmt.where(Organization.status == status)
        stmt = stmt.order_by(desc(Organization.created_at))
        result = await session.execute(stmt)
        orgs = result.scalars().all()

        responses = []
        for org in orgs:
            agent_r = await session.execute(
                select(func.count(OrgAgent.id)).where(OrgAgent.org_id == org.id)
            )
            task_r = await session.execute(
                select(func.count(OrgTask.id)).where(OrgTask.org_id == org.id)
            )
            done_r = await session.execute(
                select(func.count(OrgTask.id)).where(
                    OrgTask.org_id == org.id, OrgTask.status == "completed"
                )
            )
            responses.append(OrgResponse(
                id=org.id, name=org.name, description=org.description,
                goal=org.goal, status=org.status, config=org.config,
                budget_cap_usd=float(org.budget_cap_usd or 0),
                created_at=org.created_at, updated_at=org.updated_at,
                agent_count=agent_r.scalar() or 0,
                task_count=task_r.scalar() or 0,
                completed_tasks=done_r.scalar() or 0,
            ))
    return responses


@app.get("/api/agents/library", response_model=List[OrgAgentResponse])
async def list_reusable_agents(
    x_telegram_id: Optional[int] = Header(default=None, alias="X-Telegram-Id"),
):
    """List all agents across the owner's organizations (for reuse/clone UIs)."""
    async with async_session() as session:
        requester = await _resolve_dashboard_user(session, x_telegram_id)
        # All orgs owned by requester
        orgs_r = await session.execute(select(Organization.id).where(Organization.owner_user_id == requester.id))
        org_ids = [row[0] for row in orgs_r]
        if not org_ids:
            return []
        result = await session.execute(
            select(OrgAgent).where(OrgAgent.org_id.in_(org_ids)).order_by(OrgAgent.created_at)
        )
        agents = result.scalars().all()
    library_resp = []
    for a in agents:
        tc = a.tools_config or {}
        library_resp.append(
            OrgAgentResponse(
                id=a.id, org_id=a.org_id, name=a.name, role=a.role,
                description=a.description, instructions=a.instructions,
                tools_config=a.tools_config, model_tier=a.model_tier,
                status=a.status, created_at=a.created_at,
                skills=list(tc.get("skills", [])),
                allowed_tools=list(tc.get("allowed_tools", [])),
            )
        )
    return library_resp


@app.post("/api/orgs/setup", response_model=OrgProjectSetupResponse, status_code=201)
async def setup_org_project_endpoint(
    body: OrgProjectSetupRequest,
    x_telegram_id: Optional[int] = Header(default=None, alias="X-Telegram-Id"),
):
    """Auto-plan and create a complete project from a plain-English goal.

    If body.plan is provided (pre-built from /api/orgs/plan-stream), the LLM
    call is skipped and the pre-built plan is used directly — avoids a double
    LLM call when the user reviewed the streaming preview first.
    """
    async with async_session() as session:
        requester = await _resolve_dashboard_user(session, x_telegram_id)
        owner_id = requester.id

        if body.org_id:
            org = await _get_owned_org_or_404(session, body.org_id, owner_id)
            created_org_flag = False
        else:
            org = None
            created_org_flag = True

    # ── Plan: use pre-built or call LLM ──────────────────────────────
    if body.plan:
        plan = body.plan
        if not plan.get("agents"):
            raise HTTPException(status_code=422, detail="Pre-built plan has no agents")
    else:
        plan = await _build_org_plan(body.goal, org_name=body.org_name)

    planned_agents: list[dict] = plan.get("agents") or []
    planned_tasks: list[dict] = plan.get("tasks") or []

    resolved_name = body.org_name or plan.get("org_name") or "New Project"
    resolved_goal = plan.get("org_goal") or body.goal
    suggested_budget = plan.get("budget_cap_usd")

    # ── Atomic DB creation ────────────────────────────────────────────
    async with async_session() as session:
        if org is None:
            org = Organization(
                name=resolved_name, goal=resolved_goal,
                owner_user_id=owner_id, status="active",
                budget_cap_usd=suggested_budget,
            )
            session.add(org)
            await session.flush()
            await _log_org_activity(session, org.id, "org_created",
                f"Organization '{resolved_name}' created by project setup")

        agent_map: dict[str, OrgAgent] = {}
        for ap in planned_agents:
            tc: dict = {}
            if ap.get("skills"):
                tc["skills"] = [s.strip() for s in ap["skills"] if s.strip()]
            if ap.get("allowed_tools"):
                tc["allowed_tools"] = [t.strip() for t in ap["allowed_tools"] if t.strip()]
            model_tier = ap.get("model_tier", "general")
            if model_tier not in ("fast", "general", "capable"):
                model_tier = "general"
            db_agent = OrgAgent(
                org_id=org.id,
                name=ap.get("name", "Agent").strip(),
                role=ap.get("role", "specialist").strip(),
                description=ap.get("description"),
                instructions=ap.get("instructions"),
                tools_config=tc if tc else None,
                model_tier=model_tier,
                status="active",
            )
            session.add(db_agent)
            await session.flush()
            await _log_org_activity(session, org.id, "agent_created",
                f"Agent '{db_agent.name}' added by project setup", agent_id=db_agent.id)
            agent_map[db_agent.name] = db_agent

        created_tasks_meta: list[dict] = []
        for tp in planned_tasks:
            assigned = agent_map.get(tp.get("agent_name", ""))
            priority = tp.get("priority", "medium")
            if priority not in ("high", "medium", "low"):
                priority = "medium"
            now = datetime.now(timezone.utc)
            raw_ancestry = tp.get("goal_ancestry")
            ancestry = raw_ancestry if isinstance(raw_ancestry, list) else None
            db_task = OrgTask(
                org_id=org.id,
                agent_id=assigned.id if assigned else None,
                title=tp.get("title", "Task").strip(),
                description=tp.get("description"),
                priority=priority,
                status="in_progress" if assigned else "pending",
                source="dashboard",
                assigned_at=now if assigned else None,
                goal_ancestry=ancestry,
            )
            session.add(db_task)
            await session.flush()
            await _log_org_activity(session, org.id, "task_created",
                f"Task '{db_task.title}' created by project setup",
                task_id=db_task.id, agent_id=db_task.agent_id)
            created_tasks_meta.append({
                "id": db_task.id,
                "title": db_task.title,
                "priority": db_task.priority,
                "status": db_task.status,
                "agent_name": assigned.name if assigned else None,
                "goal_ancestry": ancestry,
            })

        await session.commit()
        final_org_id = org.id
        final_org_name = org.name

    # ── Write ScheduledTask DB rows for tasks with a schedule field ───
    # The assistant's sync_tasks_from_db job (every 30s) picks these up
    # and registers them with APScheduler — no direct scheduler call needed.
    scheduled_job_count = 0
    try:
        import uuid as _uuid
        from datetime import datetime as _dt, timezone as _tz
        async with async_session() as sched_session:
            user_result = await sched_session.execute(
                select(User).where(User.id == owner_id)
            )
            sched_user = user_result.scalar_one_or_none()
            if sched_user:
                for tp, task_meta in zip(planned_tasks, created_tasks_meta):
                    sched = tp.get("schedule")
                    if not sched or not isinstance(sched, dict):
                        continue
                    trigger = sched.get("trigger")
                    if trigger not in ("cron", "interval", "once"):
                        continue
                    job_id = f"org{final_org_id}_task{task_meta['id']}_{_uuid.uuid4().hex[:8]}"
                    desc_text = tp.get("title", "Scheduled org task")

                    if trigger == "cron":
                        trigger_config = {"cron": {
                            "hour": int(sched.get("hour", 8)),
                            "minute": int(sched.get("minute", 0)),
                            "day_of_week": str(sched.get("day_of_week", "*")),
                        }}
                    elif trigger == "interval":
                        trigger_config = {"interval": {"seconds": int(sched.get("seconds", 3600))}}
                    else:  # once
                        run_at_str = sched.get("run_at")
                        if run_at_str:
                            try:
                                run_at_dt = _dt.fromisoformat(run_at_str)
                            except ValueError:
                                run_at_dt = _dt.now(_tz.utc)
                        else:
                            run_at_dt = _dt.now(_tz.utc)
                        if run_at_dt.tzinfo is None:
                            run_at_dt = run_at_dt.replace(tzinfo=_tz.utc)
                        trigger_config = {"once": {"run_at": run_at_dt.isoformat()}}

                    # Build a prompt from the task title + description for the agent
                    task_prompt = f"{desc_text}: {tp.get('description', '')}".strip(": ")
                    db_sched = ScheduledTask(
                        user_id=sched_user.id,
                        apscheduler_id=job_id,
                        description=desc_text,
                        trigger_type=trigger,
                        trigger_config=trigger_config,
                        job_function="src.scheduler.jobs:run_agent_task",
                        job_args={"user_id": sched_user.id, "prompt": task_prompt},
                        is_active=True,
                    )
                    sched_session.add(db_sched)
                    scheduled_job_count += 1
                    logger.info("Queued scheduled job %s for org task '%s' (trigger=%s)", job_id, desc_text, trigger)
                await sched_session.commit()
    except Exception as e:
        logger.warning("Schedule DB write failed (non-fatal): %s", e)

    # ── Cohesion validation via shared helper ─────────────────────────
    async with async_session() as vsession:
        val_result = await _run_cohesion_validation(final_org_id, vsession)

    agents_meta = [
        {
            "id": a["agent_id"], "name": a["agent_name"], "role": a["role"],
            "model_tier": a["model_tier"],
            "skills": list(a["skills"].keys()),
            "allowed_tools": list(a["tools"].keys()),
            "validation": {"skills": a["skills"], "tools": a["tools"]},
        }
        for a in val_result.agent_summary
    ]

    val_suffix = (
        " ⚠️ Validation issues: " + "; ".join(val_result.warnings[:3])
        if val_result.warnings
        else f" ✅ Cohesion score: {val_result.score}/100."
    )
    sched_suffix = f" {scheduled_job_count} scheduled job(s) registered." if scheduled_job_count else ""
    summary_lines = [
        f"Project '{final_org_name}' set up with {len(agents_meta)} agents and {len(created_tasks_meta)} tasks.{val_suffix}{sched_suffix}"
    ]
    return OrgProjectSetupResponse(
        org_id=final_org_id,
        org_name=final_org_name,
        created_org=created_org_flag,
        agents=agents_meta,
        tasks=created_tasks_meta,
        scheduled_jobs=scheduled_job_count,
        summary=" ".join(summary_lines),
    )


# ── Shared planning helper (used by SSE endpoint + Telegram) ─────────

_BUILTIN_SKILLS: frozenset[str] = frozenset({
    "memory", "scheduler", "organizations",
    "gmail", "calendar", "google_tasks", "drive",
    "google_sheets", "google_docs", "google_slides", "google_contacts",
})


def _get_known_skills() -> frozenset[str]:
    """Return all known skill IDs: builtins + user-created filesystem skills."""
    from pathlib import Path
    user_skills_dir = Path("src/user_skills")
    user_skill_ids: set[str] = set()
    if user_skills_dir.exists():
        for item in user_skills_dir.iterdir():
            if item.is_dir() and (item / "SKILL.md").exists():
                user_skill_ids.add(item.name)
    return _BUILTIN_SKILLS | frozenset(user_skill_ids)

_BUILTIN_TOOL_NAMES: frozenset[str] = frozenset({
    "recall_my_memories", "store_my_memory", "list_my_memories",
    "forget_my_memory", "forget_all_my_memories",
    "summarize_my_conversation", "get_my_recent_context",
    "create_my_reminder", "create_my_morning_brief",
    "list_my_schedules", "cancel_my_schedule",
})

_PLANNING_PROMPT_TEMPLATE = """\
You are a project planner for an AI personal assistant called Atlas.

User goal: {goal}
{description_block}

Produce a JSON execution plan with this exact schema:
{{
  "org_name": "<short project name, max 6 words>",
  "org_goal": "<one-sentence mission statement>",
  "budget_cap_usd": <suggested monthly USD budget as a number, 0 if free>,
  "agents": [
    {{
      "name": "<Agent Name>",
      "role": "<slug_role>",
      "description": "<one sentence>",
      "instructions": "<detailed operating instructions for this agent>",
      "model_tier": "<fast|general|capable>",
      "skills": ["<skill_id>", ...],
      "allowed_tools": ["<tool_name>", ...]
    }}
  ],
  "tasks": [
    {{
      "title": "<task title>",
      "description": "<what to do>",
      "priority": "<high|medium|low>",
      "agent_name": "<must match an agent name above>",
      "goal_ancestry": ["<org:name>", "<goal:objective>", "<task:title>"],
      "schedule": {{
        "trigger": "<cron|interval|once|null — omit key if no schedule>",
        "hour": <0-23 for cron, omit otherwise>,
        "minute": <0-59 for cron, omit otherwise>,
        "day_of_week": "<mon-fri|* — for cron, omit otherwise>",
        "seconds": <integer for interval trigger, omit otherwise>,
        "run_at": "<ISO8601 datetime for once trigger, omit otherwise>"
      }}
    }}
  ]
}}

Rules:
- 2-5 agents, 4-10 tasks
- Each task must reference an agent_name that exists in the agents list
- goal_ancestry traces the task back to the org goal (3 levels)
- model_tier: fast=quick lookups, general=normal work, capable=complex reasoning
- CRITICAL: If the user goal mentions a time, frequency, or recurring action (e.g. "every day at 8am", "weekly", "every morning"), you MUST add a "schedule" field to the primary recurring task. Use trigger "cron" for daily/weekly patterns.
- NEVER create a "SchedulerAgent" — scheduling is handled by the platform. Focus agents on their domain work (fetching, processing, sending).
- PIPELINE RULE: Do NOT create separate tasks for steps of the same pipeline (e.g. do NOT split "fetch news", "send email", "notify Telegram" into 3 tasks). Instead, combine them into ONE task with a comprehensive description that instructs the agent to do ALL steps in sequence: fetch → process → send email → notify. The agent prompt is the task description — make it cover the full end-to-end flow.
- Example schedule for "every day at 8am": {{"trigger": "cron", "hour": 8, "minute": 0, "day_of_week": "*"}}
- Example schedule for "every Monday at 9am": {{"trigger": "cron", "hour": 9, "minute": 0, "day_of_week": "mon"}}

Available skill IDs (ONLY use exact strings from this list):
  Internal: memory, scheduler, organizations
  Google Workspace: gmail, calendar, google_tasks, drive, google_sheets, google_docs, google_slides, google_contacts

Available tool names (ONLY use exact strings from this list):
  Memory: recall_my_memories, store_my_memory, list_my_memories, forget_my_memory, summarize_my_conversation, get_my_recent_context
  Scheduler: create_my_reminder, list_my_schedules, cancel_my_schedule
  Plugin tools: browser, linkedin, onedrive

IMPORTANT: Do NOT invent skill IDs or tool names. Respond with raw JSON only.\
"""


async def _build_org_plan(
    goal: str,
    description: Optional[str] = None,
    org_name: Optional[str] = None,
) -> dict:
    """Call the LLM planning model and return the parsed plan dict.

    Raises HTTPException(502) on LLM failure, HTTPException(422) on empty plan.
    """
    import json
    from openai import AsyncOpenAI

    desc_block = f"Description: {description}" if description else ""
    prompt = _PLANNING_PROMPT_TEMPLATE.format(
        goal=goal,
        description_block=desc_block,
    )

    plan_model = os.environ.get("PLAN_MODEL", "gpt-4o-mini")
    client = AsyncOpenAI()
    try:
        resp = await client.chat.completions.create(
            model=plan_model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
            response_format={"type": "json_object"},
        )
        plan = json.loads(resp.choices[0].message.content or "{}")
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Planning LLM call failed: {exc}")

    if not plan.get("agents"):
        raise HTTPException(status_code=422, detail="LLM returned an empty plan — try a more detailed goal")

    if org_name:
        plan["org_name"] = org_name

    return plan


async def _stream_org_plan(
    goal: str,
    description: Optional[str] = None,
    org_name: Optional[str] = None,
):
    """Async generator for SSE: streams agent/task objects as they complete, then sends done."""
    import json
    from openai import AsyncOpenAI

    desc_block = f"Description: {description}" if description else ""
    prompt = _PLANNING_PROMPT_TEMPLATE.format(
        goal=goal,
        description_block=desc_block,
    )

    plan_model = os.environ.get("PLAN_MODEL", "gpt-4o-mini")
    client = AsyncOpenAI()

    # Send a keepalive comment immediately so the browser EventSource
    # doesn't time out while waiting for the LLM to start responding.
    yield ": keepalive\n\n"

    full_text = ""
    try:
        stream = await client.chat.completions.create(
            model=plan_model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
            response_format={"type": "json_object"},
            stream=True,
        )
        async for chunk in stream:
            delta = chunk.choices[0].delta.content or ""
            full_text += delta
    except Exception as exc:
        logger.exception("plan-stream LLM call failed: %s", exc)
        yield f"event: error\ndata: {json.dumps({'detail': str(exc)})}\n\n"
        return

    # Parse complete JSON — strip markdown fences as a safety net
    raw = full_text.strip()
    if raw.startswith("```"):
        # Strip opening fence (```json or ```) and closing fence
        raw = raw.split("\n", 1)[-1]
        if raw.endswith("```"):
            raw = raw[: raw.rfind("```")].rstrip()
    try:
        plan = json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.error("plan-stream JSON parse failed. raw=%r error=%s", full_text[:500], exc)
        yield f"event: error\ndata: {json.dumps({'detail': 'LLM returned invalid JSON — try rephrasing your goal'})}\n\n"
        return

    if org_name:
        plan["org_name"] = org_name

    for agent in plan.get("agents") or []:
        payload = json.dumps({"type": "agent", **agent})
        yield f"data: {payload}\n\n"

    for task in plan.get("tasks") or []:
        payload = json.dumps({"type": "task", **task})
        yield f"data: {payload}\n\n"

    # Final event carries complete plan
    yield f"event: done\ndata: {json.dumps(plan)}\n\n"


@app.get("/api/orgs/plan-stream")
async def plan_stream_endpoint(
    goal: str = Query(..., min_length=5, max_length=2000),
    description: Optional[str] = Query(default=None, max_length=1000),
    org_name: Optional[str] = Query(default=None, max_length=200),
    x_telegram_id: Optional[int] = Header(default=None, alias="X-Telegram-Id"),
):
    """SSE endpoint: streams the AI-generated org plan object-by-object.

    Client opens an EventSource. Objects arrive as `data:` lines.
    A final `event: done` carries the complete plan JSON for use with /api/orgs/setup.
    """
    return StreamingResponse(
        _stream_org_plan(goal, description, org_name),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ── M3: Standalone cohesion validation ───────────────────────────────

async def _run_cohesion_validation(org_id: int, session) -> OrgValidationResponse:
    """Validate all agents, skills, and tools in an org for cohesion.

    Checks:
    - All skill IDs are registered
    - All tool names are installed
    - All tasks have an assigned agent
    - No agents are completely unused (no tasks)
    Returns a score 0-100.
    """
    from pathlib import Path as _Path

    # Build known tools set
    try:
        from src.db.models import Tool as _T
        _db_t = (await session.execute(select(_T).where(_T.is_active == True))).scalars().all()  # noqa: E712
        known_tools: set[str] = {t.name for t in _db_t}
    except Exception:
        known_tools = set()
    plugin_dir = _Path("src/tools/plugins")
    if plugin_dir.exists():
        known_tools |= {p.name for p in plugin_dir.iterdir() if p.is_dir() and not p.name.startswith("_")}
    known_tools |= set(_BUILTIN_TOOL_NAMES)

    agents_r = await session.execute(select(OrgAgent).where(OrgAgent.org_id == org_id))
    agents = agents_r.scalars().all()
    tasks_r = await session.execute(select(OrgTask).where(OrgTask.org_id == org_id))
    tasks = tasks_r.scalars().all()

    warnings: list[str] = []
    errors: list[str] = []
    agent_summary: list[dict] = []

    assigned_agent_ids = {t.agent_id for t in tasks if t.agent_id}
    unassigned_tasks = [t for t in tasks if not t.agent_id]

    for agent in agents:
        tc = agent.tools_config or {}
        skill_results: dict[str, str] = {}
        tool_results: dict[str, str] = {}

        for sk in tc.get("skills", []):
            ok = sk in _get_known_skills()
            skill_results[sk] = "✅" if ok else "⚠️ not registered"
            if not ok:
                warnings.append(f"Agent '{agent.name}': skill '{sk}' not registered")

        for tn in tc.get("allowed_tools", []):
            ok = tn in known_tools
            tool_results[tn] = "✅" if ok else "⚠️ not installed"
            if not ok:
                warnings.append(f"Agent '{agent.name}': tool '{tn}' not installed")

        has_tasks = agent.id in assigned_agent_ids
        if not has_tasks:
            warnings.append(f"Agent '{agent.name}' has no tasks assigned")

        agent_summary.append({
            "agent_id": agent.id,
            "agent_name": agent.name,
            "role": agent.role,
            "model_tier": agent.model_tier,
            "skills": skill_results,
            "tools": tool_results,
            "has_tasks": has_tasks,
        })

    for t in unassigned_tasks:
        errors.append(f"Task '{t.title}' has no assigned agent")

    # Score: start at 100, deduct per issue
    deductions = len(errors) * 15 + len(warnings) * 5
    score = max(0, 100 - deductions)
    valid = len(errors) == 0 and score >= 60

    return OrgValidationResponse(
        org_id=org_id,
        valid=valid,
        score=score,
        warnings=warnings,
        errors=errors,
        agent_summary=agent_summary,
    )


@app.post("/api/orgs/{org_id}/validate", response_model=OrgValidationResponse)
async def validate_org_cohesion(
    org_id: int,
    x_telegram_id: Optional[int] = Header(default=None, alias="X-Telegram-Id"),
):
    """Run cohesion validation on all agents, skills, and tools in an org.

    Returns a score (0-100), list of warnings, and list of errors.
    Safe to call at any time — read-heavy, no mutations.
    """
    async with async_session() as session:
        requester = await _resolve_dashboard_user(session, x_telegram_id)
        await _get_owned_org_or_404(session, org_id, requester.id)
        result = await _run_cohesion_validation(org_id, session)
    return result


@app.get("/api/orgs/deep-repair-stream")
async def deep_repair_stream(request: Request):
    """SSE endpoint: runs 4 independent LLM repair loops on a plan.

    The client sends the plan as a JSON query param (base64-encoded) or
    calls POST /api/orgs/deep-repair-plan for the non-streaming version.

    Loop order (VIGIL stage-gate pattern):
      1. Instructions — judge each agent's instructions for clarity/completeness
      2. Skills        — judge each agent's skill assignments vs its role
      3. Tools         — judge each agent's tool assignments vs its tasks
      4. Schedule      — judge each scheduled task's cron config vs its goal
    """
    import base64, json as _json
    raw = request.query_params.get("plan", "")
    try:
        body = _json.loads(base64.b64decode(raw).decode())
    except Exception:
        async def _err():
            yield "event: error\ndata: {\"msg\": \"Invalid plan payload\"}\n\n"
        return StreamingResponse(_err(), media_type="text/event-stream")
    return StreamingResponse(
        _deep_repair_generator(body),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/api/orgs/deep-repair-plan")
async def deep_repair_plan(body: dict):
    """Non-streaming deep repair: runs all 4 LLM loops, returns repaired plan + full audit log."""
    events = []
    async for chunk in _deep_repair_generator(body):
        if chunk.startswith("data:"):
            try:
                import json as _j
                events.append(_j.loads(chunk[5:].strip()))
            except Exception:
                pass
    # Last "done" event has the final plan + summary
    done_evt = next((e for e in reversed(events) if e.get("type") == "done"), None)
    if done_evt:
        return done_evt
    return {"error": "Repair did not complete", "events": events}


async def _deep_repair_generator(body: dict):
    """Core async generator: 4 independent LLM repair loops, yields SSE events."""
    import json as _json
    from openai import AsyncOpenAI

    known_skills = sorted(_get_known_skills())
    known_tools = sorted(_BUILTIN_TOOL_NAMES)
    plan_model = os.environ.get("PLAN_MODEL", "gpt-4o-mini")
    client = AsyncOpenAI()
    org_goal = body.get("org_goal") or body.get("goal") or ""

    agents = [dict(a) for a in (body.get("agents") or [])]
    tasks = [dict(t) for t in (body.get("tasks") or [])]
    audit: list[dict] = []  # full audit trail

    def _sse(event_type: str, data: dict) -> str:
        return f"event: {event_type}\ndata: {_json.dumps(data)}\n\n"

    async def _llm(system: str, user: str, temperature: float = 0.3) -> str:
        """Single focused LLM call, returns text."""
        resp = await client.chat.completions.create(
            model=plan_model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=temperature,
            response_format={"type": "json_object"},
        )
        return (resp.choices[0].message.content or "{}").strip()

    # ── keepalive ────────────────────────────────────────────────────
    yield ": keepalive\n\n"

    # ═══════════════════════════════════════════════════════
    # LOOP 1: INSTRUCTIONS — judge + rewrite per agent
    # ═══════════════════════════════════════════════════════
    yield _sse("loop_start", {"loop": "instructions", "label": "Agent Instructions", "total": len(agents)})

    for i, agent in enumerate(agents):
        aname = agent.get("name", f"Agent{i}")
        instructions = agent.get("instructions") or ""
        role = agent.get("role") or ""
        desc = agent.get("description") or ""

        yield _sse("loop_item", {"loop": "instructions", "index": i, "name": aname, "status": "evaluating"})

        system_prompt = (
            "You are an expert AI agent architect. Evaluate the operating instructions for an agent "
            "and rewrite them if they are weak, vague, or missing key steps. "
            "Respond with JSON: {\"score\": 0-100, \"issues\": [\"...\"], \"improved_instructions\": \"...\" | null}"
        )
        user_prompt = (
            f"Org goal: {org_goal}\n"
            f"Agent name: {aname}\nRole: {role}\nDescription: {desc}\n\n"
            f"Current instructions:\n{instructions or '(none provided)'}\n\n"
            "Score the instructions 0-100 (100=excellent, <70=needs improvement). "
            "If score < 70, rewrite 'improved_instructions' as a clear, actionable, step-by-step prompt "
            "for this agent that covers: what data to fetch, how to process it, what to output, and error handling. "
            "If score >= 70, set improved_instructions to null."
        )
        try:
            raw = await _llm(system_prompt, user_prompt, temperature=0.4)
            result = _json.loads(raw)
            score = int(result.get("score", 70))
            issues = result.get("issues") or []
            improved = result.get("improved_instructions")

            if improved and score < 70:
                agent["instructions"] = improved
                agents[i] = agent
                audit.append({"loop": "instructions", "agent": aname, "score_before": score, "action": "rewritten", "issues": issues})
                yield _sse("loop_item", {"loop": "instructions", "index": i, "name": aname,
                                         "status": "fixed", "score": score, "issues": issues,
                                         "msg": f"Instructions rewritten (was {score}/100)"})
            else:
                audit.append({"loop": "instructions", "agent": aname, "score_before": score, "action": "ok"})
                yield _sse("loop_item", {"loop": "instructions", "index": i, "name": aname,
                                         "status": "ok", "score": score, "msg": f"Instructions OK ({score}/100)"})
        except Exception as exc:
            audit.append({"loop": "instructions", "agent": aname, "action": "error", "error": str(exc)})
            yield _sse("loop_item", {"loop": "instructions", "index": i, "name": aname,
                                     "status": "error", "msg": str(exc)})

    yield _sse("loop_done", {"loop": "instructions"})

    # ═══════════════════════════════════════════════════════
    # LOOP 2: SKILLS — judge + reassign per agent
    # ═══════════════════════════════════════════════════════
    yield _sse("loop_start", {"loop": "skills", "label": "Agent Skills", "total": len(agents)})

    for i, agent in enumerate(agents):
        aname = agent.get("name", f"Agent{i}")
        current_skills = agent.get("skills") or []

        yield _sse("loop_item", {"loop": "skills", "index": i, "name": aname, "status": "evaluating"})

        system_prompt = (
            "You are an AI platform skill assignment expert. "
            "Given an agent's role and the catalog of available skills, decide the optimal skill assignments. "
            "Respond with JSON: {\"score\": 0-100, \"issues\": [\"...\"], \"optimal_skills\": [\"skill_id\", ...]}"
        )
        user_prompt = (
            f"Org goal: {org_goal}\n"
            f"Agent: {aname} | Role: {agent.get('role','')} | Description: {agent.get('description','')}\n"
            f"Current skills: {current_skills}\n\n"
            f"Available skill IDs (use ONLY these exact strings):\n{known_skills}\n\n"
            "Score the current skill assignments 0-100. "
            "Return 'optimal_skills' as the best list of skill IDs from the catalog for this agent's purpose. "
            "An agent that sends email must have 'gmail'. An agent that uses memory must have 'memory'. "
            "Keep it minimal — 1-3 skills max. ONLY use IDs from the catalog."
        )
        try:
            raw = await _llm(system_prompt, user_prompt)
            result = _json.loads(raw)
            score = int(result.get("score", 70))
            issues = result.get("issues") or []
            optimal = result.get("optimal_skills") or []
            # Validate LLM output — only keep catalog members
            valid_optimal = [s for s in optimal if s in known_skills]
            if not valid_optimal:
                valid_optimal = current_skills or ["memory"]

            changed = set(valid_optimal) != set(current_skills)
            agent["skills"] = valid_optimal
            agents[i] = agent

            if changed or score < 70:
                audit.append({"loop": "skills", "agent": aname, "score_before": score,
                               "action": "reassigned", "before": current_skills, "after": valid_optimal, "issues": issues})
                yield _sse("loop_item", {"loop": "skills", "index": i, "name": aname,
                                         "status": "fixed", "score": score,
                                         "before": current_skills, "after": valid_optimal, "issues": issues})
            else:
                audit.append({"loop": "skills", "agent": aname, "score_before": score, "action": "ok"})
                yield _sse("loop_item", {"loop": "skills", "index": i, "name": aname,
                                         "status": "ok", "score": score, "msg": f"Skills OK ({score}/100)"})
        except Exception as exc:
            audit.append({"loop": "skills", "agent": aname, "action": "error", "error": str(exc)})
            yield _sse("loop_item", {"loop": "skills", "index": i, "name": aname,
                                     "status": "error", "msg": str(exc)})

    yield _sse("loop_done", {"loop": "skills"})

    # ═══════════════════════════════════════════════════════
    # LOOP 3: TOOLS — judge + reassign per agent
    # ═══════════════════════════════════════════════════════
    yield _sse("loop_start", {"loop": "tools", "label": "Agent Tools", "total": len(agents)})

    for i, agent in enumerate(agents):
        aname = agent.get("name", f"Agent{i}")
        current_tools = agent.get("allowed_tools") or []

        yield _sse("loop_item", {"loop": "tools", "index": i, "name": aname, "status": "evaluating"})

        system_prompt = (
            "You are an AI platform tool assignment expert. "
            "Given an agent's tasks and the tool catalog, choose the minimal correct tool set. "
            "Respond with JSON: {\"score\": 0-100, \"issues\": [\"...\"], \"optimal_tools\": [\"tool_name\", ...]}"
        )
        # Find tasks belonging to this agent
        agent_tasks = [t.get("title", "") for t in tasks if t.get("agent_name") == aname]
        user_prompt = (
            f"Org goal: {org_goal}\n"
            f"Agent: {aname} | Role: {agent.get('role','')} | Skills: {agent.get('skills','')}\n"
            f"Agent's tasks: {agent_tasks}\n"
            f"Current tools: {current_tools}\n\n"
            f"Available tools (use ONLY these exact names):\n{known_tools}\n\n"
            "Score the current tool assignments 0-100. "
            "Return 'optimal_tools' with the minimal set of tools needed. "
            "An agent that stores results needs 'store_my_memory'. "
            "An agent managing schedules needs 'create_my_reminder' and 'list_my_schedules'. "
            "ONLY use tool names from the catalog above."
        )
        try:
            raw = await _llm(system_prompt, user_prompt)
            result = _json.loads(raw)
            score = int(result.get("score", 70))
            issues = result.get("issues") or []
            optimal = result.get("optimal_tools") or []
            valid_optimal = [t for t in optimal if t in known_tools]
            if not valid_optimal:
                valid_optimal = [t for t in current_tools if t in known_tools] or ["recall_my_memories"]

            changed = set(valid_optimal) != set(current_tools)
            agent["allowed_tools"] = valid_optimal
            agents[i] = agent

            if changed or score < 70:
                audit.append({"loop": "tools", "agent": aname, "score_before": score,
                               "action": "reassigned", "before": current_tools, "after": valid_optimal, "issues": issues})
                yield _sse("loop_item", {"loop": "tools", "index": i, "name": aname,
                                         "status": "fixed", "score": score,
                                         "before": current_tools, "after": valid_optimal, "issues": issues})
            else:
                audit.append({"loop": "tools", "agent": aname, "action": "ok", "score_before": score})
                yield _sse("loop_item", {"loop": "tools", "index": i, "name": aname,
                                         "status": "ok", "score": score, "msg": f"Tools OK ({score}/100)"})
        except Exception as exc:
            audit.append({"loop": "tools", "agent": aname, "action": "error", "error": str(exc)})
            yield _sse("loop_item", {"loop": "tools", "index": i, "name": aname,
                                     "status": "error", "msg": str(exc)})

    yield _sse("loop_done", {"loop": "tools"})

    # ═══════════════════════════════════════════════════════
    # LOOP 4: SCHEDULE — judge + fix per scheduled task
    # ═══════════════════════════════════════════════════════
    scheduled_tasks = [t for t in tasks if t.get("schedule") and isinstance(t.get("schedule"), dict)]
    yield _sse("loop_start", {"loop": "schedule", "label": "Task Schedules", "total": len(scheduled_tasks)})

    for i, task in enumerate(tasks):
        sched = task.get("schedule")
        if not sched or not isinstance(sched, dict):
            continue
        ttitle = task.get("title", f"Task{i}")
        yield _sse("loop_item", {"loop": "schedule", "index": i, "name": ttitle, "status": "evaluating"})

        system_prompt = (
            "You are a scheduling expert for AI automation pipelines. "
            "Evaluate whether a task's cron schedule makes sense for its goal. "
            "Respond with JSON: {\"score\": 0-100, \"issues\": [\"...\"], \"optimal_schedule\": {schedule_obj} | null}\n"
            "schedule_obj shape: {\"trigger\": \"cron\", \"hour\": 0-23, \"minute\": 0-59, \"day_of_week\": \"*|mon|tue...\"}"
        )
        user_prompt = (
            f"Org goal: {org_goal}\n"
            f"Task: {ttitle}\nDescription: {task.get('description','')}\n"
            f"Current schedule: {_json.dumps(sched)}\n\n"
            "Score the schedule configuration 0-100. "
            "Issues to check: Does the trigger type match the frequency implied by the goal? "
            "Is the hour/minute appropriate for the user's timezone (assume US/Central)? "
            "Is day_of_week correct (weekday-only vs daily)? "
            "If score < 80, return 'optimal_schedule' with the corrected config. "
            "Otherwise return optimal_schedule as null."
        )
        try:
            raw = await _llm(system_prompt, user_prompt, temperature=0.2)
            result = _json.loads(raw)
            score = int(result.get("score", 80))
            issues = result.get("issues") or []
            optimal = result.get("optimal_schedule")

            if optimal and isinstance(optimal, dict) and score < 80:
                # Validate trigger
                trigger = optimal.get("trigger", "cron")
                if trigger not in ("cron", "interval", "once"):
                    optimal["trigger"] = "cron"
                task["schedule"] = optimal
                # Update in tasks list
                for j, t2 in enumerate(tasks):
                    if t2.get("title") == ttitle:
                        tasks[j]["schedule"] = optimal
                        break
                audit.append({"loop": "schedule", "task": ttitle, "score_before": score,
                               "action": "fixed", "before": sched, "after": optimal, "issues": issues})
                yield _sse("loop_item", {"loop": "schedule", "index": i, "name": ttitle,
                                         "status": "fixed", "score": score,
                                         "before": sched, "after": optimal, "issues": issues})
            else:
                audit.append({"loop": "schedule", "task": ttitle, "score_before": score, "action": "ok"})
                yield _sse("loop_item", {"loop": "schedule", "index": i, "name": ttitle,
                                         "status": "ok", "score": score,
                                         "msg": f"Schedule OK ({score}/100): {_json.dumps(sched)}"})
        except Exception as exc:
            audit.append({"loop": "schedule", "task": ttitle, "action": "error", "error": str(exc)})
            yield _sse("loop_item", {"loop": "schedule", "index": i, "name": ttitle,
                                     "status": "error", "msg": str(exc)})

    yield _sse("loop_done", {"loop": "schedule"})

    # ── Final validation pass ─────────────────────────────────────────
    repaired_plan = {**body, "agents": agents, "tasks": tasks}
    final_val = await repair_validate(repaired_plan)
    fixes_made = sum(1 for a in audit if a.get("action") in ("rewritten", "reassigned", "fixed"))

    yield _sse("done", {
        "type": "done",
        "plan": repaired_plan,
        "validation": final_val,
        "audit": audit,
        "fixes_made": fixes_made,
        "loops_run": 4,
    })


@app.post("/api/orgs/repair-plan")
async def repair_org_plan(body: dict):
    """Self-repair a wizard plan that failed validation.

    Strategy (no extra LLM calls for simple fixes):
    1. Substitute unknown skill IDs with closest valid one (by string prefix/category).
    2. Substitute unknown tool names with closest valid one.
    3. Fix tasks with invalid schedule triggers by normalising to "cron".
    4. Fix tasks whose agent_name doesn't exist by mapping to closest agent.
    5. Re-validate up to MAX_REPAIR_ITERATIONS times, stopping when green.
    Returns the repaired plan and a structured repair log.
    """
    import difflib

    MAX_REPAIR_ITERATIONS = 3
    known_skills = sorted(_get_known_skills())
    known_tools = sorted(_BUILTIN_TOOL_NAMES)

    agents = [dict(a) for a in (body.get("agents") or [])]
    tasks = [dict(t) for t in (body.get("tasks") or [])]
    repair_log: list[dict] = []

    def _best_match(name: str, candidates: list[str]) -> Optional[str]:
        """Return the closest match from candidates, or None if no good match."""
        matches = difflib.get_close_matches(name, candidates, n=1, cutoff=0.4)
        return matches[0] if matches else None

    def _skill_fallback(bad: str) -> str:
        """Map a bad skill to the best valid alternative."""
        # Category heuristics first
        low = bad.lower()
        if any(k in low for k in ("email", "gmail", "mail")):
            return "gmail"
        if any(k in low for k in ("calendar", "schedule", "event")):
            return "calendar"
        if any(k in low for k in ("memory", "mem", "recall")):
            return "memory"
        if any(k in low for k in ("drive", "file", "doc", "sheet", "slide")):
            return "drive"
        if any(k in low for k in ("task", "todo")):
            return "google_tasks"
        if any(k in low for k in ("browser", "web", "search", "news", "fetch")):
            return "memory"  # closest built-in; user should install browser plugin
        best = _best_match(bad, known_skills)
        return best if best else "memory"

    def _tool_fallback(bad: str) -> str:
        """Map a bad tool to the best valid alternative."""
        low = bad.lower()
        if any(k in low for k in ("email", "gmail", "send")):
            return "store_my_memory"
        if any(k in low for k in ("recall", "memory", "remember")):
            return "recall_my_memories"
        if any(k in low for k in ("schedule", "reminder", "cron")):
            return "create_my_reminder"
        best = _best_match(bad, known_tools)
        return best if best else "recall_my_memories"

    for iteration in range(1, MAX_REPAIR_ITERATIONS + 1):
        iteration_fixes: list[str] = []
        agent_names = {a.get("name", "") for a in agents}

        # ── Repair agents: skills + tools ────────────────────────────
        for agent in agents:
            name = agent.get("name", "?")
            new_skills = []
            for sk in list(agent.get("skills") or []):
                if sk in known_skills:
                    new_skills.append(sk)
                else:
                    replacement = _skill_fallback(sk)
                    new_skills.append(replacement)
                    iteration_fixes.append(f"Agent '{name}': replaced skill '{sk}' → '{replacement}'")
            agent["skills"] = list(dict.fromkeys(new_skills))  # dedupe, preserve order

            new_tools = []
            for tn in list(agent.get("allowed_tools") or []):
                if tn in known_tools:
                    new_tools.append(tn)
                else:
                    replacement = _tool_fallback(tn)
                    new_tools.append(replacement)
                    iteration_fixes.append(f"Agent '{name}': replaced tool '{tn}' → '{replacement}'")
            agent["allowed_tools"] = list(dict.fromkeys(new_tools))

        # ── Repair tasks: agent_name + schedule trigger ───────────────
        for task in tasks:
            title = task.get("title", "?")

            # Fix orphaned agent references
            ref = task.get("agent_name", "")
            if ref and ref not in agent_names:
                best_agent = _best_match(ref, list(agent_names)) or (agents[0]["name"] if agents else "")
                task["agent_name"] = best_agent
                iteration_fixes.append(f"Task '{title}': agent_name '{ref}' → '{best_agent}'")

            # Fix invalid schedule triggers
            sched = task.get("schedule")
            if sched and isinstance(sched, dict):
                trigger = sched.get("trigger")
                if trigger and trigger not in ("cron", "interval", "once", None):
                    sched["trigger"] = "cron"
                    if "hour" not in sched:
                        sched["hour"] = 8
                    if "minute" not in sched:
                        sched["minute"] = 0
                    if "day_of_week" not in sched:
                        sched["day_of_week"] = "*"
                    task["schedule"] = sched
                    iteration_fixes.append(f"Task '{title}': invalid trigger '{trigger}' normalised to cron 08:00 daily")

        repair_log.append({
            "iteration": iteration,
            "fixes": iteration_fixes,
        })

        # ── Re-validate ───────────────────────────────────────────────
        repaired_plan = {**body, "agents": agents, "tasks": tasks}
        val_resp = await repair_validate(repaired_plan)
        if val_resp["valid"] or not iteration_fixes:
            break

    return {
        "plan": {**body, "agents": agents, "tasks": tasks},
        "validation": val_resp,
        "repair_log": repair_log,
        "repaired": any(fix["fixes"] for fix in repair_log),
        "iterations": len(repair_log),
    }


async def repair_validate(plan: dict) -> dict:
    """Internal helper: run validate_org_plan logic synchronously on a plan dict."""
    known_skills = _get_known_skills()
    known_tools = _BUILTIN_TOOL_NAMES
    agents = plan.get("agents") or []
    tasks = plan.get("tasks") or []
    checks = []
    warnings: list[str] = []
    errors: list[str] = []

    for agent in agents:
        name = agent.get("name", "?")
        bad_skills = [s for s in (agent.get("skills") or []) if s not in known_skills]
        bad_tools = [t for t in (agent.get("allowed_tools") or []) if t not in known_tools]
        if bad_skills:
            warnings.append(f"Agent '{name}': unknown skills {bad_skills}")
        if bad_tools:
            warnings.append(f"Agent '{name}': unknown tools {bad_tools}")
        checks.append({
            "type": "agent", "name": name,
            "skills_ok": not bad_skills, "tools_ok": not bad_tools,
            "bad_skills": bad_skills, "bad_tools": bad_tools,
        })

    agent_names = {a.get("name") for a in agents}
    scheduled_count = 0
    for task in tasks:
        title = task.get("title", "?")
        ref_ok = task.get("agent_name", "") in agent_names
        sched = task.get("schedule")
        sched_ok = None
        sched_detail = None
        if sched and isinstance(sched, dict):
            trigger = sched.get("trigger")
            if trigger in ("cron", "interval", "once"):
                sched_ok = True
                scheduled_count += 1
                if trigger == "cron":
                    sched_detail = f"cron {sched.get('hour', 8):02d}:{sched.get('minute', 0):02d} day={sched.get('day_of_week', '*')}"
                elif trigger == "interval":
                    sched_detail = f"interval every {sched.get('seconds', 3600)}s"
                else:
                    sched_detail = f"once at {sched.get('run_at', '?')}"
            else:
                sched_ok = False
                warnings.append(f"Task '{title}': invalid trigger '{trigger}'")
        if not ref_ok:
            errors.append(f"Task '{title}': references missing agent '{task.get('agent_name')}'")
        checks.append({
            "type": "task", "title": title,
            "agent_ref_ok": ref_ok, "schedule": sched_detail, "schedule_valid": sched_ok,
        })

    try:
        from src.scheduler.jobs import run_agent_task  # noqa: F401
        job_fn_ok = True
    except Exception:
        job_fn_ok = False
        errors.append("Job function 'run_agent_task' could not be resolved")

    score = max(0, 100 - len(errors) * 20 - len(warnings) * 5)
    return {
        "valid": len(errors) == 0,
        "score": score,
        "scheduled_tasks": scheduled_count,
        "agents_checked": len(agents),
        "tasks_checked": len(tasks),
        "job_fn_ok": job_fn_ok,
        "warnings": warnings,
        "errors": errors,
        "checks": checks,
    }


@app.post("/api/orgs/validate-plan")
async def validate_org_plan(body: dict):
    """Dry-run validation of a wizard plan before org creation."""
    return await repair_validate(body)


@app.get("/api/orgs/catalog")
async def get_org_catalog():
    """Return the real catalog of available skill IDs and tool names.

    Use this to populate dropdowns in the Dashboard so agents are always
    assigned valid skills/tools that actually exist in Atlas.
    """
    from pathlib import Path as _Path

    _plugin_dir = _Path("src/tools/plugins")

    _BUILTIN_TOOLS = [
        {"name": "recall_my_memories", "source": "memory", "description": "Search your stored memories"},
        {"name": "store_my_memory", "source": "memory", "description": "Store a new memory or preference"},
        {"name": "list_my_memories", "source": "memory", "description": "List all stored memories"},
        {"name": "forget_my_memory", "source": "memory", "description": "Delete a specific memory by ID"},
        {"name": "forget_all_my_memories", "source": "memory", "description": "Delete ALL memories"},
        {"name": "summarize_my_conversation", "source": "memory", "description": "Archive current session to long-term memory"},
        {"name": "get_my_recent_context", "source": "memory", "description": "Retrieve recent conversation context"},
        {"name": "create_my_reminder", "source": "scheduler", "description": "Create a scheduled reminder or recurring task"},
        {"name": "create_my_morning_brief", "source": "scheduler", "description": "Set up daily morning brief"},
        {"name": "list_my_schedules", "source": "scheduler", "description": "List all active scheduled tasks"},
        {"name": "cancel_my_schedule", "source": "scheduler", "description": "Cancel a scheduled task by job ID"},
    ]

    # DB-registered tools
    try:
        async with async_session() as _vs:
            from src.db.models import Tool as _T
            db_tools = (await _vs.execute(select(_T).where(_T.is_active == True))).scalars().all()  # noqa: E712
        _db_tool_items = [{"name": t.name, "source": "db", "description": t.description or ""} for t in db_tools]
    except Exception:
        _db_tool_items = []

    # Plugin-dir tools
    _plugin_tools = []
    if _plugin_dir.exists():
        for p in _plugin_dir.iterdir():
            if p.is_dir() and not p.name.startswith("_"):
                mf = p / "manifest.json"
                if mf.exists():
                    import json as _json
                    try:
                        m = _json.loads(mf.read_text())
                        _plugin_tools.append({"name": p.name, "source": "plugin", "description": m.get("description", "")})
                    except Exception:
                        _plugin_tools.append({"name": p.name, "source": "plugin", "description": ""})

    return {
        "skills": [
            {"id": "memory", "group": "internal", "description": "Recall, store, list, and forget user memories and preferences"},
            {"id": "scheduler", "group": "internal", "description": "Create, list, pause, or cancel recurring tasks and reminders"},
            {"id": "organizations", "group": "internal", "description": "Manage organizations, agents, tasks, and org-scoped tools"},
            {"id": "gmail", "group": "google_workspace", "description": "Read, search, draft, send, and reply to emails via Gmail"},
            {"id": "calendar", "group": "google_workspace", "description": "View, create, update, and delete Google Calendar events"},
            {"id": "google_tasks", "group": "google_workspace", "description": "Create, list, update, and complete Google Tasks"},
            {"id": "drive", "group": "google_workspace", "description": "Search, create, move, rename, and share files on Google Drive"},
            {"id": "google_sheets", "group": "google_workspace", "description": "Create, read, update, and append data in Google Sheets"},
            {"id": "google_docs", "group": "google_workspace", "description": "Search, read, create, and edit Google Docs"},
            {"id": "google_slides", "group": "google_workspace", "description": "Create, read, and update Google Slides presentations"},
            {"id": "google_contacts", "group": "google_workspace", "description": "List, search, create, and update Google Contacts"},
        ],
        "tools": _BUILTIN_TOOLS + _db_tool_items + _plugin_tools,
    }


@app.post("/api/orgs/wizard", response_model=OrgWizardResponse, status_code=201)
async def create_org_via_wizard(
    body: OrgWizardRequest,
    x_telegram_id: Optional[int] = Header(default=None, alias="X-Telegram-Id"),
):
    """Atomic wizard endpoint: create an organization and assign chosen system agents in one transaction."""
    async with async_session() as session:
        requester = await _resolve_dashboard_user(session, x_telegram_id)

        # Step 1 — Create the org
        org = Organization(
            name=body.name,
            description=body.description,
            goal=body.goal,
            owner_user_id=requester.id,
        )
        session.add(org)
        await session.flush()

        await _log_org_activity(
            session, org.id, "org_created",
            f"Organization '{body.name}' created via wizard",
        )

        # Step 2 — Create OrgAgent rows from selected system agents
        created_names: list[str] = []
        for agent_cfg in body.agents:
            sys_agent = get_system_agent_by_id(agent_cfg.system_agent_id)
            if sys_agent is None:
                raise HTTPException(
                    status_code=422,
                    detail=f"Unknown system_agent_id: '{agent_cfg.system_agent_id}'",
                )
            agent = OrgAgent(
                org_id=org.id,
                name=sys_agent.name,
                role=agent_cfg.role or sys_agent.name,
                description=sys_agent.description,
                instructions=agent_cfg.instructions,
                model_tier="general",
                status="active",
            )
            session.add(agent)
            created_names.append(sys_agent.name)

        await session.flush()
        await _log_org_activity(
            session, org.id, "agents_added_via_wizard",
            f"Added {len(created_names)} agents: {', '.join(created_names) or 'none'}",
        )
        await session.commit()

    return OrgWizardResponse(
        org_id=org.id,
        org_name=org.name,
        status=org.status,
        agents_created=len(created_names),
        agent_names=created_names,
    )


@app.post("/api/orgs", response_model=OrgResponse, status_code=201)
async def create_org(
    body: OrgCreate,
    x_telegram_id: Optional[int] = Header(default=None, alias="X-Telegram-Id"),
):
    """Create a new organization."""
    async with async_session() as session:
        requester = await _resolve_dashboard_user(session, x_telegram_id)

        org = Organization(
            name=body.name, description=body.description,
            goal=body.goal, config=body.config,
            owner_user_id=requester.id,
        )
        session.add(org)
        await session.flush()
        await _log_org_activity(session, org.id, "org_created", f"Organization '{body.name}' created")
        await session.commit()
        await session.refresh(org)

    return OrgResponse(
        id=org.id, name=org.name, description=org.description,
        goal=org.goal, status=org.status, config=org.config,
        budget_cap_usd=float(org.budget_cap_usd or 0),
        created_at=org.created_at, updated_at=org.updated_at,
    )


@app.get("/api/orgs/{org_id}", response_model=OrgResponse)
async def get_org(
    org_id: int,
    x_telegram_id: Optional[int] = Header(default=None, alias="X-Telegram-Id"),
):
    """Get organization details."""
    async with async_session() as session:
        requester = await _resolve_dashboard_user(session, x_telegram_id)
        org = await _get_owned_org_or_404(session, org_id, requester.id)
        agent_r = await session.execute(
            select(func.count(OrgAgent.id)).where(OrgAgent.org_id == org_id)
        )
        task_r = await session.execute(
            select(func.count(OrgTask.id)).where(OrgTask.org_id == org_id)
        )
        done_r = await session.execute(
            select(func.count(OrgTask.id)).where(
                OrgTask.org_id == org_id, OrgTask.status == "completed"
            )
        )
    return OrgResponse(
        id=org.id, name=org.name, description=org.description,
        goal=org.goal, status=org.status, config=org.config,
        budget_cap_usd=float(org.budget_cap_usd or 0),
        created_at=org.created_at, updated_at=org.updated_at,
        agent_count=agent_r.scalar() or 0,
        task_count=task_r.scalar() or 0,
        completed_tasks=done_r.scalar() or 0,
    )


@app.patch("/api/orgs/{org_id}")
async def update_org(
    org_id: int,
    body: OrgCreate,
    x_telegram_id: Optional[int] = Header(default=None, alias="X-Telegram-Id"),
):
    """Update an organization."""
    async with async_session() as session:
        requester = await _resolve_dashboard_user(session, x_telegram_id)
        org = await _get_owned_org_or_404(session, org_id, requester.id)
        org.name = body.name
        org.description = body.description
        org.goal = body.goal
        if body.config is not None:
            org.config = body.config
        await _log_org_activity(session, org_id, "org_updated", f"Organization updated")
        await session.commit()
    return {"message": "Organization updated"}


@app.post("/api/orgs/{org_id}/pause")
async def pause_org(
    org_id: int,
    x_telegram_id: Optional[int] = Header(default=None, alias="X-Telegram-Id"),
):
    async with async_session() as session:
        requester = await _resolve_dashboard_user(session, x_telegram_id)
        org = await _get_owned_org_or_404(session, org_id, requester.id)
        org.status = "paused"
        await _log_org_activity(session, org_id, "org_paused", "Organization paused")
        await session.commit()
    return {"message": "Organization paused"}


@app.post("/api/orgs/{org_id}/resume")
async def resume_org(
    org_id: int,
    x_telegram_id: Optional[int] = Header(default=None, alias="X-Telegram-Id"),
):
    async with async_session() as session:
        requester = await _resolve_dashboard_user(session, x_telegram_id)
        org = await _get_owned_org_or_404(session, org_id, requester.id)
        org.status = "active"
        await _log_org_activity(session, org_id, "org_resumed", "Organization resumed")
        await session.commit()
    return {"message": "Organization resumed"}


@app.get("/api/orgs/{org_id}/delete-preview", response_model=OrgDeletePreview)
async def delete_org_preview(
    org_id: int,
    x_telegram_id: Optional[int] = Header(default=None, alias="X-Telegram-Id"),
):
    """Preview what would be removed when deleting an organization."""
    async with async_session() as session:
        requester = await _resolve_dashboard_user(session, x_telegram_id)
        org = await _get_owned_org_or_404(session, org_id, requester.id)

        agents_r = await session.execute(
            select(OrgAgent).where(OrgAgent.org_id == org_id).order_by(OrgAgent.name)
        )
        agents = agents_r.scalars().all()

        # Build a quick agent-id→name map for task display
        agent_map = {a.id: a.name for a in agents}

        tasks_r = await session.execute(
            select(OrgTask).where(OrgTask.org_id == org_id).order_by(OrgTask.created_at)
        )
        tasks = tasks_r.scalars().all()

        activity_r = await session.execute(
            select(func.count()).select_from(OrgActivity).where(OrgActivity.org_id == org_id)
        )
        activity_count = activity_r.scalar_one()
        exclusive_tools, exclusive_skills = await _collect_exclusive_resources(session, org_id)

    return OrgDeletePreview(
        org_id=org_id,
        org_name=org.name,
        agents=[OrgDeletePreviewAgent(id=a.id, name=a.name, role=a.role) for a in agents],
        tasks=[
            OrgDeletePreviewTask(
                id=t.id, title=t.title, status=t.status,
                agent_name=agent_map.get(t.agent_id),
            )
            for t in tasks
        ],
        activity_count=activity_count,
        exclusive_tools=exclusive_tools,
        exclusive_skills=exclusive_skills,
    )


async def _collect_exclusive_resources(
    session: AsyncSession,
    org_id: int,
) -> tuple[list[str], list[str]]:
    """Return (exclusive_tool_names, exclusive_skill_ids) that belong ONLY to this org.

    A tool/skill is exclusive when it is referenced by agents in ``org_id`` but
    NOT referenced by any agent in any other active org.  We use the
    ``tools_config`` JSONB column to gather the sets.
    """
    # Gather all agent rows for every org
    all_agents_r = await session.execute(
        select(OrgAgent.org_id, OrgAgent.tools_config)
        .join(Organization, OrgAgent.org_id == Organization.id)
        .where(Organization.status != "archived")
    )
    all_agents = all_agents_r.all()

    org_tools: set[str] = set()
    org_skills: set[str] = set()
    other_tools: set[str] = set()
    other_skills: set[str] = set()

    for row_org_id, tc in all_agents:
        if not tc:
            continue
        tools = set(tc.get("allowed_tools") or [])
        skills = set(tc.get("skills") or [])
        if row_org_id == org_id:
            org_tools |= tools
            org_skills |= skills
        else:
            other_tools |= tools
            other_skills |= skills

    return (
        sorted(org_tools - other_tools),
        sorted(org_skills - other_skills),
    )


async def _ensure_holding_org(session: AsyncSession, owner_user_id: int) -> int:
    """Get or create the system '__retained__' holding org for orphaned entities."""
    r = await session.execute(
        select(Organization.id).where(
            Organization.name == "__retained__",
            Organization.owner_user_id == owner_user_id,
        )
    )
    holding_id = r.scalar_one_or_none()
    if holding_id:
        return holding_id
    holding = Organization(
        name="__retained__",
        description="System holding org for entities retained during org deletion",
        status="archived",
        owner_user_id=owner_user_id,
    )
    session.add(holding)
    await session.flush()
    return holding.id


@app.delete("/api/orgs/{org_id}")
async def delete_org(
    org_id: int,
    body: Optional[OrgDeleteRequest] = None,
    x_telegram_id: Optional[int] = Header(default=None, alias="X-Telegram-Id"),
):
    """Delete an organization with optional selective retention.

    If ``retain_agent_ids`` or ``retain_task_ids`` are provided, those
    entities are moved to a system '__retained__' holding org instead
    of being cascade-deleted.

    Tools and skills that are **exclusively** used by this org's agents (not
    shared with any other active org) are also removed: tools from the DB and
    plugin directory, skills from the user_skills filesystem directory.
    """
    import shutil
    from pathlib import Path as _Path
    import re as _re

    retain_agent_ids = set((body.retain_agent_ids if body else None) or [])
    retain_task_ids = set((body.retain_task_ids if body else None) or [])

    async with async_session() as session:
        requester = await _resolve_dashboard_user(session, x_telegram_id)
        org = await _get_owned_org_or_404(session, org_id, requester.id)
        org_name = org.name

        # Collect exclusive tools/skills BEFORE moving retained agents away
        exclusive_tool_names, exclusive_skill_ids = await _collect_exclusive_resources(
            session, org_id
        )

        # Move retained entities to a holding org before cascade-delete
        if retain_agent_ids or retain_task_ids:
            holding_id = await _ensure_holding_org(session, requester.id)

            if retain_agent_ids:
                await session.execute(
                    update(OrgAgent)
                    .where(OrgAgent.org_id == org_id, OrgAgent.id.in_(retain_agent_ids))
                    .values(org_id=holding_id)
                )
            if retain_task_ids:
                await session.execute(
                    update(OrgTask)
                    .where(OrgTask.org_id == org_id, OrgTask.id.in_(retain_task_ids))
                    .values(org_id=holding_id)
                )

        # Remove exclusive tool DB rows (cascade also removes them from registry)
        _tools_removed: list[str] = []
        _skills_removed: list[str] = []
        PROTECTED_TOOLS = frozenset({"linkedin", "browser", "onedrive", "_example"})

        for tool_name in exclusive_tool_names:
            if tool_name in PROTECTED_TOOLS:
                continue
            # Validate name to prevent path traversal
            if not _re.match(r'^[a-zA-Z0-9_-]+$', tool_name):
                logger.warning("Skipping tool deletion — unsafe name: %s", tool_name)
                continue
            tool_row = (await session.execute(
                select(Tool).where(Tool.name == tool_name)
            )).scalar_one_or_none()
            if tool_row:
                plugin_dir = _Path("src/tools/plugins") / tool_name
                if plugin_dir.exists() and plugin_dir.is_dir():
                    try:
                        shutil.rmtree(plugin_dir)
                        logger.info("Removed tool plugin dir: %s", plugin_dir)
                    except Exception as _e:
                        logger.warning("Failed to remove tool dir %s: %s", plugin_dir, _e)
                await session.delete(tool_row)
                _tools_removed.append(tool_name)

        session.add(AuditLog(
            user_id=requester.id,
            direction="outbound",
            platform="dashboard",
            message_text=f"Organization deleted: {org_name} ({org_id})",
            agent_name="org_api",
            tools_used={
                "action": "org_deleted",
                "org_id": org_id,
                "org_name": org_name,
                "retained_agents": list(retain_agent_ids),
                "retained_tasks": list(retain_task_ids),
                "tools_removed": _tools_removed,
                "skills_removed": exclusive_skill_ids,
            },
        ))
        await session.delete(org)
        await session.commit()

    # Remove exclusive skill directories from filesystem (outside the session —
    # filesystem ops after DB commit so a rollback doesn't leave orphan dirs).
    _RESERVED_SKILL_IDS = frozenset({
        "memory", "scheduler", "organizations",
        "gmail", "calendar", "google_tasks", "drive",
        "google_sheets", "google_docs", "google_slides", "google_contacts",
    })
    for skill_id in exclusive_skill_ids:
        if skill_id in _RESERVED_SKILL_IDS:
            continue
        if not _re.match(r'^[a-zA-Z0-9_-]+$', skill_id):
            logger.warning("Skipping skill deletion — unsafe id: %s", skill_id)
            continue
        skill_dir = _Path("src/user_skills") / skill_id
        if skill_dir.exists() and skill_dir.is_dir():
            try:
                shutil.rmtree(skill_dir)
                logger.info("Removed user skill dir: %s", skill_dir)
                _skills_removed.append(skill_id)
            except Exception as _e:
                logger.warning("Failed to remove skill dir %s: %s", skill_dir, _e)

    # Evict removed tools from live registry so in-memory state matches disk
    for tool_name in _tools_removed:
        try:
            from src.tools.registry import get_registry
            reg = get_registry()
            reg._tools.pop(tool_name, None)
        except Exception as _e:
            logger.debug("Live registry eviction failed for %s: %s", tool_name, _e)

    logger.info(
        "Organization deleted: %s (%s) — retained %d agents, %d tasks, "
        "removed %d tools, %d skills",
        org_name, org_id, len(retain_agent_ids), len(retain_task_ids),
        len(_tools_removed), len(_skills_removed),
    )
    return {
        "message": f"Organization '{org_name}' deleted",
        "retained_agents": len(retain_agent_ids),
        "retained_tasks": len(retain_task_ids),
        "tools_removed": _tools_removed,
        "skills_removed": _skills_removed,
    }


# ── Org Agents ────────────────────────────────────────────────────────


@app.get("/api/orgs/{org_id}/agents", response_model=List[OrgAgentResponse])
async def list_org_agents(
    org_id: int,
    x_telegram_id: Optional[int] = Header(default=None, alias="X-Telegram-Id"),
):
    async with async_session() as session:
        requester = await _resolve_dashboard_user(session, x_telegram_id)
        await _get_owned_org_or_404(session, org_id, requester.id)
        result = await session.execute(
            select(OrgAgent).where(OrgAgent.org_id == org_id)
            .order_by(OrgAgent.created_at)
        )
        agents = result.scalars().all()
    list_resp = []
    for a in agents:
        tc = a.tools_config or {}
        list_resp.append(
            OrgAgentResponse(
                id=a.id, org_id=a.org_id, name=a.name, role=a.role,
                description=a.description, instructions=a.instructions,
                tools_config=a.tools_config, model_tier=a.model_tier,
                status=a.status, created_at=a.created_at,
                skills=list(tc.get("skills", [])),
                allowed_tools=list(tc.get("allowed_tools", [])),
            )
        )
    return list_resp


@app.post("/api/orgs/{org_id}/validate")
async def revalidate_org_agents(
    org_id: int,
    x_telegram_id: Optional[int] = Header(default=None, alias="X-Telegram-Id"),
):
    """Re-run skill/tool validation on all agents in an org.

    Useful after fixing an org that was created with bad (hallucinated) skill/tool names.
    Returns updated validation results for each agent.
    """
    from pathlib import Path as _Path

    async with async_session() as session:
        requester = await _resolve_dashboard_user(session, x_telegram_id)
        await _get_owned_org_or_404(session, org_id, requester.id)
        result = await session.execute(
            select(OrgAgent).where(OrgAgent.org_id == org_id)
        )
        agents = result.scalars().all()

    _known_skills: set[str] = _get_known_skills()
    _BUILTIN_TOOL_NAMES: set[str] = {
        "recall_my_memories", "store_my_memory", "list_my_memories",
        "forget_my_memory", "forget_all_my_memories",
        "summarize_my_conversation", "get_my_recent_context",
        "create_my_reminder", "create_my_morning_brief",
        "list_my_schedules", "cancel_my_schedule",
    }
    _plugin_dir = _Path("src/tools/plugins")
    try:
        async with async_session() as _vs:
            from src.db.models import Tool as _T
            _db_tools = (await _vs.execute(select(_T).where(_T.is_active == True))).scalars().all()  # noqa: E712
        _known_tools: set[str] = {t.name for t in _db_tools}
    except Exception:
        _known_tools = set()
    if _plugin_dir.exists():
        _known_tools |= {p.name for p in _plugin_dir.iterdir() if p.is_dir() and not p.name.startswith("_")}
    _known_tools |= _BUILTIN_TOOL_NAMES

    results = []
    async with async_session() as vsession:
        for a in agents:
            vtc = dict(a.tools_config or {})
            val: dict = {"skills": {}, "tools": {}}
            for sk in vtc.get("skills", []):
                ok = sk in _known_skills
                val["skills"][sk] = "✅ found" if ok else "⚠️ not registered"
            for tn in vtc.get("allowed_tools", []):
                ok = tn in _known_tools
                val["tools"][tn] = "✅ found" if ok else "⚠️ not installed"
            vtc["validation"] = val
            a.tools_config = vtc
            vsession.add(a)
            results.append({"agent_id": a.id, "agent_name": a.name, "validation": val})
        await vsession.commit()

    return {"org_id": org_id, "agents_validated": len(results), "results": results}


@app.post("/api/orgs/{org_id}/agents", response_model=OrgAgentResponse, status_code=201)
async def create_org_agent(
    org_id: int,
    body: OrgAgentCreate,
    x_telegram_id: Optional[int] = Header(default=None, alias="X-Telegram-Id"),
):
    async with async_session() as session:
        requester = await _resolve_dashboard_user(session, x_telegram_id)
        await _get_owned_org_or_404(session, org_id, requester.id)
        # Merge skills/allowed_tools into tools_config for consistent storage
        merged_tc = (body.tools_config or {}).copy()
        if body.skills is not None:
            merged_tc["skills"] = list(body.skills)
        if body.allowed_tools is not None:
            merged_tc["allowed_tools"] = list(body.allowed_tools)
        agent = OrgAgent(
            org_id=org_id, name=body.name, role=body.role,
            description=body.description, instructions=body.instructions,
            tools_config=merged_tc, model_tier=body.model_tier,
        )
        session.add(agent)
        await session.flush()
        await _log_org_activity(
            session, org_id, "agent_created",
            f"Agent '{body.name}' ({body.role}) created", agent_id=agent.id,
        )
        await session.commit()
        await session.refresh(agent)
    create_tc = agent.tools_config or {}
    return OrgAgentResponse(
        id=agent.id, org_id=agent.org_id, name=agent.name, role=agent.role,
        description=agent.description, instructions=agent.instructions,
        tools_config=agent.tools_config, model_tier=agent.model_tier,
        status=agent.status, created_at=agent.created_at,
        skills=list(create_tc.get("skills", [])),
        allowed_tools=list(create_tc.get("allowed_tools", [])),
    )


@app.patch("/api/orgs/{org_id}/agents/{agent_id}", response_model=OrgAgentResponse)
async def update_org_agent(
    org_id: int,
    agent_id: int,
    body: OrgAgentUpdate,
    x_telegram_id: Optional[int] = Header(default=None, alias="X-Telegram-Id"),
):
    async with async_session() as session:
        requester = await _resolve_dashboard_user(session, x_telegram_id)
        await _get_owned_org_or_404(session, org_id, requester.id)
        agent = await session.get(OrgAgent, agent_id)
        if not agent or agent.org_id != org_id:
            raise HTTPException(status_code=404, detail="Agent not found")

        updates = body.model_dump(exclude_unset=True)
        if not updates:
            raise HTTPException(status_code=400, detail="No fields provided for update")

        # Merge skills/allowed_tools into tools_config; don't pass them as direct ORM fields
        skills_val = updates.pop("skills", None)
        allowed_val = updates.pop("allowed_tools", None)
        if "tools_config" in updates:
            base_tc = updates.pop("tools_config") or {}
        else:
            base_tc = dict(agent.tools_config or {})
        if skills_val is not None:
            base_tc["skills"] = list(skills_val)
        if allowed_val is not None:
            base_tc["allowed_tools"] = list(allowed_val)
        agent.tools_config = base_tc

        for field, value in updates.items():
            setattr(agent, field, value)

        await _log_org_activity(
            session, org_id, "agent_updated",
            f"Agent '{agent.name}' updated", agent_id=agent.id,
        )
        await session.commit()
        await session.refresh(agent)

    update_tc = agent.tools_config or {}
    return OrgAgentResponse(
        id=agent.id, org_id=agent.org_id, name=agent.name, role=agent.role,
        description=agent.description, instructions=agent.instructions,
        tools_config=agent.tools_config, model_tier=agent.model_tier,
        status=agent.status, created_at=agent.created_at,
        skills=list(update_tc.get("skills", [])),
        allowed_tools=list(update_tc.get("allowed_tools", [])),
    )


@app.get("/api/orgs/{org_id}/agents/{agent_id}/delete-preview")
async def delete_agent_preview(
    org_id: int,
    agent_id: int,
    x_telegram_id: Optional[int] = Header(default=None, alias="X-Telegram-Id"),
):
    """Preview the impact of deleting an agent BEFORE the user confirms.

    Returns:
      - agent: name + role for the confirm dialog
      - org_status: blocks the delete if 'active'
      - active_tasks: list of currently in-progress / pending tasks owned by
        this agent. The DB has ON DELETE SET NULL on the FK, so deletion
        wouldn't lose tasks — but the user should know they'll be orphaned.
      - recent_activity_count: total OrgActivity rows referencing this agent
        (truncated count; full audit trail survives via SET NULL).
    """
    async with async_session() as session:
        requester = await _resolve_dashboard_user(session, x_telegram_id)
        await _get_owned_org_or_404(session, org_id, requester.id)
        agent = await session.get(OrgAgent, agent_id)
        if not agent or agent.org_id != org_id:
            raise HTTPException(status_code=404, detail="Agent not found")

        org = await session.get(Organization, org_id)
        org_status = org.status if org else "unknown"

        active_tasks_q = await session.execute(
            select(OrgTask)
            .where(
                OrgTask.agent_id == agent_id,
                OrgTask.status.in_(["pending", "in_progress", "blocked"]),
            )
            .order_by(desc(OrgTask.created_at))
            .limit(50)
        )
        active_tasks = active_tasks_q.scalars().all()

        completed_tasks_q = await session.execute(
            select(func.count())
            .select_from(OrgTask)
            .where(
                OrgTask.agent_id == agent_id,
                OrgTask.status.in_(["done", "completed", "failed", "cancelled"]),
            )
        )
        completed_count = completed_tasks_q.scalar_one()

        activity_q = await session.execute(
            select(func.count()).select_from(OrgActivity).where(OrgActivity.agent_id == agent_id)
        )
        activity_count = activity_q.scalar_one()

    return {
        "agent": {
            "id": agent.id,
            "name": agent.name,
            "role": agent.role,
            "org_id": org_id,
        },
        "org_status": org_status,
        "deletion_blocked": org_status == "active",
        "active_tasks": [
            {"id": t.id, "title": t.title, "status": t.status, "priority": t.priority}
            for t in active_tasks
        ],
        "active_tasks_count": len(active_tasks),
        "completed_tasks_count": int(completed_count),
        "activity_count": int(activity_count),
    }


@app.delete("/api/orgs/{org_id}/agents/{agent_id}")
async def delete_org_agent(
    org_id: int,
    agent_id: int,
    x_telegram_id: Optional[int] = Header(default=None, alias="X-Telegram-Id"),
):
    async with async_session() as session:
        requester = await _resolve_dashboard_user(session, x_telegram_id)
        await _get_owned_org_or_404(session, org_id, requester.id)
        agent = await session.get(OrgAgent, agent_id)
        if not agent or agent.org_id != org_id:
            raise HTTPException(status_code=404, detail="Agent not found")

        # Check if organization is active - block deletion if so
        org = await session.get(Organization, org_id)
        if org and org.status == "active":
            raise HTTPException(
                status_code=400,
                detail=f"Cannot delete agent '{agent.name}' while attached to an active organization. Pause or archive the organization first."
            )

        agent_name = agent.name
        await _log_org_activity(
            session, org_id, "agent_deleted",
            f"Agent '{agent_name}' deleted", agent_id=agent.id,
        )
        await session.delete(agent)
        await session.commit()

    return {"message": f"Agent '{agent_name}' deleted"}


# ── Agents Tab ─────────────────────────────────────────────────────────

@app.get("/api/agents/system", response_model=list[SystemAgentInfo])
async def list_system_agents(
    x_telegram_id: Optional[int] = Header(default=None, alias="X-Telegram-Id"),
):
    """List all system agents (built-in agents that power Atlas)."""
    async with async_session() as session:
        await _resolve_dashboard_user(session, x_telegram_id)
    return get_system_agents()


@app.get("/api/agents/org", response_model=list[OrgAgentWithOrgInfo])
async def list_all_org_agents(
    x_telegram_id: Optional[int] = Header(default=None, alias="X-Telegram-Id"),
):
    """List all OrgAgents with organization attachment info (for Agents tab)."""
    async with async_session() as session:
        requester = await _resolve_dashboard_user(session, x_telegram_id)

        # Join OrgAgents with Organizations to get org info and can_delete flag
        stmt = (
            select(OrgAgent, Organization.name, Organization.status)
            .join(Organization, OrgAgent.org_id == Organization.id)
            .where(Organization.owner_user_id == requester.id)
            .order_by(OrgAgent.created_at.desc())
        )
        result = await session.execute(stmt)
        rows = result.all()

        agents = []
        for agent, org_name, org_status in rows:
            can_delete = org_status != "active"
            delete_reason = None if can_delete else f"Attached to active organization '{org_name}'"

            tc = agent.tools_config or {}
            agents.append(OrgAgentWithOrgInfo(
                id=agent.id,
                org_id=agent.org_id,
                org_name=org_name,
                org_status=org_status,
                name=agent.name,
                role=agent.role,
                description=agent.description,
                instructions=agent.instructions,
                model_tier=agent.model_tier,
                status=agent.status,
                tools_config=tc,
                skills=tc.get("skills") or [],
                allowed_tools=tc.get("allowed_tools") or [],
                can_delete=can_delete,
                delete_reason=delete_reason,
                created_at=agent.created_at,
            ))

    return agents


@app.get("/api/agents/org/{agent_id}/can-delete", response_model=AgentDeletionCheck)
async def check_agent_can_delete(
    agent_id: int,
    x_telegram_id: Optional[int] = Header(default=None, alias="X-Telegram-Id"),
):
    """Check if an OrgAgent can be safely deleted."""
    async with async_session() as session:
        requester = await _resolve_dashboard_user(session, x_telegram_id)

        # Get agent with org info
        stmt = (
            select(OrgAgent, Organization.name, Organization.status)
            .join(Organization, OrgAgent.org_id == Organization.id)
            .where(OrgAgent.id == agent_id)
            .where(Organization.owner_user_id == requester.id)
        )
        result = await session.execute(stmt)
        row = result.first()

        if not row:
            raise HTTPException(status_code=404, detail="Agent not found")

        agent, org_name, org_status = row
        can_delete = org_status != "active"

        return AgentDeletionCheck(
            can_delete=can_delete,
            reason=None if can_delete else f"Cannot delete while attached to active organization '{org_name}'",
            attached_org=org_name,
            attached_org_status=org_status,
        )


# ── Org Tasks ─────────────────────────────────────────────────────────


@app.get("/api/orgs/{org_id}/tasks", response_model=List[OrgTaskResponseV2])
async def list_org_tasks(
    org_id: int,
    status: Optional[str] = None,
    x_telegram_id: Optional[int] = Header(default=None, alias="X-Telegram-Id"),
):
    async with async_session() as session:
        requester = await _resolve_dashboard_user(session, x_telegram_id)
        await _get_owned_org_or_404(session, org_id, requester.id)
        stmt = select(OrgTask).where(OrgTask.org_id == org_id)
        if status:
            stmt = stmt.where(OrgTask.status == status)
        stmt = stmt.order_by(desc(OrgTask.created_at))
        result = await session.execute(stmt)
        tasks = result.scalars().all()
    return [
        OrgTaskResponseV2(
            id=t.id, org_id=t.org_id, agent_id=t.agent_id,
            title=t.title, description=t.description,
            priority=t.priority, status=t.status, result=t.result,
            goal_ancestry=t.goal_ancestry,
            source=t.source, due_at=t.due_at, created_at=t.created_at,
            assigned_at=t.assigned_at, completed_at=t.completed_at,
        )
        for t in tasks
    ]


@app.post("/api/orgs/{org_id}/tasks", response_model=OrgTaskResponseV2, status_code=201)
async def create_org_task(
    org_id: int,
    body: OrgTaskCreateV2,
    x_telegram_id: Optional[int] = Header(default=None, alias="X-Telegram-Id"),
):
    async with async_session() as session:
        requester = await _resolve_dashboard_user(session, x_telegram_id)
        await _get_owned_org_or_404(session, org_id, requester.id)
        now = datetime.now(timezone.utc) if body.agent_id else None
        task = OrgTask(
            org_id=org_id, agent_id=body.agent_id,
            title=body.title, description=body.description,
            priority=body.priority, due_at=body.due_at,
            goal_ancestry=body.goal_ancestry,
            status="in_progress" if body.agent_id else "pending",
            assigned_at=now,
        )
        session.add(task)
        await session.flush()
        await _log_org_activity(
            session, org_id, "task_created",
            f"Task '{body.title}' created", task_id=task.id,
            agent_id=body.agent_id,
        )
        await session.commit()
        await session.refresh(task)
    return OrgTaskResponseV2(
        id=task.id, org_id=task.org_id, agent_id=task.agent_id,
        title=task.title, description=task.description,
        priority=task.priority, status=task.status, result=task.result,
        goal_ancestry=task.goal_ancestry,
        source=task.source, due_at=task.due_at, created_at=task.created_at,
        assigned_at=task.assigned_at, completed_at=task.completed_at,
    )


@app.patch("/api/orgs/{org_id}/tasks/{task_id}", response_model=OrgTaskResponseV2)
async def update_org_task(
    org_id: int,
    task_id: int,
    body: OrgTaskUpdateV2,
    x_telegram_id: Optional[int] = Header(default=None, alias="X-Telegram-Id"),
):
    async with async_session() as session:
        requester = await _resolve_dashboard_user(session, x_telegram_id)
        await _get_owned_org_or_404(session, org_id, requester.id)
        task = await session.get(OrgTask, task_id)
        if not task or task.org_id != org_id:
            raise HTTPException(status_code=404, detail="Task not found")

        updates = body.model_dump(exclude_unset=True)
        if not updates:
            raise HTTPException(status_code=400, detail="No fields provided for update")

        if updates.get("priority") and updates["priority"] not in {"low", "medium", "high"}:
            raise HTTPException(status_code=400, detail="Priority must be low, medium, or high")

        if updates.get("status") and updates["status"] not in {"pending", "in_progress", "completed"}:
            raise HTTPException(status_code=400, detail="Status must be pending, in_progress, or completed")

        if "agent_id" in updates and updates["agent_id"] is not None:
            agent = await session.get(OrgAgent, updates["agent_id"])
            if not agent or agent.org_id != org_id:
                raise HTTPException(status_code=404, detail="Agent not found")

        if "status" in updates and updates["status"] == "completed" and task.completed_at is None:
            task.completed_at = datetime.now(timezone.utc)
        elif "status" in updates and updates["status"] != "completed":
            task.completed_at = None

        if "agent_id" in updates:
            next_agent_id = updates["agent_id"]
            task.assigned_at = datetime.now(timezone.utc) if next_agent_id else None

        for field, value in updates.items():
            setattr(task, field, value)

        await _log_org_activity(
            session, org_id, "task_updated",
            f"Task '{task.title}' updated", task_id=task.id, agent_id=task.agent_id,
        )
        await session.commit()
        await session.refresh(task)

    return OrgTaskResponseV2(
        id=task.id, org_id=task.org_id, agent_id=task.agent_id,
        title=task.title, description=task.description,
        priority=task.priority, status=task.status, result=task.result,
        goal_ancestry=task.goal_ancestry,
        source=task.source, due_at=task.due_at, created_at=task.created_at,
        assigned_at=task.assigned_at, completed_at=task.completed_at,
    )


@app.delete("/api/orgs/{org_id}/tasks/{task_id}")
async def delete_org_task(
    org_id: int,
    task_id: int,
    x_telegram_id: Optional[int] = Header(default=None, alias="X-Telegram-Id"),
):
    async with async_session() as session:
        requester = await _resolve_dashboard_user(session, x_telegram_id)
        await _get_owned_org_or_404(session, org_id, requester.id)
        task = await session.get(OrgTask, task_id)
        if not task or task.org_id != org_id:
            raise HTTPException(status_code=404, detail="Task not found")

        task_title = task.title
        await _log_org_activity(
            session, org_id, "task_deleted",
            f"Task '{task_title}' deleted", task_id=task.id, agent_id=task.agent_id,
        )
        await session.delete(task)
        await session.commit()

    return {"message": f"Task '{task_title}' deleted"}


@app.post("/api/orgs/{org_id}/tasks/{task_id}/complete")
async def complete_org_task(
    org_id: int,
    task_id: int,
    result: Optional[dict] = None,
    x_telegram_id: Optional[int] = Header(default=None, alias="X-Telegram-Id"),
):
    async with async_session() as session:
        requester = await _resolve_dashboard_user(session, x_telegram_id)
        await _get_owned_org_or_404(session, org_id, requester.id)
        task = await session.get(OrgTask, task_id)
        if not task or task.org_id != org_id:
            raise HTTPException(status_code=404, detail="Task not found")
        task.status = "completed"
        task.completed_at = datetime.now(timezone.utc)
        if result:
            task.result = result
        await _log_org_activity(
            session, org_id, "task_completed",
            f"Task '{task.title}' completed", task_id=task_id,
            agent_id=task.agent_id,
        )
        await session.commit()
    return {"message": "Task completed"}


# ── Phase 2: Governance — Budget Cap + Approval Gates ─────────────────


@app.patch("/api/orgs/{org_id}/budget", response_model=OrgResponse)
async def update_org_budget(
    org_id: int,
    body: OrgBudgetUpdate,
    x_telegram_id: Optional[int] = Header(default=None, alias="X-Telegram-Id"),
):
    """Set or update the monthly USD budget cap for an org. 0 = unlimited."""
    async with async_session() as session:
        requester = await _resolve_dashboard_user(session, x_telegram_id)
        org = await _get_owned_org_or_404(session, org_id, requester.id)
        org.budget_cap_usd = body.budget_cap_usd
        await _log_org_activity(
            session, org_id, "budget_updated",
            f"Budget cap set to ${body.budget_cap_usd:.2f}/month",
        )
        await session.commit()
        await session.refresh(org)
        ac = (await session.execute(
            select(func.count(OrgAgent.id)).where(OrgAgent.org_id == org_id)
        )).scalar_one()
        tc = (await session.execute(
            select(func.count(OrgTask.id)).where(OrgTask.org_id == org_id)
        )).scalar_one()
        dc = (await session.execute(
            select(func.count(OrgTask.id)).where(
                OrgTask.org_id == org_id, OrgTask.status == "completed"
            )
        )).scalar_one()
    return OrgResponse(
        id=org.id, name=org.name, description=org.description,
        goal=org.goal, status=org.status, config=org.config,
        budget_cap_usd=float(org.budget_cap_usd),
        created_at=org.created_at, updated_at=org.updated_at,
        agent_count=ac, task_count=tc, completed_tasks=dc,
    )


@app.get("/api/orgs/{org_id}/gates", response_model=List[ApprovalGateResponse])
async def list_approval_gates(
    org_id: int,
    status: Optional[str] = None,
    x_telegram_id: Optional[int] = Header(default=None, alias="X-Telegram-Id"),
):
    """List approval gates for an org, optionally filtered by status (pending/approved/rejected)."""
    async with async_session() as session:
        requester = await _resolve_dashboard_user(session, x_telegram_id)
        await _get_owned_org_or_404(session, org_id, requester.id)
        stmt = select(OrgApprovalGate).where(OrgApprovalGate.org_id == org_id)
        if status:
            stmt = stmt.where(OrgApprovalGate.status == status)
        stmt = stmt.order_by(desc(OrgApprovalGate.created_at))
        result = await session.execute(stmt)
        gates = result.scalars().all()
    return [
        ApprovalGateResponse(
            id=g.id, org_id=g.org_id, agent_id=g.agent_id,
            action=g.action, context=g.context, status=g.status,
            decision_note=g.decision_note,
            created_at=g.created_at, decided_at=g.decided_at,
        )
        for g in gates
    ]


@app.post("/api/orgs/{org_id}/gates", response_model=ApprovalGateResponse, status_code=201)
async def create_approval_gate(
    org_id: int,
    body: ApprovalGateCreate,
    x_telegram_id: Optional[int] = Header(default=None, alias="X-Telegram-Id"),
):
    """Create a new approval gate (agent requests human sign-off)."""
    async with async_session() as session:
        requester = await _resolve_dashboard_user(session, x_telegram_id)
        await _get_owned_org_or_404(session, org_id, requester.id)
        gate = OrgApprovalGate(
            org_id=org_id,
            agent_id=body.agent_id,
            action=body.action,
            context=body.context,
        )
        session.add(gate)
        await session.flush()
        await _log_org_activity(
            session, org_id, "gate_created",
            f"Approval gate requested: '{body.action}'",
            agent_id=body.agent_id,
        )
        await session.commit()
        await session.refresh(gate)
    return ApprovalGateResponse(
        id=gate.id, org_id=gate.org_id, agent_id=gate.agent_id,
        action=gate.action, context=gate.context, status=gate.status,
        decision_note=gate.decision_note,
        created_at=gate.created_at, decided_at=gate.decided_at,
    )


@app.post("/api/orgs/{org_id}/gates/{gate_id}/decide", response_model=ApprovalGateResponse)
async def decide_approval_gate(
    org_id: int,
    gate_id: int,
    body: ApprovalGateDecision,
    x_telegram_id: Optional[int] = Header(default=None, alias="X-Telegram-Id"),
):
    """Approve or reject a pending approval gate."""
    async with async_session() as session:
        requester = await _resolve_dashboard_user(session, x_telegram_id)
        await _get_owned_org_or_404(session, org_id, requester.id)
        gate = await session.get(OrgApprovalGate, gate_id)
        if not gate or gate.org_id != org_id:
            raise HTTPException(status_code=404, detail="Approval gate not found")
        if gate.status != "pending":
            raise HTTPException(
                status_code=409,
                detail=f"Gate is already '{gate.status}' — cannot decide again",
            )
        gate.status = body.decision
        gate.decision_note = body.note
        gate.decided_at = datetime.now(timezone.utc)
        await _log_org_activity(
            session, org_id, f"gate_{body.decision}",
            f"Gate '{gate.action}' {body.decision}: {body.note or ''}",
            agent_id=gate.agent_id,
        )
        await session.commit()
        await session.refresh(gate)
    return ApprovalGateResponse(
        id=gate.id, org_id=gate.org_id, agent_id=gate.agent_id,
        action=gate.action, context=gate.context, status=gate.status,
        decision_note=gate.decision_note,
        created_at=gate.created_at, decided_at=gate.decided_at,
    )


# ── Phase 4: Per-Org Spend Tracking ────────────────────────────────────


@app.post("/api/orgs/{org_id}/spend", response_model=OrgSpendResponse, status_code=201)
async def record_org_spend(
    org_id: int,
    body: OrgSpendCreate,
    x_telegram_id: Optional[int] = Header(default=None, alias="X-Telegram-Id"),
):
    """Record a cost entry for an org (called by agents after API calls)."""
    async with async_session() as session:
        requester = await _resolve_dashboard_user(session, x_telegram_id)
        await _get_owned_org_or_404(session, org_id, requester.id)
        entry = OrgSpend(
            org_id=org_id,
            agent_id=body.agent_id,
            cost_usd=body.cost_usd,
            model_used=body.model_used,
            description=body.description,
        )
        session.add(entry)
        await session.flush()
        await _log_org_activity(
            session, org_id, "spend_recorded",
            f"${body.cost_usd:.6f} via {body.model_used or 'unknown'}: {body.description or ''}",
            agent_id=body.agent_id,
        )
        await session.commit()
        await session.refresh(entry)
    return OrgSpendResponse(
        id=entry.id, org_id=entry.org_id, agent_id=entry.agent_id,
        cost_usd=float(entry.cost_usd), model_used=entry.model_used,
        description=entry.description, recorded_at=entry.recorded_at,
    )


@app.get("/api/orgs/{org_id}/spend", response_model=OrgSpendReport)
async def get_org_spend_report(
    org_id: int,
    limit: int = 100,
    x_telegram_id: Optional[int] = Header(default=None, alias="X-Telegram-Id"),
):
    """Get spend report for an org: total, budget cap, % used, and recent entries."""
    async with async_session() as session:
        requester = await _resolve_dashboard_user(session, x_telegram_id)
        org = await _get_owned_org_or_404(session, org_id, requester.id)
        total_r = await session.execute(
            select(func.coalesce(func.sum(OrgSpend.cost_usd), 0))
            .where(OrgSpend.org_id == org_id)
        )
        total_usd = float(total_r.scalar_one())
        rows_r = await session.execute(
            select(OrgSpend)
            .where(OrgSpend.org_id == org_id)
            .order_by(desc(OrgSpend.recorded_at))
            .limit(min(limit, 500))
        )
        rows = rows_r.scalars().all()
        cap = float(org.budget_cap_usd)
        pct = (total_usd / cap * 100) if cap > 0 else 0.0
    return OrgSpendReport(
        org_id=org_id,
        total_usd=total_usd,
        budget_cap_usd=cap,
        pct_used=round(pct, 2),
        over_budget=(cap > 0 and total_usd > cap),
        entries=[
            OrgSpendResponse(
                id=e.id, org_id=e.org_id, agent_id=e.agent_id,
                cost_usd=float(e.cost_usd), model_used=e.model_used,
                description=e.description, recorded_at=e.recorded_at,
            )
            for e in rows
        ],
    )


# ── Org Activity Feed ─────────────────────────────────────────────────


@app.get("/api/orgs/{org_id}/activity", response_model=List[OrgActivityResponse])
async def list_org_activity(
    org_id: int,
    limit: int = 50,
    x_telegram_id: Optional[int] = Header(default=None, alias="X-Telegram-Id"),
):
    async with async_session() as session:
        requester = await _resolve_dashboard_user(session, x_telegram_id)
        await _get_owned_org_or_404(session, org_id, requester.id)
        result = await session.execute(
            select(OrgActivity)
            .where(OrgActivity.org_id == org_id)
            .order_by(desc(OrgActivity.created_at))
            .limit(min(limit, 200))
        )
        rows = result.scalars().all()
    return [
        OrgActivityResponse(
            id=a.id, org_id=a.org_id, agent_id=a.agent_id,
            task_id=a.task_id, action=a.action, details=a.details,
            source=a.source, created_at=a.created_at,
        )
        for a in rows
    ]


# ══════════════════════════════════════════════════════════════════════
#  PHASE C — REPAIR TICKETS (durable ticketing for repair pipeline)
# ══════════════════════════════════════════════════════════════════════


class RepairTicketCreate(BaseModel):
    title: str = Field(min_length=3, max_length=200)
    priority: str = Field(default="medium")
    source: str = Field(default="dashboard")
    error_context: Optional[dict] = None
    plan: Optional[dict] = None
    # New dashboard-driven fields (Phase 5). Stored inside error_context JSONB
    # for now so we don't need a migration until Phase 4 bundles schema changes.
    description: Optional[str] = Field(default=None, max_length=4000)
    assigned_to: Optional[str] = Field(
        default="ai_agent",
        description="Who owns this ticket. One of: 'ai_agent' (default, repair pipeline), 'admin' (manual owner action required).",
    )


class RepairTicketUpdate(BaseModel):
    status: Optional[str] = None
    priority: Optional[str] = None
    plan: Optional[dict] = None
    verification_results: Optional[dict] = None
    branch_name: Optional[str] = None


class RepairTicketResponse(BaseModel):
    id: int
    title: str
    status: str
    priority: str
    source: str
    branch_name: Optional[str] = None
    error_context: Optional[dict] = None
    plan: Optional[dict] = None
    verification_results: Optional[dict] = None

    class Config:
        from_attributes = True


@app.get("/api/tickets", response_model=list[RepairTicketResponse])
async def list_tickets(x_telegram_id: Optional[int] = Header(default=None, alias="X-Telegram-Id")):
    async with async_session() as session:
        requester = await _resolve_dashboard_user(session, x_telegram_id)
        rows = await session.execute(
            select(RepairTicket).where(RepairTicket.user_id == requester.id).order_by(desc(RepairTicket.created_at))
        )
        tickets = rows.scalars().all()
        return [
            RepairTicketResponse(
                id=t.id,
                title=t.title,
                status=t.status,
                priority=t.priority,
                source=t.source,
                branch_name=t.branch_name,
                error_context=t.error_context,
                plan=t.plan,
                verification_results=t.verification_results,
            ) for t in tickets
        ]


@app.post("/api/tickets", response_model=RepairTicketResponse, status_code=201)
async def create_ticket(body: RepairTicketCreate, x_telegram_id: Optional[int] = Header(default=None, alias="X-Telegram-Id")):
    async with async_session() as session:
        requester = await _resolve_dashboard_user(session, x_telegram_id)

        # Merge Phase-5 dashboard fields (description, assigned_to) into
        # error_context so we don't require a schema migration yet.
        merged_ctx: dict = dict(body.error_context or {})
        if body.description:
            merged_ctx["description"] = body.description.strip()
        assigned_to = (body.assigned_to or "ai_agent").strip().lower()
        if assigned_to not in {"ai_agent", "admin"}:
            assigned_to = "ai_agent"
        merged_ctx["assigned_to"] = assigned_to
        merged_ctx["created_by_dashboard"] = True

        ticket = RepairTicket(
            user_id=requester.id,
            title=body.title.strip(),
            # Admin-assigned tickets start in 'open' and pause the auto-repair
            # pipeline until the owner takes explicit action.
            status="open",
            priority=body.priority,
            source=body.source,
            error_context=merged_ctx or None,
            plan=body.plan,
        )
        session.add(ticket)
        await session.flush()  # populates ticket.id
        ticket_id = ticket.id
        await session.commit()
        logger.info(
            "Ticket created via dashboard: id=%s title=%r assigned_to=%s priority=%s",
            ticket_id, body.title.strip(), assigned_to, body.priority,
        )
        # Build the response from what we wrote (avoid a SELECT refresh which
        # can fail against pre-migration schema drift like the old
        # `debug_analysis` column mismatch).
        return RepairTicketResponse(
            id=ticket_id,
            title=body.title.strip(),
            status="open",
            priority=body.priority,
            source=body.source,
            branch_name=None,
            error_context=merged_ctx or None,
            plan=body.plan,
            verification_results=None,
        )


@app.get("/api/tickets/{ticket_id}", response_model=RepairTicketResponse)
async def get_ticket(ticket_id: int, x_telegram_id: Optional[int] = Header(default=None, alias="X-Telegram-Id")):
    async with async_session() as session:
        requester = await _resolve_dashboard_user(session, x_telegram_id)
        ticket = await session.get(RepairTicket, ticket_id)
        if ticket is None or ticket.user_id != requester.id:
            raise HTTPException(status_code=404, detail="Ticket not found")
        return RepairTicketResponse(**ticket.__dict__)


@app.patch("/api/tickets/{ticket_id}", response_model=RepairTicketResponse)
async def update_ticket(ticket_id: int, body: RepairTicketUpdate, x_telegram_id: Optional[int] = Header(default=None, alias="X-Telegram-Id")):
    async with async_session() as session:
        requester = await _resolve_dashboard_user(session, x_telegram_id)
        ticket = await session.get(RepairTicket, ticket_id)
        if ticket is None or ticket.user_id != requester.id:
            raise HTTPException(status_code=404, detail="Ticket not found")

        updates = body.model_dump(exclude_unset=True)
        if not updates:
            raise HTTPException(status_code=400, detail="No fields provided for update")

        if updates.get("priority") and updates["priority"] not in {"low", "medium", "high"}:
            raise HTTPException(status_code=400, detail="Priority must be low, medium, or high")

        if updates.get("status") and updates["status"] not in {"open", "plan_ready", "verifying", "verification_failed", "ready_for_deploy", "deployed", "closed"}:
            raise HTTPException(status_code=400, detail="Invalid ticket status")

        for key in ("status", "priority", "plan", "verification_results", "branch_name"):
            if key in updates:
                setattr(ticket, key, updates[key])
        await session.commit()
        await session.refresh(ticket)
        return RepairTicketResponse(**ticket.__dict__)


class ApproveDeployRequest(BaseModel):
    pass


@app.post("/api/tickets/{ticket_id}/approve_deploy")
async def approve_ticket_deploy_api(ticket_id: int, body: ApproveDeployRequest | None = None, x_telegram_id: Optional[int] = Header(default=None, alias="X-Telegram-Id")):
    async with async_session() as session:
        requester = await _resolve_dashboard_user(session, x_telegram_id)
    from src.repair.engine import approve_ticket_deploy
    message = await approve_ticket_deploy(ticket_id, requester.telegram_id)
    return {"message": message}


# ══════════════════════════════════════════════════════════════════════
#  WEBSOCKET
# ══════════════════════════════════════════════════════════════════════


class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def broadcast(self, message: str):
        dead: List[WebSocket] = []
        for conn in self.active_connections:
            try:
                await conn.send_text(message)
            except Exception:
                dead.append(conn)
        for d in dead:
            self.active_connections.remove(d)


ws_manager = ConnectionManager()


# ── Skills Management ─────────────────────────────────────────────────


class SkillMetadata(BaseModel):
    id: str
    name: str
    group: str
    description: str
    version: str
    author: str
    tags: list[str]
    routing_hints: list[str]
    is_knowledge_only: bool
    requires_connection: bool
    read_only: bool
    source_type: str
    is_active: bool
    has_resources: bool


class SkillDetail(SkillMetadata):
    instructions: str
    resources: dict[str, str] = Field(default_factory=dict)
    scripts: dict[str, str] = Field(default_factory=dict)
    templates: dict[str, str] = Field(default_factory=dict)
    requires_skills: list[str] = Field(default_factory=list)
    extends_skill: Optional[str] = None


class SkillCreateRequest(BaseModel):
    name: str
    description: str
    tags: list[str] = []
    routing_hints: list[str] = []
    instructions: str
    group: str = "user"


class SkillUpdateRequest(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    tags: Optional[list[str]] = None
    routing_hints: Optional[list[str]] = None
    instructions: Optional[str] = None
    is_active: Optional[bool] = None


@app.get("/api/skills", response_model=List[SkillMetadata])
async def get_skills():
    """List all installed skills with metadata."""
    from src.skills.loader import SkillLoader
    from pathlib import Path

    skills_dir = Path("src/user_skills")
    if not skills_dir.exists():
        return []

    loader = SkillLoader()
    skills = loader.load_all_from_directory()

    def _ensure_list(v):
        if isinstance(v, list):
            return v
        if isinstance(v, str):
            s = v.strip()
            if s.startswith("[") and s.endswith("]"):
                import json as _json
                try:
                    arr = _json.loads(s)
                    if isinstance(arr, list):
                        return [str(x) for x in arr]
                except (ValueError, TypeError):
                    inner = s[1:-1]
                    return [p.strip().strip('"\'') for p in inner.split(',') if p.strip()]
            if "," in s:
                return [p.strip() for p in s.split(",") if p.strip()]
            if s:
                return [s]
        return []

    items: list[SkillMetadata] = []
    for skill in skills:
        md = skill.metadata_dict()
        md["tags"] = _ensure_list(md.get("tags", []))
        md["routing_hints"] = _ensure_list(md.get("routing_hints", []))
        md["requires_skills"] = _ensure_list(md.get("requires_skills", []))
        md["requires_skills"] = _ensure_list(md.get("requires_skills", []))
        md["requires_skills"] = _ensure_list(md.get("requires_skills", []))
        # Ensure required fields exist with defaults
        md.setdefault("version", "1.0.0")
        md.setdefault("author", "user")
        md.setdefault("group", "user")
        md.setdefault("source_type", "filesystem")
        md.setdefault("is_active", True)
        md.setdefault("is_knowledge_only", True)
        md.setdefault("has_resources", False)
        items.append(SkillMetadata(**md))
    return items


@app.post("/api/skills/reload")
async def reload_skills():
    """Trigger a skills reload. Skills are loaded on-demand, but this endpoint
    exists so the Dashboard button doesn't error. Returns the current count.
    """
    from src.skills.loader import SkillLoader
    from pathlib import Path
    skills_dir = Path("src/user_skills")
    if not skills_dir.exists():
        return {"count": 0}
    loader = SkillLoader()
    skills = loader.load_all_from_directory()
    return {"count": len(skills)}


@app.get("/api/skills/{skill_id}", response_model=SkillDetail)
async def get_skill(skill_id: str):
    """Get full skill details including Level 2-3 content."""
    from src.skills.loader import SkillLoader
    from pathlib import Path

    skill_path = Path(f"src/user_skills/{skill_id}")
    if not skill_path.exists():
        raise HTTPException(status_code=404, detail="Skill not found")

    try:
        loader = SkillLoader()
        skill = loader.load_from_path(skill_path)

        md = skill.metadata_dict()
        # Normalize list fields
        def _ensure_list(v):
            if isinstance(v, list):
                return v
            if isinstance(v, str):
                s = v.strip()
                if s.startswith("[") and s.endswith("]"):
                    import ast
                    try:
                        arr = ast.literal_eval(s)
                        if isinstance(arr, list):
                            return [str(x) for x in arr]
                    except Exception:
                        inner = s[1:-1]
                        return [p.strip().strip('"\'') for p in inner.split(',') if p.strip()]
                if "," in s:
                    return [p.strip() for p in s.split(",") if p.strip()]
                if s:
                    return [s]
            return []
        md["tags"] = _ensure_list(md.get("tags", []))
        md["routing_hints"] = _ensure_list(md.get("routing_hints", []))

        return SkillDetail(
            **md,
            instructions=skill.get_full_instructions(),
            resources={k: str(v) for k, v in skill.resources.items()},
            scripts={k: str(v) for k, v in skill.scripts.items()},
            templates={k: str(v) for k, v in skill.templates.items()},
            extends_skill=skill.extends_skill,
        )
    except Exception as e:
        logger.error("Failed to load skill %s: %s", skill_id, e)
        raise HTTPException(status_code=500, detail=f"Failed to load skill: {e}")


@app.post("/api/skills", response_model=SkillMetadata, status_code=201)
async def create_skill(request: SkillCreateRequest):
    """Create a new skill from the Dashboard."""
    from pathlib import Path
    from datetime import datetime

    skill_id = _generate_skill_id(request.name)
    skill_dir = Path(f"src/user_skills/{skill_id}")

    if skill_dir.exists():
        raise HTTPException(status_code=409, detail=f"Skill '{skill_id}' already exists")

    try:
        skill_dir.mkdir(parents=True, exist_ok=False)
        skill_file = skill_dir / "SKILL.md"

        yaml_content = f"""---
name: {request.name}
description: {request.description}
version: 1.0.0
author: user
tags:
{''.join(f'  - {tag}' for tag in request.tags)}
routing_hints:
{''.join(f'  - "{hint}"' for hint in request.routing_hints)}
requires_skills: []
extends_skill: null
tools: []
requires_connection: false
read_only: true
---

{request.instructions}
"""

        skill_file.write_text(yaml_content, encoding="utf-8")

        # Return the created skill metadata
        return SkillMetadata(
            id=skill_id,
            name=request.name,
            group=request.group,
            description=request.description,
            version="1.0.0",
            author="user",
            tags=request.tags,
            routing_hints=request.routing_hints,
            is_knowledge_only=True,
            requires_connection=False,
            read_only=True,
            source_type="filesystem",
            is_active=True,
            has_resources=False,
        )

    except Exception as e:
        logger.error("Failed to create skill: %s", e)
        raise HTTPException(status_code=500, detail=f"Failed to create skill: {e}")


@app.put("/api/skills/{skill_id}", response_model=SkillDetail)
async def update_skill(skill_id: str, request: SkillUpdateRequest):
    """Update a user-created skill."""
    from pathlib import Path
    from src.skills.loader import SkillLoader

    skill_path = Path(f"src/user_skills/{skill_id}")
    skill_file = skill_path / "SKILL.md"

    if not skill_file.exists():
        raise HTTPException(status_code=404, detail="Skill not found")

    try:
        # Load existing skill
        loader = SkillLoader()
        skill = loader.load_from_path(skill_path)

        # Update fields if provided
        if request.name:
            skill.name = request.name
        if request.description:
            skill.description = request.description
        if request.tags is not None:
            skill.tags = request.tags
        if request.routing_hints is not None:
            skill.routing_hints = request.routing_hints
        if request.instructions:
            skill.instructions = request.instructions
        if request.is_active is not None:
            skill.is_active = request.is_active

        # Regenerate SKILL.md
        yaml_content = f"""---
name: {skill.name}
description: {skill.description}
version: {skill.version}
author: {skill.author}
tags:
{''.join(f'  - {tag}' for tag in skill.tags)}
routing_hints:
{''.join(f'  - "{hint}"' for hint in skill.routing_hints)}
requires_skills: []
extends_skill: null
tools: []
requires_connection: false
read_only: true
---

{skill.instructions}
"""

        skill_file.write_text(yaml_content, encoding="utf-8")

        # Reload and normalize for response
        skill = loader.load_from_path(skill_path)
        md = skill.metadata_dict()
        def _ensure_list(v):
            if isinstance(v, list):
                return v
            if isinstance(v, str):
                s = v.strip()
                if s.startswith("[") and s.endswith("]"):
                    import ast
                    try:
                        arr = ast.literal_eval(s)
                        if isinstance(arr, list):
                            return [str(x) for x in arr]
                    except Exception:
                        inner = s[1:-1]
                        return [p.strip().strip('"\'') for p in inner.split(',') if p.strip()]
                if "," in s:
                    return [p.strip() for p in s.split(",") if p.strip()]
                if s:
                    return [s]
            return []
        md["tags"] = _ensure_list(md.get("tags", []))
        md["routing_hints"] = _ensure_list(md.get("routing_hints", []))
        md["requires_skills"] = _ensure_list(md.get("requires_skills", []))

        return SkillDetail(
            **md,
            instructions=skill.get_full_instructions(),
            resources={k: str(v) for k, v in skill.resources.items()},
            scripts={k: str(v) for k, v in skill.scripts.items()},
            templates={k: str(v) for k, v in skill.templates.items()},
            extends_skill=skill.extends_skill,
        )

    except Exception as e:
        logger.error("Failed to update skill %s: %s", skill_id, e)
        raise HTTPException(status_code=500, detail=f"Failed to update skill: {e}")


@app.delete("/api/skills/{skill_id}")
async def delete_skill(skill_id: str):
    """Delete a user-created skill directory under user_skills/{skill_id}."""
    from pathlib import Path
    import shutil

    # Restrict to simple safe IDs (letters, numbers, dashes, underscores)
    import re
    if not re.fullmatch(r"[A-Za-z0-9_-]+", skill_id):
        raise HTTPException(status_code=400, detail="Invalid skill id")

    skill_path = Path(f"src/user_skills/{skill_id}")
    if not skill_path.exists() or not skill_path.is_dir():
        raise HTTPException(status_code=404, detail="Skill not found")

    try:
        shutil.rmtree(skill_path)
        return {"deleted": True, "id": skill_id}
    except Exception as e:
        logger.error("Failed to delete skill %s: %s", skill_id, e)
        raise HTTPException(status_code=500, detail=f"Failed to delete skill: {e}")


class SkillTestRequest(BaseModel):
    input: str


class SkillTestResponse(BaseModel):
    output: str
    skill_matched: bool
    routing_confidence: float
    tools_used: list[str] = []
    execution_time_ms: int


@app.post("/api/skills/{skill_id}/test", response_model=SkillTestResponse)
async def test_skill(skill_id: str, request: SkillTestRequest):
    """Test a skill in a sandbox environment (on-demand only).

    Runs routing analysis and optionally executes the skill
    to verify it works correctly. No scheduling involved.
    """
    from src.skills.validation import quick_test_skill, calculate_routing_confidence
    from src.skills.loader import SkillLoader
    from pathlib import Path
    import time

    skill_path = Path(f"src/user_skills/{skill_id}")
    if not skill_path.exists():
        raise HTTPException(status_code=404, detail="Skill not found")

    try:
        start_time = time.time()

        # Load the skill
        loader = SkillLoader()
        skill = loader.load_from_path(skill_path)

        # Calculate routing confidence
        routing_confidence = calculate_routing_confidence(request.input, skill.routing_hints)
        skill_matched = routing_confidence > 0.5

        execution_time = int((time.time() - start_time) * 1000)

        # Build response with helpful debugging info
        output_lines = [
            f"🧪 Testing skill: {skill.name}",
            f"📊 Routing confidence: {routing_confidence:.2f} (threshold: 0.5)",
            f"✅ Would trigger: {'YES' if skill_matched else 'NO'}",
            "",
            "📝 Routing hints checked:",
        ]
        for hint in skill.routing_hints[:5]:  # Show first 5
            hint_conf = calculate_routing_confidence(request.input, [hint])
            match_icon = "✓" if hint_conf > 0.3 else "○"
            output_lines.append(f"  {match_icon} '{hint}' (match: {hint_conf:.2f})")

        if len(skill.routing_hints) > 5:
            output_lines.append(f"  ... and {len(skill.routing_hints) - 5} more")

        return SkillTestResponse(
            output="\n".join(output_lines),
            skill_matched=skill_matched,
            routing_confidence=routing_confidence,
            tools_used=[],  # Skills are knowledge-only (no tools)
            execution_time_ms=execution_time,
        )

    except Exception as e:
        logger.error("Skill test failed for %s: %s", skill_id, e)
        raise HTTPException(status_code=500, detail=f"Test failed: {e}")


# ── Skill Validation (On-Demand Only) ───────────────────────────────


@app.post("/api/skills/{skill_id}/validate")
async def validate_skill_endpoint(skill_id: str, test_cases: Optional[list[dict]] = None):
    """Run immediate validation tests on a skill (on-demand only, no scheduling)."""
    from src.skills.validation import validate_skill

    try:
        results = await validate_skill(skill_id, test_cases)
        return {
            "skill_id": skill_id,
            "test_count": len(results),
            "passed": sum(1 for r in results if r.passed),
            "failed": sum(1 for r in results if not r.passed),
            "results": [
                {
                    "test_case": r.test_case,
                    "passed": r.passed,
                    "skill_matched": r.skill_matched,
                    "routing_confidence": r.routing_confidence,
                    "execution_time_ms": r.execution_time_ms,
                    "error": r.error,
                }
                for r in results
            ],
        }
    except Exception as e:
        logger.error("Failed to validate skill: %s", e)
        raise HTTPException(status_code=500, detail=f"Validation failed: {e}")


@app.post("/api/skills/reload")
async def reload_skills():
    """Hot-reload filesystem skills."""
    from src.skills.loader import SkillLoader
    from src.agents.orchestrator import _registry_cache

    loader = SkillLoader()
    skills = loader.load_all_from_directory()

    # Invalidate all registry caches
    _registry_cache.clear()

    return {"message": f"Reloaded {len(skills)} skills", "count": len(skills)}


# ── Skill Creation Wizard (AI-Assisted) ────────────────────────────

class SkillWizardStartRequest(BaseModel):
    description: str


class SkillWizardAnswerRequest(BaseModel):
    session_id: str
    answer: str


class SkillWizardResponse(BaseModel):
    step: str  # "interviewing" | "review" | "completed"
    session_id: str
    questions: Optional[list[str]] = None
    skill_preview: Optional[dict] = None
    message: Optional[str] = None


class SkillWizardSaveRequest(BaseModel):
    session_id: str
    skill_data: Optional[dict] = None  # Optional overrides from frontend review step


# In-memory session store (could be Redis in production)
_skill_wizard_sessions: dict[str, dict] = {}


@app.post("/api/skills/wizard/start", response_model=SkillWizardResponse)
async def skill_wizard_start(
    request: SkillWizardStartRequest,
    x_telegram_id: Optional[int] = Header(default=None, alias="X-Telegram-Id"),
):
    """Start AI-assisted skill creation wizard with initial description."""
    import uuid
    from src.models.router import ModelRole, select_model
    from agents import Agent, Runner
    from agents import function_tool as ft

    user_id = x_telegram_id or 1
    session_id = str(uuid.uuid4())

    # Initialize session
    session = {
        "user_id": user_id,
        "step": "interviewing",
        "description": request.description,
        "questions_asked": [],
        "answers": [],
        "skill_data": None,
    }
    _skill_wizard_sessions[session_id] = session

    # Use skill factory logic to generate initial questions with chain-of-thought
    model_selection = select_model(ModelRole.FAST)
    agent = Agent(
        name="Skill Factory",
        instructions="""You are a Skill Factory specialist. Help users create custom skills through structured interviews.

## What is a Skill?
A skill is a package of expertise that guides how the AI responds to specific requests. Unlike tools (which execute code), skills provide instructions and context.

## Interview Strategy (Chain-of-Thought)
Think step-by-step:
1. Analyze the user's description for key intent and purpose
2. Identify missing information needed for a complete skill
3. Plan 3-5 targeted questions to fill gaps
4. Format as clear, numbered questions

## What Makes Good Routing Hints?
Routing hints are natural language phrases that should trigger this skill:

GOOD examples (contextual, natural):
- "when writing my weekly report" (user describing their action)
- "for status updates" (user describing purpose)
- "help me draft a devotional" (user requesting assistance)

BAD examples (too meta, artificial):
- "use this skill" (tells system what to do)
- "activate skill X" (system command language)
- "run the skill" (implementation detail)

## Output Format
Return your response in this exact structure:
THOUGHT: <brief analysis of what the skill needs>
QUESTIONS:
1. <first specific question>
2. <second specific question>
3. <third specific question>
[continue as needed]

Be conversational but focused. Each question should reveal one key aspect of the skill.""",
        model=model_selection.model_id if hasattr(model_selection, 'model_id') else str(model_selection),
    )

    prompt = f"""The user wants to create a new skill.

Description: {request.description}

Conduct a structured interview following the chain-of-thought approach in your instructions.
Analyze the description, identify gaps, and ask 3-5 targeted clarifying questions.

Return your response in the required format with THOUGHT and QUESTIONS sections."""

    try:
        result = await Runner.run(agent, prompt)
        response_text = result.final_output

        # Extract questions from response
        questions = _extract_wizard_questions(response_text)
        session["questions_asked"] = questions

        return SkillWizardResponse(
            step="interviewing",
            session_id=session_id,
            questions=questions,
            message=response_text if not questions else None,
        )
    except Exception as e:
        logger.error("Skill wizard start failed: %s", e)
        raise HTTPException(status_code=500, detail=f"Failed to start wizard: {e}")


@app.post("/api/skills/wizard/answer", response_model=SkillWizardResponse)
async def skill_wizard_answer(request: SkillWizardAnswerRequest):
    """Submit answer to wizard question. Returns next questions or skill preview."""
    from src.models.router import ModelRole, select_model
    from agents import Agent, Runner
    import json

    session_id = request.session_id
    if session_id not in _skill_wizard_sessions:
        raise HTTPException(status_code=404, detail="Session not found")

    session = _skill_wizard_sessions[session_id]
    session["answers"].append(request.answer)

    # Check if we have enough info to generate
    if len(session["answers"]) >= len(session["questions_asked"]):
        # Generate the skill
        return await _generate_skill_from_wizard(session_id, session)

    # Otherwise, continue interview
    model_selection = select_model(ModelRole.FAST)
    agent = Agent(
        name="Skill Factory",
        instructions="""You are a Skill Factory specialist. Continue the skill creation interview.

Based on the user's answers so far, either:
1. Ask follow-up questions if you need more information
2. Indicate you're ready to generate by saying "I have enough information to create your skill."

Be conversational and build on previous answers.""",
        model=model_selection.model_id if hasattr(model_selection, 'model_id') else str(model_selection),
    )

    context = f"""Original description: {session['description']}

Q&A so far:
"""
    for q, a in zip(session["questions_asked"], session["answers"]):
        context += f"Q: {q}\nA: {a}\n\n"

    context += f"User just answered: {request.answer}\n\nContinue the interview or indicate readiness to generate."

    try:
        result = await Runner.run(agent, context)
        response_text = result.final_output

        # Check if ready to generate
        ready_indicators = ["enough information", "ready to create", "generate the skill", "create your skill"]
        if any(ind in response_text.lower() for ind in ready_indicators):
            return await _generate_skill_from_wizard(session_id, session)

        # Extract more questions
        questions = _extract_wizard_questions(response_text)
        if questions:
            session["questions_asked"].extend(questions)

        return SkillWizardResponse(
            step="interviewing",
            session_id=session_id,
            questions=questions if questions else None,
            message=response_text if not questions else None,
        )
    except Exception as e:
        logger.error("Skill wizard answer failed: %s", e)
        raise HTTPException(status_code=500, detail=f"Failed to process answer: {e}")


async def _generate_skill_from_wizard(session_id: str, session: dict) -> SkillWizardResponse:
    """Generate skill from completed wizard session."""
    from src.models.router import ModelRole, select_model
    from agents import Agent, Runner
    import json
    import re

    model_selection = select_model(ModelRole.FAST)
    agent = Agent(
        name="Skill Factory",
        instructions="""You are a Skill Factory specialist. Generate a complete skill from the interview using structured output.

## Chain-of-Thought Generation Process
1. Analyze the interview to identify the core purpose and unique value
2. Design routing hints that capture natural user language
3. Structure instructions with clear sections and examples
4. Compile metadata (name, description, tags)
5. Format as valid JSON

## Output Schema (STRICT JSON)
```json
{
  "name": "Skill Name (3-5 words, title case)",
  "description": "One-line description of what this skill does",
  "routing_hints": [
    "natural phrase 1 - when user describes their need",
    "natural phrase 2 - context where skill applies",
    "natural phrase 3 - action user wants to take"
  ],
  "instructions": "## Purpose\\n\\nWhat this skill does and when to use it.\\n\\n## Format Guidelines\\n\\nStructure rules, tone, style.\\n\\n## Process\\n\\nStep-by-step how to execute.\\n\\n## Examples\\n\\nInput: example request\\nOutput: example response\\n\\n## Edge Cases\\n\\nHow to handle special situations.",
  "tags": ["keyword1", "keyword2", "keyword3"]
}
```

## Few-Shot Examples

EXAMPLE 1 - Weekly Report Skill:
Input: User wants help writing weekly status reports for their team
Output:
{
  "name": "Weekly Status Report Writer",
  "description": "Helps draft professional weekly status reports for team updates",
  "routing_hints": [
    "when writing my weekly report",
    "help me draft a status update",
    "for my team status meeting"
  ],
  "instructions": "## Purpose\\n\\nHelp users write clear, professional weekly status reports...",
  "tags": ["writing", "reports", "work", "productivity"]
}

EXAMPLE 2 - Devotional Generator:
Input: User wants morning devotionals with theological depth
Output:
{
  "name": "Morning Devotional Writer",
  "description": "Generates thoughtful morning devotionals with scripture and reflection",
  "routing_hints": [
    "write me a devotional",
    "for my morning meditation",
    "help me reflect on scripture"
  ],
  "instructions": "## Purpose\\n\\nCreate meaningful devotionals that combine scripture...",
  "tags": ["spiritual", "devotional", "faith", "meditation"]
}

## Critical Rules
- Instructions must be comprehensive (min 200 words)
- Include at least 2 concrete examples
- Routing hints must be natural user language, NOT system commands
- Tags should be lowercase, relevant keywords
- Output ONLY valid JSON, no markdown wrapper""",
        model=model_selection.model_id if hasattr(model_selection, 'model_id') else str(model_selection),
    )

    context = f"""Create a skill based on this interview:

Original description: {session['description']}

Q&A:
"""
    for q, a in zip(session["questions_asked"], session["answers"]):
        context += f"Q: {q}\nA: {a}\n\n"

    context += "Generate the complete skill as JSON following the schema and examples in your instructions."

    try:
        result = await Runner.run(agent, context)
        response_text = result.final_output

        # Try to extract JSON
        skill_data = _extract_skill_json(response_text)

        if skill_data:
            # Generate skill ID
            skill_id = _generate_wizard_skill_id(skill_data.get("name", "unnamed"))
            skill_data["id"] = skill_id
            session["skill_data"] = skill_data
            session["step"] = "review"

            return SkillWizardResponse(
                step="review",
                session_id=session_id,
                skill_preview=skill_data,
            )
        else:
            # Failed to parse JSON, return as message
            return SkillWizardResponse(
                step="interviewing",
                session_id=session_id,
                message="I generated the skill but had trouble formatting it. Let me try again:\n\n" + response_text,
            )
    except Exception as e:
        logger.error("Skill generation failed: %s", e)
        raise HTTPException(status_code=500, detail=f"Failed to generate skill: {e}")


def _extract_wizard_questions(text: str) -> list[str]:
    """Extract numbered questions from wizard response."""
    import re
    questions = []
    for line in text.split('\n'):
        match = re.match(r'^\s*(?:\d+[\.\)])+\s*(.+)$', line)
        if match:
            questions.append(match.group(1).strip())
    return questions


def _extract_skill_json(text: str) -> Optional[dict]:
    """Extract skill JSON from response text."""
    import json
    import re
    # Try to find JSON block
    json_match = re.search(r'```json\n(.*?)\n```', text, re.DOTALL)
    if json_match:
        try:
            return json.loads(json_match.group(1))
        except:
            pass
    # Try to find raw JSON
    try:
        return json.loads(text)
    except:
        pass
    # Try to find JSON between braces
    brace_match = re.search(r'\{.*\}', text, re.DOTALL)
    if brace_match:
        try:
            return json.loads(brace_match.group(0))
        except:
            pass
    return None


def _generate_wizard_skill_id(name: str) -> str:
    """Generate URL-friendly skill ID from name."""
    import re
    skill_id = name.lower()
    skill_id = re.sub(r'[^\w\s-]', '', skill_id)
    skill_id = re.sub(r'\s+', '-', skill_id)
    return skill_id[:50]


@app.post("/api/skills/wizard/save")
async def skill_wizard_save(
    request: SkillWizardSaveRequest,
    x_telegram_id: Optional[int] = Header(default=None, alias="X-Telegram-Id"),
):
    """Save skill generated by wizard to filesystem."""
    from pathlib import Path

    session_id = request.session_id
    if session_id not in _skill_wizard_sessions:
        raise HTTPException(status_code=404, detail="Session not found")

    session = _skill_wizard_sessions[session_id]
    skill_data = session.get("skill_data")

    # Apply frontend edits if provided (user may have changed name/description/instructions)
    if request.skill_data:
        skill_data = {**(skill_data or {}), **request.skill_data}

    if not skill_data:
        raise HTTPException(status_code=400, detail="No skill data to save")

    try:
        skill_id = skill_data.get("id", "unnamed-skill")
        skill_dir = Path(f"src/user_skills/{skill_id}")
        skill_dir.mkdir(parents=True, exist_ok=True)

        skill_file = skill_dir / "SKILL.md"

        yaml_content = f"""---
name: {skill_data.get('name', 'Unnamed Skill')}
description: {skill_data.get('description', '')}
version: 1.0.0
author: user
tags:
{''.join(f'  - {tag}' for tag in skill_data.get('tags', []))}
routing_hints:
{''.join(f'  - "{hint}"' for hint in skill_data.get('routing_hints', []))}
requires_skills: []
extends_skill: null
tools: []
requires_connection: false
read_only: true
---

{skill_data.get('instructions', '')}
"""

        skill_file.write_text(yaml_content, encoding="utf-8")

        # Cleanup session
        del _skill_wizard_sessions[session_id]

        return {
            "success": True,
            "skill_id": skill_id,
            "message": f"Skill '{skill_data.get('name')}' created successfully",
        }
    except Exception as e:
        logger.error("Failed to save wizard skill: %s", e)
        raise HTTPException(status_code=500, detail=f"Failed to save skill: {e}")


@app.post("/api/skills/wizard/cancel")
async def skill_wizard_cancel(request: SkillWizardSaveRequest):
    """Cancel wizard session and cleanup."""
    session_id = request.session_id
    if session_id in _skill_wizard_sessions:
        del _skill_wizard_sessions[session_id]
    return {"success": True, "message": "Wizard session cancelled"}


# ── Tool Creation Wizard (AI-Assisted) ────────────────────────────

class ToolWizardStartRequest(BaseModel):
    description: str


class ToolWizardAnswerRequest(BaseModel):
    session_id: str
    answer: str


class ToolWizardResponse(BaseModel):
    step: str
    session_id: str
    questions: Optional[List[str]] = None
    tool_preview: Optional[dict] = None
    message: Optional[str] = None


class ToolWizardSaveRequest(BaseModel):
    session_id: str
    modified_code: Optional[str] = None


# In-memory session store
_tool_wizard_sessions: dict[str, dict] = {}


@app.post("/api/tools/wizard/start", response_model=ToolWizardResponse)
async def tool_wizard_start(request: ToolWizardStartRequest, x_telegram_id: Optional[int] = Header(default=None, alias="X-Telegram-Id")):
    """Start AI-assisted tool creation wizard."""
    import uuid
    from src.models.router import ModelRole, select_model
    from agents import Agent, Runner

    user_id = x_telegram_id or 1
    session_id = str(uuid.uuid4())

    session = {
        "user_id": user_id,
        "step": "interviewing",
        "description": request.description.strip(),
        "questions": [],
        "answers": [],
        "tool_data": None,
    }
    _tool_wizard_sessions[session_id] = session

    model_selection = select_model(ModelRole.FAST)
    instructions = (
        "You are a Tool Factory specialist. First, ask 3-5 clarifying questions to design a safe CLI tool. "
        "Focus on parameters (names, types, required), output format (text/json), network needs (hosts), and examples. "
        "Return questions as a numbered list. Do not generate code yet."
    )
    agent = Agent(name="Tool Wizard", instructions=instructions, model=model_selection.model_id)

    prompt = (
        f"User wants a CLI tool: {session['description']}\n\n"
        "Ask concise numbered questions to clarify requirements."
    )
    result = await Runner.run(agent, prompt)
    questions = _extract_wizard_questions(result.final_output) or [
        "What inputs (parameters) should this tool accept? Please name each with type and whether required.",
        "What output format do you prefer (text, json, table)?",
        "Does it need network access? If yes, list allowed hostnames.",
        "Share a quick example of expected input and output.",
    ]
    session["questions"] = questions

    return ToolWizardResponse(step="interviewing", session_id=session_id, questions=questions)


@app.post("/api/tools/wizard/answer", response_model=ToolWizardResponse)
async def tool_wizard_answer(request: ToolWizardAnswerRequest):
    """Submit an interview answer; may return more questions or a tool preview."""
    from src.models.router import ModelRole, select_model
    from agents import Agent, Runner
    import json

    session_id = request.session_id
    if session_id not in _tool_wizard_sessions:
        raise HTTPException(status_code=404, detail="Session not found")

    session = _tool_wizard_sessions[session_id]
    session["answers"].append(request.answer)

    # If we have fewer than 3 answers, continue asking questions
    if len(session["answers"]) < 3:
        model_selection = select_model(ModelRole.FAST)
        agent = Agent(
            name="Tool Wizard",
            instructions=(
                "Ask the next 1-2 concise questions needed to finalize the tool requirements. "
                "Return only a numbered list of questions."
            ),
            model=model_selection.model_id,
        )
        qna = "\n".join(
            [f"Q: {q}\nA: {a}" for q, a in zip(session.get("questions", []), session.get("answers", []))]
        )
        prompt = f"Tool: {session['description']}\n\nPrevious Q&A:\n{qna}\n\nAsk remaining questions."
        result = await Runner.run(agent, prompt)
        questions = _extract_wizard_questions(result.final_output)
        if questions:
            session["questions"].extend(questions)
        return ToolWizardResponse(step="interviewing", session_id=session_id, questions=questions or [])

    # Otherwise, attempt generation
    return await _generate_tool_from_wizard(session_id, session)


async def _generate_tool_from_wizard(session_id: str, session: dict) -> ToolWizardResponse:
    from src.models.router import ModelRole, select_model
    from agents import Agent, Runner
    import json

    model_selection = select_model(ModelRole.CODING)
    instructions = (
        "Design a safe Python CLI tool using argparse. Include --format json|text support. "
        "No subprocess/shutil/eval/exec/os.system. No environment variable reads. Use stdout/stderr. "
        "First output a JSON spec (name, description, parameters, requires_network, allowed_hosts, tags) in a ```json block. "
        "Then output the complete Python code in a ```python block."
    )
    agent = Agent(name="Tool Codegen", instructions=instructions, model=model_selection.model_id)

    qna = "\n".join(
        [f"Q: {q}\nA: {a}" for q, a in zip(session.get("questions", []), session.get("answers", []))]
    )
    prompt = (
        f"User wants a tool: {session['description']}\n\n"
        f"Interview summary:\n{qna}\n\n"
        "Generate the JSON spec then the code as instructed."
    )
    result = await Runner.run(agent, prompt)
    text = result.final_output

    spec = _extract_json_block(text) or {}
    code = _extract_code_block(text, language="python") or ""

    # Run static analysis
    try:
        from src.tools.sandbox import static_analysis
        violations = static_analysis(code)
    except Exception as e:
        violations = [f"Static analysis failed: {e}"]

    allowed_hosts = ", ".join(spec.get("allowed_hosts", [])) if isinstance(spec.get("allowed_hosts"), list) else spec.get("allowed_hosts", "")
    tool_preview = {
        "name": spec.get("name", "unnamed_tool"),
        "description": spec.get("description", session.get("description", "")),
        "parameters_json": json.dumps(spec.get("parameters", {})),
        "requires_network": bool(spec.get("requires_network", False)),
        "allowed_hosts": allowed_hosts,
        "tags": spec.get("tags", []),
        "code": code,
        "safety_violations": violations,
    }

    session["tool_data"] = tool_preview
    session["step"] = "review"

    return ToolWizardResponse(step="review", session_id=session_id, tool_preview=tool_preview)


@app.post("/api/tools/wizard/save")
async def tool_wizard_save(request: ToolWizardSaveRequest):
    """Save generated tool and register it."""
    import json
    session_id = request.session_id
    if session_id not in _tool_wizard_sessions:
        raise HTTPException(status_code=404, detail="Session not found")

    session = _tool_wizard_sessions[session_id]
    tool_data = session.get("tool_data")
    if not tool_data:
        raise HTTPException(status_code=400, detail="No tool data to save")

    # Apply modified code if provided
    if request.modified_code is not None:
        tool_data["code"] = request.modified_code
        # Re-run static analysis
        try:
            from src.tools.sandbox import static_analysis
            tool_data["safety_violations"] = static_analysis(tool_data["code"]) or []
        except Exception:
            tool_data["safety_violations"] = ["Static analysis failed on modified code"]

    # Final safety check: block save if critical violations present
    violations = tool_data.get("safety_violations") or []
    critical = [v for v in violations if "subprocess" in v or "eval" in v or "exec" in v or "os.system" in v]
    if critical:
        raise HTTPException(status_code=400, detail="Critical safety violations present. Fix code before saving.")

    try:
        from src.agents.tool_factory_agent import _generate_cli_tool_impl
        msg = await _generate_cli_tool_impl(
            name=tool_data.get("name", "unnamed_tool"),
            description=tool_data.get("description", ""),
            parameters_json=tool_data.get("parameters_json", json.dumps({})),
            tool_code=tool_data.get("code", ""),
            requires_network=bool(tool_data.get("requires_network", False)),
            allowed_hosts=tool_data.get("allowed_hosts", ""),
        )
        # Clear session
        del _tool_wizard_sessions[session_id]
        return {"success": True, "message": msg}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to save tool: {e}")


@app.post("/api/tools/wizard/cancel")
async def tool_wizard_cancel(request: ToolWizardSaveRequest):
    session_id = request.session_id
    if session_id in _tool_wizard_sessions:
        del _tool_wizard_sessions[session_id]
    return {"success": True}


# ── Agent Creation Wizard (AI-Assisted) ───────────────────────────────
# Mirror of the Tools / Skills wizard pattern: 4-step (describe → interview
# → review → done) with an in-memory session store keyed by uuid. Adapts
# the prompt to ask the things an OrgAgentCreate needs (role,
# responsibilities, skills, allowed_tools, model_tier).

class AgentWizardStartRequest(BaseModel):
    org_id: int
    description: str


class AgentWizardAnswerRequest(BaseModel):
    session_id: str
    answer: str


class AgentWizardResponse(BaseModel):
    step: str
    session_id: str
    questions: Optional[List[str]] = None
    agent_preview: Optional[dict] = None
    message: Optional[str] = None


class AgentWizardSaveRequest(BaseModel):
    session_id: str
    overrides: Optional[dict] = None


_agent_wizard_sessions: dict[str, dict] = {}


@app.post("/api/agents/wizard/start", response_model=AgentWizardResponse)
async def agent_wizard_start(
    request: AgentWizardStartRequest,
    x_telegram_id: Optional[int] = Header(default=None, alias="X-Telegram-Id"),
):
    """Start AI-assisted agent creation wizard."""
    import uuid
    from src.models.router import ModelRole, select_model
    from agents import Agent, Runner

    user_id = x_telegram_id or 1
    session_id = str(uuid.uuid4())

    session = {
        "user_id": user_id,
        "org_id": request.org_id,
        "step": "interviewing",
        "description": request.description.strip(),
        "questions": [],
        "answers": [],
        "agent_data": None,
    }
    _agent_wizard_sessions[session_id] = session

    model_selection = select_model(ModelRole.FAST)
    instructions = (
        "You are an Agent Factory specialist. The user is creating a NEW AI sub-agent inside an "
        "organization. Ask 3-5 concise clarifying questions to design it. Focus on:\n"
        "  1. The agent's primary role / job title (e.g., 'inbox triage', 'meeting scheduler').\n"
        "  2. Concrete responsibilities (what tasks does it own end-to-end?).\n"
        "  3. Which existing skills it should be able to invoke.\n"
        "  4. Which tools it needs access to (Gmail, Calendar, Tasks, web search, etc.).\n"
        "  5. Model tier preference: 'fast' (gpt-4o-mini-class), 'general' (gpt-4o-class), "
        "     or 'reasoning' (o1-class) — based on how complex its decisions are.\n"
        "Return questions as a numbered list. Do not propose an agent yet."
    )
    agent = Agent(name="Agent Wizard", instructions=instructions, model=model_selection.model_id)

    prompt = (
        f"User wants a new sub-agent: {session['description']}\n\n"
        "Ask concise numbered questions to clarify the design."
    )
    result = await Runner.run(agent, prompt)
    questions = _extract_wizard_questions(result.final_output) or [
        "What is the agent's primary role or job title?",
        "What concrete tasks does it own end-to-end?",
        "Which skills should it be able to invoke (list by name or describe)?",
        "Which tools does it need (Gmail, Calendar, Tasks, web search, etc.)?",
        "Model tier — fast, general, or reasoning?",
    ]
    session["questions"] = questions

    return AgentWizardResponse(step="interviewing", session_id=session_id, questions=questions)


@app.post("/api/agents/wizard/answer", response_model=AgentWizardResponse)
async def agent_wizard_answer(request: AgentWizardAnswerRequest):
    """Submit an interview answer; may return more questions or an agent preview."""
    from src.models.router import ModelRole, select_model
    from agents import Agent, Runner

    session_id = request.session_id
    if session_id not in _agent_wizard_sessions:
        raise HTTPException(status_code=404, detail="Session not found")

    session = _agent_wizard_sessions[session_id]
    session["answers"].append(request.answer)

    if len(session["answers"]) < 3:
        model_selection = select_model(ModelRole.FAST)
        agent = Agent(
            name="Agent Wizard",
            instructions=(
                "Ask the next 1-2 concise questions needed to finalize the agent design. "
                "Return only a numbered list of questions."
            ),
            model=model_selection.model_id,
        )
        qna = "\n".join(
            [f"Q: {q}\nA: {a}" for q, a in zip(session.get("questions", []), session.get("answers", []))]
        )
        prompt = f"Agent: {session['description']}\n\nPrevious Q&A:\n{qna}\n\nAsk remaining questions."
        result = await Runner.run(agent, prompt)
        questions = _extract_wizard_questions(result.final_output)
        if questions:
            session["questions"].extend(questions)
        return AgentWizardResponse(step="interviewing", session_id=session_id, questions=questions or [])

    return await _generate_agent_from_wizard(session_id, session)


async def _generate_agent_from_wizard(session_id: str, session: dict) -> AgentWizardResponse:
    """Synthesize an OrgAgentCreate-shaped draft from the interview answers."""
    from src.models.router import ModelRole, select_model
    from agents import Agent, Runner

    model_selection = select_model(ModelRole.GENERAL)
    instructions = (
        "Design an OrgAgent record. Output STRICTLY one JSON block:\n"
        "```json\n"
        "{\n"
        '  "name": "<short snake_or_kebab name>",\n'
        '  "role": "<concise role title>",\n'
        '  "description": "<one-sentence summary>",\n'
        '  "instructions": "<system-prompt-style guidance, 3-8 lines>",\n'
        '  "skills": ["skill_id_1", "skill_id_2"],\n'
        '  "allowed_tools": ["gmail", "calendar"],\n'
        '  "model_tier": "fast|general|reasoning"\n'
        "}\n"
        "```\n"
        "- Pick model_tier conservatively (default 'general' unless reasoning is clearly needed).\n"
        "- Skills/allowed_tools should be plausible identifiers but the user will edit them in review.\n"
        "- Keep instructions actionable and short — they go into the runtime system prompt."
    )
    agent = Agent(name="Agent Codegen", instructions=instructions, model=model_selection.model_id)

    qna = "\n".join(
        [f"Q: {q}\nA: {a}" for q, a in zip(session.get("questions", []), session.get("answers", []))]
    )
    prompt = (
        f"User wants a sub-agent: {session['description']}\n\n"
        f"Interview summary:\n{qna}\n\n"
        "Generate the OrgAgent JSON spec as instructed."
    )
    result = await Runner.run(agent, prompt)
    spec = _extract_json_block(result.final_output) or {}

    # Default-fill anything the LLM missed so the UI always gets a complete preview
    agent_preview = {
        "name": spec.get("name") or "new_agent",
        "role": spec.get("role") or "specialist",
        "description": spec.get("description") or session.get("description", ""),
        "instructions": spec.get("instructions") or "",
        "skills": list(spec.get("skills") or []),
        "allowed_tools": list(spec.get("allowed_tools") or []),
        "model_tier": spec.get("model_tier") or "general",
    }
    if agent_preview["model_tier"] not in ("fast", "general", "reasoning"):
        agent_preview["model_tier"] = "general"

    session["agent_data"] = agent_preview
    session["step"] = "review"

    return AgentWizardResponse(step="review", session_id=session_id, agent_preview=agent_preview)


@app.post("/api/agents/wizard/save")
async def agent_wizard_save(
    request: AgentWizardSaveRequest,
    x_telegram_id: Optional[int] = Header(default=None, alias="X-Telegram-Id"),
):
    """Persist the wizard's drafted agent into the org's agent roster."""
    session_id = request.session_id
    if session_id not in _agent_wizard_sessions:
        raise HTTPException(status_code=404, detail="Session not found")

    session = _agent_wizard_sessions[session_id]
    agent_data = session.get("agent_data")
    if not agent_data:
        raise HTTPException(status_code=400, detail="No agent data to save")

    # Apply user edits from the review step (if any)
    if request.overrides:
        for key in ("name", "role", "description", "instructions", "skills", "allowed_tools", "model_tier"):
            if key in request.overrides:
                agent_data[key] = request.overrides[key]

    org_id = session["org_id"]

    # Mirror the create-agent logic at POST /api/orgs/{org_id}/agents (line ~3518)
    # so validation + tools_config merging + activity logging stay identical
    # between the manual-create path and the wizard-create path.
    try:
        async with async_session() as db_session:
            requester = await _resolve_dashboard_user(db_session, x_telegram_id)
            await _get_owned_org_or_404(db_session, org_id, requester.id)
            merged_tc: dict = {}
            skills_list = agent_data.get("skills") or []
            tools_list = agent_data.get("allowed_tools") or []
            if skills_list:
                merged_tc["skills"] = list(skills_list)
            if tools_list:
                merged_tc["allowed_tools"] = list(tools_list)
            new_agent = OrgAgent(
                org_id=org_id,
                name=agent_data["name"],
                role=agent_data["role"],
                description=agent_data.get("description"),
                instructions=agent_data.get("instructions"),
                tools_config=merged_tc,
                model_tier=agent_data.get("model_tier", "general"),
            )
            db_session.add(new_agent)
            await db_session.flush()
            await _log_org_activity(
                db_session, org_id, "agent_created",
                f"Agent '{new_agent.name}' ({new_agent.role}) created via wizard",
                agent_id=new_agent.id,
            )
            await db_session.commit()
            agent_id = new_agent.id
            agent_name = new_agent.name
        del _agent_wizard_sessions[session_id]
        return {
            "success": True,
            "agent": {
                "id": agent_id,
                "name": agent_name,
                "role": agent_data["role"],
                "org_id": org_id,
            },
        }
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"Failed to save agent: {e}")


@app.post("/api/agents/wizard/cancel")
async def agent_wizard_cancel(request: AgentWizardSaveRequest):
    session_id = request.session_id
    if session_id in _agent_wizard_sessions:
        del _agent_wizard_sessions[session_id]
    return {"success": True}


def _extract_code_block(text: str, language: str = "python") -> Optional[str]:
    import re
    code_match = re.search(rf"```{language}\\n(.*?)\\n```", text, re.DOTALL)
    if code_match:
        return code_match.group(1)
    # any fenced block
    any_match = re.search(r"```[a-zA-Z0-9_+-]*\n(.*?)\n```", text, re.DOTALL)
    if any_match:
        return any_match.group(1)
    return None


def _extract_json_block(text: str) -> Optional[dict]:
    import re, json
    json_match = re.search(r"```json\n(.*?)\n```", text, re.DOTALL)
    if json_match:
        try:
            return json.loads(json_match.group(1))
        except Exception:
            pass
    # try raw object
    brace = re.search(r"\{[\s\S]*\}", text)
    if brace:
        try:
            return json.loads(brace.group(0))
        except Exception:
            pass
    return None

# ── Scheduler Health & Diagnostics ─────────────────────────────────


@app.get("/api/scheduler/health")
async def scheduler_runtime_health():
    """Dashboard-facing scheduler runtime check.

    Distinct from the public observability endpoint at `/api/health/scheduler`:
        - `/api/health/scheduler` (public, no auth) reports per-job *outcome*
          history from the Redis observability records (consecutive_failures,
          last_status, etc.) populated by the JobReleased listener. Best for
          monitoring tools.
        - This endpoint (`/api/scheduler/health`, dashboard-only) reports
          *runtime liveness*: whether APScheduler is alive, how many jobs are
          currently registered, and the upcoming-run preview from the
          `scheduled_tasks` table. Best for "is my scheduler container up?".

    To save the dashboard a round-trip, this endpoint also includes the
    observability snapshot under `per_job_health` so the UI gets both views
    in one call.
    """
    try:
        # Query scheduled_tasks table directly from DB
        async with async_session() as session:
            from sqlalchemy import select, func
            result = await session.execute(
                select(func.count(ScheduledTask.id)).where(ScheduledTask.is_active == True)
            )
            active_count = result.scalar() or 0

            # Get recent tasks
            tasks_result = await session.execute(
                select(ScheduledTask)
                .where(ScheduledTask.is_active == True)
                .order_by(ScheduledTask.next_run_at)
                .limit(10)
            )
            tasks = tasks_result.scalars().all()

        # Query APScheduler's own table via direct SQL
        live_jobs = await _get_live_apscheduler_jobs()

        # Scheduler is "healthy" if there are jobs in APScheduler table
        # or if there are active tasks in our DB (scheduler may be starting up)
        is_healthy = len(live_jobs) > 0 or active_count > 0

        # Co-locate the per-job observability snapshot so the dashboard's
        # one poll covers both runtime liveness AND per-job health.
        per_job_health: dict
        try:
            from src.scheduler.observability import get_health_snapshot
            per_job_health = await get_health_snapshot()
        except Exception as snap_err:
            logger.warning("Could not attach per_job_health snapshot: %s", snap_err)
            per_job_health = {"status": "unknown", "jobs": [], "summary": {"error": str(snap_err)}}

        return {
            "status": "healthy" if is_healthy else "starting",
            "scheduler_running": len(live_jobs) > 0,
            "active_tasks_in_db": active_count,
            "active_jobs_in_scheduler": len(live_jobs),
            "jobs": [
                {
                    "id": t.apscheduler_id,
                    "task_id": t.id,
                    "description": t.description[:50] if t.description else None,
                    "next_fire_time": t.next_run_at.isoformat() if t.next_run_at else None,
                }
                for t in tasks
            ],
            "per_job_health": per_job_health,
            "message": "Scheduler operational" if is_healthy else "Scheduler starting or no active jobs",
        }
    except Exception as e:
        logger.error("Scheduler health check failed: %s", e)
        return {
            "status": "unhealthy",
            "error": str(e),
            "message": "Could not query scheduler status",
        }


@app.post("/api/scheduler/test-cron")
async def test_cron_job():
    """Create a test cron job to verify scheduling works (runs in 1 minute).

    Note: Since this runs in orchestration-api (not assistant), we create the
    job directly in APScheduler's PostgreSQL tables. The assistant container's
    scheduler will pick it up.
    """
    from datetime import datetime, timezone, timedelta
    import uuid

    test_job_id = f"test_cron_{uuid.uuid4().hex[:8]}"

    try:
        # Calculate 1 minute from now
        now = datetime.now(timezone.utc)
        run_at = now + timedelta(minutes=1)

        # Insert directly into APScheduler's schedules table
        # This is the same table that APScheduler uses
        async with async_session() as session:
            # First, create a scheduled_tasks entry for tracking
            from sqlalchemy import select
            from src.db.models import User

            # Get owner user (assuming user_id 1 or first user)
            user_result = await session.execute(select(User).limit(1))
            user = user_result.scalar_one_or_none()

            if not user:
                raise HTTPException(status_code=400, detail="No users found to send test message to")

            # Create the task record
            task = ScheduledTask(
                user_id=user.id,
                apscheduler_id=test_job_id,
                description="🧪 Test cron job - manual test from dashboard",
                trigger_type="once",  # Use once for immediate test
                trigger_config={"once": {"run_at": run_at.isoformat()}},
                job_function="src.scheduler.jobs:send_reminder",
                is_active=True,
                next_run_at=run_at,
            )
            session.add(task)
            await session.commit()

        return {
            "success": True,
            "job_id": test_job_id,
            "user_id": user.id,
            "scheduled_for": run_at.strftime("%H:%M"),
            "message": f"Test job scheduled for {run_at.strftime('%H:%M')} UTC",
            "note": f"A reminder will be sent to user {user.telegram_id or user.id} at the scheduled time.",
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to create test cron job: %s", e)
        raise HTTPException(status_code=500, detail=f"Failed to create test job: {e}")


@app.post("/api/scheduler/test-interval")
async def test_interval_job():
    """Create a test interval job to verify heartbeat-style scheduling works.

    Creates a scheduled task that will be picked up by the assistant's scheduler.
    """
    from datetime import datetime, timezone, timedelta
    import uuid

    test_job_id = f"test_interval_{uuid.uuid4().hex[:8]}"

    try:
        async with async_session() as session:
            from sqlalchemy import select
            from src.db.models import User

            # Get owner user
            user_result = await session.execute(select(User).limit(1))
            user = user_result.scalar_one_or_none()

            if not user:
                raise HTTPException(status_code=400, detail="No users found to send test message to")

            now = datetime.now(timezone.utc)

            # Create the task record
            task = ScheduledTask(
                user_id=user.id,
                apscheduler_id=test_job_id,
                description="🧪 Test interval job (10s heartbeat)",
                trigger_type="interval",
                trigger_config={"interval": {"seconds": 10}},
                job_function="src.scheduler.jobs:send_reminder",
                is_active=True,
                next_run_at=now + timedelta(seconds=10),
            )
            session.add(task)
            await session.commit()

        return {
            "success": True,
            "job_id": test_job_id,
            "user_id": user.id,
            "interval_seconds": 10,
            "message": "Test interval job created (10 second intervals)",
            "note": "The assistant scheduler will execute this. You should receive messages every 10 seconds. Delete via Schedules panel to stop.",
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to create test interval job: %s", e)
        raise HTTPException(status_code=500, detail=f"Failed to create test job: {e}")


def _generate_skill_id(name: str) -> str:
    """Generate a URL-friendly skill ID from name."""
    import re
    skill_id = name.lower()
    skill_id = re.sub(r'[^\w\s-]', '', skill_id)
    skill_id = re.sub(r'\s+', '-', skill_id)
    return skill_id[:50]


# ── Dashboard Layout Persistence ──────────────────────────────────────


class DashboardLayoutSave(BaseModel):
    layouts: dict = Field(..., description="react-grid-layout layouts object keyed by breakpoint")


@app.get("/api/dashboard/layout")
async def get_dashboard_layout(
    x_telegram_id: Optional[int] = Header(default=None, alias="X-Telegram-Id"),
):
    """Return saved Overview grid layout from Redis (or empty → use defaults)."""
    key = f"dashboard_layout:{x_telegram_id or 'default'}"
    try:
        r = aioredis.from_url(_settings.redis_url, decode_responses=True)
        raw = await r.get(key)
        await r.close()
        if raw:
            import json
            return {"layouts": json.loads(raw)}
    except Exception as e:
        logger.debug("Layout fetch failed: %s", e)
    return {"layouts": None}


@app.put("/api/dashboard/layout")
async def save_dashboard_layout(
    body: DashboardLayoutSave,
    x_telegram_id: Optional[int] = Header(default=None, alias="X-Telegram-Id"),
):
    """Persist Overview grid layout to Redis."""
    import json
    key = f"dashboard_layout:{x_telegram_id or 'default'}"
    try:
        r = aioredis.from_url(_settings.redis_url, decode_responses=True)
        await r.set(key, json.dumps(body.layouts), ex=60 * 60 * 24 * 365)  # 1 year TTL
        await r.close()
    except Exception as e:
        logger.warning("Layout save failed: %s", e)
        raise HTTPException(status_code=500, detail="Failed to save layout")
    return {"message": "Layout saved"}


# ── WebSocket ─────────────────────────────────────────────────────────


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await ws_manager.connect(websocket)
    try:
        while True:
            data = await websocket.receive_text()
            await websocket.send_text(f"Echo: {data}")
    except WebSocketDisconnect:
        ws_manager.disconnect(websocket)
