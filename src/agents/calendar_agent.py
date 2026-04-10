"""Calendar Agent — manages Google Calendar via Google Workspace MCP (as_tool per AD-3)."""

import logging

from agents import Agent, function_tool
from src.integrations.workspace_mcp import call_workspace_tool
from src.agents.persona_mode import PersonaMode, build_persona_mode_addendum
from src.models.router import ModelRole, select_model

logger = logging.getLogger(__name__)

CALENDAR_INSTRUCTIONS = """\
You are a calendar management specialist. You help the user manage their Google Calendar.

## Capabilities
- View today's events, upcoming events, events for a specific date/range
- Check availability and free/busy status
- Create new events with title, time, location, description, attendees
- Update existing events (reschedule, change details)
- Delete/cancel events (ALWAYS confirm with user first)
- Handle recurring events

## Scope — What This Agent Handles
- Google Calendar EVENTS only: meetings, appointments, time blocks, recurring events.
- This agent does NOT handle tasks, reminders, or to-do items. If the user asks for a task,
  reminder, or to-do, tell them this is a calendar-only tool and suggest they rephrase
  as a reminder or task request so the assistant routes it correctly.

## Rules
- When creating or modifying events, ALWAYS confirm details with the user before saving.
- Show times in the user's timezone.
- For "What's on my calendar today/tomorrow?", list events chronologically.
- For event creation, confirm: title, date, start time, end time, and any attendees.
- When deleting events, ALWAYS ask for explicit confirmation.
- If you encounter an auth error, tell the user to run /connect google.

## Output Format
- Use markdown for readability.
- List events with time, title, and location.
- For single event details, show full info including attendees and description.
"""


def _build_connected_calendar_tools(connected_google_email: str) -> list:
    @function_tool(name_override="get_connected_calendar_events")
    async def get_connected_calendar_events(
        time_min: str,
        time_max: str,
        calendar_id: str = "primary",
        max_results: int = 10,
        detailed: bool = True,
        query: str | None = None,
    ) -> str:
        return await call_workspace_tool(
            "get_events",
            {
                "user_google_email": connected_google_email,
                "calendar_id": calendar_id,
                "time_min": time_min,
                "time_max": time_max,
                "max_results": max_results,
                "detailed": detailed,
                "query": query,
            },
        )

    @function_tool(name_override="manage_connected_calendar_event")
    async def manage_connected_calendar_event(
        action: str,
        summary: str | None = None,
        start_time: str | None = None,
        end_time: str | None = None,
        event_id: str | None = None,
        calendar_id: str = "primary",
        description: str | None = None,
        location: str | None = None,
        attendees: list[str] | None = None,
        timezone: str | None = None,
        add_google_meet: bool | None = None,
    ) -> str:
        return await call_workspace_tool(
            "manage_event",
            {
                "user_google_email": connected_google_email,
                "action": action,
                "summary": summary,
                "start_time": start_time,
                "end_time": end_time,
                "event_id": event_id,
                "calendar_id": calendar_id,
                "description": description,
                "location": location,
                "attendees": attendees,
                "timezone": timezone,
                "add_google_meet": add_google_meet,
            },
        )

    return [
        get_connected_calendar_events,
        manage_connected_calendar_event,
    ]


def create_calendar_agent(
    mcp_servers: list = None,
    connected_google_email: str | None = None,
    mode: PersonaMode = "workspace",
) -> Agent:
    """Create the calendar specialist agent."""
    instructions = f"{CALENDAR_INSTRUCTIONS}\n\n{build_persona_mode_addendum(mode)}"
    tools = []
    if connected_google_email:
        instructions = (
            f"{instructions}\n\n"
            f"## Connected Google Account\n"
            f"The user's connected Google email is `{connected_google_email}`. "
            f"For calendar lookups and event changes, prefer the connected-account tools `get_connected_calendar_events` and `manage_connected_calendar_event`. "
            f"When calling Google Workspace tools, pass `user_google_email` as `{connected_google_email}` unless the user explicitly asks for a different connected Google account."
        )
        tools = _build_connected_calendar_tools(connected_google_email)

    # Connected tools use call_workspace_tool() with per-call MCP lifecycle.
    # Don't pass the shared mcp_servers instance to avoid as_tool() conflicts.
    effective_mcp = [] if connected_google_email else (mcp_servers or [])

    selection = select_model(ModelRole.GENERAL)
    return Agent(
        name="CalendarAgent",
        instructions=instructions,
        model=selection.model_id,
        tools=tools,
        mcp_servers=effective_mcp,
    )
