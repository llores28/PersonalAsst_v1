"""System Agent Registry - Catalog of all built-in Atlas agents.

This module provides a read-only registry of system agents that power Atlas.
These agents are internal/built-in and cannot be modified by users.
"""

from pydantic import BaseModel
from typing import Optional


class SystemAgentInfo(BaseModel):
    """Read-only information about a built-in system agent."""
    id: str  # e.g., "email_agent", "calendar_agent"
    name: str  # Display name: "Email Agent"
    description: str  # What this agent does
    category: str  # "google_workspace", "internal", "utility"
    capabilities: list[str]  # List of what it can do
    tool_count: int  # Number of tools it provides
    status: str  # "active" | "beta" | "deprecated"


# Registry of all system agents
SYSTEM_AGENTS: list[SystemAgentInfo] = [
    SystemAgentInfo(
        id="email_agent",
        name="Email Agent",
        description="Manages Gmail operations including sending, drafting, searching, and organizing emails. Handles threading, labels, and attachments.",
        category="google_workspace",
        capabilities=["send_email", "draft_email", "search_emails", "manage_labels", "handle_attachments"],
        tool_count=6,
        status="active"
    ),
    SystemAgentInfo(
        id="calendar_agent",
        name="Calendar Agent",
        description="Manages Google Calendar events, meetings, and scheduling. Creates events, checks availability, and handles recurring meetings.",
        category="google_workspace",
        capabilities=["create_events", "check_availability", "list_events", "update_events"],
        tool_count=2,
        status="active"
    ),
    SystemAgentInfo(
        id="drive_agent",
        name="Drive Agent",
        description="Manages Google Drive files and folders. Uploads, downloads, searches, and organizes files with proper permissions.",
        category="google_workspace",
        capabilities=["upload_files", "download_files", "search_files", "manage_folders", "set_permissions"],
        tool_count=7,
        status="active"
    ),
    SystemAgentInfo(
        id="tasks_agent",
        name="Tasks Agent",
        description="Manages Google Tasks and to-do lists. Creates, completes, and organizes tasks with due dates and priorities.",
        category="google_workspace",
        capabilities=["create_tasks", "complete_tasks", "list_tasks", "organize_lists"],
        tool_count=4,
        status="active"
    ),
    SystemAgentInfo(
        id="docs_agent",
        name="Docs Agent",
        description="Manages Google Docs operations. Creates, edits, formats, and searches documents.",
        category="google_workspace",
        capabilities=["create_docs", "edit_docs", "format_content", "search_docs"],
        tool_count=7,
        status="active"
    ),
    SystemAgentInfo(
        id="sheets_agent",
        name="Sheets Agent",
        description="Manages Google Sheets operations. Creates spreadsheets, manipulates data, formulas, and charts.",
        category="google_workspace",
        capabilities=["create_sheets", "edit_cells", "manage_formulas", "create_charts"],
        tool_count=6,
        status="active"
    ),
    SystemAgentInfo(
        id="slides_agent",
        name="Slides Agent",
        description="Manages Google Slides presentations. Creates slides, adds content, and formats presentations.",
        category="google_workspace",
        capabilities=["create_presentations", "add_slides", "format_slides"],
        tool_count=5,
        status="active"
    ),
    SystemAgentInfo(
        id="contacts_agent",
        name="Contacts Agent",
        description="Manages Google Contacts. Searches, creates, and organizes contact information.",
        category="google_workspace",
        capabilities=["search_contacts", "create_contacts", "organize_groups"],
        tool_count=4,
        status="active"
    ),
    SystemAgentInfo(
        id="memory_agent",
        name="Memory Agent",
        description="Manages long-term memory storage and retrieval. Stores facts, recalls information, and maintains conversation context.",
        category="internal",
        capabilities=["store_memory", "recall_memory", "list_memories", "forget_memory", "summarize_conversations"],
        tool_count=7,
        status="active"
    ),
    SystemAgentInfo(
        id="scheduler_agent",
        name="Scheduler Agent",
        description="Manages scheduled jobs and reminders. Creates cron jobs, one-time reminders, and morning briefings.",
        category="internal",
        capabilities=["create_reminders", "schedule_jobs", "list_schedules", "cancel_schedules"],
        tool_count=4,
        status="active"
    ),
    SystemAgentInfo(
        id="reflector_agent",
        name="Reflector Agent",
        description="Quality assurance agent that evaluates responses and provides feedback for continuous improvement.",
        category="internal",
        capabilities=["evaluate_quality", "provide_feedback", "track_performance"],
        tool_count=0,
        status="active"
    ),
    SystemAgentInfo(
        id="repair_agent",
        name="Repair Agent",
        description="Diagnostic and repair agent that helps troubleshoot issues and suggest fixes for the system.",
        category="internal",
        capabilities=["diagnose_issues", "suggest_fixes", "run_repairs"],
        tool_count=0,
        status="active"
    ),
    SystemAgentInfo(
        id="safety_agent",
        name="Safety Agent",
        description="Security guardrail agent that monitors for PII, sensitive data, and policy violations in inputs and outputs.",
        category="internal",
        capabilities=["check_input_safety", "check_output_safety", "enforce_policies"],
        tool_count=0,
        status="active"
    ),
    SystemAgentInfo(
        id="curator_agent",
        name="Curator Agent",
        description="Content curation agent that organizes and summarizes information from various sources.",
        category="internal",
        capabilities=["summarize_content", "organize_info", "extract_insights"],
        tool_count=0,
        status="active"
    ),
    SystemAgentInfo(
        id="skill_factory_agent",
        name="Skill Factory Agent",
        description="Creates custom skills through guided interviews. Helps users define new capabilities for Atlas.",
        category="utility",
        capabilities=["interview_users", "generate_skills", "validate_skills"],
        tool_count=0,
        status="active"
    ),
    SystemAgentInfo(
        id="tool_factory_agent",
        name="Tool Factory Agent",
        description="Creates custom CLI tools through guided interviews. Generates safe, sandboxed tools for specific tasks.",
        category="utility",
        capabilities=["interview_users", "generate_tools", "validate_tools"],
        tool_count=0,
        status="active"
    ),
    SystemAgentInfo(
        id="org_agent",
        name="Organization Agent",
        description="Manages organizations, agents, and tasks. Creates projects and coordinates specialized agent teams.",
        category="utility",
        capabilities=["create_orgs", "manage_agents", "assign_tasks", "track_progress"],
        tool_count=13,
        status="active"
    ),
    SystemAgentInfo(
        id="persona_interview_agent",
        name="Persona Interview Agent",
        description="Conducts structured interviews to build a deep psychological profile. Uses Stanford OCEAN framework for personality assessment.",
        category="utility",
        capabilities=["conduct_interviews", "analyze_personality", "generate_profile"],
        tool_count=0,
        status="active"
    ),
]


def get_system_agents() -> list[SystemAgentInfo]:
    """Return all system agents."""
    return SYSTEM_AGENTS


def get_system_agent_by_id(agent_id: str) -> Optional[SystemAgentInfo]:
    """Get a specific system agent by ID."""
    for agent in SYSTEM_AGENTS:
        if agent.id == agent_id:
            return agent
    return None


def get_agents_by_category(category: str) -> list[SystemAgentInfo]:
    """Get system agents filtered by category."""
    return [agent for agent in SYSTEM_AGENTS if agent.category == category]
