"""Sandbox execution for CLI tools — subprocess with restrictions.

Resolves PRD gap C3 (sandbox isolation) and X1 (subprocess block clarification).
"""

import ast
import asyncio
import logging
import re
from pathlib import Path
from typing import Optional

from src.settings import settings
from src.tools.credentials import build_sandbox_env, get_credentials

logger = logging.getLogger(__name__)

# Blocked module names — detected via AST import analysis.
# NOTE: subprocess is conditionally allowed for system-binary tools (FFmpeg, etc.).
# See SYSTEM_BINARY_ALLOWLIST below for the approved set.
BLOCKED_MODULES = {
    "shutil", "ctypes", "pickle", "importlib",
    "multiprocessing", "pty", "os",
}

# System binaries that generated CLI tools are permitted to invoke via subprocess.
# Tools that only call these binaries (not arbitrary shell commands) are safe.
SYSTEM_BINARY_ALLOWLIST = {
    "ffmpeg", "ffprobe", "ffplay",       # video/audio processing
    "convert", "identify", "mogrify",    # ImageMagick
    "sox", "soxi",                        # audio processing
    "exiftool",                           # metadata
    "yt-dlp", "yt_dlp",                  # video download
    "gs",                                 # Ghostscript
    "pdftk",                              # PDF tools
}

# Additional patterns always blocked in subprocess-using tools
BLOCKED_SUBPROCESS_PATTERNS = [
    r"shell\s*=\s*True",      # never allow shell=True
    r"subprocess\.Popen\s*\(\s*[\"']",  # Popen("string") — shell injection risk
]

# Blocked attribute calls on 'os' (os.system, os.popen, etc.)
BLOCKED_OS_ATTRS = {"system", "popen", "execv", "execve", "execvp", "execl", "spawnl"}

# Blocked built-in call names
BLOCKED_BUILTINS = {"eval", "exec", "compile", "__import__"}

BLOCKED_PATTERNS_IN_GENERATED = [
    r"rm\s+-rf",
    r"DROP\s+TABLE",
    r"DELETE\s+FROM",
    r"os\.environ",
    r"open\s*\(.*/etc/",
]

# Import statement strings for each blocked module — used in tests and documentation.
BLOCKED_IMPORTS_IN_GENERATED = [f"import {mod}" for mod in sorted(BLOCKED_MODULES)]


def _uses_only_allowed_binaries(code: str) -> bool:
    """Return True if subprocess usage is limited to whitelisted system binaries.

    Looks for subprocess.run/call/check_output calls and verifies the first
    argument is a list whose first element is a known-safe binary.
    """
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return False

    assigned_binaries: dict[str, str] = {}

    def _extract_binary(node: ast.AST) -> str | None:
        if isinstance(node, ast.List) and node.elts:
            first = node.elts[0]
            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                return first.value
        if isinstance(node, ast.Name):
            return assigned_binaries.get(node.id)
        return None

    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            if len(node.targets) != 1:
                continue
            target = node.targets[0]
            if isinstance(target, ast.Name):
                binary = _extract_binary(node.value)
                if binary:
                    assigned_binaries[target.id] = binary
        elif isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Name) and node.value is not None:
                binary = _extract_binary(node.value)
                if binary:
                    assigned_binaries[node.target.id] = binary

    calls: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (
            isinstance(func, ast.Attribute)
            and isinstance(func.value, ast.Name)
            and func.value.id == "subprocess"
            and func.attr in {"run", "call", "check_output", "check_call"}
        ):
            continue
        if not node.args:
            return False
        binary = _extract_binary(node.args[0])
        if not binary:
            return False
        calls.append(binary)

    if not calls:
        return False

    def _normalize(binary: str) -> str:
        binary = binary.lower().replace("\\", "/")
        return binary.split("/")[-1]

    return all(_normalize(binary) in SYSTEM_BINARY_ALLOWLIST for binary in calls)


def static_analysis(code: str, allow_system_binary: bool = False) -> list[str]:
    """Check generated code for blocked imports and dangerous patterns.

    Uses AST parsing for import checks (prevents comment/name bypass).
    Falls back to regex for non-import patterns.

    Args:
        code: Source code to analyse.
        allow_system_binary: If True, subprocess is permitted when the tool only
            calls binaries from SYSTEM_BINARY_ALLOWLIST (e.g. ffmpeg). Shell=True
            is still blocked regardless.

    Returns list of violation descriptions. Empty list = code is safe.
    """
    violations: list[str] = []
    uses_subprocess = False

    # ── AST-based import and call checks ──
    try:
        tree = ast.parse(code)
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                if isinstance(node, ast.Import):
                    names = [alias.name.split(".")[0] for alias in node.names]
                else:
                    names = [node.module.split(".")[0]] if node.module else []
                for name in names:
                    if name == "subprocess":
                        uses_subprocess = True
                    elif name in BLOCKED_MODULES:
                        violations.append(f"Blocked import: '{name}'")
            elif isinstance(node, ast.Call):
                func = node.func
                if isinstance(func, ast.Name) and func.id in BLOCKED_BUILTINS:
                    violations.append(f"Blocked built-in call: '{func.id}()'")
                elif (
                    isinstance(func, ast.Attribute)
                    and isinstance(func.value, ast.Name)
                    and func.value.id == "os"
                    and func.attr in BLOCKED_OS_ATTRS
                ):
                    violations.append(f"Blocked os call: 'os.{func.attr}()'")
    except SyntaxError as e:
        violations.append(f"Syntax error in generated code: {e}")

    # ── subprocess handling ──
    if uses_subprocess:
        # Always block shell=True and Popen("string") regardless of allow flag
        for pattern in BLOCKED_SUBPROCESS_PATTERNS:
            if re.search(pattern, code, re.IGNORECASE):
                violations.append(f"Blocked subprocess pattern: '{pattern}' — use a list of args, never shell=True")
        # Conditionally allow subprocess for whitelisted system binaries
        if not allow_system_binary:
            violations.append(
                "Blocked import: 'subprocess' — set requires_system_binary=True and use only approved binaries (ffmpeg, convert, sox, etc.)"
            )
        elif not _uses_only_allowed_binaries(code):
            violations.append(
                f"subprocess allowed only for these system binaries: {sorted(SYSTEM_BINARY_ALLOWLIST)}. "
                "Use subprocess.run([binary, ...]) with a list, not a shell string."
            )

    # ── Regex checks for non-AST-detectable patterns ──
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
    script_path = (tool_dir / entrypoint).resolve()
    tool_dir = tool_dir.resolve()

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

    For system-binary tools (requires_system_binary=True in manifest.json),
    checks whether the required binary is available before running --help.
    If the binary is missing, skips the live test and returns success with a
    warning — the tool is still registered so it works when the binary is
    installed at runtime.

    Returns (success, message).
    """
    import json as _json
    import shutil as _shutil

    manifest_path = tool_dir / "manifest.json"
    if manifest_path.exists():
        try:
            manifest = _json.loads(manifest_path.read_text())
            if manifest.get("requires_system_binary"):
                # Detect which binary the tool needs from the first subprocess call in cli.py
                cli_path = tool_dir / entrypoint
                binary_name: str | None = None
                if cli_path.exists():
                    import re as _re
                    match = _re.search(
                        r'subprocess\.(?:run|call|check_output|check_call)\s*\(\s*\[\s*["\']([^"\']+)["\']',
                        cli_path.read_text(),
                        _re.IGNORECASE,
                    )
                    if match:
                        binary_name = match.group(1).split("/")[-1]  # basename only
                if binary_name and _shutil.which(binary_name) is None:
                    return (
                        True,
                        f"Sandbox test skipped — '{binary_name}' binary not found in PATH. "
                        "Tool registered; will work when the binary is available at runtime.",
                    )
        except Exception:
            pass

    rc, stdout, stderr = await run_cli_tool(tool_dir, entrypoint, ["--help"], timeout=10)
    if rc == 0:
        return (True, "Tool passes sandbox test (--help returns 0)")
    return (False, f"Tool failed sandbox test: exit={rc}, stderr={stderr[:200]}")
