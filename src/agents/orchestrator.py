"""Orchestrator agent — the main triage/persona agent."""

import logging
import os
from pathlib import Path

import yaml
from agents import Agent, Runner, WebSearchTool, InputGuardrail, OutputGuardrail

from src.settings import settings
from src.agents.safety_agent import safety_check_guardrail, pii_check_guardrail
from src.agents.email_agent import create_email_agent
from src.agents.calendar_agent import create_calendar_agent
from src.agents.drive_agent import create_drive_agent
from src.agents.memory_agent import create_memory_agent
from src.agents.scheduler_agent import create_scheduler_agent
from src.agents.tool_factory_agent import create_tool_factory_agent
from src.integrations.workspace_mcp import create_workspace_mcp_server, is_google_configured

logger = logging.getLogger(__name__)

def _load_persona_config() -> dict:
    """Load persona from config file (fallback when DB persona not yet created)."""
    config_path = Path("config/persona_default.yaml")
    if config_path.exists():
        with open(config_path) as f:
            return yaml.safe_load(f)
    return {
        "assistant_name": settings.default_assistant_name,
        "personality": {
            "traits": ["helpful", "proactive", "concise"],
            "style": settings.default_persona_style,
        },
    }


def build_persona_prompt(user_name: str = "there") -> str:
    """Build a static persona prompt (Phase 1-2 fallback)."""
    from src.memory.persona import PERSONA_TEMPLATE

    config = _load_persona_config()
    traits = config.get("personality", {}).get("traits", ["helpful"])
    style = config.get("personality", {}).get("style", settings.default_persona_style)

    return PERSONA_TEMPLATE.format(
        name=config.get("assistant_name", settings.default_assistant_name),
        user_name=user_name,
        personality_traits=", ".join(traits) if isinstance(traits, list) else str(traits),
        communication_style=style,
        user_preferences="(Still learning your preferences)",
        procedural_memories="(No learned workflows yet)",
        recent_context="(New conversation)",
    )


async def create_orchestrator_async(user_id: int, user_name: str = "there") -> Agent:
    """Create the orchestrator with dynamic Mem0-backed persona (Phase 3+)."""
    from src.memory.persona import build_dynamic_persona_prompt

    try:
        persona_prompt = await build_dynamic_persona_prompt(user_id, user_name)
    except Exception as e:
        logger.warning("Failed to load dynamic persona, using static fallback: %s", e)
        persona_prompt = build_persona_prompt(user_name)

    tools = [WebSearchTool()]

    # Phase 3: Memory Agent
    memory_agent = create_memory_agent()
    tools.append(
        memory_agent.as_tool(
            tool_name="manage_memory",
            tool_description=(
                "Recall what you know about the user, store new preferences or facts, "
                "forget specific memories, or list all memories. Use this when the user "
                "asks what you remember, wants you to remember something, or asks to forget."
            ),
        )
    )

    # Phase 4: Scheduler Agent
    scheduler_agent = create_scheduler_agent()
    tools.append(
        scheduler_agent.as_tool(
            tool_name="manage_schedules",
            tool_description=(
                "Create, list, pause, or cancel recurring tasks and reminders. "
                "Use this when the user asks to schedule something, set a reminder, "
                "create a morning brief, or manage existing schedules."
            ),
        )
    )

    # Phase 5: Load dynamic tools from registry
    try:
        from src.tools.registry import get_registry
        registry = await get_registry()
        dynamic_tools = await registry.load_all()
        tools.extend(dynamic_tools)
        if dynamic_tools:
            logger.info("%d dynamic tools loaded from registry", len(dynamic_tools))
    except Exception as e:
        logger.warning("Failed to load dynamic tools: %s", e)

    # Phase 2: Wire in Google Workspace specialist agents if configured
    mcp_servers = []
    workspace_mcp = create_workspace_mcp_server()
    if workspace_mcp is not None:
        mcp_servers.append(workspace_mcp)

    if is_google_configured():
        email_agent = create_email_agent(mcp_servers=mcp_servers)
        calendar_agent = create_calendar_agent(mcp_servers=mcp_servers)
        drive_agent = create_drive_agent(mcp_servers=mcp_servers)

        tools.extend([
            email_agent.as_tool(
                tool_name="manage_email",
                tool_description="Read, search, draft, send, and reply to emails via Gmail. Use this for any email-related requests.",
            ),
            calendar_agent.as_tool(
                tool_name="manage_calendar",
                tool_description="View, create, update, and delete Google Calendar events. Use this for scheduling and calendar queries.",
            ),
            drive_agent.as_tool(
                tool_name="manage_drive",
                tool_description="Search, upload, download, and share files on Google Drive. Use this for file-related requests.",
            ),
        ])
        logger.info("Google Workspace agents registered with orchestrator")
    else:
        logger.info("Google Workspace not configured — specialist agents not loaded")

    # Phase 5: Tool Factory Agent (Handoff — only agent that gets handoff per AD-3)
    tool_factory_agent = create_tool_factory_agent()

    return Agent(
        name="PersonalAssistant",
        instructions=persona_prompt,
        model=settings.model_orchestrator,
        tools=tools,
        handoffs=[tool_factory_agent],
        input_guardrails=[
            InputGuardrail(guardrail_function=safety_check_guardrail),
        ],
        output_guardrails=[
            OutputGuardrail(guardrail_function=pii_check_guardrail),
        ],
    )


def create_orchestrator(user_name: str = "there") -> Agent:
    """Create orchestrator with static persona (sync fallback for tests)."""
    persona_prompt = build_persona_prompt(user_name)
    return Agent(
        name="PersonalAssistant",
        instructions=persona_prompt,
        model=settings.model_orchestrator,
        tools=[WebSearchTool()],
        input_guardrails=[
            InputGuardrail(guardrail_function=safety_check_guardrail),
        ],
        output_guardrails=[
            OutputGuardrail(guardrail_function=pii_check_guardrail),
        ],
    )


async def run_orchestrator(user_telegram_id: int, user_message: str) -> str:
    """Run the orchestrator agent on a user message and return the response text.

    Phase 3: Uses dynamic persona, conversation sessions, and reflector.
    """
    from src.db.session import async_session
    from src.db.models import User
    from src.memory.conversation import add_turn, get_session_context
    from src.agents.reflector_agent import reflect_on_interaction
    from sqlalchemy import select

    # Look up user
    user_name = "there"
    user_db_id = None
    async with async_session() as session:
        result = await session.execute(
            select(User).where(User.telegram_id == user_telegram_id)
        )
        user = result.scalar_one_or_none()
        if user:
            user_name = user.display_name or "there"
            user_db_id = user.id

    # Record user message in conversation session
    await add_turn(user_telegram_id, "user", user_message)

    # Create orchestrator with dynamic persona (Mem0 + conversation context)
    agent = await create_orchestrator_async(user_telegram_id, user_name)

    try:
        result = await Runner.run(agent, user_message)
        response_text = result.final_output

        # Record assistant response in conversation session
        await add_turn(user_telegram_id, "assistant", response_text)

        # Run reflector asynchronously (don't block the response)
        import asyncio
        asyncio.create_task(
            _run_reflector_background(
                user_message, response_text, str(user_telegram_id)
            )
        )

        return response_text
    except Exception as e:
        logger.exception("Orchestrator run failed: %s", e)
        raise


async def _run_reflector_background(
    user_message: str, assistant_response: str, user_id: str
) -> None:
    """Run the reflector in the background without blocking the main response."""
    try:
        from src.agents.reflector_agent import reflect_on_interaction
        reflection = await reflect_on_interaction(user_message, assistant_response, user_id)
        score = reflection.get("quality_score", 0.5)
        if score < 0.4:
            logger.warning("Low quality interaction for user %s (score: %.1f)", user_id, score)
    except Exception as e:
        logger.debug("Reflector background task failed (non-critical): %s", e)
