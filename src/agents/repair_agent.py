"""Self-healing repair agent — READ-ONLY by default.

This agent analyzes errors, proposes patches, and reports findings.
It **never** writes files or restarts services without explicit owner
approval that passes the security challenge gate.

Trigger: orchestrator detects repair-intent in user message and
delegates here.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from agents import Agent, function_tool, RunContextWrapper

from src.models.router import ModelRole, TaskComplexity, select_model
from src.repair.engine import (
    list_recent_errors_for_user,
    read_repo_file,
    run_repo_diagnostics,
    search_repo,
    store_repair_plan,
)
from src.memory.conversation import get_last_tool_error

logger = logging.getLogger(__name__)


# ── Data structures ────────────────────────────────────────────────────

@dataclass
class RepairContext:
    """Passed via RunContextWrapper to repair tools."""
    user_telegram_id: int
    error_logs: str = ""
    stack_trace: str = ""
    relevant_source: str = ""
    proposed_patch: Optional[str] = None
    patch_approved: bool = False


# ── Tools (read-only) ─────────────────────────────────────────────────

@function_tool
async def analyze_error_logs(
    ctx: RunContextWrapper[RepairContext],
    logs: str,
) -> str:
    """Analyze recent error logs and identify the root cause.

    Args:
        logs: Raw error log text to analyze.

    Returns a structured analysis with:
    - Root cause identification
    - Affected files and functions
    - Severity assessment (low/medium/high/critical)
    """
    ctx.context.error_logs = logs
    return (
        "Log analysis stored. Use `propose_patch` to generate a minimal fix "
        "based on the identified root cause."
    )


@function_tool
async def search_repo_code(
    ctx: RunContextWrapper[RepairContext],
    query: str,
    base_path: str = "src",
    limit: int = 20,
) -> str:
    """Search repository files for matching text."""
    return await search_repo(query=query, base_path=base_path, limit=limit)


@function_tool
async def read_repo_source(
    ctx: RunContextWrapper[RepairContext],
    file_path: str,
    start_line: int = 1,
    end_line: int = 200,
) -> str:
    """Read a repository file with line numbers."""
    return await read_repo_file(file_path=file_path, start_line=start_line, end_line=end_line)


@function_tool
async def run_repo_diagnostic_command(
    ctx: RunContextWrapper[RepairContext],
    command: str,
) -> str:
    """Run an allowlisted read-only diagnostic command such as pytest, ruff check, or mypy."""
    return await run_repo_diagnostics(command)


@function_tool
async def classify_complexity(
    ctx: RunContextWrapper[RepairContext],
    error_summary: str,
) -> str:
    """Classify the complexity of a repair task.

    Args:
        error_summary: Brief description of the error and context.

    Returns a complexity rating: none, low, medium, high, or xhigh.
    """
    # The agent itself does the classification via its reasoning.
    # This tool just provides structure for the response.
    return (
        "Classify this error as one of: none, low, medium, high, xhigh.\n"
        "Consider: number of files affected, risk of regression, "
        "whether tests exist, and whether the fix is mechanical or requires "
        "architectural changes."
    )


@function_tool
async def propose_patch(
    ctx: RunContextWrapper[RepairContext],
    file_path: str,
    description: str,
    diff: str,
    verification_commands: str = "",
) -> str:
    """Propose a minimal code patch (READ-ONLY — does NOT apply it).

    Args:
        file_path: Path to the file that needs patching.
        description: What the patch does and why.
        diff: The proposed unified diff.
        verification_commands: Optional newline-separated allowlisted commands to run after apply.

    Returns confirmation that the patch was recorded for owner review.
    """
    try:
        payload = await store_repair_plan(
            ctx.context.user_telegram_id,
            file_path=file_path,
            description=description,
            diff=diff,
            verification_commands=verification_commands,
        )
        verification_block = ""
        if payload["verification_commands"]:
            verification_block = (
                "\n**Verification Commands:**\n"
                + "\n".join(f"- `{command}`" for command in payload["verification_commands"])
            )

        ctx.context.proposed_patch = (
            f"**File:** `{payload['file_path']}`\n"
            f"**Description:** {description}\n"
            f"{verification_block}\n\n"
            f"```diff\n{diff}\n```"
        )
        return (
            f"✅ Patch proposal recorded for `{payload['file_path']}`.\n\n"
            "⚠️ This patch has NOT been applied. The owner must:\n"
            "1. Review the diff above\n"
            "2. Say 'apply patch' to trigger the security verification\n"
            "3. Pass the security challenge (PIN or security question)\n\n"
            "After approval, the system will apply the stored patch and run the verification commands."
        )
    except Exception as e:
        return f"Error recording repair plan: {e}"


@function_tool
async def get_error_context(
    ctx: RunContextWrapper[RepairContext],
) -> str:
    """Retrieve the most recent tool error context stored by Atlas.

    This is auto-captured when a tool call fails. Returns the original user
    request, Atlas's error response, and the timestamp. Use this FIRST when
    diagnosing a reported failure.
    """
    error = await get_last_tool_error(ctx.context.user_telegram_id)
    if not error:
        return "No stored error context found. Ask the user to describe the error, or use `list_recent_errors`."
    import json
    return json.dumps(error, indent=2)


@function_tool
async def list_recent_errors(
    ctx: RunContextWrapper[RepairContext],
    limit: int = 10,
) -> str:
    """List recent errors from the audit log.

    Args:
        limit: Maximum number of errors to retrieve (default 10).

    Returns formatted list of recent errors with timestamps.
    """
    return await list_recent_errors_for_user(ctx.context.user_telegram_id, limit=limit)


@function_tool
async def generate_report(
    ctx: RunContextWrapper[RepairContext],
    root_cause: str,
    proposed_fix: str,
    risk_assessment: str,
    test_plan: str,
) -> str:
    """Generate a structured repair report for the owner.

    Args:
        root_cause: What caused the error.
        proposed_fix: Summary of the proposed patch.
        risk_assessment: What could go wrong if the patch is applied.
        test_plan: How to verify the fix works.

    Returns the formatted report.
    """
    report = (
        "## 🔧 Repair Report\n\n"
        f"### Root Cause\n{root_cause}\n\n"
        f"### Proposed Fix\n{proposed_fix}\n\n"
        f"### Risk Assessment\n{risk_assessment}\n\n"
        f"### Test Plan\n{test_plan}\n\n"
        "---\n"
        "**Status:** Awaiting owner approval. "
        "Say `apply patch` to proceed (security verification required)."
    )
    return report


# ── Agent factory ──────────────────────────────────────────────────────

REPAIR_AGENT_INSTRUCTIONS = """\
You are Atlas's repair specialist. Your job is to diagnose errors with
real evidence, prepare a safe repair plan, and hand that plan off for
approval — but you NEVER apply changes directly yourself.

## CRITICAL LIMITATIONS — read carefully
- You DO have READ-ONLY access to the repository via dedicated tools.
- You CANNOT write or modify source files directly.
- You CANNOT apply patches, restart services, or execute code that changes state.
- You are NOT the execution path for routine third-party work such as
  moving, renaming, or organizing files in OneDrive, Drive, or other external apps
  when those tools are already working correctly.
- You ARE the right path when the owner reports that OneDrive, Drive, Gmail,
  browser automation, LinkedIn, or another Atlas integration is broken, failing,
  misrouting, returning errors, or behaving incorrectly.
- Do NOT fabricate file paths, function names, or code snippets.
- Do NOT generate unified diffs for files you have not actually read.
- If you don't know the exact file or function involved, say so honestly.

## What you CAN do
1. Retrieve auto-captured error context from the last failed tool call.
2. Analyze error messages and stack traces the user provides.
3. Inspect the repo with read-only tools to gather evidence.
4. Run allowlisted diagnostics such as pytest, ruff check, or mypy.
5. Identify likely root causes and explain them in plain language.
6. Prepare a patch proposal (single or multi-file) with verification commands.
7. Generate a diagnostic report summarizing findings and the repair plan.

## Full Repair Pipeline
When the owner asks you to fix something, follow this pipeline:

### Phase 1: AUDIT
1. **Get error context** — call `get_error_context` FIRST. The system auto-captures
   tool errors, so you often already have the failed request, error message, and timestamp.
2. **Collect more evidence** — use `search_repo_code` and `read_repo_source` to find
   the relevant code. Use `list_recent_errors` if audit log has more details.
3. **Run diagnostics** — use `run_repo_diagnostic_command` (pytest, ruff, mypy) to
   gather additional evidence about the current state.

### Phase 2: DIAGNOSE
4. **Classify complexity** — use `classify_complexity` to rate severity.
5. **Identify root cause** — analyze the evidence. Be specific: name the exact file,
   function, line, and what's wrong.
6. **Explain in plain language** — tell the owner what went wrong and why.

### Phase 3: FIX PLAN
7. **Read the files** — you MUST read every file you plan to patch with `read_repo_source`.
8. **Propose a fix** — use `propose_patch` with a real unified diff and verification
   commands. Multi-file diffs are supported — include all affected files in one diff.
9. **Generate report** — use `generate_report` summarizing root cause, fix, risks, and tests.

### Phase 4: APPROVAL & EXECUTION (handled by the system)
10. The owner reviews your plan and says `apply patch` to approve.
11. The system creates a sandbox git branch, applies the patch, and runs your
    verification commands automatically.
12. If verification passes → merged to main, deployed live.
13. If verification fails → branch deleted, failure stored. Owner can say "fix it"
    to re-engage you with the failure details for another attempt.

## When to use propose_patch
Use `propose_patch` only after you have actually read the relevant file(s)
with the repo tools. Verification commands must be allowlisted repo checks
such as `python -m pytest ...`, `python -m ruff check ...`, or `python -m mypy ...`.
Never invent code you haven't inspected.

## Retry After Verification Failure
If the system tells you a previous patch failed verification, the failure details
are available via `get_error_context`. Analyze what went wrong and propose a
revised patch. Do NOT re-propose the same diff that already failed.

## Safety Rules
- Never access secrets, credentials, or .env files.
- Never propose changes that weaken security.
- If you're unsure about a fix, say so — don't guess.
- Always include a test plan in your report.
- When the owner later says `apply patch`, the system will handle security
  verification and execution of the stored repair plan on a sandbox branch.
"""


def create_repair_agent() -> Agent[RepairContext]:
    """Create the read-only self-healing repair agent."""
    selection = select_model(ModelRole.REPAIR, TaskComplexity.MEDIUM)
    logger.info(
        "RepairAgent using model=%s reasoning_effort=%s",
        selection.model_id,
        selection.reasoning_effort,
    )
    return Agent(
        name="RepairAgent",
        handoff_description=(
            "Read-only diagnostics for Atlas/tool failures, logs, routing issues, "
            "and broken integrations such as OneDrive/Gmail/browser failures. "
            "Not for routine file organization tasks when the tools are working."
        ),
        instructions=REPAIR_AGENT_INSTRUCTIONS,
        model=selection.model_id,
        tools=[
            get_error_context,
            analyze_error_logs,
            search_repo_code,
            read_repo_source,
            run_repo_diagnostic_command,
            classify_complexity,
            propose_patch,
            list_recent_errors,
            generate_report,
        ],
    )
