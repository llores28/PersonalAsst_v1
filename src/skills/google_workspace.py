"""Google Workspace skill builders — Gmail, Calendar, Tasks, Drive, Docs, Sheets, Slides, Contacts.

Each function returns a SkillDefinition that wraps the existing
``_build_connected_*_tools()`` closure builders from the specialist agent modules.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from src.skills.definition import SkillDefinition, SkillGroup

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


def build_gmail_skill(connected_google_email: str) -> SkillDefinition:
    """Build the Gmail skill with connected-account tools."""
    from src.agents.email_agent import _build_connected_gmail_tools

    return SkillDefinition(
        id="gmail",
        group=SkillGroup.GOOGLE_WORKSPACE,
        description="Read, search, draft, send, and reply to emails via Gmail.",
        tools=_build_connected_gmail_tools(connected_google_email),
        instructions=(
            "Use the direct Gmail skill tools for all email work. "
            "ALWAYS draft before sending. Never forward without explicit user request."
        ),
        routing_hints=[
            "Email: inbox checks, search, read, draft, reply, send, filter",
            "NOT for calendar events, tasks, files, or contacts",
        ],
        requires_connection=True,
        tags=["email", "gmail", "workspace"],
    )


def build_calendar_skill(connected_google_email: str) -> SkillDefinition:
    """Build the Calendar skill with connected-account tools."""
    from src.agents.calendar_agent import _build_connected_calendar_tools

    return SkillDefinition(
        id="calendar",
        group=SkillGroup.GOOGLE_WORKSPACE,
        description=(
            "View, create, update, and delete Google Calendar events. "
            "ONLY for calendar events (meetings, appointments, time blocks). "
            "Do NOT use for tasks, reminders, or to-do items."
        ),
        tools=_build_connected_calendar_tools(connected_google_email),
        instructions=(
            "Use calendar skill tools ONLY for calendar events. "
            "NEVER use calendar tools for tasks, reminders, or to-do items."
        ),
        routing_hints=[
            "Calendar events, meetings, appointments, schedule blocks",
            "NOT for tasks, reminders, to-do items, or Telegram notifications",
        ],
        requires_connection=True,
        tags=["calendar", "workspace"],
    )


def build_tasks_skill(connected_google_email: str) -> SkillDefinition:
    """Build the Google Tasks skill with connected-account tools."""
    from src.agents.tasks_agent import _build_connected_tasks_tools

    return SkillDefinition(
        id="google_tasks",
        group=SkillGroup.GOOGLE_WORKSPACE,
        description=(
            "Create, list, update, and complete Google Tasks (to-do items). "
            "For internal reminders that send Telegram notifications, use the scheduler skill instead."
        ),
        tools=_build_connected_tasks_tools(connected_google_email),
        instructions=(
            "Use Google Tasks skill tools when the user says 'add to my task', "
            "'todo', 'to-do list', or wants to manage Google Tasks."
        ),
        routing_hints=[
            "Google Tasks: 'add to my task', 'todo', 'to-do list', 'complete task'",
            "NOT for calendar events or internal Telegram reminders",
        ],
        requires_connection=True,
        tags=["tasks", "todo", "workspace"],
    )


def build_drive_skill(connected_google_email: str) -> SkillDefinition:
    """Build the Drive skill with direct connected tools."""
    from src.agents.drive_agent import _build_connected_drive_tools

    return SkillDefinition(
        id="drive",
        group=SkillGroup.GOOGLE_WORKSPACE,
        description="Search, create, move, rename, organize, and share files on Google Drive.",
        tools=_build_connected_drive_tools(connected_google_email),
        instructions=(
            "Use the direct Drive skill tools for file work. "
            "ALWAYS confirm before sharing files or deleting. "
            "Use ID-based Drive mutations for moves and renames whenever possible. "
            "For search results, show file name, type, and last modified date."
        ),
        routing_hints=[
            "Drive: file search, upload, download, create folders, move, rename, share, organize",
            "NOT for editing document content (use Docs), spreadsheet data (use Sheets), or slides (use Slides)",
        ],
        requires_connection=True,
        tags=["drive", "files", "workspace"],
    )


def build_sheets_skill(connected_google_email: str) -> SkillDefinition:
    """Build the Google Sheets skill with direct connected tools."""
    from src.agents.sheets_agent import _build_connected_sheets_tools

    return SkillDefinition(
        id="google_sheets",
        group=SkillGroup.GOOGLE_WORKSPACE,
        description=(
            "Create, read, update, and append data in Google Sheets spreadsheets. "
            "Use for spreadsheet work — not for documents or slides."
        ),
        tools=_build_connected_sheets_tools(connected_google_email),
        instructions=(
            "Use the Google Sheets skill tools for spreadsheet work. "
            "When reading data, specify the range clearly (e.g., 'Sheet1!A1:D10'). "
            "When writing data, format values as a JSON array of arrays."
        ),
        routing_hints=[
            "Sheets: spreadsheet, cells, rows, columns, data table, CSV-like data",
            "NOT for documents (use Docs) or presentations (use Slides)",
        ],
        requires_connection=True,
        tags=["sheets", "spreadsheet", "workspace"],
    )


def build_docs_skill(connected_google_email: str) -> SkillDefinition:
    """Build the Google Docs skill with direct connected tools."""
    from src.agents.docs_agent import _build_connected_docs_tools

    return SkillDefinition(
        id="google_docs",
        group=SkillGroup.GOOGLE_WORKSPACE,
        description=(
            "Search, read, create, edit, and export Google Docs. "
            "Use for document work — not for spreadsheets or slides."
        ),
        tools=_build_connected_docs_tools(connected_google_email),
        instructions=(
            "Use the Google Docs skill tools for document work. "
            "When reading a doc, prefer the markdown format for readability. "
            "When creating a doc, confirm the title with the user first."
        ),
        routing_hints=[
            "Docs: documents, create doc, read doc, edit doc, find-and-replace, export PDF",
            "NOT for spreadsheets (use Sheets) or presentations (use Slides)",
        ],
        requires_connection=True,
        tags=["docs", "documents", "workspace"],
    )


def build_slides_skill(connected_google_email: str) -> SkillDefinition:
    """Build the Google Slides skill with direct connected tools."""
    from src.agents.slides_agent import _build_connected_slides_tools

    return SkillDefinition(
        id="google_slides",
        group=SkillGroup.GOOGLE_WORKSPACE,
        description=(
            "Create, read, and update Google Slides presentations. "
            "Use for presentation work — not for documents or spreadsheets."
        ),
        tools=_build_connected_slides_tools(connected_google_email),
        instructions=(
            "Use the Google Slides skill tools for presentation work. "
            "When creating a presentation, confirm the title with the user first. "
            "Use batch_update for adding slides, text, images, and shapes."
        ),
        routing_hints=[
            "Slides: presentation, slide deck, create presentation, add slides",
            "NOT for documents (use Docs) or spreadsheets (use Sheets)",
        ],
        requires_connection=True,
        tags=["slides", "presentation", "workspace"],
    )


def build_contacts_skill(connected_google_email: str) -> SkillDefinition:
    """Build the Google Contacts skill with direct connected tools."""
    from src.agents.contacts_agent import _build_connected_contacts_tools

    return SkillDefinition(
        id="google_contacts",
        group=SkillGroup.GOOGLE_WORKSPACE,
        description=(
            "List, search, create, update, and delete Google Contacts. "
            "Use for contact/people lookup — phone numbers, emails, organizations."
        ),
        tools=_build_connected_contacts_tools(connected_google_email),
        instructions=(
            "Use the Google Contacts skill tools for people lookup. "
            "ALWAYS confirm before deleting contacts. "
            "When creating contacts, confirm the details with the user first."
        ),
        routing_hints=[
            "Contacts: people lookup, phone number, email address, address book, 'who is'",
            "NOT for email sending (use Gmail) or calendar invites (use Calendar)",
        ],
        requires_connection=True,
        tags=["contacts", "people", "workspace"],
    )
