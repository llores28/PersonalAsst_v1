"""Google Tasks Agent — manages Google Tasks via Google Workspace MCP (as_tool per AD-3).

This agent handles task lists and task items via the Google Tasks API,
which is separate from both Google Calendar events and the internal
APScheduler-based reminder system.

Routing rule: "add to my task" / "todo" / "to-do" requests come here
when Google Tasks is the desired backend.  Internal reminders that
trigger a Telegram notification go to the SchedulerAgent instead.
"""

import logging

from agents import Agent, function_tool
from src.integrations.workspace_mcp import call_workspace_tool
from src.agents.persona_mode import PersonaMode, build_persona_mode_addendum
from src.models.router import ModelRole, select_model

logger = logging.getLogger(__name__)

TASKS_INSTRUCTIONS = """\
You are a Google Tasks specialist. You help the user manage their task lists and to-do items
using Google Tasks (the Tasks sidebar in Gmail / Google Calendar).

## Capabilities
- List all task lists and tasks within a list
- Create, update, complete, and delete tasks
- Create and manage task lists
- Set due dates on tasks

## Scope — What This Agent Handles
- Google Tasks ONLY: to-do items, task lists, due dates.
- This agent does NOT create calendar events. If the user asks for a meeting or
  appointment, tell them this is a task-only tool.
- This agent does NOT manage internal reminders or scheduled Telegram notifications.
  Those go through the scheduler.

## Rules
- When creating a task, confirm title and optional due date with the user.
- When deleting tasks, ALWAYS ask for explicit confirmation.
- Show tasks in a clear numbered list with title, status, and due date.
- If a Google Tasks tool returns an error, show the exact error to the user.
- For auth, permission, or scope issues, tell the user to run `/connect google` again.

## Output Format
- Use markdown for readability.
- List tasks with checkbox-style status: ☐ (pending) or ✅ (completed).
"""


def _format_google_tasks_error(operation: str, connected_google_email: str, exc: Exception) -> str:
    error_text = str(exc).strip() or exc.__class__.__name__
    lowered = error_text.lower()

    if any(keyword in lowered for keyword in (
        "auth",
        "oauth",
        "permission",
        "forbidden",
        "unauthorized",
        "scope",
        "token",
        "invalid_grant",
        "insufficient",
    )):
        reconnect_hint = (
            f" Google Tasks may need to be reconnected with the latest permissions. "
            f"Run `/connect google {connected_google_email}` and approve Google Tasks access again."
        )
    else:
        reconnect_hint = (
            f" If this keeps happening, run `/connect google {connected_google_email}` again "
            f"to refresh Google Workspace access."
        )

    return f"Google Tasks error while trying to {operation}: {error_text}.{reconnect_hint}"


def _ensure_google_tasks_tool_success(result_text: str) -> str:
    lowered = result_text.strip().lower()
    if not lowered:
        return result_text

    if lowered.startswith("error calling tool"):
        raise RuntimeError(result_text.strip())
    if "userinputerror:" in lowered:
        raise RuntimeError(result_text.strip())
    if "input error in manage_task" in lowered:
        raise RuntimeError(result_text.strip())
    if "input error in list_tasks" in lowered:
        raise RuntimeError(result_text.strip())
    if "input error in list_task_lists" in lowered:
        raise RuntimeError(result_text.strip())

    return result_text


def _build_connected_tasks_tools(connected_google_email: str) -> list:
    """Build bound Google Tasks tools for the connected account."""

    @function_tool(name_override="list_my_task_lists")
    async def list_my_task_lists() -> str:
        """List all Google Task lists for the connected user."""
        try:
            result = await call_workspace_tool(
                "list_task_lists",
                {"user_google_email": connected_google_email},
            )
            return _ensure_google_tasks_tool_success(result)
        except Exception as exc:
            logger.exception("Google Tasks list_task_lists failed for %s: %s", connected_google_email, exc)
            return _format_google_tasks_error("list task lists", connected_google_email, exc)

    @function_tool(name_override="list_my_tasks")
    async def list_my_tasks(
        task_list_id: str = "@default",
        show_completed: bool = False,
        max_results: int = 50,
    ) -> str:
        """List tasks in a Google Task list."""
        try:
            result = await call_workspace_tool(
                "list_tasks",
                {
                    "user_google_email": connected_google_email,
                    "task_list_id": task_list_id,
                    "show_completed": show_completed,
                    "max_results": max_results,
                },
            )
            return _ensure_google_tasks_tool_success(result)
        except Exception as exc:
            logger.exception("Google Tasks list_tasks failed for %s: %s", connected_google_email, exc)
            return _format_google_tasks_error("list tasks", connected_google_email, exc)

    @function_tool(name_override="manage_my_task")
    async def manage_my_task(
        action: str,
        task_list_id: str = "@default",
        task_id: str | None = None,
        title: str | None = None,
        notes: str | None = None,
        due: str | None = None,
        status: str | None = None,
    ) -> str:
        """Create, update, complete, or delete a Google Task.

        Args:
            action: "create", "update", "complete", or "delete"
            task_list_id: Task list ID (use "@default" for the primary list)
            task_id: Required for update/complete/delete
            title: Task title (required for create)
            notes: Optional task notes/description
            due: Optional due date in ISO format (e.g. "2026-03-19T09:00:00Z")
            status: "needsAction" or "completed"
        """
        operation = {
            "create": "create the task",
            "update": "update the task",
            "complete": "complete the task",
            "delete": "delete the task",
        }.get(action, f"run `{action}` on the task")

        tool_action = action
        tool_status = status
        if action == "complete":
            tool_action = "update"
            tool_status = "completed"

        try:
            result = await call_workspace_tool(
                "manage_task",
                {
                    "user_google_email": connected_google_email,
                    "action": tool_action,
                    "task_list_id": task_list_id,
                    "task_id": task_id,
                    "title": title,
                    "notes": notes,
                    "due": due,
                    "status": tool_status,
                },
            )
            return _ensure_google_tasks_tool_success(result)
        except Exception as exc:
            logger.exception("Google Tasks manage_task failed for %s: %s", connected_google_email, exc)
            return _format_google_tasks_error(operation, connected_google_email, exc)

    @function_tool(name_override="manage_my_task_list")
    async def manage_my_task_list(
        action: str,
        title: str | None = None,
        task_list_id: str | None = None,
    ) -> str:
        """Create, update, or delete a Google Task list.

        Args:
            action: "create", "update", or "delete"
            title: List title (required for create/update)
            task_list_id: Required for update/delete
        """
        operation = {
            "create": "create the task list",
            "update": "update the task list",
            "delete": "delete the task list",
        }.get(action, f"run `{action}` on the task list")

        try:
            result = await call_workspace_tool(
                "manage_task_list",
                {
                    "user_google_email": connected_google_email,
                    "action": action,
                    "title": title,
                    "task_list_id": task_list_id,
                },
            )
            return _ensure_google_tasks_tool_success(result)
        except Exception as exc:
            logger.exception("Google Tasks manage_task_list failed for %s: %s", connected_google_email, exc)
            return _format_google_tasks_error(operation, connected_google_email, exc)

    return [
        list_my_task_lists,
        list_my_tasks,
        manage_my_task,
        manage_my_task_list,
    ]


def create_tasks_agent(
    mcp_servers: list = None,
    connected_google_email: str | None = None,
    mode: PersonaMode = "workspace",
) -> Agent:
    """Create the Google Tasks specialist agent."""
    instructions = f"{TASKS_INSTRUCTIONS}\n\n{build_persona_mode_addendum(mode)}"
    tools = []
    if connected_google_email:
        instructions = (
            f"{instructions}\n\n"
            f"## Connected Google Account\n"
            f"The user's connected Google email is `{connected_google_email}`. "
            f"Use the bound tools `list_my_task_lists`, `list_my_tasks`, `manage_my_task`, and `manage_my_task_list`. "
            f"Do not ask the user for their email."
        )
        tools = _build_connected_tasks_tools(connected_google_email)

    # Connected tools use call_workspace_tool() with per-call MCP lifecycle.
    # Don't pass the shared mcp_servers instance to avoid as_tool() conflicts.
    effective_mcp = [] if connected_google_email else (mcp_servers or [])

    selection = select_model(ModelRole.GENERAL)
    return Agent(
        name="TasksAgent",
        instructions=instructions,
        model=selection.model_id,
        tools=tools,
        mcp_servers=effective_mcp,
    )
