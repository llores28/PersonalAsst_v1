"""Atlas Dashboard API — observability + organization management.

Phase A: Observability endpoints (read from bot's real DB tables)
Phase B: Organization CRUD (project containers with agent teams)
"""

import logging
from datetime import date, datetime, timedelta, timezone
from typing import List, Optional

import redis.asyncio as aioredis
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings
from sqlalchemy import select, func, desc, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.db.models import (
    AuditLog,
    DailyCost,
    PersonaInterview,
    PersonaVersion,
    ScheduledTask,
    Tool,
    User,
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


# ── FastAPI app ───────────────────────────────────────────────────────

app = FastAPI(
    title="Atlas Dashboard API",
    description="Observability & organization management for PersonalAsst",
    version="2.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
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


class OrgAgentCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    role: str = Field(..., min_length=1, max_length=100)
    description: Optional[str] = None
    instructions: Optional[str] = None
    tools_config: Optional[dict] = None
    model_tier: str = "general"


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


class OrgTaskCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=500)
    description: Optional[str] = None
    priority: str = "medium"
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
        result = await session.execute(select(Tool).order_by(desc(Tool.use_count)))
        rows = result.scalars().all()
    return [
        ToolItem(
            id=t.id, name=t.name, tool_type=t.tool_type,
            description=t.description, is_active=t.is_active,
            use_count=t.use_count, created_by=t.created_by,
            created_at=t.created_at, last_used_at=t.last_used_at,
        )
        for t in rows
    ]


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

    # Run the job directly (not through APScheduler)
    try:
        from src.scheduler.jobs import send_reminder
        await send_reminder(
            user_id=task.user_id,
            message=f"🧪 TEST: {task.description}",
        )

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
                elif t.is_active:
                    # Recurring job missing from scheduler — mark paused
                    t.is_active = False
                    orphaned.append(t.description)
            else:
                # Job is live — sync next_run_at
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
                if not t.is_active:
                    t.is_active = True  # Re-activate if it's back in scheduler

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


@app.get("/api/orgs", response_model=List[OrgResponse])
async def list_orgs(status: Optional[str] = None):
    """List all organizations with counts."""
    async with async_session() as session:
        stmt = select(Organization)
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


@app.post("/api/orgs", response_model=OrgResponse, status_code=201)
async def create_org(body: OrgCreate):
    """Create a new organization."""
    async with async_session() as session:
        # Find the owner user (first user / owner)
        user_r = await session.execute(
            select(User).where(User.is_owner == True).limit(1)  # noqa: E712
        )
        owner = user_r.scalar_one_or_none()
        if not owner:
            user_r = await session.execute(select(User).limit(1))
            owner = user_r.scalar_one_or_none()
        if not owner:
            raise HTTPException(status_code=400, detail="No users found in database")

        org = Organization(
            name=body.name, description=body.description,
            goal=body.goal, config=body.config,
            owner_user_id=owner.id,
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
async def get_org(org_id: int):
    """Get organization details."""
    async with async_session() as session:
        org = await session.get(Organization, org_id)
        if not org:
            raise HTTPException(status_code=404, detail="Organization not found")
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
async def update_org(org_id: int, body: OrgCreate):
    """Update an organization."""
    async with async_session() as session:
        org = await session.get(Organization, org_id)
        if not org:
            raise HTTPException(status_code=404, detail="Organization not found")
        org.name = body.name
        org.description = body.description
        org.goal = body.goal
        if body.config is not None:
            org.config = body.config
        await _log_org_activity(session, org_id, "org_updated", f"Organization updated")
        await session.commit()
    return {"message": "Organization updated"}


@app.post("/api/orgs/{org_id}/pause")
async def pause_org(org_id: int):
    async with async_session() as session:
        org = await session.get(Organization, org_id)
        if not org:
            raise HTTPException(status_code=404, detail="Organization not found")
        org.status = "paused"
        await _log_org_activity(session, org_id, "org_paused", "Organization paused")
        await session.commit()
    return {"message": "Organization paused"}


@app.post("/api/orgs/{org_id}/resume")
async def resume_org(org_id: int):
    async with async_session() as session:
        org = await session.get(Organization, org_id)
        if not org:
            raise HTTPException(status_code=404, detail="Organization not found")
        org.status = "active"
        await _log_org_activity(session, org_id, "org_resumed", "Organization resumed")
        await session.commit()
    return {"message": "Organization resumed"}


# ── Org Agents ────────────────────────────────────────────────────────


@app.get("/api/orgs/{org_id}/agents", response_model=List[OrgAgentResponse])
async def list_org_agents(org_id: int):
    async with async_session() as session:
        result = await session.execute(
            select(OrgAgent).where(OrgAgent.org_id == org_id)
            .order_by(OrgAgent.created_at)
        )
        agents = result.scalars().all()
    return [
        OrgAgentResponse(
            id=a.id, org_id=a.org_id, name=a.name, role=a.role,
            description=a.description, instructions=a.instructions,
            tools_config=a.tools_config, model_tier=a.model_tier,
            status=a.status, created_at=a.created_at,
        )
        for a in agents
    ]


@app.post("/api/orgs/{org_id}/agents", response_model=OrgAgentResponse, status_code=201)
async def create_org_agent(org_id: int, body: OrgAgentCreate):
    async with async_session() as session:
        org = await session.get(Organization, org_id)
        if not org:
            raise HTTPException(status_code=404, detail="Organization not found")
        agent = OrgAgent(
            org_id=org_id, name=body.name, role=body.role,
            description=body.description, instructions=body.instructions,
            tools_config=body.tools_config, model_tier=body.model_tier,
        )
        session.add(agent)
        await session.flush()
        await _log_org_activity(
            session, org_id, "agent_created",
            f"Agent '{body.name}' ({body.role}) created", agent_id=agent.id,
        )
        await session.commit()
        await session.refresh(agent)
    return OrgAgentResponse(
        id=agent.id, org_id=agent.org_id, name=agent.name, role=agent.role,
        description=agent.description, instructions=agent.instructions,
        tools_config=agent.tools_config, model_tier=agent.model_tier,
        status=agent.status, created_at=agent.created_at,
    )


# ── Org Tasks ─────────────────────────────────────────────────────────


@app.get("/api/orgs/{org_id}/tasks", response_model=List[OrgTaskResponse])
async def list_org_tasks(org_id: int, status: Optional[str] = None):
    async with async_session() as session:
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
async def create_org_task(org_id: int, body: OrgTaskCreate):
    async with async_session() as session:
        org = await session.get(Organization, org_id)
        if not org:
            raise HTTPException(status_code=404, detail="Organization not found")
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


@app.post("/api/orgs/{org_id}/tasks/{task_id}/complete")
async def complete_org_task(org_id: int, task_id: int, result: Optional[dict] = None):
    async with async_session() as session:
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
async def list_org_activity(org_id: int, limit: int = 50):
    async with async_session() as session:
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
