"""Sandbox execution for CLI tools — subprocess with restrictions.

Resolves PRD gap C3 (sandbox isolation) and X1 (subprocess block clarification).
"""

import asyncio
import logging
import re
from pathlib import Path
from typing import Optional

from src.settings import settings
from src.tools.credentials import build_sandbox_env, get_credentials

logger = logging.getLogger(__name__)

# Blocked in GENERATED tool code (cli.py files) — NOT in wrapper code
BLOCKED_IMPORTS_IN_GENERATED = [
    "subprocess", "shutil", "ctypes", "pickle", "os.system",
    "os.popen", "os.exec", "importlib", "__import__",
]

BLOCKED_PATTERNS_IN_GENERATED = [
    r"rm\s+-rf",
    r"DROP\s+TABLE",
    r"DELETE\s+FROM",
    r"os\.environ",
    r"open\s*\(.*/etc/",
    r"eval\s*\(",
    r"exec\s*\(",
    r"compile\s*\(",
]


def static_analysis(code: str) -> list[str]:
    """Check generated code for blocked imports and dangerous patterns.

    Returns list of violation descriptions. Empty list = code is safe.
    """
    violations = []

    for blocked in BLOCKED_IMPORTS_IN_GENERATED:
        if blocked in code:
            violations.append(f"Blocked import/call: '{blocked}'")

    for pattern in BLOCKED_PATTERNS_IN_GENERATED:
        if re.search(pattern, code, re.IGNORECASE):
            violations.append(f"Blocked pattern: '{pattern}'")

    return violations


async def run_cli_tool(
    tool_dir: Path,
    entrypoint: str,
    args: list[str],
    timeout: Optional[int] = None,
    credential_keys: list[str] | None = None,
) -> tuple[int, str, str]:
    """Run a CLI tool in a sandboxed subprocess.

    Args:
        tool_dir: Path to the tool directory (e.g. tools/stock_checker/)
        entrypoint: Script filename (e.g. cli.py)
        args: Command-line arguments
        timeout: Override timeout in seconds
        credential_keys: If provided, fetch these credentials from the vault
            and inject them as TOOL_* env vars.

    Returns:
        (return_code, stdout, stderr)
    """
    timeout = timeout or settings.tool_subprocess_timeout
    script_path = tool_dir / entrypoint

    if not script_path.exists():
        return (1, "", f"Tool entrypoint not found: {script_path}")

    # Build safe env with Python paths + vault credentials
    tool_name = tool_dir.name
    creds = await get_credentials(tool_name) if credential_keys else {}
    env = build_sandbox_env(creds, allowed_keys=credential_keys)

    cmd = ["python", str(script_path)] + args

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(tool_dir),
            env=env,
        )

        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.communicate()
            return (1, "", f"Tool timed out after {timeout}s")

        return (
            proc.returncode or 0,
            stdout.decode("utf-8", errors="replace").strip(),
            stderr.decode("utf-8", errors="replace").strip(),
        )

    except Exception as e:
        logger.exception("Sandbox execution failed: %s", e)
        return (1, "", f"Execution error: {str(e)}")


async def test_tool_in_sandbox(tool_dir: Path, entrypoint: str) -> tuple[bool, str]:
    """Test a tool with --help to verify it runs without errors.

    Returns (success, message).
    """
    rc, stdout, stderr = await run_cli_tool(tool_dir, entrypoint, ["--help"], timeout=10)
    if rc == 0:
        return (True, "Tool passes sandbox test (--help returns 0)")
    return (False, f"Tool failed sandbox test: exit={rc}, stderr={stderr[:200]}")
