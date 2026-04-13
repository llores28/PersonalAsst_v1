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


async def _find_owned_orgs_by_name(
    session,
    owner_id: int,
    query: str,
) -> list[Organization]:
    """Find owned organizations by partial name match, newest first."""
    search = (query or "").strip()
    if not search:
        return []

    result = await session.execute(
        select(Organization)
        .where(Organization.owner_user_id == owner_id)
        .where(Organization.name.ilike(f"%{search}%"))
        .order_by(Organization.created_at.desc())
    )
    return list(result.scalars().all())


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
        name_override="find_organization",
        failure_error_function=_org_tool_error,
        timeout=15,
    )
    async def find_organization(name_query: str) -> str:
        """Find an organization by name and return the best matching ID(s).

        Args:
            name_query: Full or partial organization name, e.g. 'DevOps'
        """
        if not name_query or not name_query.strip():
            return "Provide part of the organization name so I can find the correct org ID."

        async with async_session() as session:
            matches = await _find_owned_orgs_by_name(session, user_id, name_query)

        if not matches:
            return (
                f"I couldn't find an organization matching '{name_query.strip()}'. "
                "Use `list_organizations` to see your available orgs and IDs."
            )

        if len(matches) == 1:
            org = matches[0]
            return (
                f"Matched organization **{org.name}** with ID `{org.id}`. "
                "Use that org ID for follow-up task, agent, or tool actions."
            )

        lines = [f"I found {len(matches)} organizations matching '{name_query.strip()}':\n"]
        for org in matches[:10]:
            lines.append(f"- **{org.name}** (ID: `{org.id}`) — status: {org.status}")
        lines.append("Reply with the org ID you want me to use.")
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
        skills: Optional[str] = None,
        allowed_tools: Optional[str] = None,
    ) -> str:
        """Add a specialized agent to an organization.

        Args:
            org_id: Organization ID to add the agent to
            name: Agent name (e.g., 'Resume Analyzer', 'Content Writer')
            role: Agent role (e.g., 'researcher', 'writer', 'analyst')
            description: What this agent does
            instructions: Specific instructions for this agent's behavior
            skills: Comma-separated skill names this agent should use (e.g., 'code_audit,scheduler_diagnostics')
            allowed_tools: Comma-separated tool names this agent may call (e.g., 'browser_scrape_page,linkedin_scrape_page')
        """
        async with async_session() as session:
            org = await _get_owned_org(session, org_id, user_id)
            if not org:
                return f"Organization {org_id} not found or you don't own it."

            # Build tools_config from skills and allowed_tools if provided
            tc: dict = {}
            if skills:
                tc["skills"] = [s.strip() for s in skills.split(",") if s.strip()]
            if allowed_tools:
                tc["allowed_tools"] = [t.strip() for t in allowed_tools.split(",") if t.strip()]

            agent = OrgAgent(
                org_id=org_id,
                name=name.strip(),
                role=role.strip(),
                description=description,
                instructions=instructions,
                tools_config=tc if tc else None,
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
            skills_note = f"\nSkills: {', '.join(tc['skills'])}" if tc.get("skills") else ""
            tools_note = f"\nAllowed tools: {', '.join(tc['allowed_tools'])}" if tc.get("allowed_tools") else ""
            return (
                f"Added agent **{name}** (role: {role}) to **{org.name}**.\n"
                f"Agent ID: {agent.id}{skills_note}{tools_note}"
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

        from src.agents.tool_factory_agent import _generate_cli_tool_impl

        result = await _generate_cli_tool_impl(
            name=name,
            description=f"[{org.name}] {description}",
            parameters_json=parameters_json,
            tool_code=tool_code,
            requires_network=requires_network,
            allowed_hosts=allowed_hosts,
        )

        if "created and registered" in result.lower() or "✅" in result:
            async with async_session() as session:
                await _log_activity(
                    session, org_id, "tool_created",
                    f"CLI tool '{name}' created via Telegram",
                )
                await session.commit()

        return result

    # ── Goal Decomposition: Full project setup from a plain-English goal ──

    @function_tool(
        name_override="setup_org_project",
        failure_error_function=_org_tool_error,
        timeout=60,
    )
    async def setup_org_project(
        goal: str,
        org_name: Optional[str] = None,
        org_id: Optional[int] = None,
    ) -> str:
        """Plan and fully set up a project from a plain-English goal.

        Given a goal such as 'audit the Atlas Personal Assistant code to verify
        it is working correctly', this tool will:
        1. Derive a structured plan — which specialist agents are needed, what
           skills and tools each agent should have, and which tasks to create.
        2. Create or reuse an organization as the project container.
        3. Add every planned agent (with skills and allowed_tools) to the org.
        4. Create every planned task and assign it to the correct agent.
        5. Return a human-readable summary of everything that was set up.

        Use this whenever the user asks to 'set up a project', 'create a team
        for X', or describes a goal that clearly needs multiple agents and tasks.

        Args:
            goal: Plain-English description of the project goal, e.g.
                  'audit the Atlas code to check it is working correctly'
            org_name: Optional name for the new organization (auto-derived if omitted)
            org_id: Optional existing org ID to add the plan into instead of creating one
        """
        import json
        from openai import AsyncOpenAI

        client = AsyncOpenAI()

        # ── Step 1: Ask the LLM to produce a structured plan ──────────
        planning_prompt = f"""You are a project planner for an AI personal assistant system called Atlas.
The user wants to achieve the following goal:

  GOAL: {goal}

Produce a JSON execution plan with this exact structure (no markdown fences, raw JSON only):
{{
  "org_name": "<short descriptive project name, e.g. 'Atlas Code Audit'>",
  "org_goal": "<one-sentence mission statement>",
  "agents": [
    {{
      "name": "<agent name>",
      "role": "<role slug, e.g. 'auditor', 'reporter', 'monitor'>",
      "description": "<what this agent does>",
      "instructions": "<specific behaviour instructions for this agent>",
      "skills": ["<skill_id>", ...],
      "allowed_tools": ["<tool_name>", ...]
    }}
  ],
  "tasks": [
    {{
      "title": "<task title>",
      "description": "<task details>",
      "priority": "high|medium|low",
      "agent_name": "<name of agent from the agents list above who owns this task>"
    }}
  ]
}}

Rules:
- Include 2–5 agents maximum.  Keep the team small and focused.
- Include 4–10 tasks that cover the full workflow from start to finish.
- Skills must come from this list only: code_audit, scheduler_diagnostics,
  memory_review, tool_registry_check, api_health, log_analysis, self_improvement.
- Allowed tools must come from this list only: get_my_recent_context,
  summarize_my_conversation, list_tools, run_code_audit, check_scheduler_health,
  list_schedules, get_org_status.
- Each task MUST reference an agent_name that exists in the agents list.
- Respond with raw JSON only — no explanation, no markdown.
"""
        try:
            response = await client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": planning_prompt}],
                temperature=0.2,
                response_format={"type": "json_object"},
            )
            raw = response.choices[0].message.content or "{}"
            plan = json.loads(raw)
        except Exception as exc:
            logger.exception("Planning LLM call failed: %s", exc)
            return (
                f"I couldn't generate a plan for that goal due to an error: {exc}. "
                "Please try rephrasing the goal or create the org, agents, and tasks manually."
            )

        planned_agents: list[dict] = plan.get("agents") or []
        planned_tasks: list[dict] = plan.get("tasks") or []
        resolved_org_name = org_name or plan.get("org_name") or "New Project"
        resolved_org_goal = plan.get("org_goal") or goal

        if not planned_agents:
            return "The plan came back empty. Please describe the goal in more detail."

        # ── Step 2: Create or reuse the organization ───────────────────
        async with async_session() as session:
            if org_id:
                org = await _get_owned_org(session, org_id, user_id)
                if not org:
                    return f"Organization {org_id} not found or you don't own it."
                created_org = False
            else:
                org = Organization(
                    name=resolved_org_name,
                    goal=resolved_org_goal,
                    owner_user_id=user_id,
                    status="active",
                )
                session.add(org)
                await session.flush()
                await _log_activity(
                    session, org.id, "org_created",
                    f"Organization '{resolved_org_name}' created by project setup",
                )
                created_org = True

            # ── Step 3: Create agents ──────────────────────────────────
            agent_name_to_obj: dict[str, OrgAgent] = {}
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
                await _log_activity(
                    session, org.id, "agent_created",
                    f"Agent '{db_agent.name}' added by project setup",
                    agent_id=db_agent.id,
                )
                agent_name_to_obj[db_agent.name] = db_agent

            # ── Step 4: Create tasks and assign them ───────────────────
            created_tasks: list[OrgTask] = []
            for tp in planned_tasks:
                assigned_agent = agent_name_to_obj.get(tp.get("agent_name", ""))
                priority = tp.get("priority", "medium")
                if priority not in ("high", "medium", "low"):
                    priority = "medium"
                db_task = OrgTask(
                    org_id=org.id,
                    agent_id=assigned_agent.id if assigned_agent else None,
                    title=tp.get("title", "Task").strip(),
                    description=tp.get("description"),
                    priority=priority,
                    status="in_progress" if assigned_agent else "pending",
                    source="telegram",
                    assigned_at=datetime.now(timezone.utc) if assigned_agent else None,
                )
                session.add(db_task)
                await session.flush()
                await _log_activity(
                    session, org.id, "task_created",
                    f"Task '{db_task.title}' created by project setup",
                    agent_id=db_task.agent_id,
                    task_id=db_task.id,
                )
                created_tasks.append(db_task)

            await session.commit()
            final_org_id = org.id
            final_org_name = org.name

        # ── Step 5: Validate skills and tools exist (sandbox check) ───
        from pathlib import Path as _Path
        _tools_plugin_dir = _Path("src/tools/plugins")

        # Build live skill ID set from registry
        try:
            from src.skills.registry import SkillRegistry
            from src.skills.internal import (
                build_memory_skill, build_organization_skill, build_scheduler_skill,
            )
            _check_registry = SkillRegistry()
            _check_registry.register(build_memory_skill(user_id))
            _check_registry.register(build_organization_skill(user_id))
            _check_registry.register(build_scheduler_skill(user_id))
            _known_skill_ids = set(_check_registry._skills.keys())
        except Exception:
            _known_skill_ids = set()

        # Build live tool name set from DB + plugin dir
        try:
            from src.db.models import Tool as _Tool
            from sqlalchemy import select as _sel
            async with async_session() as _vs:
                _db_tools = (await _vs.execute(_sel(_Tool).where(_Tool.is_active == True))).scalars().all()  # noqa: E712
            _known_tool_names = {t.name for t in _db_tools}
        except Exception:
            _known_tool_names = set()
        # Also accept any plugin directory that exists on disk
        if _tools_plugin_dir.exists():
            _known_tool_names |= {p.name for p in _tools_plugin_dir.iterdir() if p.is_dir()}

        validation_warnings: list[str] = []
        async with async_session() as vsession:
            for agent_name, db_agent in agent_name_to_obj.items():
                vtc = dict(db_agent.tools_config or {})
                val: dict = {"skills": {}, "tools": {}}
                for sk in vtc.get("skills", []):
                    ok = sk in _known_skill_ids
                    val["skills"][sk] = "✅ found" if ok else "⚠️ not registered"
                    if not ok:
                        validation_warnings.append(f"Agent '{agent_name}': skill '{sk}' not found in registry")
                for tn in vtc.get("allowed_tools", []):
                    ok = tn in _known_tool_names
                    val["tools"][tn] = "✅ found" if ok else "⚠️ not installed"
                    if not ok:
                        validation_warnings.append(f"Agent '{agent_name}': tool '{tn}' not installed")
                vtc["validation"] = val
                db_agent.tools_config = vtc
                vsession.add(db_agent)
            await vsession.commit()

        # ── Step 6: Build the human-readable summary ───────────────────
        lines: list[str] = []
        if created_org:
            lines.append(f"✅ **Project created: {final_org_name}** (ID: {final_org_id})")
        else:
            lines.append(f"✅ **Plan added to: {final_org_name}** (ID: {final_org_id})")
        lines.append(f"**Goal:** {resolved_org_goal}\n")

        lines.append(f"**Agents ({len(planned_agents)}):**")
        for name, a in agent_name_to_obj.items():
            tc = a.tools_config or {}
            skill_note = f" | skills: {', '.join(tc['skills'])}" if tc.get("skills") else ""
            tool_note = f" | tools: {', '.join(tc['allowed_tools'])}" if tc.get("allowed_tools") else ""
            lines.append(f"  • **{a.name}** ({a.role}){skill_note}{tool_note}")

        lines.append(f"\n**Tasks ({len(created_tasks)}):**")
        for t in created_tasks:
            assigned = agent_name_to_obj.get(
                next((n for n, a in agent_name_to_obj.items() if a.id == t.agent_id), ""),
                None,
            )
            owner = f" → {assigned.name}" if assigned else " (unassigned)"
            icon = {"high": "🔴", "medium": "🟡", "low": "🟢"}.get(t.priority, "⚪")
            lines.append(f"  {icon} {t.title}{owner}")

        if validation_warnings:
            lines.append("\n⚠️ **Validation issues found** (project still created — fix these to make agents fully operational):")
            for w in validation_warnings:
                lines.append(f"  • {w}")
        else:
            lines.append("\n✅ **All assigned skills and tools validated successfully.**")

        lines.append(
            f"\nEverything is visible in the **Atlas Dashboard** under Organizations."
        )
        return "\n".join(lines)

    return [
        list_organizations,
        find_organization,
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
        setup_org_project,
    ]
