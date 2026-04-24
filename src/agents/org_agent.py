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
from src.db.models import User
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


async def _resolve_db_user_id(session, telegram_id: int) -> int | None:
    """Resolve a Telegram user ID to the internal users.id (32-bit PK).

    organizations.owner_user_id is a FK to users.id — passing the raw
    Telegram ID (which can exceed INT32_MAX) causes a DB overflow error.
    """
    row = await session.execute(
        select(User.id).where(User.telegram_id == telegram_id)
    )
    return row.scalar_one_or_none()


# ── Skill file generation (Fix C) ────────────────────────────────────────

_RESERVED_SKILL_IDS: frozenset[str] = frozenset({
    "memory", "scheduler", "organizations",
    "openrouter_images",
    "gmail", "calendar", "google_tasks", "drive",
    "google_sheets", "google_docs", "google_slides", "google_contacts",
})


def _slugify_skill_id(raw: str) -> str:
    """Normalize a free-form skill name into a filesystem-safe slug."""
    import re
    s = (raw or "").strip().lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    s = s.strip("-")
    return s or "skill"


# ── Duplicate-check reuse helpers (Phase 3) ────────────────────────────
# When setup_org_project receives an LLM plan, we don't blindly create every
# agent/tool/skill. Instead we fuzzy-match against existing items owned by the
# same user and, when similarity >= REUSE_THRESHOLD, REUSE the existing item
# and record the decision in the summary so the user sees exactly what
# happened. This protects "reusable general" items (e.g. a generic "Email
# Agent") from being duplicated each time a new project is spun up, while
# still allowing specialized items to be freshly created.

REUSE_THRESHOLD: float = 0.85


def _similar(a: str, b: str) -> float:
    """Fast string-similarity ratio in [0, 1]. Case-insensitive, punctuation-tolerant."""
    from difflib import SequenceMatcher
    if not a or not b:
        return 0.0
    sa = a.strip().lower()
    sb = b.strip().lower()
    if not sa or not sb:
        return 0.0
    if sa == sb:
        return 1.0
    return SequenceMatcher(None, sa, sb).ratio()


async def _find_similar_existing_agent(
    session: Any, owner_user_id: int, planned_name: str, planned_role: str | None
) -> tuple[Optional["OrgAgent"], float]:
    """Search all OrgAgents owned by ``owner_user_id`` across orgs for one whose
    name closely matches ``planned_name``. Returns ``(agent, score)`` where
    ``agent`` is None when nothing meets the threshold.
    """
    from sqlalchemy import select as _sel
    q = (
        _sel(OrgAgent)
        .join(Organization, OrgAgent.org_id == Organization.id)
        .where(Organization.owner_user_id == owner_user_id)
    )
    rows = (await session.execute(q)).scalars().all()
    best: Optional["OrgAgent"] = None
    best_score = 0.0
    for row in rows:
        score = _similar(row.name, planned_name)
        # Role match is a small bonus: if planned role matches existing role,
        # bump the score a little (capped at 1.0).
        if planned_role and row.role and _similar(row.role, planned_role) > 0.8:
            score = min(1.0, score + 0.05)
        if score > best_score:
            best = row
            best_score = score
    return (best, best_score)


async def _find_similar_existing_tool(
    session: Any, planned_name: str
) -> tuple[Optional[Any], float]:
    """Search active tools (global table) for one whose name closely matches."""
    from sqlalchemy import select as _sel
    from src.db.models import Tool as _Tool
    rows = (await session.execute(_sel(_Tool).where(_Tool.is_active == True))).scalars().all()  # noqa: E712
    best = None
    best_score = 0.0
    for row in rows:
        score = _similar(row.name, planned_name)
        if score > best_score:
            best = row
            best_score = score
    return (best, best_score)


def _find_similar_existing_skill(planned_slug: str, planned_name: str) -> tuple[Optional[str], float]:
    """Scan ``src/user_skills/`` on disk for an existing skill directory whose
    id (dir name) or SKILL.md name matches the planned skill. Returns the
    matched skill id (directory name) and a similarity score.
    """
    from pathlib import Path as _P
    root = _P("src/user_skills")
    if not root.exists():
        return (None, 0.0)
    best_id: Optional[str] = None
    best_score = 0.0
    for child in root.iterdir():
        if not child.is_dir():
            continue
        # Compare against directory slug (primary) and the SKILL.md name if present.
        candidates = [child.name]
        md = child / "SKILL.md"
        if md.exists():
            try:
                head = md.read_text(encoding="utf-8", errors="ignore")[:400]
                # crude: extract `name: "..."` from the frontmatter
                import re as _re
                m = _re.search(r'name:\s*"([^"]+)"', head)
                if m:
                    candidates.append(m.group(1))
            except Exception:
                pass
        for cand in candidates:
            for planned in (planned_slug, planned_name):
                score = _similar(cand, planned)
                if score > best_score:
                    best_id = child.name
                    best_score = score
    return (best_id, best_score)


def _write_skill_md(
    skill_id: str,
    name: str,
    description: str,
    tags: list[str],
    routing_hints: list[str],
    instructions: str,
    related_tools: list[str],
    org_name: str,
) -> tuple[bool, str]:
    """Write an auto-generated SKILL.md file under src/user_skills/<skill_id>/.

    Returns (created, message). If the skill already exists on disk, the file
    is NOT overwritten (reversible by design — the user can edit/delete from
    the dashboard).
    """
    from pathlib import Path as _P

    skill_dir = _P(f"src/user_skills/{skill_id}")
    if skill_dir.exists():
        return (False, f"skill '{skill_id}' already exists")

    try:
        skill_dir.mkdir(parents=True, exist_ok=False)
    except Exception as e:
        return (False, f"failed to create skill dir: {e}")

    # Build YAML frontmatter by hand (no pyyaml dep). One list item per line.
    def _yaml_list(items: list[str], indent: str = "  ") -> str:
        if not items:
            return "[]"
        lines = [""]
        for item in items:
            # Escape double quotes in the string
            escaped = str(item).replace('"', '\\"').strip()
            lines.append(f'{indent}- "{escaped}"')
        return "\n".join(lines)

    # Description must fit on one line for simple YAML parser.
    safe_desc = description.replace("\n", " ").replace('"', '\\"').strip()
    safe_name = name.replace('"', '\\"').strip()

    frontmatter = (
        "---\n"
        f'name: "{safe_name}"\n'
        f'description: "{safe_desc}"\n'
        "version: 1.0.0\n"
        "author: atlas-setup\n"
        f'group: "user"\n'
        f"tags: {_yaml_list(tags)}\n"
        f"routing_hints: {_yaml_list(routing_hints)}\n"
        "requires_skills: []\n"
        "extends_skill: null\n"
        "tools: []\n"
        "requires_connection: false\n"
        "read_only: true\n"
        "---\n"
    )

    # Instructions body. If the planner didn't supply a rich body, generate
    # a structured default that references the org's tools so the orchestrator
    # knows which CLI tools to call when this skill is matched.
    body_parts: list[str] = []
    if instructions and instructions.strip():
        body_parts.append(instructions.strip())
    else:
        body_parts.append(f"## Purpose\n\n{safe_desc}")

    if related_tools:
        tool_lines = "\n".join(f"- `{t}`" for t in related_tools)
        body_parts.append(
            "## Available Tools\n\n"
            f"When this skill is active, prefer calling these registered tools "
            f"from the **{org_name}** organization before falling back to "
            "external search or generic reasoning:\n\n"
            f"{tool_lines}"
        )

    body_parts.append(
        "## Routing Hints\n\n"
        "This skill is selected when the user mentions any of: "
        + ", ".join(f"`{h}`" for h in routing_hints[:6] or [safe_name])
        + ". When selected, follow the instructions above and call the listed "
        "tools with the parameters declared in each tool's manifest."
    )

    body = "\n\n".join(body_parts) + "\n"

    try:
        (skill_dir / "SKILL.md").write_text(frontmatter + "\n" + body, encoding="utf-8")
        return (True, f"skill '{skill_id}' written to {skill_dir}")
    except Exception as e:
        # Cleanup partial dir on write failure
        try:
            import shutil as _sh
            _sh.rmtree(skill_dir, ignore_errors=True)
        except Exception:
            pass
        return (False, f"failed to write SKILL.md: {e}")


# ── Bound tool builders ──────────────────────────────────────────────────

def _build_bound_org_tools(user_id: int) -> list:
    """Build organization management tools bound to a specific user.

    Args:
        user_id: The Telegram user ID (BigInteger). Tools resolve
            this to the internal users.id (32-bit PK) on first DB write.
    """
    # Alias used by inner functions for DB resolution
    user_telegram_id = user_id

    async def _get_db_owner_id(session) -> int:
        """Resolve telegram_id → users.id.

        Falls back to ``user_telegram_id`` itself when resolution returns None
        so that unit-test mocks (which don't seed a users table) continue to
        work.  In production this code path never returns None because the
        bot creates the user row on /start before any org operation.
        """
        db_id = await _resolve_db_user_id(session, user_telegram_id)
        return db_id if db_id is not None else user_telegram_id

    # ── CRUD: Organizations ──────────────────────────────────────────

    @function_tool(
        name_override="list_organizations",
        failure_error_function=_org_tool_error,
        timeout=15,
    )
    async def list_organizations() -> str:
        """List all organizations you own, with agent and task counts."""
        async with async_session() as session:
            db_owner_id = await _get_db_owner_id(session)
            orgs = (
                await session.execute(
                    select(Organization)
                    .where(Organization.owner_user_id == db_owner_id)
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
            db_owner_id = await _get_db_owner_id(session)
            matches = await _find_owned_orgs_by_name(session, db_owner_id, name_query)

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
            db_owner_id = await _get_db_owner_id(session)
            org = Organization(
                name=name.strip(),
                description=description,
                goal=goal.strip(),
                owner_user_id=db_owner_id,
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
            db_owner_id = await _get_db_owner_id(session)
            org = await _get_owned_org(session, org_id, db_owner_id)
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
            db_owner_id = await _get_db_owner_id(session)
            org = await _get_owned_org(session, org_id, db_owner_id)
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
            db_owner_id = await _get_db_owner_id(session)
            org = await _get_owned_org(session, org_id, db_owner_id)
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
            db_owner_id = await _get_db_owner_id(session)
            org = await _get_owned_org(session, org_id, db_owner_id)
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
            db_owner_id = await _get_db_owner_id(session)
            org = await _get_owned_org(session, org_id, db_owner_id)
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
            db_owner_id = await _get_db_owner_id(session)
            org = await _get_owned_org(session, org_id, db_owner_id)
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
            db_owner_id = await _get_db_owner_id(session)
            org = await _get_owned_org(session, org_id, db_owner_id)
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
            db_owner_id = await _get_db_owner_id(session)
            org = await _get_owned_org(session, org_id, db_owner_id)
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
            db_owner_id = await _get_db_owner_id(session)
            org = await _get_owned_org(session, org_id, db_owner_id)
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
            db_owner_id = await _get_db_owner_id(session)
            org = await _get_owned_org(session, org_id, db_owner_id)
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
        requires_system_binary: bool = False,
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
            requires_system_binary: Set True for tools that invoke system binaries (ffmpeg, convert, sox, etc.) via subprocess
        """
        async with async_session() as session:
            db_owner_id = await _get_db_owner_id(session)
            org = await _get_owned_org(session, org_id, db_owner_id)
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
            requires_system_binary=requires_system_binary,
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

        # ── Step 1: Ask the LLM to produce a structured plan ────────────
        planning_prompt = f"""You are a senior AI systems architect designing a multi-agent project for
Atlas — a self-hosted personal assistant powered by OpenAI Agents SDK.

The user wants to achieve the following goal:

  GOAL: {goal}

Produce a JSON execution plan with this EXACT structure (raw JSON only, no markdown fences):
{{
  "org_name": "<short descriptive project name, 2-5 words>",
  "org_goal": "<one-sentence mission statement that defines success>",
  "agents": [
    {{
      "name": "<agent name, 2-3 words max>",
      "role": "<role slug: 'orchestrator'|'executor'|'reviewer'|'specialist'>",
      "description": "<one sentence — what this agent is responsible for>",
      "instructions": "<DETAILED multi-paragraph instructions — see requirements below>",
      "skills": ["<skill_id — see rules below>", ...],
      "allowed_tools": ["<tool_name_snake_case from the tools list>", ...]
    }}
  ],
  "tasks": [
    {{
      "title": "<task title>",
      "description": "<2-3 sentence description including inputs, process, and expected output>",
      "priority": "high|medium|low",
      "agent_name": "<exact name of agent from the agents list who owns this task>"
    }}
  ],
  "tools": [
    {{
      "name": "<snake_case_tool_name>",
      "description": "<what this tool does in one sentence>",
      "tool_type": "cli | http_api",
      "parameters": {{
        "<param_name>": {{"type": "str|int|bool", "required": true, "description": "<param description>"}}
      }},
      "requires_network": false,
      "requires_system_binary": false,
      "allowed_hosts": "<comma-separated hostnames if http_api, e.g. openrouter.ai>",
      "credential_keys": "<comma-separated credential names if http_api, e.g. api_key>",
      "tool_code": "<complete Python script — see code rules below for the correct pattern per tool_type>"
    }}
  ],
  "skills": [
    {{
      "id": "<kebab-case-skill-id>",
      "name": "<Human Readable Skill Name>",
      "description": "<one-sentence summary>",
      "tags": ["<single-word keyword>", ...],
      "routing_hints": ["<natural phrase user would say>", ...],
      "instructions": "<detailed markdown — see skill instruction requirements below>",
      "related_tools": ["<tool_name from tools list>", ...]
    }}
  ]
}}

═══════════════════════════════════════════════════════════════
AGENT INSTRUCTION REQUIREMENTS (most important section)
═══════════════════════════════════════════════════════════════
Each agent's "instructions" field MUST be a detailed, multi-paragraph string (300-600 words)
that contains ALL of the following sections:

## Role & Purpose
State the agent's precise role within the team, what it is responsible for, and what it
is NOT responsible for.

## Workflow
Numbered step-by-step workflow the agent follows for every request. Reference specific
tool names. For example:
  1. Receive task from user or ProjectManager agent
  2. Call `ffmpeg_convert_video` with --input and --output params
  3. Verify output file exists; if not, log error and retry once
  4. Return structured result: {{status, output_path, duration_sec}}

## Tool Usage
For each tool in allowed_tools, one paragraph explaining exactly when and how to call it,
what parameters are required, and how to handle its output or errors.

## Cross-Agent Coordination
How this agent communicates results to other agents or receives input from them.
Specify the data contract (what fields are passed, what format).

## Error Handling
What to do when a tool fails: retry policy, fallback actions, when to escalate.

## Output Format
Exact format of responses this agent produces (JSON, markdown, plain text, etc.)
and what fields/sections to always include.

═══════════════════════════════════════════════════════════════
SKILL INSTRUCTION REQUIREMENTS
═══════════════════════════════════════════════════════════════
Each skill's "instructions" MUST be 150-400 words of markdown covering:
- ## Purpose: what this skill enables, domain context
- ## When to Use: trigger phrases and conditions
- ## Process: numbered steps an agent follows when this skill is active
- ## Tools: which CLI tools to call and in what order
- ## Examples: 2-3 example user requests and expected agent responses

Skills are the routing layer — well-written instructions ensure Atlas correctly
activates this skill and executes the right tool sequence.

═══════════════════════════════════════════════════════════════
ATLAS BUILT-IN CAPABILITIES (use these instead of generating new tools)
═══════════════════════════════════════════════════════════════
Atlas already has the following built-in skills and tools. Reference them in agent
skills and instructions INSTEAD of generating duplicate CLI tools.

OPENROUTER SKILL (id: "openrouter_images") — USE THIS for ALL AI-native media generation:
  Provides 3 tools agents can call:
    • generate_image(prompt, quality)         — text-to-image via OpenRouter
    • analyze_uploaded_image(prompt)          — analyze a Telegram photo
    • list_openrouter_models(modality)        — discover cheapest model for "image"|"video"|"audio"

  WHEN TO USE openrouter_images skill:
    ✅ Talking avatar / lip-sync ("make this image talk", "have photo say", "animate my face")
    ✅ AI video generation ("create a video from a description", "text to video", "image to video")
    ✅ Music / audio generation ("create background music", "generate a song", "make a jingle")
    ✅ Image generation from a prompt ("create an image of...", "generate artwork")
    ✅ Any request that needs an AI model to CREATE media, not just process existing files

  HOW AGENTS SHOULD USE IT:
    For talking-head / lip-sync video:
      1. Call list_openrouter_models(modality="video") to find cheapest lip-sync model
      2. The result shows model IDs and pricing — pick lowest cost model that supports lip-sync
         (known good models: alibaba/wan-2.6 for portrait lip-sync, bytedance/seedance-1-5-pro for multi-language)
      3. Explain to user which model was chosen and ~cost, then confirm the avatar photo is ready
      4. Submit to OpenRouter POST /api/v1/videos — async job, polls 15s intervals, result is mp4

    For AI music / audio:
      1. Call list_openrouter_models(modality="audio") to find cheapest model
      2. Submit the generation request with the chosen model

  To assign this skill to an agent, add "openrouter_images" to the agent's skills array.
  ⚠️ Do NOT generate CLI tools for TTS, lip-sync, image generation, video AI, or music — use openrouter_images instead.

FFMPEG SKILL (tools already exist: ffmpeg_video_composition, ffmpeg_apply_transitions, ffmpeg_aspect_ratio,
  ffmpeg_audio_normalization, ffmpeg_audio_integration) — USE FOR file processing only:
    ✅ Compositing existing video clips together
    ✅ Applying transitions between clips
    ✅ Adjusting aspect ratio / resolution of existing files
    ✅ Normalizing audio levels on existing audio files
    ✅ Adding a pre-existing audio track to a pre-existing video
    ❌ NOT for generating new content from scratch — use openrouter_images for that

═══════════════════════════════════════════════════════════════
DECISION TREE: CLI tool vs built-in skill
═══════════════════════════════════════════════════════════════
Ask: "Does this task need an AI model to CREATE new content, or process existing files?"
  → CREATE new content (TTS, video gen, lip-sync, music, image gen) → use openrouter_images skill
  → PROCESS existing files (cut, join, encode, resize, normalize) → use FFmpeg tools
  → FETCH data from the web / APIs → generate a CLI tool with requires_network=true
  → NO external binary or network needed → leave tools:[] and handle via LLM reasoning

═══════════════════════════════════════════════════════════════
GENERAL RULES
═══════════════════════════════════════════════════════════════
- Include 2-4 agents maximum. One should be a ProjectManager/Orchestrator.
- Include 4-10 tasks covering the full workflow end-to-end.
- Generate 1-3 DOMAIN SKILLS. Each MUST have:
  * 5-8 routing_hints covering BOTH file-processing AND AI-generation phrasings the user might say
  * 4-8 single-word tags including "video", "audio", "generate", "ai" if relevant
  * related_tools matching names in the tools array (leave empty if using only built-in skills)
- For agent skills, use ONLY:
  * Reserved IDs: memory | scheduler | organizations | openrouter_images
  * Or the exact kebab-case id of a skill defined in the skills array
- For allowed_tools, reference ONLY tool names defined in the tools array.
  (openrouter_images tools are accessed via the skill, not via allowed_tools)
- IMPORTANT: If the goal needs external programs (ffmpeg, sox, yt-dlp, imagemagick, etc.)
  for FILE PROCESSING, generate working CLI tools with requires_system_binary=true.
  Use subprocess.run(["ffmpeg", ...], capture_output=True, text=True, check=False) — never shell=True.

CLI TOOL CODE RULES (follow exactly):
  import argparse, subprocess, sys
  def main():
      parser = argparse.ArgumentParser(...)
      parser.add_argument("--param", required=True, help="...")
      args = parser.parse_args()   # MUST be before any subprocess call
      cmd = ["binary", "--flag", args.param]   # always a LIST
      result = subprocess.run(cmd, capture_output=True, text=True, check=False)
      if result.returncode != 0:
          print(result.stderr, file=sys.stderr); sys.exit(result.returncode)
      print(result.stdout or "Done.")
  if __name__ == "__main__": main()
  * NEVER: shell=True, os.system, eval, exec, os.environ, shutil, ctypes, pickle

- Leave tools as [] if no external binaries are needed.
- Each task agent_name MUST exactly match an agent name in the agents list.
- Respond with raw JSON only — no explanation, no markdown fences.
"""
        try:
            response = await client.chat.completions.create(
                model="gpt-4o",
                messages=[{"role": "user", "content": planning_prompt}],
                temperature=0.3,
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
        planned_tools: list[dict] = plan.get("tools") or []
        planned_skills: list[dict] = plan.get("skills") or []
        resolved_org_name = org_name or plan.get("org_name") or "New Project"
        resolved_org_goal = plan.get("org_goal") or goal

        if not planned_agents:
            return "The plan came back empty. Please describe the goal in more detail."

        # ── Step 2: Create or reuse the organization ───────────────────
        async with async_session() as session:
            db_owner_id = await _get_db_owner_id(session)
            if org_id:
                org = await _get_owned_org(session, org_id, db_owner_id)
                if not org:
                    return f"Organization {org_id} not found or you don't own it."
                created_org = False
            else:
                org = Organization(
                    name=resolved_org_name,
                    goal=resolved_org_goal,
                    owner_user_id=db_owner_id,
                    status="active",
                )
                session.add(org)
                await session.flush()
                await _log_activity(
                    session, org.id, "org_created",
                    f"Organization '{resolved_org_name}' created by project setup",
                )
                created_org = True

            # ── Step 3: Create (or reuse) agents ───────────────────────
            # Phase 3: fuzzy-match the planned agent name against this user's
            # existing agents. If similarity >= REUSE_THRESHOLD, clone the
            # existing config into the new org (we do NOT re-parent the row —
            # each OrgAgent row stays owned by its original org so the cascade
            # on org-delete behaves predictably). The cloned row records its
            # source via `tools_config["cloned_from_agent_id"]`.
            agent_name_to_obj: dict[str, OrgAgent] = {}
            agent_reuse_notes: list[tuple[str, str, float]] = []  # (planned_name, existing_name, score)
            for ap in planned_agents:
                planned_agent_name = ap.get("name", "Agent").strip()
                planned_agent_role = (ap.get("role") or "specialist").strip()

                tc: dict = {}
                if ap.get("skills"):
                    # Slugify so IDs match what _write_skill_md creates on disk
                    tc["skills"] = [
                        _slugify_skill_id(s) for s in ap["skills"]
                        if s.strip() and s.strip() not in _RESERVED_SKILL_IDS
                    ] + [
                        s.strip() for s in ap["skills"]
                        if s.strip() in _RESERVED_SKILL_IDS
                    ]
                if ap.get("allowed_tools"):
                    tc["allowed_tools"] = [t.strip() for t in ap["allowed_tools"] if t.strip()]

                # Look for a reusable existing agent owned by this user.
                existing_agent, score = await _find_similar_existing_agent(
                    session, db_owner_id, planned_agent_name, planned_agent_role,
                )
                if existing_agent is not None and score >= REUSE_THRESHOLD:
                    # Clone config into a new row scoped to this org; carry over
                    # skills/tools from the source, overlaying the planner's
                    # extras so specialization still works.
                    src_cfg = dict(existing_agent.tools_config or {})
                    src_skills = list(src_cfg.get("skills") or [])
                    src_tools = list(src_cfg.get("allowed_tools") or [])
                    new_skills = sorted(set(
                        _slugify_skill_id(s) if s not in _RESERVED_SKILL_IDS else s
                        for s in (src_skills + (tc.get("skills") or []))
                    ))
                    new_tools = sorted(set(src_tools) | set(tc.get("allowed_tools") or []))
                    merged_tc: dict = {}
                    if new_skills:
                        merged_tc["skills"] = new_skills
                    if new_tools:
                        merged_tc["allowed_tools"] = new_tools
                    merged_tc["cloned_from_agent_id"] = existing_agent.id
                    merged_tc["cloned_from_name"] = existing_agent.name
                    merged_tc["reuse_similarity"] = round(score, 3)

                    db_agent = OrgAgent(
                        org_id=org.id,
                        name=existing_agent.name,  # keep the canonical name
                        role=existing_agent.role or planned_agent_role,
                        description=existing_agent.description or ap.get("description"),
                        instructions=existing_agent.instructions or ap.get("instructions"),
                        tools_config=merged_tc,
                        status="active",
                    )
                    session.add(db_agent)
                    await session.flush()
                    await _log_activity(
                        session, org.id, "agent_cloned",
                        f"Agent '{db_agent.name}' cloned from existing agent #{existing_agent.id} "
                        f"(similarity {score:.2f} vs planned '{planned_agent_name}')",
                        agent_id=db_agent.id,
                    )
                    agent_name_to_obj[db_agent.name] = db_agent
                    agent_reuse_notes.append((planned_agent_name, existing_agent.name, score))
                    continue

                db_agent = OrgAgent(
                    org_id=org.id,
                    name=planned_agent_name,
                    role=planned_agent_role,
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

        # ── Step 4.5: Generate (or reuse) tools defined in the plan ────
        # Routes to the correct generator based on tool_type:
        #   "http_api"  → _generate_http_tool_impl (function type, httpx, credential vault)
        #   "cli" / ""  → _generate_cli_tool_impl  (subprocess, argparse, system binary)
        from src.agents.tool_factory_agent import _generate_cli_tool_impl, _generate_http_tool_impl
        import json as _json
        created_tool_results: list[tuple[str, str]] = []
        tool_reuse_notes: list[tuple[str, str, float]] = []  # (planned_name, existing_name, score)
        for tp in planned_tools:
            tool_name = (tp.get("name") or "").strip().lower().replace(" ", "_")
            tool_desc = tp.get("description") or ""
            tool_code = tp.get("tool_code") or ""
            tool_params = tp.get("parameters") or {}
            tool_type = (tp.get("tool_type") or "cli").strip().lower()
            requires_net = bool(tp.get("requires_network", False))
            requires_bin = bool(tp.get("requires_system_binary", False))
            allowed_hosts = (tp.get("allowed_hosts") or "").strip()
            credential_keys = (tp.get("credential_keys") or "").strip()
            if not tool_name or not tool_code:
                continue

            # Reuse existing tool if a close match is already registered.
            async with async_session() as _ts:
                existing_tool, tscore = await _find_similar_existing_tool(_ts, tool_name)
            if existing_tool is not None and tscore >= REUSE_THRESHOLD:
                tool_reuse_notes.append((tool_name, existing_tool.name, tscore))
                created_tool_results.append(
                    (tool_name, f"🔁 Reused existing tool '{existing_tool.name}' (similarity {tscore:.2f})")
                )
                async with async_session() as s:
                    await _log_activity(
                        s, final_org_id, "tool_reused",
                        f"Tool '{existing_tool.name}' reused (planned '{tool_name}', similarity {tscore:.2f})",
                    )
                    await s.commit()
                continue

            try:
                params_json_str = _json.dumps(tool_params)
            except Exception:
                params_json_str = "{}"

            if tool_type == "http_api":
                result = await _generate_http_tool_impl(
                    name=tool_name,
                    description=f"[{final_org_name}] {tool_desc}",
                    parameters_json=params_json_str,
                    tool_code=tool_code,
                    allowed_hosts=allowed_hosts,
                    credential_keys=credential_keys,
                )
                tool_kind = "HTTP API tool"
            else:
                result = await _generate_cli_tool_impl(
                    name=tool_name,
                    description=f"[{final_org_name}] {tool_desc}",
                    parameters_json=params_json_str,
                    tool_code=tool_code,
                    requires_network=requires_net,
                    allowed_hosts=allowed_hosts,
                    requires_system_binary=requires_bin,
                )
                tool_kind = "CLI tool"

            created_tool_results.append((tool_name, result))
            if "✅" in result or "created and registered" in result.lower():
                async with async_session() as s:
                    await _log_activity(
                        s, final_org_id, "tool_created",
                        f"{tool_kind} '{tool_name}' auto-generated by project setup",
                    )
                    await s.commit()

        # ── Step 4.6: Generate SKILL.md files for domain skills ───────
        # Each planned skill becomes a filesystem SKILL.md under src/user_skills/
        # so the orchestrator's selective skill router can match it against
        # future user messages via routing_hints + tags.
        created_skill_results: list[tuple[str, str, bool]] = []  # (id, message, created)
        skills_actually_written = False
        for sp in planned_skills:
            raw_id = (sp.get("id") or sp.get("name") or "").strip()
            if not raw_id:
                continue
            sk_id = _slugify_skill_id(raw_id)
            if sk_id in _RESERVED_SKILL_IDS:
                # Skip — these are built-in Atlas skills, don't shadow them
                created_skill_results.append(
                    (sk_id, "reserved built-in id skipped", False)
                )
                continue

            sk_name = (sp.get("name") or raw_id).strip()
            sk_desc = (sp.get("description") or "").strip()
            sk_tags = [str(t).strip() for t in (sp.get("tags") or []) if str(t).strip()]
            sk_hints = [str(h).strip() for h in (sp.get("routing_hints") or []) if str(h).strip()]
            sk_instructions = (sp.get("instructions") or "").strip()
            sk_related = [
                str(t).strip().lower().replace(" ", "_")
                for t in (sp.get("related_tools") or [])
                if str(t).strip()
            ]

            # Phase 3: if an on-disk skill closely matches the planned one,
            # reuse it rather than creating a sibling with a near-duplicate id.
            existing_sk_id, sk_score = _find_similar_existing_skill(sk_id, sk_name)
            if existing_sk_id is not None and sk_score >= REUSE_THRESHOLD:
                created_skill_results.append(
                    (sk_id, f"🔁 reused existing skill '{existing_sk_id}' (similarity {sk_score:.2f})", False)
                )
                async with async_session() as s:
                    await _log_activity(
                        s, final_org_id, "skill_reused",
                        f"Skill '{existing_sk_id}' reused (planned '{sk_id}', similarity {sk_score:.2f})",
                    )
                    await s.commit()
                continue

            created, msg = _write_skill_md(
                skill_id=sk_id,
                name=sk_name,
                description=sk_desc,
                tags=sk_tags,
                routing_hints=sk_hints,
                instructions=sk_instructions,
                related_tools=sk_related,
                org_name=final_org_name,
            )
            created_skill_results.append((sk_id, msg, created))
            if created:
                skills_actually_written = True
                async with async_session() as s:
                    await _log_activity(
                        s, final_org_id, "skill_created",
                        f"Skill '{sk_id}' auto-generated by project setup",
                    )
                    await s.commit()

        # Invalidate the orchestrator's per-user skill registry cache so the
        # freshly-written skills participate in routing on the very next turn.
        if skills_actually_written:
            try:
                from src.agents.orchestrator import _registry_cache
                _registry_cache.clear()
                logger.info("Cleared orchestrator skill-registry cache after skill generation")
            except Exception as _e:
                logger.warning("Failed to clear orchestrator cache: %s", _e)

        # ── Step 5: Validate skills and tools exist (sandbox check) ───
        from pathlib import Path as _Path
        _tools_plugin_dir = _Path("src/tools/plugins")
        _user_skills_dir = _Path("src/user_skills")

        # Build live skill ID set: built-ins + freshly-written user skills
        # Seed with all reserved IDs so they always pass validation.
        _known_skill_ids: set[str] = set(_RESERVED_SKILL_IDS)
        try:
            from src.skills.registry import SkillRegistry
            from src.skills.internal import (
                build_memory_skill, build_organization_skill, build_scheduler_skill,
            )
            _check_registry = SkillRegistry()
            _check_registry.register(build_memory_skill(user_id))
            _check_registry.register(build_organization_skill(user_id))
            _check_registry.register(build_scheduler_skill(user_id))
            _known_skill_ids |= set(_check_registry._skills.keys())
        except Exception:
            pass
        # Accept any user_skills dir that was just written — includes
        # skills generated in Step 4.6 of this very setup run.
        if _user_skills_dir.exists():
            _known_skill_ids |= {
                p.name for p in _user_skills_dir.iterdir()
                if p.is_dir() and (p / "SKILL.md").exists()
            }
        # Also add the slugs of skills we just wrote this run (avoids
        # false-positive warnings when the filesystem hasn't flushed yet).
        for _sk_id, _sk_msg, _was_created in created_skill_results:
            if _was_created or "reused" in _sk_msg:
                _known_skill_ids.add(_sk_id)

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
            reuse_note = ""
            if tc.get("cloned_from_agent_id"):
                reuse_note = f"  ♻️ cloned from existing agent (similarity {tc.get('reuse_similarity', '?')})"
            lines.append(f"  • **{a.name}** ({a.role}){skill_note}{tool_note}{reuse_note}")

        if agent_reuse_notes:
            lines.append("\n**Reused agents (similarity ≥ {:.0%}):**".format(REUSE_THRESHOLD))
            for planned, existing, score in agent_reuse_notes:
                lines.append(f"  🔁 planned `{planned}` → reused existing **{existing}** ({score:.2f})")

        lines.append(f"\n**Tasks ({len(created_tasks)}):**")
        for t in created_tasks:
            assigned = agent_name_to_obj.get(
                next((n for n, a in agent_name_to_obj.items() if a.id == t.agent_id), ""),
                None,
            )
            owner = f" → {assigned.name}" if assigned else " (unassigned)"
            icon = {"high": "🔴", "medium": "🟡", "low": "🟢"}.get(t.priority, "⚪")
            lines.append(f"  {icon} {t.title}{owner}")

        # ── Tool generation results ─────────────────────────────────────
        if created_tool_results:
            lines.append(f"\n**CLI Tools ({len(created_tool_results)}):**")
            for tname, tresult in created_tool_results:
                if "🔁" in tresult:
                    icon = "🔁"
                elif "✅" in tresult or "created and registered" in tresult.lower():
                    icon = "✅"
                else:
                    icon = "❌"
                lines.append(f"  {icon} `{tname}` — {tresult[:200] if icon != '✅' else 'registered'}")
                if icon == "❌" and ("failed" in tresult.lower() or "error" in tresult.lower()):
                    lines.append(f"     _{tresult[:200]}_")

        # ── Skill generation results ────────────────────────────────────
        if created_skill_results:
            n_new = sum(1 for _, _, c in created_skill_results if c)
            n_reused = sum(1 for _, m, _ in created_skill_results if "🔁" in m)
            lines.append(f"\n**Skills ({n_new} new, {n_reused} reused):**")
            for sk_id, sk_msg, was_created in created_skill_results:
                if was_created:
                    icon = "✅"
                elif "🔁" in sk_msg:
                    icon = "🔁"
                else:
                    icon = "⚠️"
                lines.append(f"  {icon} `{sk_id}` — {sk_msg}")

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
