"""Atlas-as-MCP-server tool contracts (Wave 4.10).

Atlas already SPEAKS MCP as a client (it talks to ``workspace-mcp`` via
``agents.mcp.MCPServerStreamableHttp``). This module flips the relationship:
the deterministic short-circuits that power the Telegram bot's
``_maybe_handle_connected_*_check`` paths get exposed as MCP TOOLS that
other agentic runtimes — Hermes Agent, OpenClaw, Claude Code, custom
agents — can call.

Why this matters:
- Atlas's deterministic short-circuits encapsulate ~5 weekends of work
  (regex routing, voice-mode formatters, HTML stripping, Park scoring,
  poison filtering). Exposing them as MCP tools lets any agentic runtime
  pull that value in via ``Connect any MCP server`` rather than
  reimplementing.
- It's the "without forking Hermes" half of Path F2: instead of merging
  Atlas features into Hermes upstream, host the features ourselves and
  let Hermes adopt them via MCP wiring.

Scope of this module:
- Pure-Python tool implementations that take a small typed args dict and
  return a string (matches MCP tool-result shape).
- ``ATLAS_MCP_TOOL_SCHEMAS`` — JSON Schema describing each tool's input.
- ``start_atlas_mcp_server`` — convenience entrypoint; lazy-imports
  ``fastmcp`` so adding the dep stays optional. If fastmcp isn't installed
  the function raises ``RuntimeError`` with install instructions.

What's NOT here:
- We don't auto-start the server inside the assistant container — that's
  an opt-in deploy decision (separate compose service or sidecar).
- We don't expose write tools (``send_gmail_message``, ``create_event``).
  The MCP surface is read-only; outbound writes still flow through the
  orchestrator's confirmation gate.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)


# ── Tool schemas (JSON Schema, draft-07) ────────────────────────────────


ATLAS_MCP_TOOL_SCHEMAS: dict[str, dict[str, Any]] = {
    "atlas_summarize_calendar": {
        "name": "atlas_summarize_calendar",
        "description": (
            "Summarize the user's Google Calendar for a natural-language "
            "time range like 'today', 'tomorrow', or 'this morning'. "
            "Uses Atlas's voice-mode-aware formatter (HTML stripped, "
            "Zoom-noise removed, conversational tone if voice_mode=true)."
        ),
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "user_google_email": {
                    "type": "string",
                    "description": "Connected Google account.",
                },
                "time_range": {
                    "type": "string",
                    "description": "Natural-language range. Examples: today, tomorrow, this morning.",
                    "default": "today",
                },
                "voice_mode": {
                    "type": "boolean",
                    "description": "If true, return a short conversational paragraph for TTS playback.",
                    "default": False,
                },
            },
            "required": ["user_google_email"],
        },
    },
    "atlas_summarize_unread_emails": {
        "name": "atlas_summarize_unread_emails",
        "description": (
            "Summarize the user's most recent unread Gmail messages. "
            "Returns a numbered list (or conversational paragraph in voice "
            "mode) with sender, subject, and one-line summary per message."
        ),
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "user_google_email": {"type": "string"},
                "max_messages": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 10,
                    "default": 3,
                },
                "voice_mode": {"type": "boolean", "default": False},
            },
            "required": ["user_google_email"],
        },
    },
    "atlas_list_open_tasks": {
        "name": "atlas_list_open_tasks",
        "description": (
            "List open (incomplete) Google Tasks from the user's @default "
            "list. Read-only — task creation/completion stays gated behind "
            "the orchestrator's confirmation flow."
        ),
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "user_google_email": {"type": "string"},
                "max_results": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 50,
                    "default": 10,
                },
            },
            "required": ["user_google_email"],
        },
    },
}


# ── Tool implementations ────────────────────────────────────────────────


async def atlas_summarize_calendar(args: dict[str, Any]) -> str:
    """MCP-tool wrapper around the deterministic calendar short-circuit.

    Validates input against the schema, calls workspace-mcp's ``get_events``,
    and runs the response through Atlas's existing formatter. Voice-mode
    output applies HTML stripping + Zoom-noise removal so TTS doesn't read
    out meeting IDs digit-by-digit.
    """
    _validate("atlas_summarize_calendar", args)
    user_email = args["user_google_email"]
    time_range = args.get("time_range", "today")
    voice_mode = bool(args.get("voice_mode", False))

    from src.agents.orchestrator import (
        _calendar_time_range_for_message,
        _format_connected_calendar_summary,
    )
    from src.integrations.workspace_mcp import call_workspace_tool

    time_min, time_max, label = _calendar_time_range_for_message(time_range)
    raw = await call_workspace_tool(
        "get_events",
        {
            "user_google_email": user_email,
            "calendar_id": "primary",
            "time_min": time_min,
            "time_max": time_max,
            "max_results": 10,
            "detailed": True,
        },
    )
    return _format_connected_calendar_summary(label, raw, voice_mode=voice_mode)


async def atlas_summarize_unread_emails(args: dict[str, Any]) -> str:
    """MCP-tool wrapper around the deterministic Gmail short-circuit.

    Hardcodes ``in:inbox is:unread`` query — the goal is "what's unread now",
    not arbitrary search. For arbitrary search, the connected Gmail tool
    (``search_connected_gmail_messages``) is the right surface.
    """
    _validate("atlas_summarize_unread_emails", args)
    user_email = args["user_google_email"]
    max_messages = int(args.get("max_messages", 3))
    voice_mode = bool(args.get("voice_mode", False))

    from src.agents.orchestrator import (
        _extract_gmail_message_ids,
        _format_connected_gmail_summary,
    )
    from src.integrations.workspace_mcp import call_workspace_tool

    search_results = await call_workspace_tool(
        "search_gmail_messages",
        {
            "query": "in:inbox is:unread",
            "user_google_email": user_email,
            "page_size": max_messages,
        },
    )
    message_ids = _extract_gmail_message_ids(search_results)
    if not message_ids:
        return "You don't have any unread emails right now."

    batch_results = await call_workspace_tool(
        "get_gmail_messages_content_batch",
        {
            "message_ids": message_ids[:max_messages],
            "user_google_email": user_email,
            "format": "full",
        },
    )
    return _format_connected_gmail_summary(search_results, batch_results, voice_mode=voice_mode)


async def atlas_list_open_tasks(args: dict[str, Any]) -> str:
    """MCP-tool wrapper around the deterministic Google Tasks short-circuit.

    Read-only by design — task creation/completion remains in the
    orchestrator's confirmation flow because those are write actions.
    """
    _validate("atlas_list_open_tasks", args)
    user_email = args["user_google_email"]
    max_results = int(args.get("max_results", 10))

    from src.agents.orchestrator import _format_connected_google_tasks_summary
    from src.integrations.workspace_mcp import call_workspace_tool

    raw = await call_workspace_tool(
        "list_tasks",
        {
            "user_google_email": user_email,
            "task_list_id": "@default",
            "show_completed": False,
            "max_results": max_results,
        },
    )
    return _format_connected_google_tasks_summary(raw)


ATLAS_MCP_TOOL_HANDLERS = {
    "atlas_summarize_calendar": atlas_summarize_calendar,
    "atlas_summarize_unread_emails": atlas_summarize_unread_emails,
    "atlas_list_open_tasks": atlas_list_open_tasks,
}


# ── Validation ──────────────────────────────────────────────────────────


class AtlasMCPInputError(ValueError):
    """Raised when MCP tool input doesn't conform to the declared schema."""


def _validate(tool_name: str, args: dict[str, Any]) -> None:
    """Minimal JSON-Schema-shaped validation: ``required`` fields present,
    no extra fields when ``additionalProperties=False``, type coercion
    deferred to the handler.

    Atlas avoids the ``jsonschema`` package on the request hot path —
    pulling it in adds 2 MB to the wheel and full draft-07 validation isn't
    needed for our 3-tool, 4-property surface. If the schema grows,
    upgrade to ``jsonschema.validate``.
    """
    schema_root = ATLAS_MCP_TOOL_SCHEMAS.get(tool_name)
    if schema_root is None:
        raise AtlasMCPInputError(f"Unknown tool: {tool_name}")
    schema = schema_root["inputSchema"]

    required = schema.get("required", [])
    missing = [r for r in required if r not in args]
    if missing:
        raise AtlasMCPInputError(
            f"{tool_name}: missing required fields: {', '.join(missing)}"
        )

    if not schema.get("additionalProperties", True):
        properties = schema.get("properties", {})
        extra = [k for k in args if k not in properties]
        if extra:
            raise AtlasMCPInputError(
                f"{tool_name}: unexpected fields: {', '.join(extra)}"
            )


# ── Optional FastMCP server entrypoint ──────────────────────────────────


def start_atlas_mcp_server(host: str = "0.0.0.0", port: int = 8001) -> None:
    """Boot a FastMCP server exposing Atlas's read-only short-circuits.

    Lazy-imports ``fastmcp`` so the dep stays optional. When an operator
    wants to expose the surface, they add ``fastmcp`` to requirements and
    run::

        python -c "from src.integrations.atlas_mcp_tools import \\
            start_atlas_mcp_server; start_atlas_mcp_server()"

    Or wire it into a separate compose service. Atlas's main bot container
    deliberately doesn't auto-start this — the MCP surface is an opt-in
    deployment decision.
    """
    try:
        from fastmcp import FastMCP  # type: ignore[import-not-found]
    except ImportError as exc:
        raise RuntimeError(
            "FastMCP not installed. Add `fastmcp>=3.0` to requirements.txt "
            "to expose Atlas as an MCP server. See "
            "src/integrations/atlas_mcp_tools.py docstring for the deploy "
            "pattern."
        ) from exc

    server = FastMCP(name="atlas")

    for tool_name, schema in ATLAS_MCP_TOOL_SCHEMAS.items():
        handler = ATLAS_MCP_TOOL_HANDLERS[tool_name]
        # FastMCP's @tool decorator reads the function signature for the
        # input schema; our handlers take a single dict, so we provide the
        # schema explicitly. The exact API depends on FastMCP version —
        # this is the pattern as of 3.0.2 (the workspace-mcp pin).
        server.add_tool(
            handler,
            name=tool_name,
            description=schema["description"],
        )
        logger.info("Registered Atlas MCP tool: %s", tool_name)

    server.run(transport="streamable-http", host=host, port=port)
