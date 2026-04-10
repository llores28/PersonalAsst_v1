"""Tool Factory Agent — generates CLI tools on demand (Handoff per AD-3).

This is the only agent that uses Handoff (not as_tool) because tool creation
may require multi-turn conversation with the user for clarification.
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from agents import Agent, function_tool

from src.models.router import ModelRole, select_model
from src.tools.sandbox import static_analysis, test_tool_in_sandbox

logger = logging.getLogger(__name__)

TOOLS_DIR = Path("src/tools/plugins")

TOOL_FACTORY_INSTRUCTIONS = """\
You are a Tool Factory specialist. You design and create new capabilities for the personal assistant.

## Audit-First Process
1. Understand the user outcome and the job to be done
2. Fill the audit-first capability spec from `docs/AUDIT_FIRST_SPECIALIST_TEMPLATE.md`
3. Build a scenario matrix covering happy path, ambiguity, follow-up confirmations, retries, and failures
4. Build a routing boundary matrix showing what should route here vs elsewhere
5. Produce a runtime wireframe covering guardrails, direct handlers, pending state, tool/MCP execution, and user-facing errors
6. Decide the delivery shape (CLI, function_tool, MCP, specialist agent, or hybrid)
7. Only then generate implementation artifacts and tests
8. Name the audit coverage and regression tests that will prove the capability works

## Tool Type Decision Tree
- Can it run as a standalone script with deterministic inputs/outputs? → YES → CLI Tool (default)
- Does it need deep agent integration but not multi-agent discovery? → YES → function_tool
- Do multiple agents or external clients need to discover it? → YES → MCP Server
- Does it need multi-turn clarification, routing rules, or owned conversation state? → YES → specialist agent
- If routing, approvals, or pending state are unclear, stop and clarify before generating code

## Required Planning Output Before Code
Every proposal must include:
- Capability classification
- Scenario matrix summary
- Routing boundary summary
- Runtime wireframe summary
- Audit plan summary
- Implementation plan

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

## Specialist / MCP / Tool Design Rules
- Do not force a CLI when the scenario matrix shows the capability needs a specialist agent or MCP contract
- Name the authoritative entry point that will own the capability
- Define pending-state behavior for follow-up phrases like `yes`, `send it`, `retry`, or `go ahead`
- Define targeted error messages instead of generic fallback apologies
- If the capability touches Google Workspace or another MCP-backed surface, specify how direct contracts and routed regressions will be audited
- If a write path cannot be audited with a cleanup-safe canary, add deterministic local contract checks and explicit routed regression tests

## Credential Management
- Tools that need API keys or passwords declare them in manifest.json ``credentials`` field
- Credentials are stored in a Redis vault (never in .env or code)
- After creating a tool that needs credentials, tell the user to run:
  ``/tools credentials set <tool_name> <key> <value>`` in Telegram
- CLI tools receive credentials as TOOL_* environment variables via the sandbox
- Function-type tools call ``get_credentials(tool_name)`` from src.tools.credentials

## Safety Rules
- Generated code is checked by static analysis before registration
- CLI tools run in a sandboxed subprocess with minimal environment + vault credentials
- Function-type tools run in-process but must use the credential vault (never hardcode secrets)
- Network access is allowed only to hosts listed in allowed_hosts
- Always ask the user to confirm before registering a new tool
- Never claim a capability is complete without naming the tests and audit checks that cover it
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
        f"**Location:** src/tools/plugins/{name}/\n"
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
        result = await session.execute(select(Tool).where(Tool.is_active == True))  # noqa: E712
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
    selection = select_model(ModelRole.CODING)
    return Agent(
        name="ToolFactoryAgent",
        instructions=TOOL_FACTORY_INSTRUCTIONS,
        model=selection.model_id,
        tools=[generate_cli_tool, list_available_tools, review_tool_code],
    )
