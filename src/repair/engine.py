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
    return payload


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


async def _run_verification_commands(commands: list[str]) -> list[dict]:
    results: list[dict] = []
    for command in commands:
        parts = _validate_command(command)
        rc, stdout, stderr = await _run_command_parts(parts)
        results.append(
            {
                "command": command,
                "returncode": rc,
                "stdout": _truncate(stdout),
                "stderr": _truncate(stderr),
            }
        )
        if rc != 0:
            break
    return results


async def execute_pending_repair(user_telegram_id: int) -> str:
    """Apply the stored patch in a sandbox branch, verify, and wait for deploy approval.

    Pipeline:
    1. Create a repair branch from current HEAD
    2. Apply the patch on the branch
    3. Run verification commands (pytest, ruff, mypy)
    4. If verification passes → merge to main branch, clean up
    5. If verification fails → delete branch, store failure for retry
    """
    payload = await get_pending_repair(user_telegram_id)
    if not payload:
        return "There is no pending repair patch to apply."

    patch_file = await _write_patch_file(payload["diff"])
    branch_name = f"repair/{int(time.time())}"
    original_branch = None

    try:
        # Determine current branch
        rc, stdout, _ = await _run_command_parts(["git", "rev-parse", "--abbrev-ref", "HEAD"])
        original_branch = stdout.strip() if rc == 0 else "main"

        # Dry-run check first
        check_rc, _, check_err = await _run_command_parts(["git", "apply", "--check", patch_file])
        if check_rc != 0:
            return "Pending repair patch no longer applies cleanly:\n\n" + _truncate(check_err)

        # Create and switch to repair branch
        create_rc, _, create_err = await _run_command_parts(["git", "checkout", "-b", branch_name])
        if create_rc != 0:
            return f"Failed to create repair branch: {_truncate(create_err)}"

        # Apply patch on the branch
        apply_rc, apply_out, apply_err = await _run_command_parts(["git", "apply", patch_file])
        if apply_rc != 0:
            await _run_command_parts(["git", "checkout", original_branch])
            await _run_command_parts(["git", "branch", "-D", branch_name])
            return "Patch application failed:\n\n" + _truncate(apply_err or apply_out)

        # Commit the change on the repair branch
        affected = payload.get("affected_files", [payload["file_path"]])
        for f in affected:
            await _run_command_parts(["git", "add", f])
        await _run_command_parts([
            "git", "commit", "-m",
            f"repair: {payload['description'][:80]}",
        ])

        # Run verification commands
        verification_results = await _run_verification_commands(payload.get("verification_commands", []))
        failed_verification = next((item for item in verification_results if item["returncode"] != 0), None)

        if failed_verification is not None:
            # Verification failed → switch back, delete branch, store failure for retry
            await _run_command_parts(["git", "checkout", original_branch])
            await _run_command_parts(["git", "branch", "-D", branch_name])

            from src.memory.conversation import store_last_tool_error
            await store_last_tool_error(user_telegram_id, {
                "user_message": f"Repair patch for: {payload['description']}",
                "assistant_response": (
                    f"Verification failed after applying patch.\n"
                    f"Failed command: `{failed_verification['command']}`\n"
                    f"stderr: {failed_verification['stderr'][:500]}\n"
                    f"stdout: {failed_verification['stdout'][:500]}"
                ),
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "retry_context": True,
            })

            return (
                "## Verification Failed — Patch Rolled Back\n\n"
                f"The patch was applied on branch `{branch_name}` but verification failed.\n"
                f"Branch has been deleted and changes rolled back.\n\n"
                f"**Failed command:** `{failed_verification['command']}`\n"
                f"**stderr:**\n```\n{_truncate(failed_verification['stderr'])}\n```\n\n"
                "The failure details have been stored. Say **\"fix it\"** to let the "
                "repair agent analyze the failure and try a revised approach."
            )

        # Verification passed → persist ticket status and wait for deploy approval
        await _run_command_parts(["git", "checkout", original_branch])

        try:
            from src.db.session import async_session
            from src.db.models import RepairTicket, User
            async with async_session() as session:
                ticket_id = payload.get("ticket_id")
                # Map telegram to user.id
                user_row = None
                user_result = await session.execute(select(User).where(User.telegram_id == user_telegram_id))
                user_row = user_result.scalar_one_or_none()
                if ticket_id:
                    ticket = await session.get(RepairTicket, ticket_id)
                else:
                    ticket = None
                if ticket is None:
                    # Create a ticket if one wasn't created earlier
                    ticket = RepairTicket(
                        user_id=user_row.id if user_row else None,
                        title=(payload.get("description") or "Code repair")[:200],
                        source="telegram",
                        status="ready_for_deploy",
                        priority="medium",
                        plan={
                            "file_path": payload.get("file_path"),
                            "affected_files": payload.get("affected_files", []),
                            "verification_commands": payload.get("verification_commands", []),
                        },
                        branch_name=branch_name,
                        verification_results={"results": verification_results},
                        approval_required=True,
                    )
                    session.add(ticket)
                else:
                    ticket.status = "ready_for_deploy"
                    ticket.branch_name = branch_name
                    ticket.verification_results = {"results": verification_results}
                await session.commit()
        except Exception:
            # Non-fatal for chat flow
            pass

        await clear_pending_repair(user_telegram_id)

        summary_lines = [
            "## ✅ Patch Verified in Sandbox — Awaiting Deploy Approval",
            "",
            f"Branch: `{branch_name}` (not merged)",
            f"**Files:** {', '.join(f'`{f}`' for f in affected)}",
            f"**Summary:** {payload['description']}",
            "",
            "Use /ticket approve <id> or the dashboard to deploy this patch.",
        ]
        if verification_results:
            summary_lines.append("")
            summary_lines.append("**Verification:**")
            for item in verification_results:
                summary_lines.append(f"- `{item['command']}` → exit {item['returncode']}")

        return "\n".join(summary_lines)
    finally:
        try:
            Path(patch_file).unlink(missing_ok=True)
        except OSError:
            logger.warning("Failed to delete temporary patch file %s", patch_file)


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


async def maybe_handle_pending_repair(user_telegram_id: int, user_message: str) -> Optional[str]:
    """Handle approval + execution for a stored repair plan."""
    pending = await get_pending_repair(user_telegram_id)
    if pending is None:
        return None

    if await has_pending_challenge(user_telegram_id):
        verified = await verify_challenge(user_telegram_id, user_message)
        if not verified:
            return "Security verification failed. The repair patch was not applied."
        return await execute_pending_repair(user_telegram_id)

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
            "The repair plan is still saved. Set up `/settings security`, then say "
            "`apply patch` again to approve and run it."
        )
    return (
        f"{challenge['prompt']}\n\n"
        "Reply with the answer to apply the pending repair patch."
    )


async def approve_ticket_deploy(ticket_id: int, approver_telegram_id: int) -> str:
    """Merge a verified repair branch to main after owner approval and write deploy signal."""
    from src.db.session import async_session
    from src.db.models import RepairTicket, User

    async with async_session() as session:
        ticket = await session.get(RepairTicket, ticket_id)
        if ticket is None:
            return "Ticket not found."
        if ticket.status != "ready_for_deploy" or not ticket.branch_name:
            return "Ticket is not ready for deploy or branch missing. Run verification first."

        # Approver lookup
        user_result = await session.execute(select(User).where(User.telegram_id == approver_telegram_id))
        approver = user_result.scalar_one_or_none()

        # Merge flow
        original_branch = None
        rc, stdout, _ = await _run_command_parts(["git", "rev-parse", "--abbrev-ref", "HEAD"])
        original_branch = stdout.strip() if rc == 0 else "main"
        await _run_command_parts(["git", "checkout", original_branch])
        merge_rc, merge_out, merge_err = await _run_command_parts(["git", "merge", "--ff-only", ticket.branch_name])
        if merge_rc != 0:
            merge_rc, merge_out, merge_err = await _run_command_parts([
                "git", "merge", ticket.branch_name, "-m", f"repair: merge {ticket.branch_name}",
            ])
        if merge_rc != 0:
            return f"Deploy failed during merge: {_truncate(merge_err or merge_out)}"

        # Clean up branch
        await _run_command_parts(["git", "branch", "-d", ticket.branch_name])
        ticket.status = "deployed"
        ticket.approved_by = approver.id if approver else None
        from datetime import datetime, timezone
        ticket.approved_at = datetime.now(timezone.utc)
        ticket.deployed_at = ticket.approved_at
        await session.commit()

    note = await _maybe_trigger_deploy()
    return ("✅ Deploy completed. " + (note or ""))
