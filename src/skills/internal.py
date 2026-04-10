"""Internal skill builders — Memory, Scheduler.

Both are flattened to direct bound tools (no agent wrapper needed).
"""

from __future__ import annotations

import logging

from src.skills.definition import SkillDefinition, SkillGroup

logger = logging.getLogger(__name__)


def build_memory_skill(user_id: int) -> SkillDefinition:
    """Build the Memory skill with direct bound tools (no agent wrapper needed)."""
    from src.agents.memory_agent import _build_bound_memory_tools

    return SkillDefinition(
        id="memory",
        group=SkillGroup.INTERNAL,
        description=(
            "Recall, store, list, and forget user memories and preferences. "
            "Also manages conversation sessions (summarize/archive, retrieve context)."
        ),
        tools=_build_bound_memory_tools(user_id),
        instructions=(
            "Use memory skill tools when the user asks what you remember, "
            "wants you to remember something, or asks to forget. "
            "Use summarize_my_conversation to archive the session to long-term memory. "
            "Use get_my_recent_context to retrieve conversation history when needed. "
            "Never fabricate memories — only report what's actually stored."
        ),
        routing_hints=[
            "Memory: 'what do you remember', 'remember this', 'forget that', preferences",
            "NOT for Google Tasks, calendar events, or file storage",
        ],
        read_only=False,
        tags=["memory", "preferences", "conversation"],
    )


def build_organization_skill(user_id: int) -> SkillDefinition:
    """Build the Organization management skill with direct bound tools."""
    from src.agents.org_agent import _build_bound_org_tools

    return SkillDefinition(
        id="organizations",
        group=SkillGroup.INTERNAL,
        description=(
            "Manage organizations (project teams): create/update orgs, add specialized agents, "
            "create/assign/track tasks, schedule org-scoped cron jobs, and create CLI tools. "
            "Organizations are project containers visible in the Atlas Dashboard."
        ),
        tools=_build_bound_org_tools(user_id),
        instructions=(
            "Use organization tools when the user wants to manage project teams, agent organizations, "
            "tracked tasks, org-scoped schedules, or create tools within an organization.\n\n"
            "**CRUD:**\n"
            "- `list_organizations` — show all orgs the user owns\n"
            "- `create_organization` — create a new project/mission container\n"
            "- `update_organization` — change name, goal, description, or status\n"
            "- `get_organization_status` — detailed view with agents and tasks\n"
            "- `add_org_agent` — add a specialized agent to an org\n"
            "- `add_org_task` — create a tracked task in an org\n"
            "- `assign_org_task` — assign/reassign a task to an agent\n"
            "- `complete_org_task` — mark a task as done\n"
            "- `list_org_tasks` — list tasks with optional status filter\n\n"
            "**Scheduling (org-scoped cron/interval/one-shot):**\n"
            "- `schedule_org_task` — create a recurring or one-shot job tied to an org\n"
            "- `list_org_schedules` — show active schedules for an org\n"
            "- `cancel_org_schedule` — cancel an org schedule by job ID\n\n"
            "**Tool Creation (org-scoped CLI tools):**\n"
            "- `create_org_tool` — generate, validate, and register a CLI tool for an org\n\n"
            "These are NOT Google Tasks — they are internal project management tasks "
            "tracked in the Atlas Dashboard. For Google Tasks, use the Google Tasks skill."
        ),
        routing_hints=[
            "Organizations: 'create an organization', 'my organizations', 'org status', 'update org'",
            "Org agents: 'add an agent to', 'team members', 'specialist agent'",
            "Org tasks: 'add a task to org', 'complete task', 'org tasks', 'project tasks', 'assign task'",
            "Org scheduling: 'schedule for org', 'org cron', 'recurring org task', 'org reminder'",
            "Org tools: 'create a tool for org', 'org cli tool', 'build tool for project'",
            "NOT Google Tasks or Google Calendar — those use workspace skills",
        ],
        read_only=False,
        tags=["organization", "org", "project", "team", "agent team", "dashboard",
              "schedule", "cron", "tool", "cli"],
    )


def build_scheduler_skill(user_id: int) -> SkillDefinition:
    """Build the Scheduler skill with direct bound tools (no agent wrapper needed)."""
    from src.agents.scheduler_agent import _build_bound_scheduler_tools

    return SkillDefinition(
        id="scheduler",
        group=SkillGroup.INTERNAL,
        description=(
            "Create, list, pause, or cancel recurring tasks, reminders, and morning briefs. "
            "These are internal Telegram-notification reminders — not Google Calendar events."
        ),
        tools=_build_bound_scheduler_tools(user_id),
        instructions=(
            "Use scheduler skill tools for internal reminders that send Telegram notifications. "
            "Convert natural language time expressions: "
            "'every Monday at 9am' → cron day_of_week=mon hour=9; "
            "'every 30 minutes' → interval minutes=30; "
            "'tomorrow at 3pm' → once run_at=<ISO datetime>. "
            "Confirm schedule details before creating. "
            "For Google Calendar events, use calendar skill tools instead."
        ),
        routing_hints=[
            "Scheduler: 'remind me', 'set a reminder', 'every Monday at 9am', morning brief",
            "Internal Telegram notifications only — NOT Google Calendar events or Google Tasks",
        ],
        read_only=False,
        tags=["scheduler", "reminders", "cron"],
    )
