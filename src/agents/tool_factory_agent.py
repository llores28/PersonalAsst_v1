"""Tool Factory Agent — generates CLI tools on demand (Handoff per AD-3).

This is the only agent that uses Handoff (not as_tool) because tool creation
may require multi-turn conversation with the user for clarification.
"""

import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path

from agents import Agent, function_tool

from src.settings import settings
from src.tools.sandbox import static_analysis, test_tool_in_sandbox

logger = logging.getLogger(__name__)

TOOLS_DIR = Path("tools")

TOOL_FACTORY_INSTRUCTIONS = """\
You are a Tool Factory specialist. You create new CLI tools for the personal assistant.

## Process (CLI-first per HC-2)
1. Understand what the user needs the tool to do
2. Decide the tool type (almost always CLI — see decision tree)
3. Generate the CLI script (argparse, json output)
4. Generate the manifest.json
5. Run static analysis to check for safety issues
6. Test the tool in the sandbox
7. Register it so the orchestrator can use it

## Tool Type Decision Tree
- Can it run as a standalone script? → YES → CLI Tool (default)
- Does it need deep agent integration? → YES → function_tool (rare)
- Do multiple agents need to discover it? → YES → MCP Server (very rare)

## CLI Tool Template
Every CLI tool must:
- Use argparse for argument parsing
- Accept --format json|text for output format
- Print results to stdout
- Print errors to stderr
- Return exit code 0 on success, non-zero on failure
- NOT import subprocess, shutil, ctypes, pickle, os.system
- NOT access environment variables (no os.environ)
- NOT use eval(), exec(), or compile()

## Safety Rules
- Generated code is checked by static analysis before registration
- Tools run in a sandboxed subprocess with empty environment (no API keys)
- Network access is allowed only to hosts listed in allowed_hosts
- Always ask the user to confirm before registering a new tool
"""


@function_tool
async def generate_cli_tool(
    name: str,
    description: str,
    parameters_json: str,
    tool_code: str,
    requires_network: bool = False,
    allowed_hosts: str = "",
) -> str:
    """Generate and register a new CLI tool.

    Args:
        name: Tool name in snake_case (e.g. 'stock_checker')
        description: What the tool does
        parameters_json: JSON string of parameter definitions: {"param_name": {"type": "str", "required": true, "description": "..."}}
        tool_code: The complete Python CLI script code (must use argparse)
        requires_network: Whether the tool needs network access
        allowed_hosts: Comma-separated list of allowed hostnames for network access
    """
    # Validate name
    if not name.replace("_", "").isalnum():
        return "Error: Tool name must be snake_case alphanumeric."

    tool_dir = TOOLS_DIR / name
    if tool_dir.exists():
        return f"Error: Tool '{name}' already exists. Choose a different name."

    # Static analysis on generated code
    violations = static_analysis(tool_code)
    if violations:
        return (
            "⚠️ Code safety check failed:\n"
            + "\n".join(f"  - {v}" for v in violations)
            + "\n\nPlease fix these issues and try again."
        )

    # Parse parameters
    try:
        params = json.loads(parameters_json)
    except json.JSONDecodeError as e:
        return f"Error: Invalid parameters JSON: {e}"

    # Create tool directory and files
    tool_dir.mkdir(parents=True, exist_ok=True)

    # Write CLI script
    cli_path = tool_dir / "cli.py"
    cli_path.write_text(tool_code)

    # Write manifest
    hosts = [h.strip() for h in allowed_hosts.split(",") if h.strip()] if allowed_hosts else []
    manifest = {
        "$schema": "tool-manifest-v1",
        "name": name,
        "version": "1.0.0",
        "description": description,
        "type": "cli",
        "entrypoint": "cli.py",
        "wrapper": "cli.py",
        "parameters": params,
        "output_format": "text",
        "timeout_seconds": 30,
        "requires_approval": False,
        "requires_network": requires_network,
        "allowed_hosts": hosts,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "created_by": "tool_factory",
    }
    manifest_path = tool_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2))

    # Test in sandbox
    success, test_msg = await test_tool_in_sandbox(tool_dir, "cli.py")

    if not success:
        # Clean up on failure
        import shutil
        shutil.rmtree(tool_dir, ignore_errors=True)
        return f"❌ Tool failed sandbox test: {test_msg}\n\nThe tool was not registered."

    # Register in database
    try:
        from sqlalchemy import select
        from src.db.session import async_session
        from src.db.models import Tool

        async with async_session() as session:
            existing = await session.execute(
                select(Tool).where(Tool.name == name)
            )
            if existing.scalar_one_or_none() is None:
                session.add(Tool(
                    name=name,
                    tool_type="cli",
                    description=description,
                    manifest_path=str(manifest_path),
                    is_active=True,
                    created_by="tool_factory",
                ))
                await session.commit()
    except Exception as e:
        logger.error("Failed to register tool in DB: %s", e)

    return (
        f"✅ Tool **{name}** created and registered!\n\n"
        f"**Description:** {description}\n"
        f"**Location:** tools/{name}/\n"
        f"**Sandbox test:** {test_msg}\n\n"
        "The tool is now available for use. The orchestrator will pick it up automatically."
    )


@function_tool
async def list_available_tools() -> str:
    """List all registered tools with their status."""
    from sqlalchemy import select
    from src.db.session import async_session
    from src.db.models import Tool

    async with async_session() as session:
        result = await session.execute(select(Tool).where(Tool.is_active == True))
        tools = result.scalars().all()

    if not tools:
        return "No custom tools registered yet."

    lines = [f"**Registered Tools ({len(tools)}):**\n"]
    for t in tools:
        lines.append(
            f"• **{t.name}** ({t.tool_type}) — {t.description}\n"
            f"  Used {t.use_count}x | Created by: {t.created_by}"
        )
    return "\n".join(lines)


@function_tool
async def review_tool_code(code: str) -> str:
    """Review generated code for safety issues before registration."""
    violations = static_analysis(code)
    if violations:
        return (
            "⚠️ Safety issues found:\n"
            + "\n".join(f"  - {v}" for v in violations)
        )
    return "✅ Code passes static analysis. Safe to register."


def create_tool_factory_agent() -> Agent:
    """Create the Tool Factory specialist agent.

    This agent uses Handoff (not as_tool) because tool creation
    may require multi-turn interaction with the user.
    """
    return Agent(
        name="ToolFactoryAgent",
        instructions=TOOL_FACTORY_INSTRUCTIONS,
        model=settings.model_code_gen,
        tools=[generate_cli_tool, list_available_tools, review_tool_code],
    )
