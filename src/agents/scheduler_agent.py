"""Scheduler Agent — manages recurring tasks and reminders (as_tool per AD-3)."""
  
import logging
import uuid
  
from agents import Agent, function_tool
  
from src.agents.persona_mode import PersonaMode, build_persona_mode_addendum
from src.models.router import ModelRole, select_model
  
logger = logging.getLogger(__name__)

SCHEDULER_INSTRUCTIONS = """\
You are a scheduling specialist. You help the user create, manage, and cancel recurring tasks and reminders.

## Capabilities
- Create reminders at specific times or on recurring schedules
- Create one-shot tasks or to-do items that should trigger at a specific time
- Parse natural language time expressions into schedules
- List all active scheduled tasks
- Cancel or pause scheduled tasks
- Create morning briefs and email digest schedules

## Time Parsing Guide
Convert natural language to cron parameters:
- "every Monday at 9am" → day_of_week="mon", hour=9, minute=0
- "every weekday at 8am" → day_of_week="mon-fri", hour=8, minute=0
- "every day at 7am" → hour=7, minute=0
- "every 30 minutes" → interval, minutes=30
- "every 2 hours" → interval, hours=2
- "tomorrow at 3pm" → one-shot job

## Rules
- Always confirm the schedule details with the user before creating.
- Treat reminder, task, todo, and to-do requests as scheduler-managed items unless the user explicitly asks for a calendar event.
- Show the next run time after creating a schedule.
- When listing schedules, show ID, description, and next run time.
- Use clear, human-readable descriptions for all jobs.
"""


async def _create_reminder_impl(
    description: str,
    schedule_type: str,
    user_id: int,
    day_of_week: str = "",
    hour: int = 9,
    minute: int = 0,
    interval_minutes: int = 0,
    interval_hours: int = 0,
    run_at: str = "",
    message: str = "",
) -> str:
    """Core logic for creating a scheduled reminder (plain async, not a tool)."""
    from src.scheduler.engine import add_cron_job, add_interval_job, add_one_shot_job
    from src.db.session import async_session
    from src.db.models import ScheduledTask

    job_id = f"user_{user_id}_{uuid.uuid4().hex[:8]}"
    reminder_msg = message or description

    try:
        if schedule_type == "cron":
            cron_kwargs = {"hour": hour, "minute": minute}
            if day_of_week:
                cron_kwargs["day_of_week"] = day_of_week

            await add_cron_job(
                func_path="src.scheduler.jobs:send_reminder",
                job_id=job_id,
                cron_kwargs=cron_kwargs,
                kwargs={"user_id": user_id, "message": reminder_msg},
            )
            trigger_config = cron_kwargs

        elif schedule_type == "interval":
            await add_interval_job(
                func_path="src.scheduler.jobs:send_reminder",
                job_id=job_id,
                minutes=interval_minutes or None,
                hours=interval_hours or None,
                kwargs={"user_id": user_id, "message": reminder_msg},
            )
            trigger_config = {"minutes": interval_minutes, "hours": interval_hours}

        elif schedule_type == "once":
            if not run_at:
                return "Error: one-shot jobs need a 'run_at' datetime (ISO format)."
            await add_one_shot_job(
                func_path="src.scheduler.jobs:send_reminder",
                job_id=job_id,
                run_at=run_at,
                kwargs={"user_id": user_id, "message": reminder_msg},
            )
            trigger_config = {"run_at": run_at}
        else:
            return f"Unknown schedule_type: {schedule_type}. Use 'cron', 'interval', or 'once'."

        # Persist metadata in our DB
        async with async_session() as session:
            task = ScheduledTask(
                user_id=user_id,
                apscheduler_id=job_id,
                description=description,
                natural_lang=message,
                trigger_type=schedule_type,
                trigger_config=trigger_config,
                job_function="src.scheduler.jobs:send_reminder",
                job_args={"user_id": user_id, "message": reminder_msg},
                is_active=True,
            )
            session.add(task)
            await session.commit()

        return f"✅ Scheduled: {description}\nJob ID: `{job_id}`\nType: {schedule_type}"

    except Exception as e:
        logger.exception("Failed to create reminder: %s", e)
        return f"Failed to create schedule: {str(e)}"


@function_tool
async def create_reminder(
    description: str,
    schedule_type: str,
    user_id: int,
    day_of_week: str = "",
    hour: int = 9,
    minute: int = 0,
    interval_minutes: int = 0,
    interval_hours: int = 0,
    run_at: str = "",
    message: str = "",
) -> str:
    """Create a scheduled reminder or recurring task.

    Args:
        description: Human-readable description of the task
        schedule_type: "cron" for recurring, "interval" for periodic, "once" for one-shot
        user_id: Internal user ID
        day_of_week: Cron day(s) e.g. "mon", "mon-fri", "mon,wed,fri" (cron only)
        hour: Hour to run (0-23, cron only)
        minute: Minute to run (0-59, cron only)
        interval_minutes: Minutes between runs (interval only)
        interval_hours: Hours between runs (interval only)
        run_at: ISO datetime for one-shot jobs (once only)
        message: The reminder message to send
    """
    return await _create_reminder_impl(
        description=description, schedule_type=schedule_type, user_id=user_id,
        day_of_week=day_of_week, hour=hour, minute=minute,
        interval_minutes=interval_minutes, interval_hours=interval_hours,
        run_at=run_at, message=message,
    )


async def _create_morning_brief_impl(user_id: int, hour: int = 8, minute: int = 0) -> str:
    """Core logic for morning brief creation (plain async, not a tool)."""
    from src.scheduler.engine import add_cron_job
    from src.db.session import async_session
    from src.db.models import ScheduledTask

    job_id = f"morning_brief_{user_id}"

    try:
        await add_cron_job(
            func_path="src.scheduler.jobs:morning_brief",
            job_id=job_id,
            cron_kwargs={"hour": hour, "minute": minute},
            kwargs={"user_id": user_id},
        )

        async with async_session() as session:
            task = ScheduledTask(
                user_id=user_id,
                apscheduler_id=job_id,
                description=f"Daily morning brief at {hour:02d}:{minute:02d}",
                trigger_type="cron",
                trigger_config={"hour": hour, "minute": minute},
                job_function="src.scheduler.jobs:morning_brief",
                job_args={"user_id": user_id},
                is_active=True,
            )
            session.add(task)
            await session.commit()

        return f"✅ Morning brief scheduled daily at {hour:02d}:{minute:02d}"
    except Exception as e:
        return f"Failed: {str(e)}"


@function_tool
async def create_morning_brief(user_id: int, hour: int = 8, minute: int = 0) -> str:
    """Set up a daily morning brief (calendar + email summary)."""
    return await _create_morning_brief_impl(user_id=user_id, hour=hour, minute=minute)


async def _list_schedules_impl(user_id: int) -> str:
    """Core logic for listing schedules (plain async, not a tool)."""
    from sqlalchemy import select
    from src.db.session import async_session
    from src.db.models import ScheduledTask

    async with async_session() as session:
        result = await session.execute(
            select(ScheduledTask).where(
                ScheduledTask.user_id == user_id,
                ScheduledTask.is_active == True,  # noqa: E712
            )
        )
        tasks = result.scalars().all()

    if not tasks:
        return "No active scheduled tasks."

    lines = [f"**Active schedules ({len(tasks)}):**\n"]
    for t in tasks:
        lines.append(
            f"• **{t.description}**\n"
            f"  ID: `{t.apscheduler_id}` | Type: {t.trigger_type}"
        )
    return "\n".join(lines)


@function_tool
async def list_schedules(user_id: int) -> str:
    """List all active scheduled tasks for the user."""
    return await _list_schedules_impl(user_id=user_id)


async def _cancel_schedule_impl(job_id: str, user_id: int) -> str:
    """Core logic for cancelling a schedule (plain async, not a tool)."""
    from src.scheduler.engine import remove_job
    from sqlalchemy import update
    from src.db.session import async_session
    from src.db.models import ScheduledTask

    success = await remove_job(job_id)

    async with async_session() as session:
        await session.execute(
            update(ScheduledTask)
            .where(
                ScheduledTask.apscheduler_id == job_id,
                ScheduledTask.user_id == user_id,
            )
            .values(is_active=False)
        )
        await session.commit()

    if success:
        return f"✅ Schedule `{job_id}` cancelled."
    return f"Schedule `{job_id}` removed from DB (may not have been active in scheduler)."


@function_tool
async def cancel_schedule(job_id: str, user_id: int) -> str:
    """Cancel a scheduled task by its job ID."""
    return await _cancel_schedule_impl(job_id=job_id, user_id=user_id)


def _build_bound_scheduler_tools(bound_user_id: int) -> list:
    @function_tool(name_override="create_my_reminder")
    async def create_my_reminder(
        description: str,
        schedule_type: str,
        day_of_week: str = "",
        hour: int = 9,
        minute: int = 0,
        interval_minutes: int = 0,
        interval_hours: int = 0,
        run_at: str = "",
        message: str = "",
    ) -> str:
        """Create a scheduled reminder or recurring task.

        Args:
            description: Human-readable description of the task
            schedule_type: "cron" for recurring, "interval" for periodic, "once" for one-shot
            day_of_week: Cron day(s) e.g. "mon", "mon-fri", "mon,wed,fri" (cron only)
            hour: Hour to run (0-23, cron only)
            minute: Minute to run (0-59, cron only)
            interval_minutes: Minutes between runs (interval only)
            interval_hours: Hours between runs (interval only)
            run_at: ISO datetime for one-shot jobs (once only)
            message: The reminder message to send
        """
        return await _create_reminder_impl(
            description=description,
            schedule_type=schedule_type,
            user_id=bound_user_id,
            day_of_week=day_of_week,
            hour=hour,
            minute=minute,
            interval_minutes=interval_minutes,
            interval_hours=interval_hours,
            run_at=run_at,
            message=message,
        )

    @function_tool(name_override="create_my_morning_brief")
    async def create_my_morning_brief(hour: int = 8, minute: int = 0) -> str:
        """Set up a daily morning brief (calendar + email summary)."""
        return await _create_morning_brief_impl(user_id=bound_user_id, hour=hour, minute=minute)

    @function_tool(name_override="list_my_schedules")
    async def list_my_schedules() -> str:
        """List all active scheduled tasks for the user."""
        return await _list_schedules_impl(user_id=bound_user_id)

    @function_tool(name_override="cancel_my_schedule")
    async def cancel_my_schedule(job_id: str) -> str:
        """Cancel a scheduled task by its job ID."""
        return await _cancel_schedule_impl(job_id=job_id, user_id=bound_user_id)

    return [create_my_reminder, create_my_morning_brief, list_my_schedules, cancel_my_schedule]


def create_scheduler_agent(mode: PersonaMode = "scheduler", bound_user_id: int | None = None) -> Agent:
    """Create the scheduler specialist agent."""
    instructions = f"{SCHEDULER_INSTRUCTIONS}\n\n{build_persona_mode_addendum(mode)}"
    tools = [create_reminder, create_morning_brief, list_schedules, cancel_schedule]
    if bound_user_id is not None:
        instructions = (
            f"{instructions}\n\n"
            "## Bound User Context\n"
            f"The current internal scheduler user id is `{bound_user_id}`. "
            "Prefer the bound tools `create_my_reminder`, `create_my_morning_brief`, `list_my_schedules`, and `cancel_my_schedule`. "
            "Do not ask the user for their user id and do not attempt to guess it."
        )
        tools = _build_bound_scheduler_tools(bound_user_id)
    selection = select_model(ModelRole.GENERAL)
    return Agent(
        name="SchedulerAgent",
        instructions=instructions,
        model=selection.model_id,
        tools=tools,
    )
