"""Organization management tools for Telegram ↔ Atlas Dashboard bridge.

Phase C: Lets the user manage organizations, agents, tasks, cron jobs, and
CLI/MCP tool creation via natural language through Telegram.  Tools talk
directly to PostgreSQL via the shared async session.

Design principles (research-backed):
- Single DB session per tool call (no nested sessions)
- SDK ``failure_error_function`` for graceful LLM-visible errors
- SDK ``timeout=`` on every async tool (15 s read, 30 s write)
- Eager-load related rows inside the same session instead of re-opening
- Activity log every mutation so the dashboard stays in sync
"""

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from agents import RunContextWrapper, function_tool
from sqlalchemy import select, update, func as sa_func
from sqlalchemy.orm import selectinload

from src.db.session import async_session
from src.orchestration.agent_registry import (
    Organization,
    OrgAgent,
    OrgTask,
    OrgActivity,
)

logger = logging.getLogger(__name__)

_PRIORITY_ICONS = {"high": "🔴", "medium": "🟡", "low": "🟢"}
_VALID_PRIORITIES = frozenset({"high", "medium", "low"})
_VALID_TASK_STATUSES = frozenset({"pending", "in_progress", "completed"})


# ── Error handler (SDK best practice) ────────────────────────────────────

def _org_tool_error(ctx: RunContextWrapper[Any], error: Exception) -> str:
    """Return a user-friendly error instead of crashing the agent run."""
    logger.exception("Organization tool error: %s", error)
    return (
        f"Organization tool encountered an error: {type(error).__name__}: {error}. "
        "Please try again or rephrase your request."
    )


# ── Helpers ──────────────────────────────────────────────────────────────

async def _log_activity(
    session,
    org_id: int,
    action: str,
    details: str,
    *,
    agent_id: int | None = None,
    task_id: int | None = None,
    source: str = "telegram",
) -> None:
    """Record an activity entry for an organization (within caller's session)."""
    session.add(OrgActivity(
        org_id=org_id,
        agent_id=agent_id,
        task_id=task_id,
        action=action,
        details=details,
        source=source,
    ))


async def _get_owned_org(session, org_id: int, owner_id: int) -> Organization | None:
    """Fetch an org only if the user owns it."""
    org = await session.get(Organization, org_id)
    if org is None or org.owner_user_id != owner_id:
        return None
    return org


def _agent_display(agents_by_id: dict[int, OrgAgent], agent_id: int | None) -> str:
    if agent_id and agent_id in agents_by_id:
        return f" → {agents_by_id[agent_id].name}"
    return ""


# ── Bound tool builders ──────────────────────────────────────────────────

def _build_bound_org_tools(user_id: int) -> list:
    """Build organization management tools bound to a specific user."""

    # ── CRUD: Organizations ──────────────────────────────────────────

    @function_tool(
        name_override="list_organizations",
        failure_error_function=_org_tool_error,
        timeout=15,
    )
    async def list_organizations() -> str:
        """List all organizations you own, with agent and task counts."""
        async with async_session() as session:
            orgs = (
                await session.execute(
                    select(Organization)
                    .where(Organization.owner_user_id == user_id)
                    .where(Organization.status == "active")
                    .order_by(Organization.created_at.desc())
                )
            ).scalars().all()

            if not orgs:
                return "You don't have any organizations yet. Say 'create an organization' to get started."

            org_ids = [o.id for o in orgs]

            agent_counts = dict(
                (await session.execute(
                    select(OrgAgent.org_id, sa_func.count(OrgAgent.id))
                    .where(OrgAgent.org_id.in_(org_ids))
                    .group_by(OrgAgent.org_id)
                )).all()
            )
            task_counts = dict(
                (await session.execute(
                    select(OrgTask.org_id, sa_func.count(OrgTask.id))
                    .where(OrgTask.org_id.in_(org_ids))
                    .group_by(OrgTask.org_id)
                )).all()
            )
            active_counts = dict(
                (await session.execute(
                    select(OrgTask.org_id, sa_func.count(OrgTask.id))
                    .where(OrgTask.org_id.in_(org_ids))
                    .where(OrgTask.status.in_(["pending", "in_progress"]))
                    .group_by(OrgTask.org_id)
                )).all()
            )

        lines = [f"You have {len(orgs)} organization(s):\n"]
        for org in orgs:
            ac = agent_counts.get(org.id, 0)
            tc = task_counts.get(org.id, 0)
            atc = active_counts.get(org.id, 0)
            lines.append(
                f"• **{org.name}** (ID: {org.id})\n"
                f"  Goal: {org.goal or '(none set)'}\n"
                f"  Agents: {ac}, Tasks: {tc} ({atc} active)"
            )
        return "\n".join(lines)

    @function_tool(
        name_override="create_organization",
        failure_error_function=_org_tool_error,
        timeout=15,
    )
    async def create_organization(
        name: str,
        goal: str,
        description: Optional[str] = None,
    ) -> str:
        """Create a new organization (project container for agent teams).

        Args:
            name: Organization name (e.g., 'Job Search Team', 'Content Marketing')
            goal: Primary objective of this organization
            description: Optional longer description
        """
        if not name or not name.strip():
            return "Organization name cannot be empty."
        if not goal or not goal.strip():
            return "Organization goal cannot be empty."

        async with async_session() as session:
            org = Organization(
                name=name.strip(),
                description=description,
                goal=goal.strip(),
                owner_user_id=user_id,
                status="active",
            )
            session.add(org)
            await session.flush()
            await _log_activity(
                session, org.id, "org_created",
                f"Organization '{name}' created via Telegram",
            )
            await session.commit()
            return (
                f"Created organization **{name}** (ID: {org.id}).\n"
                f"Goal: {goal}\n\n"
                "You can now add agents and tasks to it."
            )

    @function_tool(
        name_override="update_organization",
        failure_error_function=_org_tool_error,
        timeout=15,
    )
    async def update_organization(
        org_id: int,
        name: Optional[str] = None,
        goal: Optional[str] = None,
        description: Optional[str] = None,
        status: Optional[str] = None,
    ) -> str:
        """Update an existing organization's name, goal, description, or status.

        Args:
            org_id: Organization ID to update
            name: New name (or None to keep current)
            goal: New goal (or None to keep current)
            description: New description (or None to keep current)
            status: New status: 'active', 'paused', or 'archived' (or None to keep current)
        """
        if status and status not in ("active", "paused", "archived"):
            return "Status must be 'active', 'paused', or 'archived'."

        async with async_session() as session:
            org = await _get_owned_org(session, org_id, user_id)
            if not org:
                return f"Organization {org_id} not found or you don't own it."

            changes = []
            if name and name.strip():
                org.name = name.strip()
                changes.append(f"name → '{name.strip()}'")
            if goal and goal.strip():
                org.goal = goal.strip()
                changes.append(f"goal → '{goal.strip()}'")
            if description is not None:
                org.description = description
                changes.append("description updated")
            if status:
                org.status = status
                changes.append(f"status → {status}")

            if not changes:
                return "Nothing to update — provide at least one field to change."

            await _log_activity(
                session, org.id, "org_updated",
                f"Organization updated: {', '.join(changes)}",
            )
            await session.commit()
            return f"Updated **{org.name}**: {', '.join(changes)}."

    @function_tool(
        name_override="get_organization_status",
        failure_error_function=_org_tool_error,
        timeout=15,
    )
    async def get_organization_status(org_id: int) -> str:
        """Get detailed status of an organization including its agents and tasks.

        Args:
            org_id: The organization ID to inspect
        """
        async with async_session() as session:
            org = await _get_owned_org(session, org_id, user_id)
            if not org:
                return f"Organization {org_id} not found or you don't own it."

            agents = (
                await session.execute(
                    select(OrgAgent)
                    .where(OrgAgent.org_id == org_id)
                    .order_by(OrgAgent.created_at)
                )
            ).scalars().all()

            tasks = (
                await session.execute(
                    select(OrgTask)
                    .where(OrgTask.org_id == org_id)
                    .order_by(OrgTask.created_at.desc())
                )
            ).scalars().all()

            agents_by_id = {a.id: a for a in agents}

        lines = [
            f"**{org.name}** (ID: {org.id})",
            f"Goal: {org.goal or '(none)'}",
            f"Status: {org.status}",
            f"Created: {org.created_at.strftime('%Y-%m-%d') if org.created_at else 'N/A'}",
            "",
        ]

        if agents:
            lines.append(f"**Agents ({len(agents)}):**")
            for a in agents:
                lines.append(f"  • {a.name} — {a.role} ({a.status})")
        else:
            lines.append("**Agents:** None yet")

        lines.append("")

        if tasks:
            active = [t for t in tasks if t.status in ("pending", "in_progress")]
            completed = [t for t in tasks if t.status == "completed"]
            lines.append(f"**Tasks ({len(tasks)} total, {len(active)} active, {len(completed)} done):**")
            for t in tasks[:10]:
                icon = _PRIORITY_ICONS.get(t.priority, "⚪")
                lines.append(
                    f"  {icon} [{t.status}] {t.title}{_agent_display(agents_by_id, t.agent_id)}"
                )
            if len(tasks) > 10:
                lines.append(f"  ... and {len(tasks) - 10} more")
        else:
            lines.append("**Tasks:** None yet")

        return "\n".join(lines)

    # ── CRUD: Agents ─────────────────────────────────────────────────

    @function_tool(
        name_override="add_org_agent",
        failure_error_function=_org_tool_error,
        timeout=15,
    )
    async def add_org_agent(
        org_id: int,
        name: str,
        role: str,
        description: Optional[str] = None,
        instructions: Optional[str] = None,
    ) -> str:
        """Add a specialized agent to an organization.

        Args:
            org_id: Organization ID to add the agent to
            name: Agent name (e.g., 'Resume Analyzer', 'Content Writer')
            role: Agent role (e.g., 'researcher', 'writer', 'analyst')
            description: What this agent does
            instructions: Specific instructions for this agent's behavior
        """
        async with async_session() as session:
            org = await _get_owned_org(session, org_id, user_id)
            if not org:
                return f"Organization {org_id} not found or you don't own it."

            agent = OrgAgent(
                org_id=org_id,
                name=name.strip(),
                role=role.strip(),
                description=description,
                instructions=instructions,
                status="active",
            )
            session.add(agent)
            await session.flush()
            await _log_activity(
                session, org_id, "agent_created",
                f"Agent '{name}' ({role}) added via Telegram",
                agent_id=agent.id,
            )
            await session.commit()
            return (
                f"Added agent **{name}** (role: {role}) to **{org.name}**.\n"
                f"Agent ID: {agent.id}"
            )

    # ── CRUD: Tasks ──────────────────────────────────────────────────

    @function_tool(
        name_override="add_org_task",
        failure_error_function=_org_tool_error,
        timeout=15,
    )
    async def add_org_task(
        org_id: int,
        title: str,
        description: Optional[str] = None,
        priority: str = "medium",
        agent_id: Optional[int] = None,
    ) -> str:
        """Add a task to an organization, optionally assigning it to an agent.

        Args:
            org_id: Organization ID
            title: Task title
            description: Task details
            priority: 'high', 'medium', or 'low'
            agent_id: Optional agent ID to assign the task to
        """
        if priority not in _VALID_PRIORITIES:
            priority = "medium"

        async with async_session() as session:
            org = await _get_owned_org(session, org_id, user_id)
            if not org:
                return f"Organization {org_id} not found or you don't own it."

            agent_name = None
            if agent_id:
                agent = await session.get(OrgAgent, agent_id)
                if not agent or agent.org_id != org_id:
                    return f"Agent {agent_id} not found in organization {org_id}."
                agent_name = agent.name

            task = OrgTask(
                org_id=org_id,
                agent_id=agent_id,
                title=title.strip(),
                description=description,
                priority=priority,
                status="in_progress" if agent_id else "pending",
                source="telegram",
                assigned_at=datetime.now(timezone.utc) if agent_id else None,
            )
            session.add(task)
            await session.flush()
            await _log_activity(
                session, org_id, "task_created",
                f"Task '{title}' created via Telegram",
                agent_id=agent_id,
                task_id=task.id,
            )
            await session.commit()

            status_note = ""
            if agent_name:
                status_note = f" Assigned to **{agent_name}** (in progress)."
            return (
                f"Created task **{title}** in **{org.name}** "
                f"(priority: {priority}).{status_note}\n"
                f"Task ID: {task.id}"
            )

    @function_tool(
        name_override="assign_org_task",
        failure_error_function=_org_tool_error,
        timeout=15,
    )
    async def assign_org_task(
        org_id: int,
        task_id: int,
        agent_id: int,
    ) -> str:
        """Assign or reassign an organization task to an agent.

        Args:
            org_id: Organization ID
            task_id: Task ID to assign
            agent_id: Agent ID to assign the task to
        """
        async with async_session() as session:
            org = await _get_owned_org(session, org_id, user_id)
            if not org:
                return f"Organization {org_id} not found or you don't own it."

            task = await session.get(OrgTask, task_id)
            if not task or task.org_id != org_id:
                return f"Task {task_id} not found in organization {org_id}."

            agent = await session.get(OrgAgent, agent_id)
            if not agent or agent.org_id != org_id:
                return f"Agent {agent_id} not found in organization {org_id}."

            old_agent_id = task.agent_id
            task.agent_id = agent_id
            task.assigned_at = datetime.now(timezone.utc)
            if task.status == "pending":
                task.status = "in_progress"

            await _log_activity(
                session, org_id, "task_assigned",
                f"Task '{task.title}' assigned to '{agent.name}'",
                agent_id=agent_id,
                task_id=task_id,
            )
            await session.commit()
            return f"Assigned task **{task.title}** to **{agent.name}**."

    @function_tool(
        name_override="complete_org_task",
        failure_error_function=_org_tool_error,
        timeout=15,
    )
    async def complete_org_task(org_id: int, task_id: int) -> str:
        """Mark a task as completed in an organization.

        Args:
            org_id: Organization ID
            task_id: Task ID to complete
        """
        async with async_session() as session:
            org = await _get_owned_org(session, org_id, user_id)
            if not org:
                return f"Organization {org_id} not found or you don't own it."

            task = await session.get(OrgTask, task_id)
            if not task or task.org_id != org_id:
                return f"Task {task_id} not found in organization {org_id}."

            if task.status == "completed":
                return f"Task '{task.title}' is already completed."

            task.status = "completed"
            task.completed_at = datetime.now(timezone.utc)
            await _log_activity(
                session, org_id, "task_completed",
                f"Task '{task.title}' marked completed via Telegram",
                agent_id=task.agent_id,
                task_id=task.id,
            )
            await session.commit()
            return f"Marked task **{task.title}** as completed."

    @function_tool(
        name_override="list_org_tasks",
        failure_error_function=_org_tool_error,
        timeout=15,
    )
    async def list_org_tasks(
        org_id: int,
        status_filter: Optional[str] = None,
    ) -> str:
        """List tasks in an organization, optionally filtered by status.

        Args:
            org_id: Organization ID
            status_filter: Optional filter: 'pending', 'in_progress', 'completed', or None for all
        """
        async with async_session() as session:
            org = await _get_owned_org(session, org_id, user_id)
            if not org:
                return f"Organization {org_id} not found or you don't own it."

            stmt = select(OrgTask).where(OrgTask.org_id == org_id)
            if status_filter and status_filter in _VALID_TASK_STATUSES:
                stmt = stmt.where(OrgTask.status == status_filter)
            stmt = stmt.order_by(OrgTask.created_at.desc())

            tasks = (await session.execute(stmt)).scalars().all()

            agents_by_id = {}
            agent_ids = {t.agent_id for t in tasks if t.agent_id}
            if agent_ids:
                agents = (
                    await session.execute(
                        select(OrgAgent).where(OrgAgent.id.in_(agent_ids))
                    )
                ).scalars().all()
                agents_by_id = {a.id: a for a in agents}

        if not tasks:
            filter_note = f" with status '{status_filter}'" if status_filter else ""
            return f"No tasks found in **{org.name}**{filter_note}."

        lines = [f"Tasks in **{org.name}**:\n"]
        for t in tasks:
            icon = _PRIORITY_ICONS.get(t.priority, "⚪")
            lines.append(
                f"{icon} **{t.title}** (ID: {t.id})\n"
                f"   Status: {t.status}{_agent_display(agents_by_id, t.agent_id)}"
            )
        return "\n".join(lines)

    # ── Scheduling: Org-scoped cron jobs ─────────────────────────────

    @function_tool(
        name_override="schedule_org_task",
        failure_error_function=_org_tool_error,
        timeout=30,
    )
    async def schedule_org_task(
        org_id: int,
        description: str,
        schedule_type: str,
        message: str,
        day_of_week: str = "",
        hour: int = 9,
        minute: int = 0,
        interval_minutes: int = 0,
        interval_hours: int = 0,
        run_at: str = "",
    ) -> str:
        """Create a scheduled job scoped to an organization.

        The job will send a Telegram reminder with the message and log
        activity to the organization's feed.

        Args:
            org_id: Organization ID to scope the schedule to
            description: Human-readable description of the scheduled task
            schedule_type: 'cron' for recurring, 'interval' for periodic, 'once' for one-shot
            message: The reminder message to send when the job fires
            day_of_week: Cron day(s) e.g. 'mon', 'mon-fri', 'mon,wed,fri' (cron only)
            hour: Hour to run (0-23, cron only)
            minute: Minute to run (0-59, cron only)
            interval_minutes: Minutes between runs (interval only)
            interval_hours: Hours between runs (interval only)
            run_at: ISO datetime for one-shot jobs (once only)
        """
        if schedule_type not in ("cron", "interval", "once"):
            return "schedule_type must be 'cron', 'interval', or 'once'."

        async with async_session() as session:
            org = await _get_owned_org(session, org_id, user_id)
            if not org:
                return f"Organization {org_id} not found or you don't own it."

        from src.agents.scheduler_agent import _create_reminder_impl

        result = await _create_reminder_impl(
            description=f"[{org.name}] {description}",
            schedule_type=schedule_type,
            user_id=user_id,
            day_of_week=day_of_week,
            hour=hour,
            minute=minute,
            interval_minutes=interval_minutes,
            interval_hours=interval_hours,
            run_at=run_at,
            message=f"[{org.name}] {message}",
        )

        if result.startswith("✅"):
            async with async_session() as session:
                await _log_activity(
                    session, org_id, "schedule_created",
                    f"Scheduled: {description} ({schedule_type})",
                )
                await session.commit()

        return result

    @function_tool(
        name_override="list_org_schedules",
        failure_error_function=_org_tool_error,
        timeout=15,
    )
    async def list_org_schedules(org_id: int) -> str:
        """List all scheduled jobs scoped to an organization.

        Args:
            org_id: Organization ID
        """
        async with async_session() as session:
            org = await _get_owned_org(session, org_id, user_id)
            if not org:
                return f"Organization {org_id} not found or you don't own it."

        from src.agents.scheduler_agent import _list_schedules_impl
        from sqlalchemy import select as sa_select
        from src.db.models import ScheduledTask

        async with async_session() as session:
            result = await session.execute(
                sa_select(ScheduledTask)
                .where(ScheduledTask.user_id == user_id)
                .where(ScheduledTask.is_active == True)  # noqa: E712
                .where(ScheduledTask.description.ilike(f"%[{org.name}]%"))
            )
            tasks = result.scalars().all()

        if not tasks:
            return f"No active schedules found for **{org.name}**."

        lines = [f"**Schedules for {org.name}:**\n"]
        for t in tasks:
            lines.append(
                f"• **{t.description}**\n"
                f"  ID: `{t.apscheduler_id}` | Type: {t.trigger_type}"
            )
        return "\n".join(lines)

    @function_tool(
        name_override="cancel_org_schedule",
        failure_error_function=_org_tool_error,
        timeout=15,
    )
    async def cancel_org_schedule(org_id: int, job_id: str) -> str:
        """Cancel a scheduled job that belongs to an organization.

        Args:
            org_id: Organization ID (for ownership verification)
            job_id: The APScheduler job ID to cancel (shown by list_org_schedules)
        """
        async with async_session() as session:
            org = await _get_owned_org(session, org_id, user_id)
            if not org:
                return f"Organization {org_id} not found or you don't own it."

        from src.agents.scheduler_agent import _cancel_schedule_impl

        result = await _cancel_schedule_impl(job_id=job_id, user_id=user_id)

        if "cancelled" in result.lower() or "removed" in result.lower():
            async with async_session() as session:
                await _log_activity(
                    session, org_id, "schedule_cancelled",
                    f"Schedule `{job_id}` cancelled via Telegram",
                )
                await session.commit()

        return result

    # ── Tool Creation: Org-scoped CLI tools ──────────────────────────

    @function_tool(
        name_override="create_org_tool",
        failure_error_function=_org_tool_error,
        timeout=30,
    )
    async def create_org_tool(
        org_id: int,
        name: str,
        description: str,
        parameters_json: str,
        tool_code: str,
        requires_network: bool = False,
        allowed_hosts: str = "",
    ) -> str:
        """Create a new CLI tool scoped to an organization.

        The tool is validated via static analysis, tested in the sandbox,
        and registered for immediate use. Activity is logged to the org feed.

        Args:
            name: Tool name in snake_case (e.g., 'price_checker')
            description: What the tool does
            parameters_json: JSON string of parameters: {"param": {"type": "str", "required": true, "description": "..."}}
            tool_code: Complete Python CLI script (must use argparse, print to stdout)
            requires_network: Whether the tool needs internet access
            allowed_hosts: Comma-separated allowed hostnames if network required
            org_id: Organization ID to scope the tool to
        """
        async with async_session() as session:
            org = await _get_owned_org(session, org_id, user_id)
            if not org:
                return f"Organization {org_id} not found or you don't own it."

        from src.agents.tool_factory_agent import generate_cli_tool

        result = await generate_cli_tool.on_invoke_tool(
            None,  # context
            json.dumps({
                "name": name,
                "description": f"[{org.name}] {description}",
                "parameters_json": parameters_json,
                "tool_code": tool_code,
                "requires_network": requires_network,
                "allowed_hosts": allowed_hosts,
            }),
        )

        if "created and registered" in result.lower() or "✅" in result:
            async with async_session() as session:
                await _log_activity(
                    session, org_id, "tool_created",
                    f"CLI tool '{name}' created via Telegram",
                )
                await session.commit()

        return result

    return [
        list_organizations,
        create_organization,
        update_organization,
        get_organization_status,
        add_org_agent,
        add_org_task,
        assign_org_task,
        complete_org_task,
        list_org_tasks,
        schedule_org_task,
        list_org_schedules,
        cancel_org_schedule,
        create_org_tool,
    ]
