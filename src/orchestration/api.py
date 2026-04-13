"""Atlas Dashboard API — observability + organization management.

Phase A: Observability endpoints (read from bot's real DB tables)
Phase B: Organization CRUD (project containers with agent teams)
"""

import logging
from datetime import date, datetime, timedelta, timezone
from typing import List, Optional
import os
import importlib

import redis.asyncio as aioredis
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi import Header
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings
from sqlalchemy import select, func, desc, text
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
)
from src.orchestration.agent_registry import (
    Organization,
    OrgAgent,
    OrgTask,
    OrgActivity,
)

logger = logging.getLogger(__name__)


# ── Settings ──────────────────────────────────────────────────────────

class DashboardSettings(BaseSettings):
    database_url: str
    redis_url: str = "redis://redis:6379/0"
    cors_allowed_origins: str = "http://localhost:3001,http://127.0.0.1:3001"

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

app = FastAPI(
    title="Atlas Dashboard API",
    description="Observability & organization management for PersonalAsst",
    version="2.0.0",
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
    allow_methods=["*"],
    allow_headers=["*"],
)


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
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    agent_count: int = 0
    task_count: int = 0
    completed_tasks: int = 0


class OrgProjectSetupRequest(BaseModel):
    goal: str = Field(..., min_length=5, max_length=2000, description="Plain-English project goal")
    org_name: Optional[str] = Field(default=None, max_length=200)
    org_id: Optional[int] = Field(default=None, description="Reuse an existing org instead of creating one")


class OrgProjectSetupResponse(BaseModel):
    org_id: int
    org_name: str
    created_org: bool
    agents: list[dict]
    tasks: list[dict]
    summary: str


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


# ══════════════════════════════════════════════════════════════════════
#  PHASE A — OBSERVABILITY ENDPOINTS (read from bot tables)
# ══════════════════════════════════════════════════════════════════════


@app.get("/")
async def root():
    return {"name": "Atlas Dashboard API", "version": "2.0.0"}


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
    """Run a scheduled task immediately (for testing).

    This executes the job's function right now, outside of APScheduler,
    without affecting the scheduled next_run_at time.
    """
    async with async_session() as session:
        task = await session.get(ScheduledTask, schedule_id)
        if not task:
            raise HTTPException(status_code=404, detail="Schedule not found")

    # Run the job directly (not through APScheduler) using a safe whitelist
    try:
        job_path = (task.job_function or "").strip()
        if not job_path or ":" not in job_path:
            raise HTTPException(status_code=400, detail="Job function path is missing or invalid")

        module_path, func_name = job_path.split(":", 1)
        if module_path != "src.scheduler.jobs":
            raise HTTPException(status_code=400, detail="Job function module not allowed for Run now")

        allowed_funcs = {
            "send_reminder",
            "run_agent_task",
            "summarize_new_emails",
            "morning_brief",
        }
        if func_name not in allowed_funcs:
            raise HTTPException(status_code=400, detail="Job function not allowed for Run now")

        module = importlib.import_module(module_path)
        target = getattr(module, func_name, None)
        if not callable(target):
            raise HTTPException(status_code=400, detail="Resolved job function is not callable")

        # Use the same safety wrapper as the scheduler
        safe_wrapper = getattr(module, "safe_job_wrapper", None)
        job_kwargs = task.job_args or {}

        if callable(safe_wrapper):
            await safe_wrapper(target, **job_kwargs)
        else:
            # Fallback: call target directly
            await target(**job_kwargs)

        return {
            "message": "Test executed successfully",
            "schedule_id": schedule_id,
            "description": task.description,
            "executed_at": datetime.now(timezone.utc).isoformat(),
        }
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
async def get_activity(limit: int = 50):
    """Recent activity from the audit log."""
    async with async_session() as session:
        result = await session.execute(
            select(AuditLog)
            .order_by(desc(AuditLog.timestamp))
            .limit(min(limit, 200))
        )
        rows = result.scalars().all()
    return [
        ActivityItem(
            id=a.id, timestamp=a.timestamp, direction=a.direction,
            platform=a.platform, agent_name=a.agent_name,
            model_used=a.model_used, cost_usd=float(a.cost_usd) if a.cost_usd else None,
            duration_ms=a.duration_ms, error=a.error,
            message_preview=(a.message_text[:120] + "…") if a.message_text and len(a.message_text) > 120 else a.message_text,
        )
        for a in rows
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
async def get_budget():
    """Daily and monthly spend vs. caps from .env."""
    daily_cap = float(os.getenv("DAILY_COST_CAP_USD", "5.00"))
    monthly_cap = float(os.getenv("MONTHLY_COST_CAP_USD", "100.00"))

    today = date.today()
    first_of_month = today.replace(day=1)

    async with async_session() as session:
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


@app.get("/api/repairs", response_model=List[RepairTicketItem])
async def get_repairs(limit: int = 30):
    """Recent repair tickets with risk level and auto-apply status."""
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
            risk_level=r.risk_level,
            auto_applied=r.auto_applied,
            approval_required=r.approval_required,
            created_at=r.created_at,
            updated_at=r.updated_at,
        )
        for r in rows
    ]


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
        stmt = select(Organization).where(Organization.owner_user_id == requester.id)
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

    The LLM produces a plan (agents with skills/tools + tasks), then this
    endpoint creates the org (or reuses an existing one), all agents, and
    all tasks atomically.  Use this from the Dashboard to set up a full
    project team in one click.
    """
    import json
    from openai import AsyncOpenAI

    async with async_session() as session:
        requester = await _resolve_dashboard_user(session, x_telegram_id)
        owner_id = requester.id

        if body.org_id:
            org = await _get_owned_org_or_404(session, body.org_id, owner_id)
            created_org_flag = False
        else:
            org = None
            created_org_flag = True

    # ── LLM planning call ────────────────────────────────────────────
    client = AsyncOpenAI()
    planning_prompt = f"""You are a project planner for an AI personal assistant system called Atlas.
The user wants to achieve the following goal:

  GOAL: {body.goal}

Produce a JSON execution plan:
{{
  "org_name": "<short project name>",
  "org_goal": "<one-sentence mission>",
  "agents": [
    {{"name": "<name>", "role": "<slug>", "description": "<desc>",
      "instructions": "<instructions>",
      "skills": ["<skill_id>", ...],
      "allowed_tools": ["<tool_name>", ...]}}
  ],
  "tasks": [
    {{"title": "<title>", "description": "<desc>",
      "priority": "high|medium|low",
      "agent_name": "<agent name from agents list above>"}}
  ]
}}

Rules: 2-5 agents, 4-10 tasks, each task must reference an agent_name that exists.
Skills: code_audit, scheduler_diagnostics, memory_review, tool_registry_check, api_health, log_analysis, self_improvement.
Tools: get_my_recent_context, summarize_my_conversation, list_tools, run_code_audit, check_scheduler_health, list_schedules, get_org_status.
Respond with raw JSON only."""

    try:
        resp = await client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": planning_prompt}],
            temperature=0.2,
            response_format={"type": "json_object"},
        )
        plan = json.loads(resp.choices[0].message.content or "{}")
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Planning LLM call failed: {exc}")

    planned_agents: list[dict] = plan.get("agents") or []
    planned_tasks: list[dict] = plan.get("tasks") or []
    if not planned_agents:
        raise HTTPException(status_code=422, detail="LLM returned an empty plan — try a more detailed goal")

    resolved_name = body.org_name or plan.get("org_name") or "New Project"
    resolved_goal = plan.get("org_goal") or body.goal

    # ── Atomic DB creation ────────────────────────────────────────────
    async with async_session() as session:
        if org is None:
            org = Organization(
                name=resolved_name, goal=resolved_goal,
                owner_user_id=owner_id, status="active",
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
            db_agent = OrgAgent(
                org_id=org.id,
                name=ap.get("name", "Agent").strip(),
                role=ap.get("role", "specialist").strip(),
                description=ap.get("description"),
                instructions=ap.get("instructions"),
                tools_config=tc if tc else None,
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
            db_task = OrgTask(
                org_id=org.id,
                agent_id=assigned.id if assigned else None,
                title=tp.get("title", "Task").strip(),
                description=tp.get("description"),
                priority=priority,
                status="in_progress" if assigned else "pending",
                source="dashboard",
                assigned_at=now if assigned else None,
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
            })

        await session.commit()
        final_org_id = org.id
        final_org_name = org.name

    # ── Validation pass: check skills + tools actually exist ──────────
    from pathlib import Path as _Path
    _plugin_dir = _Path("src/tools/plugins")

    try:
        from src.skills.registry import SkillRegistry as _SR
        from src.skills.internal import (
            build_memory_skill as _bm,
            build_organization_skill as _bo,
            build_scheduler_skill as _bs,
        )
        _vr = _SR()
        _vr.register(_bm(owner_id))
        _vr.register(_bo(owner_id))
        _vr.register(_bs(owner_id))
        _known_skills: set[str] = set(_vr._skills.keys())
    except Exception:
        _known_skills = set()

    try:
        async with async_session() as _vs2:
            from src.db.models import Tool as _T2
            _db_t = (await _vs2.execute(select(_T2).where(_T2.is_active == True))).scalars().all()  # noqa: E712
        _known_tools: set[str] = {t.name for t in _db_t}
    except Exception:
        _known_tools = set()
    if _plugin_dir.exists():
        _known_tools |= {p.name for p in _plugin_dir.iterdir() if p.is_dir()}

    val_warnings: list[str] = []
    async with async_session() as vsession:
        for a in agent_map.values():
            vtc = dict(a.tools_config or {})
            val: dict = {"skills": {}, "tools": {}}
            for sk in vtc.get("skills", []):
                ok = sk in _known_skills
                val["skills"][sk] = "✅ found" if ok else "⚠️ not registered"
                if not ok:
                    val_warnings.append(f"Agent '{a.name}': skill '{sk}' not found")
            for tn in vtc.get("allowed_tools", []):
                ok = tn in _known_tools
                val["tools"][tn] = "✅ found" if ok else "⚠️ not installed"
                if not ok:
                    val_warnings.append(f"Agent '{a.name}': tool '{tn}' not installed")
            vtc["validation"] = val
            a.tools_config = vtc
            vsession.add(a)
        await vsession.commit()

    agents_meta = [
        {
            "id": a.id, "name": a.name, "role": a.role,
            "skills": (a.tools_config or {}).get("skills", []),
            "allowed_tools": (a.tools_config or {}).get("allowed_tools", []),
            "validation": (a.tools_config or {}).get("validation", {}),
        }
        for a in agent_map.values()
    ]

    val_suffix = (
        " ⚠️ Validation issues: " + "; ".join(val_warnings)
        if val_warnings
        else " ✅ All skills and tools validated."
    )
    summary_lines = [
        f"Project '{final_org_name}' set up with {len(agents_meta)} agents and {len(created_tasks_meta)} tasks.{val_suffix}"
    ]
    return OrgProjectSetupResponse(
        org_id=final_org_id,
        org_name=final_org_name,
        created_org=created_org_flag,
        agents=agents_meta,
        tasks=created_tasks_meta,
        summary=" ".join(summary_lines),
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


@app.delete("/api/orgs/{org_id}")
async def delete_org(
    org_id: int,
    x_telegram_id: Optional[int] = Header(default=None, alias="X-Telegram-Id"),
):
    """Delete an organization and its dependent agents/tasks/activity rows."""
    async with async_session() as session:
        requester = await _resolve_dashboard_user(session, x_telegram_id)
        org = await _get_owned_org_or_404(session, org_id, requester.id)

        org_name = org.name
        session.add(AuditLog(
            user_id=requester.id,
            direction="outbound",
            platform="dashboard",
            message_text=f"Organization deleted: {org_name} ({org_id})",
            agent_name="org_api",
            tools_used={"action": "org_deleted", "org_id": org_id, "org_name": org_name},
        ))
        await session.delete(org)
        await session.commit()

    logger.info("Organization deleted: %s (%s)", org_name, org_id)
    return {"message": f"Organization '{org_name}' deleted"}


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

        agent_name = agent.name
        await _log_org_activity(
            session, org_id, "agent_deleted",
            f"Agent '{agent_name}' deleted", agent_id=agent.id,
        )
        await session.delete(agent)
        await session.commit()

    return {"message": f"Agent '{agent_name}' deleted"}


# ── Org Tasks ─────────────────────────────────────────────────────────


@app.get("/api/orgs/{org_id}/tasks", response_model=List[OrgTaskResponse])
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
        OrgTaskResponse(
            id=t.id, org_id=t.org_id, agent_id=t.agent_id,
            title=t.title, description=t.description,
            priority=t.priority, status=t.status, result=t.result,
            source=t.source, due_at=t.due_at, created_at=t.created_at,
            assigned_at=t.assigned_at, completed_at=t.completed_at,
        )
        for t in tasks
    ]


@app.post("/api/orgs/{org_id}/tasks", response_model=OrgTaskResponse, status_code=201)
async def create_org_task(
    org_id: int,
    body: OrgTaskCreate,
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
    return OrgTaskResponse(
        id=task.id, org_id=task.org_id, agent_id=task.agent_id,
        title=task.title, description=task.description,
        priority=task.priority, status=task.status, result=task.result,
        source=task.source, due_at=task.due_at, created_at=task.created_at,
        assigned_at=task.assigned_at, completed_at=task.completed_at,
    )


@app.patch("/api/orgs/{org_id}/tasks/{task_id}", response_model=OrgTaskResponse)
async def update_org_task(
    org_id: int,
    task_id: int,
    body: OrgTaskUpdate,
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

    return OrgTaskResponse(
        id=task.id, org_id=task.org_id, agent_id=task.agent_id,
        title=task.title, description=task.description,
        priority=task.priority, status=task.status, result=task.result,
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
        ticket = RepairTicket(
            user_id=requester.id,
            title=body.title.strip(),
            status="open",
            priority=body.priority,
            source=body.source,
            error_context=body.error_context,
            plan=body.plan,
        )
        session.add(ticket)
        await session.commit()
        await session.refresh(ticket)
        return RepairTicketResponse(**ticket.__dict__)


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

    skills_dir = Path("user_skills")
    if not skills_dir.exists():
        return []

    loader = SkillLoader()
    skills = loader.load_all_from_directory()

    return [skill.metadata_dict() for skill in skills]


@app.get("/api/skills/{skill_id}", response_model=SkillDetail)
async def get_skill(skill_id: str):
    """Get full skill details including Level 2-3 content."""
    from src.skills.loader import SkillLoader
    from pathlib import Path

    skill_path = Path(f"user_skills/{skill_id}")
    if not skill_path.exists():
        raise HTTPException(status_code=404, detail="Skill not found")

    try:
        loader = SkillLoader()
        skill = loader.load_from_path(skill_path)

        return SkillDetail(
            **skill.metadata_dict(),
            instructions=skill.get_full_instructions(),
            resources={k: str(v) for k, v in skill.resources.items()},
            scripts={k: str(v) for k, v in skill.scripts.items()},
            templates={k: str(v) for k, v in skill.templates.items()},
            requires_skills=skill.requires_skills,
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
    skill_dir = Path(f"user_skills/{skill_id}")

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

    skill_path = Path(f"user_skills/{skill_id}")
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

        return SkillDetail(
            **skill.metadata_dict(),
            instructions=skill.get_full_instructions(),
            resources={k: str(v) for k, v in skill.resources.items()},
            scripts={k: str(v) for k, v in skill.scripts.items()},
            templates={k: str(v) for k, v in skill.templates.items()},
            requires_skills=skill.requires_skills,
            extends_skill=skill.extends_skill,
        )

    except Exception as e:
        logger.error("Failed to update skill %s: %s", skill_id, e)
        raise HTTPException(status_code=500, detail=f"Failed to update skill: {e}")


@app.delete("/api/skills/{skill_id}")
async def delete_skill(skill_id: str):
    """Delete a user-created skill."""
    from pathlib import Path
    import shutil

    skill_path = Path(f"user_skills/{skill_id}")

    if not skill_path.exists():
        raise HTTPException(status_code=404, detail="Skill not found")

    try:
        shutil.rmtree(skill_path)
        return {"message": f"Skill '{skill_id}' deleted"}
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

    skill_path = Path(f"user_skills/{skill_id}")
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


# ── Scheduler Health & Diagnostics ─────────────────────────────────


@app.get("/api/scheduler/health")
async def scheduler_health():
    """Check if the scheduler (cron/heartbeat) is working properly.

    Note: This runs in the orchestration-api container, not the assistant container.
    We query the database directly to check scheduler status.
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
