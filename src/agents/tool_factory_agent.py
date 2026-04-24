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
- Calls a REST/HTTP API (OpenRouter, OpenAI, ElevenLabs, Replicate, etc.)? → YES → HTTP API Tool (`generate_http_tool`)
- Can it run as a standalone script calling a local binary (ffmpeg, sox, yt-dlp)? → YES → CLI Tool (`generate_cli_tool`)
- Needs deep agent integration but not multi-agent discovery? → YES → function_tool
- Multiple agents or external clients need to discover it? → YES → MCP Server
- Needs multi-turn clarification, routing rules, or owned conversation state? → YES → specialist agent
- If routing, approvals, or pending state are unclear, stop and clarify before generating code

## Catalog-First Workflow for AI API Tools
Before generating ANY tool that calls an AI API (image gen, video gen, TTS, music, embeddings):
1. Call `get_org_catalog(provider="openrouter", modality="<image|video|audio|text>")` to see live models + pricing
2. Pick the cheapest model that supports the required capability
3. Use the EXACT model ID returned (never invent or hardcode stale names)
4. Tell the user: which model was selected, why, and the expected cost per call
5. Only then generate the tool code with that model ID baked in as the default

For OpenAI-native tasks (DALL-E, TTS, Whisper, GPT):
  Call `get_org_catalog(provider="openai", modality="<image|audio|text>")` first.

## HTTP API Tool Template
Every HTTP tool must:
- Be a Python module exposing `tool_function` decorated with `@function_tool`
- Import `httpx` for async HTTP (already in requirements.txt)
- Load API keys from the credential vault: `from src.tools.credentials import get_credentials`
- NEVER hardcode API keys, tokens, or secrets
- Declare `allowed_hosts` in the manifest (e.g. "openrouter.ai")
- Handle HTTP errors and timeouts gracefully, return user-friendly messages

```python
from agents import function_tool
import httpx

async def _call_api(prompt: str, model: str, api_key: str) -> str:
    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "HTTP-Referer": "https://atlas.local"},
            json={"model": model, "messages": [{"role": "user", "content": prompt}]},
        )
        resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]

@function_tool
async def tool_function(prompt: str) -> str:
    'One-sentence description of what this tool does.'
    from src.tools.credentials import get_credentials
    creds = await get_credentials("tool_name")
    api_key = creds.get("api_key", "")
    if not api_key:
        return "API key not set. Run: /tools credentials set tool_name api_key <your_key>"
    try:
        return await _call_api(prompt, model="chosen-model-id", api_key=api_key)
    except httpx.HTTPStatusError as e:
        return f"API error {e.response.status_code}: {e.response.text[:200]}"
    except Exception as e:
        return f"Tool error: {e}"
```

For async video/audio jobs (polling pattern):
- Submit job → get job_id/polling_url → poll every 15s until status=completed → return result URL
- Set manifest timeout_seconds=300 for long-running generation tasks

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
- NOT access environment variables (no os.environ)
- NOT use eval(), exec(), or compile()
- NOT import: shutil, ctypes, pickle, importlib, multiprocessing, pty, os

## System-Binary CLI Tools (FFmpeg, ImageMagick, sox, yt-dlp, etc.)
When the tool must invoke an external binary:
- Set requires_system_binary=True when calling generate_cli_tool
- Import subprocess (allowed for system-binary tools only)
- ALL subprocess calls must go INSIDE main(), never at module top level
- Build the command as a LIST — never a string, never shell=True
- Call argparse.parse_args() BEFORE any subprocess call so --help always works

Correct pattern:
```python
import argparse
import subprocess
import sys

def main():
    parser = argparse.ArgumentParser(description="...")
    parser.add_argument("--input", required=True)
    args = parser.parse_args()          # --help exits here, before any ffmpeg call
    cmd = ["ffmpeg", "-i", args.input, "-c:v", "libx264", args.output]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        print(result.stderr, file=sys.stderr)
        sys.exit(result.returncode)
    print("Done.")

if __name__ == "__main__":
    main()
```

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


async def _generate_cli_tool_impl(
    name: str,
    description: str,
    parameters_json: str,
    tool_code: str,
    requires_network: bool = False,
    allowed_hosts: str = "",
    requires_system_binary: bool = False,
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
    violations = static_analysis(tool_code, allow_system_binary=requires_system_binary)
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
        "timeout_seconds": 60,
        "requires_approval": False,
        "requires_network": requires_network,
        "requires_system_binary": requires_system_binary,
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
async def generate_cli_tool(
    name: str,
    description: str,
    parameters_json: str,
    tool_code: str,
    requires_network: bool = False,
    allowed_hosts: str = "",
    requires_system_binary: bool = False,
) -> str:
    """Generate and register a new CLI tool.

    Args:
        name: Tool name in snake_case (e.g. 'stock_checker')
        description: What the tool does
        parameters_json: JSON string of parameter definitions: {"param_name": {"type": "str", "required": true, "description": "..."}}
        tool_code: The complete Python CLI script code (must use argparse)
        requires_network: Whether the tool needs network access
        allowed_hosts: Comma-separated list of allowed hostnames for network access
        requires_system_binary: Set True when the tool calls a system binary (ffmpeg, convert, sox, etc.) via subprocess
    """
    return await _generate_cli_tool_impl(
        name=name,
        description=description,
        parameters_json=parameters_json,
        tool_code=tool_code,
        requires_network=requires_network,
        allowed_hosts=allowed_hosts,
        requires_system_binary=requires_system_binary,
    )


async def _generate_http_tool_impl(
    name: str,
    description: str,
    parameters_json: str,
    tool_code: str,
    allowed_hosts: str = "",
    credential_keys: str = "",
) -> str:
    """Generate and register a function-type HTTP API tool.

    Unlike CLI tools (which run as subprocesses), HTTP tools are Python modules
    loaded in-process. They use httpx for async HTTP and the credential vault
    for API keys. Use this for OpenRouter, OpenAI, ElevenLabs, Stability AI,
    or any REST API integration.

    Args:
        name: Tool name in snake_case
        description: What the tool does
        parameters_json: JSON parameter definitions
        tool_code: Complete Python module — must expose tool_function (a @function_tool)
        allowed_hosts: Comma-separated hostnames (e.g. "openrouter.ai,api.openai.com")
        credential_keys: Comma-separated credential key names declared in manifest
                         (e.g. "api_key,org_id") — user sets them via /tools credentials set
    """
    if not name.replace("_", "").isalnum():
        return "Error: Tool name must be snake_case alphanumeric."

    tool_dir = TOOLS_DIR / name
    if tool_dir.exists():
        return f"Error: Tool '{name}' already exists. Choose a different name."

    try:
        params = json.loads(parameters_json)
    except json.JSONDecodeError as e:
        return f"Error: Invalid parameters JSON: {e}"

    tool_dir.mkdir(parents=True, exist_ok=True)

    tool_path = tool_dir / "tool.py"
    tool_path.write_text(tool_code)

    hosts = [h.strip() for h in allowed_hosts.split(",") if h.strip()] if allowed_hosts else []
    cred_keys = [k.strip() for k in credential_keys.split(",") if k.strip()] if credential_keys else []
    credentials: dict = {k: {"description": f"API credential: {k}", "required": True} for k in cred_keys}

    manifest = {
        "$schema": "tool-manifest-v1",
        "name": name,
        "version": "1.0.0",
        "description": description,
        "type": "function",
        "entrypoint": "tool.py",
        "wrapper": "tool.py",
        "parameters": params,
        "output_format": "text",
        "timeout_seconds": 120,
        "requires_approval": False,
        "requires_network": True,
        "allowed_hosts": hosts,
        "credentials": credentials,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "created_by": "tool_factory",
    }
    manifest_path = tool_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2))

    success, test_msg = await test_tool_in_sandbox(tool_dir, "tool.py")
    if not success:
        import shutil
        shutil.rmtree(tool_dir, ignore_errors=True)
        return f"❌ Tool failed sandbox test: {test_msg}\n\nThe tool was not registered."

    try:
        from sqlalchemy import select
        from src.db.session import async_session
        from src.db.models import Tool

        async with async_session() as session:
            existing = await session.execute(select(Tool).where(Tool.name == name))
            if existing.scalar_one_or_none() is None:
                session.add(Tool(
                    name=name,
                    tool_type="function",
                    description=description,
                    manifest_path=str(manifest_path),
                    is_active=True,
                    created_by="tool_factory",
                ))
                await session.commit()
    except Exception as e:
        logger.error("Failed to register HTTP tool in DB: %s", e)

    cred_instructions = ""
    if cred_keys:
        cred_instructions = (
            f"\n\n**Credentials required** — set them in Telegram:\n"
            + "\n".join(f"  `/tools credentials set {name} {k} <value>`" for k in cred_keys)
        )

    return (
        f"✅ HTTP tool **{name}** created and registered!\n\n"
        f"**Description:** {description}\n"
        f"**Location:** src/tools/plugins/{name}/\n"
        f"**Allowed hosts:** {', '.join(hosts) or 'none declared'}\n"
        f"**Sandbox test:** {test_msg}"
        f"{cred_instructions}"
    )


@function_tool
async def generate_http_tool(
    name: str,
    description: str,
    parameters_json: str,
    tool_code: str,
    allowed_hosts: str = "",
    credential_keys: str = "",
) -> str:
    """Generate and register a function-type HTTP API tool.

    Use this for tools that call external REST APIs: OpenRouter, OpenAI,
    ElevenLabs, Stability AI, Replicate, or any HTTP service. The tool
    module must expose a `tool_function` decorated with @function_tool.
    API keys are stored in the credential vault, never hardcoded.

    Args:
        name: Tool name in snake_case (e.g. 'openrouter_image_gen')
        description: What the tool does in one sentence
        parameters_json: JSON parameter definitions
        tool_code: Complete Python module exposing tool_function (@function_tool)
        allowed_hosts: Comma-separated hostnames (e.g. "openrouter.ai,openai.com")
        credential_keys: Comma-separated names for credentials the tool needs
                         (e.g. "api_key") — user sets via /tools credentials set
    """
    return await _generate_http_tool_impl(
        name=name,
        description=description,
        parameters_json=parameters_json,
        tool_code=tool_code,
        allowed_hosts=allowed_hosts,
        credential_keys=credential_keys,
    )


@function_tool
async def get_org_catalog(provider: str = "openrouter", modality: str = "image") -> str:
    """Query a live AI provider catalog to discover available models and pricing.

    Call this BEFORE generating any tool that uses an AI API so you can:
    - Pick the cheapest model that supports the required capability
    - Use the exact current model ID (not a stale hardcoded name)
    - Know the pricing to advise the user on expected costs

    Args:
        provider: Which provider to query — "openrouter" or "openai"
        modality: Filter by capability — "image", "video", "audio", "text",
                  "embedding", or "all" (no filter)
    """
    import httpx
    from src.models.provider_resolution import ProviderResolver

    try:
        resolver = ProviderResolver()
        config = resolver.resolve(provider)
        api_key = config.api_key or ""
    except Exception:
        api_key = ""

    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}

    try:
        if provider == "openrouter":
            params = {}
            if modality and modality != "all":
                params["output_modalities"] = modality
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(
                    "https://openrouter.ai/api/v1/models",
                    params=params,
                    headers={**headers, "HTTP-Referer": "https://atlas.local", "X-Title": "Atlas"},
                )
                resp.raise_for_status()
            models = resp.json().get("data") or []

            def _price(m: dict) -> float:
                p = m.get("pricing") or {}
                try:
                    return float(p.get("prompt") or p.get("image") or p.get("completion") or 999)
                except (TypeError, ValueError):
                    return 999.0

            models = sorted(models, key=_price)[:12]
            lines = [f"OpenRouter models (modality={modality!r}, cheapest first):"]
            for m in models:
                mid = m.get("id", "?")
                mname = m.get("name", mid)
                desc = (m.get("description") or "")[:100]
                pricing = m.get("pricing") or {}
                price_parts = [f"{k}={v}" for k, v in pricing.items() if v and v != "0"]
                lines.append(f"  • {mid} | {mname}")
                if desc:
                    lines.append(f"    {desc}")
                if price_parts:
                    lines.append(f"    Pricing: {', '.join(price_parts)}")
            return "\n".join(lines)

        elif provider == "openai":
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(
                    "https://api.openai.com/v1/models",
                    headers=headers,
                )
                resp.raise_for_status()
            models = resp.json().get("data") or []
            if modality and modality != "all":
                keyword_map = {
                    "image": ["dall-e", "gpt-image"],
                    "audio": ["tts", "whisper", "audio"],
                    "video": ["video", "sora"],
                    "text": ["gpt-", "o1", "o3"],
                    "embedding": ["embed"],
                }
                keywords = keyword_map.get(modality, [modality])
                models = [m for m in models if any(k in m.get("id", "").lower() for k in keywords)]
            lines = [f"OpenAI models (modality={modality!r}):"]
            for m in models[:12]:
                lines.append(f"  • {m.get('id', '?')}")
            return "\n".join(lines)

        else:
            return f"Unknown provider '{provider}'. Use 'openrouter' or 'openai'."

    except Exception as exc:
        return (
            f"Could not reach {provider} catalog: {exc}. "
            "You can still generate the tool using known model IDs from documentation."
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
        tools=[
            generate_cli_tool,
            generate_http_tool,
            get_org_catalog,
            list_available_tools,
            review_tool_code,
        ],
    )
