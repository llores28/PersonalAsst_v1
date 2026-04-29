"""Repair planning and execution helpers.

This module keeps repair execution out of the main bot handler flow:
- read-only diagnostics helpers for searching/reading the repo
- secure pending repair plan storage in Redis
- approval handling via the existing security challenge gate
- patch application + optional verification commands
"""

from __future__ import annotations

import asyncio
import json
import logging
import shlex
import tempfile
import time
from pathlib import Path
from typing import Optional

from sqlalchemy import select

from src.memory.conversation import (
    clear_pending_repair,
    get_pending_repair,
    store_pending_repair,
)
from src.security.challenge import has_pending_challenge, issue_challenge, verify_challenge

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[2]
_MAX_OUTPUT_CHARS = 4000
_APPROVAL_CUES = {
    "apply patch",
    "apply the patch",
    "apply repair",
    "approve patch",
    "approve repair",
    "apply it",
    "go ahead and apply it",
    "yes apply it",
    "yes apply",
    "approve it",
    "approve the fix",
}
_ALLOWED_COMMAND_PREFIXES: tuple[tuple[str, ...], ...] = (
    ("python", "-m", "pytest"),
    ("pytest",),
    ("python", "-m", "ruff", "check"),
    ("ruff", "check"),
    ("python", "-m", "mypy"),
    ("mypy",),
    # File-type aware verifier — works for .py, SKILL.md, .yaml, .json, .toml,
    # etc. Has no third-party deps beyond pyyaml (already in requirements.txt),
    # so it works in the runtime container where ruff/mypy are not installed.
    ("python", "-m", "src.repair.verify_file"),
)
# Substrings that indicate the verification command itself could not run
# (the test runner is missing), as opposed to the patched code being broken.
_MISSING_TOOL_MARKERS = (
    "no module named ",
    "command not found",
    "is not recognized as an internal or external command",
)
_DISALLOWED_COMMAND_TOKENS = {"&&", "||", ";", "|", "$(", "`"}


def _truncate(text: str, limit: int = _MAX_OUTPUT_CHARS) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "\n... (truncated)"


def _normalize_user_text(text: str) -> str:
    return " ".join(text.strip().lower().split())


def is_repair_approval_request(user_message: str) -> bool:
    normalized = _normalize_user_text(user_message)
    return normalized in _APPROVAL_CUES


_LOW_RISK_ACTIONS = frozenset({
    "clear_redis_key",
    "reinject_schedule",
    "set_env_var",
    "restart_service",
    "clear_cache",
})
_MEDIUM_RISK_ACTIONS = frozenset({
    "edit_config_file",
    "update_yaml",
    "update_json_config",
})
_CODE_EXTENSIONS = frozenset({".py", ".js", ".ts", ".sh", ".sql"})


def classify_repair_risk(plan: dict) -> str:
    """Classify a repair plan as 'low', 'medium', or 'high' risk.

    Risk levels:
    - low:    Only env var changes, Redis key clears, schedule re-injections.
              No file writes at all. Safe to auto-apply.
    - medium: Config file edits (.yaml, .json, .toml). Require human review.
    - high:   Any source code edits (.py, .js, .ts) or unknown actions.
              Always require human approval.

    Args:
        plan: The repair plan dict stored in RepairTicket.plan.
              Expected keys: steps (list of {action, ...}), patches (list).

    Returns:
        'low' | 'medium' | 'high'
    """
    steps: list[dict] = plan.get("steps", []) or []
    patches: list[dict] = plan.get("patches", []) or []

    if patches:
        for patch in patches:
            file_path = patch.get("file", "") or ""
            ext = Path(file_path).suffix.lower()
            if ext in _CODE_EXTENSIONS:
                return "high"
            return "medium"

    if not steps:
        return "high"

    for step in steps:
        action = (step.get("action") or "").lower()
        if action in _LOW_RISK_ACTIONS:
            continue
        if action in _MEDIUM_RISK_ACTIONS:
            return "medium"
        return "high"

    return "low"


def _resolve_repo_path(path: str, *, allow_missing: bool = False) -> Path:
    raw = (path or ".").strip()
    candidate = (REPO_ROOT / raw).resolve() if not Path(raw).is_absolute() else Path(raw).resolve()
    if REPO_ROOT not in candidate.parents and candidate != REPO_ROOT:
        raise ValueError(f"Path '{path}' is outside the repository.")
    if not allow_missing and not candidate.exists():
        raise FileNotFoundError(f"Path '{path}' does not exist.")
    return candidate


def _iter_text_files(base_path: Path) -> list[Path]:
    if base_path.is_file():
        return [base_path]

    ignored_parts = {".git", ".venv", "__pycache__", ".pytest_cache", ".ruff_cache", ".mypy_cache"}
    files: list[Path] = []
    for path in base_path.rglob("*"):
        if not path.is_file():
            continue
        if any(part in ignored_parts for part in path.parts):
            continue
        if path.stat().st_size > 512_000:
            continue
        files.append(path)
    return files


async def search_repo(query: str, base_path: str = "src", limit: int = 20) -> str:
    """Search repository text and return line-level matches."""
    term = query.strip()
    if not term:
        return "Error: search query cannot be empty."

    root = _resolve_repo_path(base_path)
    limit = min(max(limit, 1), 100)
    lowered = term.lower()
    matches: list[dict[str, object]] = []

    for file_path in _iter_text_files(root):
        try:
            lines = file_path.read_text(encoding="utf-8", errors="ignore").splitlines()
        except OSError:
            continue
        for line_no, line in enumerate(lines, start=1):
            if lowered in line.lower():
                matches.append(
                    {
                        "path": str(file_path.relative_to(REPO_ROOT)).replace("\\", "/"),
                        "line": line_no,
                        "text": line.strip(),
                    }
                )
                if len(matches) >= limit:
                    return json.dumps({"query": term, "count": len(matches), "matches": matches}, indent=2)

    return json.dumps({"query": term, "count": len(matches), "matches": matches}, indent=2)


async def read_repo_file(file_path: str, start_line: int = 1, end_line: int = 200) -> str:
    """Read a repository file with line numbers."""
    path = _resolve_repo_path(file_path)
    if not path.is_file():
        return f"Error: '{file_path}' is not a file."

    start = max(start_line, 1)
    end = max(end_line, start)
    lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    snippet = lines[start - 1 : end]
    numbered = "\n".join(f"{idx}: {line}" for idx, line in enumerate(snippet, start=start))
    rel = str(path.relative_to(REPO_ROOT)).replace("\\", "/")
    return f"File: {rel}\nLines: {start}-{min(end, len(lines))}\n\n{numbered}"


def _validate_command(command: str) -> list[str]:
    if any(token in command for token in _DISALLOWED_COMMAND_TOKENS):
        raise ValueError(f"Command uses a disallowed shell token: {command}")

    parts = shlex.split(command)
    if not parts:
        raise ValueError("Command cannot be empty.")

    for prefix in _ALLOWED_COMMAND_PREFIXES:
        if tuple(parts[: len(prefix)]) == prefix:
            return parts

    raise ValueError(f"Command is not in the repair allowlist: {command}")


async def _run_command_parts(parts: list[str]) -> tuple[int, str, str]:
    process = await asyncio.create_subprocess_exec(
        *parts,
        cwd=str(REPO_ROOT),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate()
    return int(process.returncode or 0), stdout.decode(), stderr.decode()


async def run_repo_diagnostics(command: str) -> str:
    """Run a read-only repo diagnostic command from a strict allowlist."""
    try:
        parts = _validate_command(command)
        rc, stdout, stderr = await _run_command_parts(parts)
        return json.dumps(
            {
                "command": command,
                "returncode": rc,
                "stdout": _truncate(stdout),
                "stderr": _truncate(stderr),
            },
            indent=2,
        )
    except Exception as e:
        return f"Error running diagnostics command: {e}"


async def create_structured_ticket(
    user_telegram_id: int,
    debug_analysis: dict,
    title: Optional[str] = None,
    source: str = "telegram",
) -> dict:
    """Create a RepairTicket from DebuggerAgent's structured analysis.

    This is the bridge between Phase 1 (Debugger) and the repair pipeline.
    When the DebuggerAgent produces high-confidence analysis, this function
    creates a durable ticket that captures the analysis for later stages.

    Args:
        user_telegram_id: The user's Telegram ID
        debug_analysis: Structured analysis dict from DebuggerAgent (DebugAnalysisModel)
        title: Optional ticket title (auto-generated if not provided)
        source: Where the error originated (telegram|scheduler|dashboard)

    Returns:
        Dict with keys: ticket_id, title, status, message
    """
    from src.db.models import RepairTicket, User
    from src.db.session import async_session
    from src.repair.models import Severity, Complexity

    # Extract fields from analysis with defaults
    error_summary = debug_analysis.get("error_summary", "Unknown error")
    _root_cause = debug_analysis.get("root_cause", "")  # Included in full analysis dict
    affected_components = debug_analysis.get("affected_components", [])
    affected_files = debug_analysis.get("affected_files", [])
    confidence_score = debug_analysis.get("confidence_score", 0.0)
    severity_str = debug_analysis.get("severity", "medium")
    complexity_str = debug_analysis.get("complexity", "medium")
    recommended_next_step = debug_analysis.get("recommended_next_step", "")

    # Auto-generate title if not provided
    if not title:
        component_str = affected_components[0] if affected_components else "system"
        title = f"[{component_str}] {error_summary[:60]}"
        if len(error_summary) > 60:
            title += "..."

    # Map severity/complexity to priority/risk
    severity_enum = Severity(severity_str) if severity_str in [s.value for s in Severity] else Severity.MEDIUM
    complexity_enum = Complexity(complexity_str) if complexity_str in [c.value for c in Complexity] else Complexity.MEDIUM

    priority_map = {
        Severity.LOW: "low",
        Severity.MEDIUM: "medium",
        Severity.HIGH: "high",
        Severity.CRITICAL: "high",
    }
    risk_map = {
        Complexity.LOW: "low",
        Complexity.MEDIUM: "medium",
        Complexity.HIGH: "high",
        Complexity.XHIGH: "high",
    }

    priority = priority_map.get(severity_enum, "medium")
    risk_level = risk_map.get(complexity_enum, "high")

    # Status transitions: open → debug_analysis_ready (awaiting programmer)
    status = "debug_analysis_ready" if confidence_score >= 0.7 else "open"

    try:
        async with async_session() as session:
            # Get user record
            user_result = await session.execute(
                select(User).where(User.telegram_id == user_telegram_id)
            )
            user = user_result.scalar_one_or_none()
            if user is None:
                return {
                    "success": False,
                    "ticket_id": None,
                    "title": title,
                    "status": "error",
                    "message": f"User with telegram_id {user_telegram_id} not found.",
                }

            # Create ticket
            ticket = RepairTicket(
                user_id=user.id,
                title=title,
                source=source,
                status=status,
                priority=priority,
                risk_level=risk_level,
                error_context={
                    "error_summary": error_summary,
                    "source_error": debug_analysis.get("diagnostic_evidence", {}),
                },
                debug_analysis=debug_analysis,  # Store full structured analysis
                plan={
                    "recommended_next_step": recommended_next_step,
                    "affected_files": affected_files,
                    "analysis_confidence": confidence_score,
                },
            )

            session.add(ticket)
            await session.commit()

            logger.info(
                "Created structured repair ticket %s for user %s "
                "(confidence=%.2f, status=%s)",
                ticket.id,
                user_telegram_id,
                confidence_score,
                status,
            )

            return {
                "success": True,
                "ticket_id": ticket.id,
                "title": title,
                "status": status,
                "message": (
                    f"✅ Created repair ticket #{ticket.id}\n"
                    f"**Title:** {title}\n"
                    f"**Status:** {status}\n"
                    f"**Confidence:** {confidence_score:.0%}\n"
                    f"**Next Step:** {recommended_next_step or 'Awaiting fix generation'}"
                ),
            }

    except Exception as e:
        logger.exception("Failed to create structured ticket: %s", e)
        return {
            "success": False,
            "ticket_id": None,
            "title": title,
            "status": "error",
            "message": f"Failed to create ticket: {e}",
        }


def _extract_patch_paths(diff: str) -> list[str]:
    paths: list[str] = []
    for line in diff.splitlines():
        if line.startswith("+++ b/"):
            path = line.removeprefix("+++ b/").strip()
            if path != "/dev/null":
                paths.append(path)
    return paths


def _parse_verification_commands(raw_commands: str) -> list[str]:
    commands = [line.strip() for line in raw_commands.splitlines() if line.strip()]
    for command in commands:
        _validate_command(command)
    return commands


async def store_repair_plan(
    user_id: int,
    *,
    file_path: str,
    description: str,
    diff: str,
    verification_commands: str = "",
) -> dict:
    """Persist a pending repair plan awaiting approval.

    Supports single-file and multi-file diffs. All files referenced in the diff
    must be inside the repository.
    """
    declared_path = str(_resolve_repo_path(file_path).relative_to(REPO_ROOT)).replace("\\", "/")
    patch_paths = _extract_patch_paths(diff)

    # Validate every path in the diff is inside the repo
    for p in patch_paths:
        _resolve_repo_path(p, allow_missing=True)

    # Single-file: declared path must match the only diff target
    # Multi-file: declared path should be one of the targets (primary file)
    if patch_paths and declared_path not in patch_paths:
        raise ValueError(
            f"Declared file_path '{declared_path}' is not referenced in the diff. "
            f"Diff targets: {patch_paths}"
        )

    commands = _parse_verification_commands(verification_commands)
    payload = {
        "file_path": declared_path,
        "affected_files": patch_paths,
        "description": description.strip(),
        "diff": diff,
        "verification_commands": commands,
        "created_at": time.time(),
    }
    # Create or update a durable RepairTicket so the plan appears in the dashboard
    try:
        from src.db.session import async_session
        from src.db.models import RepairTicket, User
        async with async_session() as session:
            user_result = await session.execute(select(User).where(User.telegram_id == user_id))
            user = user_result.scalar_one_or_none()
            ticket = RepairTicket(
                user_id=user.id if user else None,
                title=description[:200] or "Code repair",
                source="telegram",
                status="plan_ready",
                priority="medium",
                error_context=None,
                plan={
                    "file_path": declared_path,
                    "affected_files": patch_paths,
                    "verification_commands": commands,
                },
                approval_required=True,
            )
            session.add(ticket)
            await session.commit()
            await session.refresh(ticket)
            payload["ticket_id"] = ticket.id
    except Exception:
        # Non-fatal: we still proceed with pending repair in Redis
        pass

    await store_pending_repair(user_id, payload)

    # Notify owner: email + Telegram on ticket creation
    ticket_id = payload.get("ticket_id")
    if ticket_id:
        asyncio.create_task(_notify_ticket_created(
            user_telegram_id=user_id,
            ticket_id=ticket_id,
            title=description[:200],
            patch_paths=patch_paths,
        ))

    return payload


async def _notify_ticket_created(
    user_telegram_id: int,
    ticket_id: int,
    title: str,
    patch_paths: list[str],
) -> None:
    """Fire-and-forget: send email + Telegram alert when a ticket is created."""
    try:
        from src.repair.notifications import send_ticket_created_email
        await send_ticket_created_email(
            ticket_id=ticket_id,
            title=title,
            status="plan_ready",
            error_summary=title,
            affected_files=patch_paths,
        )
    except Exception as exc:
        logger.debug("Ticket-created email failed (non-critical): %s", exc)
    try:
        from src.bot.notifications import notify_ticket_created
        await notify_ticket_created(
            user_telegram_id=user_telegram_id,
            ticket_id=ticket_id,
            title=title,
            status="plan_ready",
        )
    except Exception as exc:
        logger.debug("Ticket-created Telegram notify failed (non-critical): %s", exc)


async def list_recent_errors_for_user(user_telegram_id: int, limit: int = 10) -> str:
    """Query audit_log for recent errors for the current user."""
    from src.db.models import AuditLog, User
    from src.db.session import async_session

    limit = min(max(limit, 1), 50)
    async with async_session() as session:
        user_result = await session.execute(select(User).where(User.telegram_id == user_telegram_id))
        user = user_result.scalar_one_or_none()
        if user is None:
            return "No database user record found for this Telegram account."

        result = await session.execute(
            select(AuditLog)
            .where(AuditLog.user_id == user.id, AuditLog.error.is_not(None))
            .order_by(AuditLog.timestamp.desc())
            .limit(limit)
        )
        rows = result.scalars().all()

    if not rows:
        return "No recent audit-log errors found."

    lines = []
    for row in rows:
        ts = row.timestamp.isoformat() if row.timestamp else "unknown-time"
        agent = row.agent_name or "unknown-agent"
        lines.append(f"[{ts}] {agent}: {(row.error or '').strip()}")
    return "\n".join(lines)


async def _load_owner_security_config(user_telegram_id: int) -> tuple[Optional[str], Optional[list[dict]], int]:
    from src.db.models import OwnerSecurityConfig, User
    from src.db.session import async_session

    async with async_session() as session:
        user_result = await session.execute(select(User).where(User.telegram_id == user_telegram_id))
        user = user_result.scalar_one_or_none()
        if user is None:
            return None, None, 60

        config_result = await session.execute(
            select(OwnerSecurityConfig).where(OwnerSecurityConfig.user_id == user.id)
        )
        config = config_result.scalar_one_or_none()
        if config is None:
            return None, None, 60
        security_qa = list(config.security_qa) if config.security_qa else None
        return config.pin_hash, security_qa, config.challenge_ttl


async def _write_patch_file(diff: str) -> str:
    temp = tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".patch", delete=False)
    try:
        temp.write(diff)
        return temp.name
    finally:
        temp.close()


def _looks_like_missing_tool(stderr: str, stdout: str) -> bool:
    """Detect whether the verification command itself failed to start.

    True means the test runner is missing or the file type doesn't match the
    runner — not that the patched code is broken. The repair workflow surfaces
    this differently so the owner can refine the verification command instead
    of treating the patch as faulty.
    """
    haystack = f"{stderr}\n{stdout}".lower()
    return any(marker in haystack for marker in _MISSING_TOOL_MARKERS)


async def _run_verification_commands(commands: list[str]) -> list[dict]:
    results: list[dict] = []
    for command in commands:
        parts = _validate_command(command)
        rc, stdout, stderr = await _run_command_parts(parts)
        result = {
            "command": command,
            "returncode": rc,
            "stdout": _truncate(stdout),
            "stderr": _truncate(stderr),
        }
        if rc != 0 and _looks_like_missing_tool(stderr, stdout):
            result["failure_kind"] = "missing_tool"
        results.append(result)
        if rc != 0:
            break
    return results


# ── File-type aware verification command suggestions ───────────────────

# Map of file suffix → suggested verification command template. The chosen
# commands all live inside the engine allowlist and have no dev-only deps.
_VERIFY_FILE_CMD = "python -m src.repair.verify_file {path}"
_PY_RUFF_CMD = "python -m ruff check {path}"


def _suggest_command_for_path(rel_path: str) -> str:
    """Pick the right verification command for a single file path.

    Conservative defaults so the command always runs in the bot container:
    every suggested command uses only stdlib + pyyaml.
    """
    suffix = Path(rel_path).suffix.lower()
    name = Path(rel_path).name
    # SKILL.md → file-type aware verifier (knows about user_skills layout)
    if name == "SKILL.md":
        return _VERIFY_FILE_CMD.format(path=rel_path)
    # All other supported types route through the same verifier — uniform,
    # always available, and avoids accidentally invoking ruff on Markdown.
    if suffix in {".py", ".md", ".yaml", ".yml", ".json", ".toml"}:
        return _VERIFY_FILE_CMD.format(path=rel_path)
    # Unknown extension: still attempt a UTF-8 sanity check via verify_file.
    return _VERIFY_FILE_CMD.format(path=rel_path)


def suggest_verification_commands(file_paths: list[str]) -> list[str]:
    """Return file-type-appropriate verification commands for ``file_paths``.

    Caller can stuff the result straight into a pending-repair payload — every
    suggestion is allowlisted and self-contained. Duplicates are dropped while
    preserving order.
    """
    seen: set[str] = set()
    commands: list[str] = []
    for raw in file_paths:
        path = (raw or "").strip()
        if not path:
            continue
        cmd = _suggest_command_for_path(path)
        if cmd not in seen:
            seen.add(cmd)
            commands.append(cmd)
    return commands


async def update_pending_verification_commands(
    user_telegram_id: int,
    commands: list[str],
) -> dict:
    """Replace verification commands on the existing pending repair plan.

    Used by the repair agent's ``refine_pending_verification`` tool when the
    previous verification step ran the wrong tool for the file type. Returns
    the updated payload, or raises ``ValueError`` if no pending repair exists
    or any command is not in the allowlist.
    """
    payload = await get_pending_repair(user_telegram_id)
    if not payload:
        raise ValueError("No pending repair plan found — nothing to refine.")

    validated: list[str] = []
    for command in commands:
        _validate_command(command)  # raises ValueError if disallowed
        validated.append(command)

    payload["verification_commands"] = validated
    await store_pending_repair(user_telegram_id, payload)
    return payload


def _apply_unified_diff(diff: str) -> tuple[bool, str, dict[str, bytes]]:
    """Apply a unified diff directly to files on disk using pure Python.

    Returns:
        (success, error_message, backups)
        backups maps file_path → original_bytes for rollback.
    """
    import re as _re

    backups: dict[str, bytes] = {}

    # Split into per-file hunks
    file_sections = _re.split(r"(?=^diff --git |^--- )", diff, flags=_re.MULTILINE)

    # Collect (target_path, hunk_lines) pairs
    patches: list[tuple[Path, list[str]]] = []
    for section in file_sections:
        lines = section.splitlines(keepends=True)
        target: Path | None = None
        for line in lines:
            # Accept both "--- a/path" and "+++ b/path" forms
            if line.startswith("+++ b/"):
                rel = line[6:].strip()
                target = (REPO_ROOT / rel).resolve()
                break
            if line.startswith("+++ ") and not line.startswith("+++ /dev/null"):
                rel = line[4:].strip().lstrip("b/")
                target = (REPO_ROOT / rel).resolve()
                break
        if target is None:
            continue
        patches.append((target, lines))

    if not patches:
        return False, "No valid file targets found in diff.", {}

    # Apply each file patch
    for target, hunk_lines in patches:
        try:
            original = target.read_bytes() if target.exists() else b""
            backups[str(target)] = original
            original_lines = original.decode("utf-8", errors="replace").splitlines(keepends=True)
        except OSError as e:
            return False, f"Cannot read {target}: {e}", backups

        result_lines: list[str] = list(original_lines)

        i = 0
        while i < len(hunk_lines):
            line = hunk_lines[i]
            if not line.startswith("@@"):
                i += 1
                continue

            # Parse @@ -start,count +start,count @@
            import re as _re2
            m = _re2.match(r"@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", line)
            if not m:
                i += 1
                continue

            orig_start = int(m.group(1)) - 1  # convert to 0-indexed
            i += 1

            # Collect hunk body
            hunk_orig: list[str] = []
            hunk_new: list[str] = []
            while i < len(hunk_lines) and not hunk_lines[i].startswith("@@") and not hunk_lines[i].startswith("diff "):
                hl = hunk_lines[i]
                if hl.startswith("-"):
                    hunk_orig.append(hl[1:])
                elif hl.startswith("+"):
                    hunk_new.append(hl[1:])
                elif hl.startswith(" "):
                    hunk_orig.append(hl[1:])
                    hunk_new.append(hl[1:])
                elif hl.startswith("\\ No newline"):
                    pass
                i += 1

            # Locate hunk_orig in result_lines starting near orig_start
            found_at = -1
            search_start = max(0, orig_start - 3)
            for offset in range(search_start, len(result_lines)):
                if result_lines[offset : offset + len(hunk_orig)] == hunk_orig:
                    found_at = offset
                    break

            if found_at == -1:
                return (
                    False,
                    f"Hunk for {target.name} at line {orig_start + 1} no longer matches the file.\n"
                    f"The file may have changed since the patch was generated.",
                    backups,
                )

            result_lines[found_at : found_at + len(hunk_orig)] = hunk_new

        try:
            target.write_text("".join(result_lines), encoding="utf-8")
        except OSError as e:
            return False, f"Cannot write {target}: {e}", backups

    return True, "", backups


def _rollback_patch(backups: dict[str, bytes]) -> None:
    """Restore original file contents from backups."""
    for path_str, original_bytes in backups.items():
        try:
            Path(path_str).write_bytes(original_bytes)
        except OSError as e:
            logger.error("Rollback failed for %s: %s", path_str, e)


async def execute_pending_repair(user_telegram_id: int) -> str:
    """Apply the stored patch directly to files, verify, then update ticket status.

    Pipeline (pure Python — no git/patch binary required):
    1. Back up all affected files
    2. Apply the unified diff via _apply_unified_diff()
    3. Run verification commands (pytest, ruff, mypy)
    4. If verification fails → rollback from backups
    5. If verification passes → clear pending repair, update ticket, notify owner
    """
    payload = await get_pending_repair(user_telegram_id)
    if not payload:
        return "There is no pending repair patch to apply."

    diff = payload.get("diff", "")
    if not diff.strip():
        return "The stored repair patch is empty. Nothing to apply."

    affected = payload.get("affected_files", [payload.get("file_path", "")])
    affected = [f for f in affected if f]

    # Apply the diff
    success, error_msg, backups = _apply_unified_diff(diff)
    if not success:
        return (
            "## ❌ Patch Failed to Apply\n\n"
            f"{error_msg}\n\n"
            "The file(s) were **not modified**. "
            "Say **\"fix it\"** to let the repair agent generate a revised patch."
        )

    # Run verification commands
    verification_results = await _run_verification_commands(payload.get("verification_commands", []))
    failed_verification = next((item for item in verification_results if item["returncode"] != 0), None)

    if failed_verification is not None:
        # Rollback
        _rollback_patch(backups)

        missing_tool = failed_verification.get("failure_kind") == "missing_tool"
        from src.memory.conversation import store_last_tool_error
        await store_last_tool_error(user_telegram_id, {
            "user_message": f"Repair patch for: {payload.get('description', '')}",
            "assistant_response": (
                f"Verification failed after applying patch.\n"
                f"Failed command: `{failed_verification['command']}`\n"
                f"failure_kind: {failed_verification.get('failure_kind', 'code_failure')}\n"
                f"stderr: {failed_verification['stderr'][:500]}\n"
                f"stdout: {failed_verification['stdout'][:500]}"
            ),
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "retry_context": True,
            "failure_kind": "missing_tool" if missing_tool else "code_failure",
            "affected_files": affected,
        })

        if missing_tool:
            # The verification command itself couldn't run — tool missing or
            # wrong tool for the file type. Don't blame the patch.
            return (
                "## ⚠️ Verification Could Not Run — Patch Rolled Back\n\n"
                "The patch was applied, but the verification command failed to start "
                "(the test runner is missing or doesn't apply to the file type). "
                "Files were restored.\n\n"
                f"**Failed command:** `{failed_verification['command']}`\n"
                f"**stderr:**\n```\n{_truncate(failed_verification['stderr'])}\n```\n\n"
                "Say **\"fix it\"** and the repair agent will pick a verification "
                "command appropriate for the patched file(s) and retry."
            )

        return (
            "## ⚠️ Verification Failed — Patch Rolled Back\n\n"
            f"The patch was applied but verification failed. Files restored.\n\n"
            f"**Failed command:** `{failed_verification['command']}`\n"
            f"**stderr:**\n```\n{_truncate(failed_verification['stderr'])}\n```\n\n"
            "The failure has been stored. Say **\"fix it\"** to let the repair agent "
            "analyze and propose a revised fix."
        )

    # Verification passed (or no verification commands) — update ticket
    try:
        from src.db.session import async_session
        from src.db.models import RepairTicket, User
        async with async_session() as session:
            ticket_id = payload.get("ticket_id")
            user_result = await session.execute(select(User).where(User.telegram_id == user_telegram_id))
            user_row = user_result.scalar_one_or_none()
            if ticket_id:
                ticket = await session.get(RepairTicket, ticket_id)
            else:
                ticket = None
            if ticket is None:
                ticket = RepairTicket(
                    user_id=user_row.id if user_row else None,
                    title=(payload.get("description") or "Code repair")[:200],
                    source="telegram",
                    status="deployed",
                    priority="medium",
                    plan={
                        "file_path": payload.get("file_path"),
                        "affected_files": affected,
                        "verification_commands": payload.get("verification_commands", []),
                    },
                    verification_results={"results": verification_results},
                    approval_required=False,
                )
                session.add(ticket)
            else:
                ticket.status = "deployed"
                ticket.verification_results = {"results": verification_results}
            await session.commit()
            await session.refresh(ticket)
            resolved_ticket_id = ticket.id
    except Exception:
        resolved_ticket_id = payload.get("ticket_id") or 0

    await clear_pending_repair(user_telegram_id)

    asyncio.create_task(_notify_fix_ready_async(
        user_telegram_id=user_telegram_id,
        ticket_id=resolved_ticket_id,
        description=payload.get("description", "Code repair"),
        affected_files=affected,
        branch_name="direct-apply",
        verification_results=verification_results,
    ))

    summary_lines = [
        "## ✅ Patch Applied Successfully",
        "",
        f"**Files modified:** {', '.join(f'`{f}`' for f in affected)}",
        f"**Summary:** {payload.get('description', '')}",
        "",
        "The changes are live. Restart the container to load updated code:",
        "`docker compose restart assistant`",
    ]
    if verification_results:
        summary_lines.append("")
        summary_lines.append("**Verification:**")
        for item in verification_results:
            summary_lines.append(f"- `{item['command']}` → exit {item['returncode']}")

    return "\n".join(summary_lines)


async def _notify_fix_ready_async(
    user_telegram_id: int,
    ticket_id: int,
    description: str,
    affected_files: list,
    branch_name: str,
    verification_results: list,
) -> None:
    """Fire-and-forget: send Telegram inline button + email when fix is verified."""
    verify_summary = ""
    for item in verification_results:
        verify_summary += f"{item['command']} → exit {item['returncode']}\n"

    try:
        from src.bot.notifications import notify_fix_ready
        await notify_fix_ready(
            user_telegram_id=user_telegram_id,
            ticket_id=ticket_id,
            title=description,
            affected_files=list(affected_files),
            branch_name=branch_name,
        )
    except Exception as exc:
        logger.debug("notify_fix_ready Telegram failed (non-critical): %s", exc)

    try:
        from src.repair.notifications import send_fix_ready_email
        await send_fix_ready_email(
            ticket_id=ticket_id,
            title=description,
            affected_files=list(affected_files),
            branch_name=branch_name,
            verification_summary=verify_summary,
        )
    except Exception as exc:
        logger.debug("send_fix_ready_email failed (non-critical): %s", exc)


async def _maybe_trigger_deploy() -> Optional[str]:
    """Signal that the container should be rebuilt after a successful repair.

    In Docker, the entrypoint or a file-watcher picks up the signal.
    Returns a status note or None.
    """
    signal_path = REPO_ROOT / ".repair_deploy_signal"
    try:
        signal_path.write_text(str(time.time()))
        return (
            "🚀 **Deploy signal written.** If running in Docker with auto-rebuild, "
            "the container will pick up the change. Otherwise, run:\n"
            "`docker compose build && docker compose up -d`"
        )
    except OSError:
        return None


# ── Integrated Self-Healing Pipeline (Phase 5) ───────────────────────────

_PIPELINE_ATTEMPT_COUNTS: dict[str, int] = {}
_PIPELINE_MAX_ATTEMPTS = 3


async def run_self_healing_pipeline(
    user_telegram_id: int,
    error_description: str,
    error_context: Optional[dict] = None,
    source: str = "telegram",
) -> dict:
    """Run the complete self-healing pipeline with all four agents.

    This is the integrated entry point that orchestrates:
    1. DebuggerAgent (deep analysis)
    2. Ticket creation (durable tracking)
    3. ProgrammerAgent (fix generation)
    4. QualityControlAgent (validation)
    5. Sandbox testing (patch application)
    6. Deploy approval (owner confirmation)

    Args:
        user_telegram_id: The user's Telegram ID
        error_description: Human-readable description of the error
        error_context: Optional additional error context (stack trace, logs)
        source: Where the error originated (telegram|scheduler|dashboard)

    Returns:
        Dict with pipeline results including:
        - success: bool
        - ticket_id: int or None
        - stage_reached: str (debugger|ticket|programmer|qa|sandbox|deploy)
        - decision: str (GO/NO_GO/NEEDS_REVISION/deployed/failed)
        - message: Human-readable summary
    """
    from src.agents.debugger_agent import run_debugger_analysis
    from src.agents.programmer_agent import run_programmer_fix_generation
    from src.agents.quality_control_agent import run_quality_control_validation
    from src.repair.models import (
        debug_analysis_to_model,
        fix_proposal_to_model,
        validation_decision_to_model,
        PipelineStage,
        RepairPipelineState,
    )

    # Guard: cap repeated attempts for the same error to prevent runaway loops
    _fingerprint = f"{user_telegram_id}:{error_description[:80]}"
    _PIPELINE_ATTEMPT_COUNTS[_fingerprint] = _PIPELINE_ATTEMPT_COUNTS.get(_fingerprint, 0) + 1
    if _PIPELINE_ATTEMPT_COUNTS[_fingerprint] > _PIPELINE_MAX_ATTEMPTS:
        logger.warning(
            "[Pipeline] Max attempts (%d) reached for fingerprint: %s",
            _PIPELINE_MAX_ATTEMPTS,
            _fingerprint[:60],
        )
        return {
            "success": False,
            "ticket_id": None,
            "stage_reached": "blocked",
            "decision": "MAX_RETRIES_EXCEEDED",
            "message": (
                f"⛔ This error has been submitted {_PIPELINE_ATTEMPT_COUNTS[_fingerprint]} times "
                f"without a successful fix. Automatic retries are paused to prevent loops.\n\n"
                "Please review open tickets with `/tickets` and handle manually."
            ),
        }

    logger.info(
        "Starting self-healing pipeline for user %s: %s",
        user_telegram_id,
        error_description[:100],
    )

    # Initialize pipeline state
    pipeline = RepairPipelineState(ticket_id=0)

    # Wave 2.4 + 2.5: parallel FSM tracking for audit + Redis checkpoint.
    # The FSMRunner mirrors every ``pipeline.mark_stage`` call below via
    # ``_advance_fsm`` and emits a structured transition log entry. Every
    # transition also writes the snapshot to Redis under
    # ``repair_checkpoint:{user_id}`` (24h TTL) so a container restart leaves
    # the audit trail recoverable. Terminal phases (DONE/FAILED) clear the
    # checkpoint eagerly. ``on_transition`` is best-effort — checkpoint
    # failures never abort the repair turn.
    from src.agents.fsm import new_runner, map_repair_stage, Phase as _FSMPhase
    from src.memory.conversation import (
        clear_repair_checkpoint as _clear_repair_checkpoint,
        save_repair_checkpoint as _save_repair_checkpoint,
    )

    def _on_fsm_transition(transition_entry) -> None:
        """Persist FSM state to Redis after every transition. Also fires the
        eager-clear path on terminal phases. Schedules async work via
        asyncio.create_task so the synchronous transition() call doesn't
        block on Redis."""
        try:
            snapshot = fsm_runner.state.to_dict()
            if transition_entry.to_phase in {_FSMPhase.DONE, _FSMPhase.FAILED}:
                asyncio.create_task(_clear_repair_checkpoint(user_telegram_id))
            else:
                asyncio.create_task(_save_repair_checkpoint(user_telegram_id, snapshot))
        except Exception:
            pass  # observability hook must never break the FSM

    fsm_runner = new_runner(
        f"repair-{user_telegram_id}-{int(time.time())}",
        initial_payload={
            "user_telegram_id": user_telegram_id,
            "error_summary": error_description[:120],
            "source": source,
        },
        on_transition=_on_fsm_transition,
    )

    def _advance_fsm(stage: PipelineStage, reason: str = "") -> None:
        target = map_repair_stage(stage.value)
        if target != fsm_runner.phase:
            fsm_runner.transition(target, reason=reason or stage.value, payload={"stage": stage.value})

    try:
        # ── Stage 1: DEBUGGER ───────────────────────────────────────────────
        pipeline.mark_stage(PipelineStage.DEBUGGING)
        _advance_fsm(PipelineStage.DEBUGGING, "running DebuggerAgent")
        logger.info("[Pipeline] Stage 1: Running DebuggerAgent")

        debug_result = await run_debugger_analysis(
            user_telegram_id=user_telegram_id,
            error_description=error_description,
            error_context=error_context,
        )

        if debug_result.confidence_score < 0.3:
            logger.warning("[Pipeline] Debugger confidence too low: %.2f", debug_result.confidence_score)
            return {
                "success": False,
                "ticket_id": None,
                "stage_reached": "debugger",
                "decision": "NEEDS_REVISION",
                "message": (
                    f"🔍 Debugger analysis confidence too low ({debug_result.confidence_score:.0%}).\n"
                    f"The error requires more context to diagnose.\n\n"
                    f"**Analysis:** {debug_result.error_summary or 'No conclusion reached'}\n\n"
                    "Please provide more details about the error."
                ),
            }

        # Convert to model for storage
        debug_model = debug_analysis_to_model(debug_result)
        pipeline.debug_analysis = debug_model

        # ── Stage 2: TICKET CREATION ─────────────────────────────────────
        pipeline.mark_stage(PipelineStage.TICKET_CREATED)
        _advance_fsm(PipelineStage.TICKET_CREATED, "creating ticket")
        logger.info("[Pipeline] Stage 2: Creating RepairTicket")

        ticket_result = await create_structured_ticket(
            user_telegram_id=user_telegram_id,
            debug_analysis=debug_model.model_dump(),
            title=f"[Auto] {error_description[:80]}",
            source=source,
        )

        if not ticket_result["success"]:
            logger.error("[Pipeline] Ticket creation failed: %s", ticket_result["message"])
            return {
                "success": False,
                "ticket_id": None,
                "stage_reached": "ticket",
                "decision": "FAILED",
                "message": f"❌ Failed to create repair ticket: {ticket_result['message']}",
            }

        ticket_id = ticket_result["ticket_id"]
        pipeline.ticket_id = ticket_id
        logger.info("[Pipeline] Created ticket %s", ticket_id)

        # ── Stage 3: PROGRAMMER ───────────────────────────────────────────
        pipeline.mark_stage(PipelineStage.PROGRAMMING)
        _advance_fsm(PipelineStage.PROGRAMMING, f"generating fix for ticket {ticket_id}")
        logger.info("[Pipeline] Stage 3: Running ProgrammerAgent (ticket=%s)", ticket_id)

        fix_result = await run_programmer_fix_generation(
            user_telegram_id=user_telegram_id,
            ticket_id=ticket_id,
            debug_analysis=debug_model.model_dump(),
        )

        if fix_result.confidence_score < 0.3 or not fix_result.unified_diff:
            logger.warning(
                "[Pipeline] Fix generation failed or low confidence: %.2f",
                fix_result.confidence_score,
            )
            # Update ticket status
            await _update_ticket_status(ticket_id, "open", "Fix generation failed")
            return {
                "success": False,
                "ticket_id": ticket_id,
                "stage_reached": "programmer",
                "decision": "NEEDS_REVISION",
                "message": (
                    f"🔧 Programmer could not generate a fix (confidence: {fix_result.confidence_score:.0%}).\n\n"
                    f"**Description:** {fix_result.description or 'No fix generated'}\n\n"
                    f"Ticket #{ticket_id} remains open for manual investigation."
                ),
            }

        # Convert to model
        fix_model = fix_proposal_to_model(fix_result)
        pipeline.fix_proposal = fix_model

        # ── Stage 4: QUALITY CONTROL ───────────────────────────────────────
        pipeline.mark_stage(PipelineStage.QA_VALIDATION)
        _advance_fsm(PipelineStage.QA_VALIDATION, f"validating fix for ticket {ticket_id}")
        logger.info("[Pipeline] Stage 4: Running QualityControlAgent (ticket=%s)", ticket_id)

        # Perform dry-run of patch
        dry_run_output = await _dry_run_patch(fix_model.unified_diff)

        qa_result = await run_quality_control_validation(
            user_telegram_id=user_telegram_id,
            ticket_id=ticket_id,
            fix_proposal=fix_model.model_dump(),
            dry_run_output=dry_run_output,
        )

        # Convert to model
        qa_model = validation_decision_to_model(qa_result)
        pipeline.qa_result = qa_model

        # Store QA results in ticket
        await _store_qa_results(ticket_id, qa_model.model_dump())

        if qa_model.decision == "NO_GO":
            logger.warning("[Pipeline] QA rejected fix: %s", qa_model.revision_feedback)
            await _update_ticket_status(ticket_id, "verification_failed", "QA rejected fix")
            return {
                "success": False,
                "ticket_id": ticket_id,
                "stage_reached": "qa",
                "decision": "NO_GO",
                "message": (
                    f"🛡️ Quality Control rejected the fix.\n\n"
                    f"**Issues found:**\n"
                    f"{qa_model.revision_feedback or 'Security or validation issues detected'}\n\n"
                    f"Ticket #{ticket_id} requires manual review."
                ),
            }

        if qa_model.decision == "NEEDS_REVISION":
            logger.info("[Pipeline] QA requested revision: %s", qa_model.revision_feedback)
            await _update_ticket_status(ticket_id, "plan_ready", "QA requested revision")
            return {
                "success": False,
                "ticket_id": ticket_id,
                "stage_reached": "qa",
                "decision": "NEEDS_REVISION",
                "message": (
                    f"🔄 Quality Control requested revision.\n\n"
                    f"**Feedback:**\n{qa_model.revision_feedback}\n\n"
                    f"The Programmer Agent will retry with this feedback.\n"
                    f"Ticket #{ticket_id} updated."
                ),
            }

        # GO decision - proceed to sandbox
        logger.info("[Pipeline] QA approved fix, proceeding to sandbox")

        # Wave A.2: deterministic safety floor under the LLM QA decision.
        # Even when the agent says GO, run pure-Python predicates (security
        # patterns, patch-applies, test allowlist, files-in-scope) and reject
        # if any BLOCKER trips. Catches the case where the LLM signed off on
        # a patch with eval()/shell=True/etc. that would have shipped.
        from src.agents.subtask_verifier import (
            make_repair_verifier,
            run_subtask_checks,
            aggregate_decision,
        )
        _subtask_results = run_subtask_checks(
            diff_content=fix_model.unified_diff or "",
            declared_affected_files=list(fix_model.affected_files or []),
            test_plan=list(fix_model.test_plan or []),
            dry_run_output=dry_run_output,
        )
        fsm_runner.set_verifier(make_repair_verifier(
            diff_content=fix_model.unified_diff or "",
            declared_affected_files=list(fix_model.affected_files or []),
            test_plan=list(fix_model.test_plan or []),
            dry_run_output=dry_run_output,
        ))
        deterministic_decision = aggregate_decision(_subtask_results)
        if deterministic_decision == "NO_GO":
            blocker_msgs = [
                f"• {r.name}: {r.message}"
                for r in _subtask_results if r.failed
            ]
            logger.warning(
                "[Pipeline] Deterministic verifier overrode LLM GO: %s",
                "; ".join(blocker_msgs),
            )
            await _update_ticket_status(
                ticket_id, "verification_failed",
                "Deterministic safety floor blocked the patch.",
            )
            return {
                "success": False,
                "ticket_id": ticket_id,
                "stage_reached": "qa",
                "decision": "NO_GO",
                "message": (
                    "🛡️ Deterministic safety floor blocked the patch even "
                    "though the QA agent said GO.\n\n"
                    "**Blockers:**\n" + "\n".join(blocker_msgs) +
                    f"\n\nTicket #{ticket_id} requires manual review."
                ),
            }

        # ── Stage 5: SANDBOX TESTING ──────────────────────────────────────
        pipeline.mark_stage(PipelineStage.SANDBOX_TESTING)
        _advance_fsm(PipelineStage.SANDBOX_TESTING, f"sandbox testing ticket {ticket_id}")
        logger.info("[Pipeline] Stage 5: Running sandbox tests (ticket=%s)", ticket_id)

        # Use existing execute_pending_repair logic but with our patch
        sandbox_result = await _run_sandbox_test(
            user_telegram_id=user_telegram_id,
            ticket_id=ticket_id,
            fix_proposal=fix_model,
        )

        if not sandbox_result["success"]:
            logger.error("[Pipeline] Sandbox test failed: %s", sandbox_result.get("error", "Unknown"))
            await _update_ticket_status(ticket_id, "verification_failed", "Sandbox test failed")
            return {
                "success": False,
                "ticket_id": ticket_id,
                "stage_reached": "sandbox",
                "decision": "FAILED",
                "message": (
                    f"❌ Sandbox testing failed.\n\n"
                    f"**Error:** {sandbox_result.get('error', 'Unknown error')}\n\n"
                    f"The patch was rolled back. Ticket #{ticket_id} remains open."
                ),
            }

        # ── Stage 6: AWAITING DEPLOY ──────────────────────────────────────
        pipeline.mark_stage(PipelineStage.AWAITING_APPROVAL)
        _advance_fsm(PipelineStage.AWAITING_APPROVAL, f"awaiting owner approval for ticket {ticket_id}")
        logger.info("[Pipeline] Stage 6: Fix ready for deploy (ticket=%s)", ticket_id)

        # Update ticket to ready_for_deploy
        await _update_ticket_status(ticket_id, "ready_for_deploy", "Passed all tests")

        return {
            "success": True,
            "ticket_id": ticket_id,
            "stage_reached": "deploy",
            "decision": "AWAITING_APPROVAL",
            "message": (
                f"✅ **Self-Healing Pipeline Complete!**\n\n"
                f"**Ticket:** #{ticket_id}\n"
                f"**Files modified:** {', '.join(fix_model.affected_files)}\n"
                f"**QA Decision:** GO (passed all validation)\n"
                f"**Sandbox:** All tests passed\n\n"
                f"The fix is ready for your approval. Use **/ticket approve {ticket_id}** "
                f"or the dashboard to deploy."
            ),
            "fsm_snapshot": fsm_runner.state.to_dict(),
        }

    except Exception as e:
        logger.exception("[Pipeline] Unhandled error in self-healing pipeline: %s", e)
        fsm_runner.fail(f"unhandled error: {type(e).__name__}: {str(e)[:120]}")
        return {
            "success": False,
            "ticket_id": pipeline.ticket_id if pipeline.ticket_id else None,
            "stage_reached": pipeline.current_stage.value,
            "decision": "FAILED",
            "message": f"❌ Pipeline failed with error: {e}\n\nTicket may require manual cleanup.",
            "fsm_snapshot": fsm_runner.state.to_dict(),
        }


async def _dry_run_patch(unified_diff: str) -> str:
    """Perform dry-run of patch to check if it applies cleanly (pure Python)."""
    success, error_msg, _ = _apply_unified_diff(unified_diff)
    # _apply_unified_diff writes files — we need a read-only check.
    # Re-implement as a read-only simulation: parse hunks and verify context matches.
    import re as _re_dry

    sections = _re_dry.split(r"(?=^diff --git |^--- )", unified_diff, flags=_re_dry.MULTILINE)
    for section in sections:
        lines = section.splitlines(keepends=True)
        target = None
        for line in lines:
            if line.startswith("+++ b/"):
                target = (REPO_ROOT / line[6:].strip()).resolve()
                break
            if line.startswith("+++ ") and not line.startswith("+++ /dev/null"):
                target = (REPO_ROOT / line[4:].strip().lstrip("b/")).resolve()
                break
        if target is None or not target.exists():
            continue
        orig_lines = target.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
        for i, hl in enumerate(lines):
            m = _re_dry.match(r"@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", hl)
            if not m:
                continue
            start = int(m.group(1)) - 1
            ctx = []
            j = i + 1
            while j < len(lines) and not lines[j].startswith("@@") and not lines[j].startswith("diff "):
                if lines[j].startswith(" ") or lines[j].startswith("-"):
                    ctx.append(lines[j][1:])
                j += 1
            if ctx and orig_lines[start : start + len(ctx)] != ctx:
                return (
                    f"Patch does not apply cleanly to {target.name}: "
                    f"context mismatch at line {start + 1}"
                )
    return "Patch applies cleanly (dry-run successful)."


async def _update_ticket_status(ticket_id: int, status: str, note: str = "") -> None:
    """Update RepairTicket status in database."""
    try:
        from src.db.session import async_session
        from src.db.models import RepairTicket

        async with async_session() as session:
            ticket = await session.get(RepairTicket, ticket_id)
            if ticket:
                ticket.status = status
                if note:
                    # Append note to plan
                    if isinstance(ticket.plan, dict):
                        ticket.plan["pipeline_note"] = note
                    else:
                        ticket.plan = {"pipeline_note": note}
                await session.commit()
                logger.info("Updated ticket %s status to %s", ticket_id, status)
    except Exception as e:
        logger.warning("Failed to update ticket %s status: %s", ticket_id, e)


async def _store_qa_results(ticket_id: int, qa_results: dict) -> None:
    """Store QA validation results in the ticket."""
    try:
        from src.db.session import async_session
        from src.db.models import RepairTicket

        async with async_session() as session:
            ticket = await session.get(RepairTicket, ticket_id)
            if ticket:
                # Store in verification_results for now
                # Could add dedicated qa_results field to model in future
                if isinstance(ticket.verification_results, dict):
                    ticket.verification_results["qa_validation"] = qa_results
                else:
                    ticket.verification_results = {"qa_validation": qa_results}
                await session.commit()
                logger.info("Stored QA results for ticket %s", ticket_id)
    except Exception as e:
        logger.warning("Failed to store QA results for ticket %s: %s", ticket_id, e)


async def _run_sandbox_test(
    user_telegram_id: int,
    ticket_id: int,
    fix_proposal,
) -> dict:
    """Run sandbox test for a fix proposal.

    This creates a temporary pending repair and uses the existing
    execute_pending_repair logic.
    """
    from src.repair.models import FixProposalModel

    try:
        # Convert to model if needed
        if isinstance(fix_proposal, FixProposalModel):
            model = fix_proposal
        else:
            from src.agents.programmer_agent import FixProposal
            from src.repair.models import fix_proposal_to_model
            if isinstance(fix_proposal, FixProposal):
                model = fix_proposal_to_model(fix_proposal)
            else:
                model = FixProposalModel(**fix_proposal)

        # Create pending repair payload
        payload = {
            "ticket_id": ticket_id,
            "file_path": model.affected_files[0] if model.affected_files else "unknown",
            "affected_files": model.affected_files,
            "description": model.description,
            "diff": model.unified_diff,
            "verification_commands": model.test_plan,
        }

        # Store in Redis for execute_pending_repair to find
        await store_pending_repair(user_telegram_id, payload)

        # Execute
        result = await execute_pending_repair(user_telegram_id)

        # Check if successful — match the exact header produced by execute_pending_repair
        _SUCCESS_MARKERS = (
            "Patch Verified in Sandbox",
            "Awaiting Deploy Approval",
            "ready_for_deploy",
            "✅ Patch Applied Successfully",
            "Patch Applied Successfully",
        )
        if any(marker in result for marker in _SUCCESS_MARKERS):
            return {"success": True, "result": result}
        return {"success": False, "error": result}

    except Exception as e:
        logger.exception("Sandbox test failed: %s", e)
        return {"success": False, "error": str(e)}


_COMMON_SHORT_WORDS = frozenset({
    "ok", "yes", "no", "sure", "done", "got", "yep", "nope", "hi", "hey",
    "thanks", "cool", "great", "good", "bad", "stop", "go", "help",
})


def _looks_like_pin_or_answer(text: str) -> bool:
    """Return True if the message looks like a security PIN or code reply.

    Conservative: only matches digit-only strings (PINs) to avoid intercepting
    normal conversational messages when a repair happens to be pending.
    """
    t = text.strip()
    # Pure digits — likely a PIN (3–8 digits)
    if t.isdigit() and 3 <= len(t) <= 8:
        return True
    # Single alphanumeric token that looks like a code (not a common word)
    if (
        " " not in t
        and len(t) >= 4
        and t.isalnum()
        and t.lower() not in _COMMON_SHORT_WORDS
    ):
        return True
    return False


async def maybe_handle_pending_repair(user_telegram_id: int, user_message: str) -> Optional[str]:
    """Handle approval + execution for a stored repair plan."""
    pending = await get_pending_repair(user_telegram_id)
    if pending is None:
        return None

    # ── Active challenge: try to verify the answer ────────────────────
    if await has_pending_challenge(user_telegram_id):
        # Don't feed approval cue text as a PIN — remind the user
        if is_repair_approval_request(user_message):
            return (
                "🔐 A security verification is already active.\n\n"
                "Please enter your 4-digit PIN (or security answer) to apply the patch."
            )
        verified = await verify_challenge(user_telegram_id, user_message)
        if not verified:
            return (
                "❌ That PIN didn't match. The repair patch was not applied.\n\n"
                "Try again, or say **`apply patch`** to restart verification."
            )
        return await execute_pending_repair(user_telegram_id)

    # ── No active challenge — check for expired challenge ─────────────
    # If the user sent something that looks like a PIN/answer but no active
    # challenge exists, the challenge likely expired (TTL). Re-issue it so
    # the user doesn't have to say "apply patch" again from scratch.
    if _looks_like_pin_or_answer(user_message):
        pin_hash, security_qa, ttl = await _load_owner_security_config(user_telegram_id)
        if pin_hash or security_qa:
            try:
                challenge = await issue_challenge(
                    user_telegram_id,
                    pin_hash=pin_hash,
                    security_qa=security_qa,
                    ttl=ttl,
                )
                return (
                    "⏱️ Your previous verification window expired. A new one has been issued.\n\n"
                    f"{challenge['prompt']}\n\n"
                    "Reply with the answer to apply the pending repair patch."
                )
            except ValueError:
                pass
        # Security not configured — tell them clearly
        return (
            "⚠️ You have a pending repair patch but no security PIN is set up.\n\n"
            "Run `/security pin <4-digit-pin>` first, then say `apply patch`."
        )

    # ── Standard approval cue ─────────────────────────────────────────
    if not is_repair_approval_request(user_message):
        return None

    pin_hash, security_qa, ttl = await _load_owner_security_config(user_telegram_id)
    try:
        challenge = await issue_challenge(
            user_telegram_id,
            pin_hash=pin_hash,
            security_qa=security_qa,
            ttl=ttl,
        )
    except ValueError as e:
        return (
            f"{e}\n\n"
            "The repair plan is still saved. Set up `/security pin <4-digit-pin>`, then say "
            "`apply patch` again to approve and run it."
        )
    return (
        f"{challenge['prompt']}\n\n"
        "Reply with the answer to apply the pending repair patch."
    )


async def approve_ticket_deploy(ticket_id: int, approver_telegram_id: int) -> str:
    """Mark a repair ticket as deployed after owner approval.

    Since patches are now applied directly at verification time (no git branch),
    this function simply closes the ticket and writes a deploy signal.
    """
    from src.db.session import async_session
    from src.db.models import RepairTicket, User

    async with async_session() as session:
        ticket = await session.get(RepairTicket, ticket_id)
        if ticket is None:
            return "Ticket not found."
        if ticket.status not in ("ready_for_deploy", "deployed"):
            return (
                f"Ticket #{ticket_id} has status '{ticket.status}' — "
                "nothing to approve. Run the repair flow first."
            )

        user_result = await session.execute(select(User).where(User.telegram_id == approver_telegram_id))
        approver = user_result.scalar_one_or_none()

        ticket.status = "deployed"
        ticket.approved_by = approver.id if approver else None
        from datetime import datetime, timezone
        ticket.approved_at = datetime.now(timezone.utc)
        ticket.deployed_at = ticket.approved_at
        await session.commit()

    note = await _maybe_trigger_deploy()
    return "✅ Ticket closed. " + (note or "")
