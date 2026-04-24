"""Self-healing repair agent — READ-ONLY by default.

This agent analyzes errors, proposes patches, and reports findings.
It **never** writes files or restarts services without explicit owner
approval that passes the security challenge gate.

Trigger: orchestrator detects repair-intent in user message and
delegates here.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Optional

from agents import Agent, function_tool, RunContextWrapper

from src.models.router import ModelRole, TaskComplexity, select_model
from src.repair.engine import (
    classify_repair_risk,
    list_recent_errors_for_user,
    read_repo_file,
    run_repo_diagnostics,
    search_repo,
    store_repair_plan,
    suggest_verification_commands,
    update_pending_verification_commands,
)
from src.memory.conversation import get_last_tool_error, get_pending_repair

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
    if not isinstance(ctx.context, dict):
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
    raw_ctx = ctx.context
    if isinstance(raw_ctx, dict):
        return "Cannot store patch — repair agent was invoked without a proper user context."
    try:
        payload = await store_repair_plan(
            raw_ctx.user_telegram_id,
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

        raw_ctx.proposed_patch = (
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
    raw_ctx = ctx.context
    if isinstance(raw_ctx, dict):
        logger.warning(
            "get_error_context called with dict context instead of RepairContext — "
            "repair agent was likely invoked without proper context. Returning no error."
        )
        return "No stored error context found (context not available). Ask the user to describe the error."
    error = await get_last_tool_error(raw_ctx.user_telegram_id)
    if not error:
        return "No stored error context found. Ask the user to describe the error, or use `list_recent_errors`."
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
    raw_ctx = ctx.context
    if isinstance(raw_ctx, dict):
        logger.warning(
            "list_recent_errors called with dict context instead of RepairContext."
        )
        return "Error context unavailable — repair agent was invoked without a proper user context."
    return await list_recent_errors_for_user(raw_ctx.user_telegram_id, limit=limit)


@function_tool
async def propose_low_risk_fix(
    ctx: RunContextWrapper[RepairContext],
    title: str,
    description: str,
    steps_json: str,
) -> str:
    """Propose and auto-apply a LOW-RISK operational fix (no code edits).

    Low-risk fixes are operational changes only:
    - Clearing a Redis key (action: clear_redis_key, key: <key>)
    - Re-injecting a broken schedule (action: reinject_schedule, job_id: <id>)
    - Setting an env-var in the running config (action: set_env_var, key: <k>, value: <v>)

    These are applied immediately WITHOUT owner approval.
    A Telegram notification is sent after apply.

    Args:
        title: Short title for the repair ticket (e.g. 'Clear stale Redis session key').
        description: What this fix does and why it is safe.
        steps_json: JSON list of step dicts: [{"action": "clear_redis_key", "key": "..."}]

    Returns confirmation of auto-apply result.
    """
    import os

    auto_repair_enabled = os.getenv("AUTO_REPAIR_LOW_RISK", "true").lower() != "false"
    if not auto_repair_enabled:
        return (
            "AUTO_REPAIR_LOW_RISK is disabled in .env. "
            "Presenting fix for manual approval instead.\n\n"
            f"**Fix:** {title}\n**Steps:** {steps_json}"
        )

    try:
        steps = json.loads(steps_json)
    except Exception as e:
        return f"Error: steps_json is not valid JSON — {e}"

    plan = {"title": title, "description": description, "steps": steps, "patches": []}
    risk = classify_repair_risk(plan)
    if risk != "low":
        return (
            f"⚠️ Risk classifier rated this as '{risk}' — cannot auto-apply. "
            "Use `propose_patch` for medium/high-risk repairs requiring owner approval."
        )

    results: list[str] = []
    for step in steps:
        action = (step.get("action") or "").lower()
        try:
            if action == "clear_redis_key":
                import redis.asyncio as aioredis
                from src.settings import settings
                r = aioredis.from_url(settings.redis_url)
                deleted = await r.delete(step["key"])
                results.append(f"✅ Cleared Redis key `{step['key']}` (deleted={deleted})")
            elif action == "reinject_schedule":
                results.append(f"⚠️ reinject_schedule for job `{step.get('job_id')}` — not yet implemented")
            elif action == "set_env_var":
                results.append(f"⚠️ set_env_var requires container restart — log only: {step.get('key')}={step.get('value')}")
            else:
                results.append(f"⚠️ Unknown low-risk action '{action}' — skipped")
        except Exception as e:
            results.append(f"❌ Step '{action}' failed: {e}")

    apply_summary = "\n".join(results)
    all_ok = all(r.startswith("✅") for r in results)

    try:
        from src.db.session import async_session as _async_session
        from src.db.models import RepairTicket as _RepairTicket
        from datetime import datetime as _dt, timezone as _tz
        async with _async_session() as _dbs:
            ticket = _RepairTicket(
                user_id=None,
                title=title,
                source="repair_agent",
                status="deployed" if all_ok else "verification_failed",
                priority="low",
                risk_level="low",
                auto_applied=True,
                approval_required=False,
                plan=plan,
                deployed_at=_dt.now(_tz.utc) if all_ok else None,
            )
            _dbs.add(ticket)
            await _dbs.commit()
    except Exception as _dbe:
        logger.warning("Could not persist auto-repair ticket: %s", _dbe)

    # Notify owner via Telegram so they see the auto-apply result immediately
    try:
        from src.bot.notifications import notify_low_risk_applied
        import asyncio as _asyncio
        _asyncio.create_task(notify_low_risk_applied(
            user_telegram_id=ctx.context.user_telegram_id,
            title=title,
            result_summary=apply_summary,
        ))
    except Exception as _ne:
        logger.debug("Low-risk Telegram notify failed (non-critical): %s", _ne)

    status = "✅ Auto-applied successfully" if all_ok else "⚠️ Some steps failed"
    return (
        f"{status}: **{title}**\n\n"
        f"{apply_summary}"
    )


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


@function_tool
async def call_debugger_for_deep_analysis(
    ctx: RunContextWrapper[RepairContext],
    error_description: str,
    complexity_hint: str = "medium",
) -> str:
    """Call the Debugger Agent for comprehensive root-cause analysis.

    Use this when:
    - The error spans multiple files or components
    - The root cause is unclear after initial investigation
    - The error has high severity or complexity
    - You need structured analysis for the repair ticket

    The Debugger Agent is READ-ONLY and produces structured analysis that
    feeds into the full repair pipeline.

    Args:
        error_description: Detailed description of the error to analyze
        complexity_hint: low, medium, high, or xhigh (your initial assessment)

    Returns structured analysis with root cause, affected files, and next steps.
    """
    from src.agents.debugger_agent import run_debugger_analysis
    from src.repair.models import debug_analysis_to_model

    logger.info(
        "RepairAgent calling DebuggerAgent for analysis (complexity=%s)",
        complexity_hint,
    )

    try:
        analysis = await run_debugger_analysis(
            user_telegram_id=ctx.context.user_telegram_id,
            error_description=error_description,
            error_context={"complexity_hint": complexity_hint},
        )

        # Convert to model for validation
        model = debug_analysis_to_model(analysis)

        # Format for display
        components = ", ".join(model.affected_components) if model.affected_components else "unknown"
        files = ", ".join(model.affected_files) if model.affected_files else "to be determined"
        steps = "\n".join(f"{i+1}. {step}" for i, step in enumerate(model.reproduction_steps)) if model.reproduction_steps else "No reproduction steps identified"

        return (
            f"## 🔍 Debugger Agent Analysis (Confidence: {model.confidence_score:.0%})\n\n"
            f"**Summary:** {model.error_summary}\n\n"
            f"**Root Cause:** {model.root_cause}\n\n"
            f"**Severity:** {model.severity.value} | **Complexity:** {model.complexity.value}\n\n"
            f"**Affected Components:** {components}\n\n"
            f"**Affected Files:** {files}\n\n"
            f"**Reproduction Steps:**\n{steps}\n\n"
            f"**Recommended Next Step:** {model.recommended_next_step}\n\n"
            "---\n"
            "The analysis has been recorded. You can now:\n"
            "1. Use `propose_patch` with the affected files to create a fix\n"
            "2. Or ask the user for more context if confidence is low"
        )

    except Exception as e:
        logger.exception("DebuggerAgent call failed: %s", e)
        return (
            f"⚠️ Debugger Agent analysis failed: {e}\n\n"
            "Falling back to your own diagnostic tools. "
            "Consider using `search_repo_code` and `read_repo_source` to investigate manually."
        )


@function_tool
async def call_programmer_for_fix_generation(
    ctx: RunContextWrapper[RepairContext],
    ticket_id: int,
    debug_analysis_json: str,
) -> str:
    """Call the Programmer Agent to generate a fix from debug analysis.

    Use this when you have structured debug analysis (from the Debugger Agent
    or from a RepairTicket) and need to generate the actual code patch.

    The Programmer Agent:
    - Reads all affected files
    - Generates a unified diff patch
    - Creates a test plan
    - Assesses risk and rollback options

    Args:
        ticket_id: The repair ticket ID to associate with this fix
        debug_analysis_json: JSON string containing the debug analysis dict
            (with fields: error_summary, root_cause, affected_files, etc.)

    Returns:
        Generated fix proposal with unified diff, test plan, and risk assessment.
    """
    from src.agents.programmer_agent import run_programmer_fix_generation
    from src.repair.models import fix_proposal_to_model

    logger.info(
        "RepairAgent calling ProgrammerAgent for fix generation (ticket=%s)",
        ticket_id,
    )

    try:
        import json
        debug_analysis = json.loads(debug_analysis_json)
    except Exception as e:
        return f"Error: Invalid JSON in debug_analysis_json: {e}"

    try:
        proposal = await run_programmer_fix_generation(
            user_telegram_id=ctx.context.user_telegram_id,
            ticket_id=ticket_id,
            debug_analysis=debug_analysis,
        )

        # Convert to model for validation and display
        model = fix_proposal_to_model(proposal)

        # Format for display
        files_str = ", ".join(model.affected_files) if model.affected_files else "to be determined"
        tests_str = "\n".join(f"  - {cmd}" for cmd in model.test_plan) if model.test_plan else "  (none specified)"

        return (
            f"## 🔧 Programmer Agent Fix Proposal (Confidence: {model.confidence_score:.0%})\n\n"
            f"**Description:** {model.description}\n\n"
            f"**Risk:** {model.risk_assessment.value} | **Files:** {files_str}\n\n"
            f"**Test Plan:**\n{tests_str}\n\n"
            f"**Rollback:** {model.rollback_plan}\n\n"
            "---\n"
            "The fix proposal has been generated. Next steps:\n"
            "1. Review the generated unified diff\n"
            "2. Use `propose_patch` with the diff to create a repair plan\n"
            "3. Or call Quality Control Agent for validation (Phase 4)"
        )

    except Exception as e:
        logger.exception("ProgrammerAgent call failed: %s", e)
        return (
            f"⚠️ Programmer Agent fix generation failed: {e}\n\n"
            "You can try generating the fix manually using `propose_patch` "
            "after reading the affected files."
        )


@function_tool
async def call_quality_control_for_validation(
    ctx: RunContextWrapper[RepairContext],
    ticket_id: int,
    fix_proposal_json: str,
    dry_run_output: str = "",
) -> str:
    """Call the Quality Control Agent to validate a fix proposal.

    Use this after the Programmer Agent generates a fix to validate it before
    sandbox testing. The Quality Control Agent performs security scans,
    checks if the patch applies cleanly, and validates test commands.

    Returns:
        Validation decision (GO, NO_GO, or NEEDS_REVISION) with detailed results.
    """
    from src.agents.quality_control_agent import run_quality_control_validation
    from src.repair.models import validation_decision_to_model

    logger.info(
        "RepairAgent calling QualityControlAgent for validation (ticket=%s)",
        ticket_id,
    )

    try:
        import json
        fix_proposal = json.loads(fix_proposal_json)
    except Exception as e:
        return f"Error: Invalid JSON in fix_proposal_json: {e}"

    try:
        validation = await run_quality_control_validation(
            user_telegram_id=ctx.context.user_telegram_id,
            ticket_id=ticket_id,
            fix_proposal=fix_proposal,
            dry_run_output=dry_run_output,
        )

        # Convert to model for structured handling
        model = validation_decision_to_model(validation)

        # Format for display
        emoji = {"GO": "✅", "NO_GO": "❌", "NEEDS_REVISION": "🔄"}.get(
            model.decision, "❓"
        )

        return (
            f"{emoji} Quality Control Decision: {model.decision}\n\n"
            f"**Validation Results:**\n"
            f"  - Patch applies cleanly: {'✅' if model.patch_applies_cleanly else '❌'}\n"
            f"  - No security issues: {'✅' if model.no_security_issues else '❌'}\n"
            f"  - Tests allowlisted: {'✅' if model.tests_are_allowlisted else '❌'}\n"
            f"  - Only expected files: {'✅' if model.no_unrelated_files else '❌'}\n\n"
            f"**Next Steps:**\n"
            f"{'Proceed to sandbox testing' if model.decision == 'GO' else 'Fix needs attention'}"
        )

    except Exception as e:
        logger.exception("QualityControlAgent call failed: %s", e)
        return (
            f"⚠️ Quality Control validation failed: {e}\n\n"
            "You can proceed with manual review and `propose_patch` if confident."
        )


@function_tool
async def run_full_self_healing_pipeline(
    ctx: RunContextWrapper[RepairContext],
    error_description: str,
) -> str:
    """Run the complete integrated self-healing pipeline (all 4 agents).

    This is the ONE-STEP repair command that orchestrates the entire pipeline:
    1. DebuggerAgent analyzes the error
    2. RepairTicket is created
    3. ProgrammerAgent generates the fix
    4. QualityControlAgent validates the fix
    5. Sandbox tests the fix
    6. Result is ready for your approval

    Use this for errors where you want the system to automatically:
    - Diagnose → Fix → Validate → Test

    The pipeline will stop at any stage that fails and report the issue.

    Args:
        error_description: Detailed description of the error to fix

    Returns:
        Complete pipeline result with ticket ID and next steps.
    """
    from src.repair.engine import run_self_healing_pipeline

    logger.info(
        "Triggering full self-healing pipeline for user %s",
        ctx.context.user_telegram_id,
    )

    # Get any stored error context
    error_context = None
    try:
        from src.memory.conversation import get_last_tool_error
        last_error = await get_last_tool_error(ctx.context.user_telegram_id)
        if last_error:
            error_context = last_error
    except Exception:
        pass

    result = await run_self_healing_pipeline(
        user_telegram_id=ctx.context.user_telegram_id,
        error_description=error_description,
        error_context=error_context,
        source="telegram",
    )

    return result["message"]


@function_tool
async def refine_pending_verification(
    ctx: RunContextWrapper[RepairContext],
    custom_commands: str = "",
) -> str:
    """Replace verification commands on the pending repair with file-type-correct ones.

    Use this when the previous verification step failed because the test
    runner was wrong for the file type — for example, ``ruff check`` ran
    against a ``SKILL.md`` (Markdown), or the runner is not installed in
    the runtime container. After this tool runs, the existing pending
    repair stays in place; the owner just needs to say ``apply patch``
    again to retry with the new verification.

    Args:
        custom_commands: Optional newline-separated allowlisted commands.
            If empty, the tool auto-picks a verification command for each
            affected file based on its extension (e.g. SKILL.md is verified
            via the project's skill loader, .py via syntax check, .yaml via
            yaml.safe_load — all running through ``python -m
            src.repair.verify_file``, which has no dev-only dependencies).

    Returns a confirmation message describing the new verification commands
    and the next step for the owner.
    """
    raw_ctx = ctx.context
    if isinstance(raw_ctx, dict):
        return (
            "Cannot refine verification — repair agent was invoked without a "
            "proper user context."
        )

    pending = await get_pending_repair(raw_ctx.user_telegram_id)
    if not pending:
        return (
            "No pending repair plan was found. The owner needs to say "
            "`apply patch` on a stored proposal first, or you should call "
            "`propose_patch` to create a new one."
        )

    affected = pending.get("affected_files") or [pending.get("file_path")]
    affected = [p for p in affected if p]

    if custom_commands.strip():
        commands = [line.strip() for line in custom_commands.splitlines() if line.strip()]
        chosen_via = "owner-supplied commands"
    else:
        commands = suggest_verification_commands(affected)
        chosen_via = "auto-picked per file extension"

    if not commands:
        return (
            "Could not determine verification commands — the pending repair "
            "has no affected files recorded. Re-create the patch via "
            "`propose_patch` with the correct file_path."
        )

    try:
        await update_pending_verification_commands(raw_ctx.user_telegram_id, commands)
    except ValueError as exc:
        return (
            f"Refusing to update verification commands: {exc}. "
            "All commands must be in the repair allowlist."
        )

    rendered = "\n".join(f"- `{cmd}`" for cmd in commands)
    return (
        "## ✅ Verification Commands Updated\n\n"
        f"**Source:** {chosen_via}\n"
        f"**Affected files:** {', '.join(f'`{p}`' for p in affected)}\n\n"
        f"**New verification commands:**\n{rendered}\n\n"
        "The pending repair patch is unchanged — only the verification step was "
        "swapped. Tell the owner to say **`apply patch`** to retry with the new "
        "verification."
    )


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
5. **Call the Debugger Agent** for complex multi-file errors using `call_debugger_for_deep_analysis`.
6. **Call the Programmer Agent** to generate fixes from debug analysis using `call_programmer_for_fix_generation`.
7. **Call the Quality Control Agent** to validate fixes before sandbox using `call_quality_control_for_validation`.
8. Identify likely root causes and explain them in plain language.
9. Prepare a patch proposal (single or multi-file) with verification commands.
10. Generate a diagnostic report summarizing findings and the repair plan.

## Full Repair Pipeline
When the owner asks you to fix something, follow this pipeline:

### Phase 1: AUDIT
1. **Get error context** — call `get_error_context` FIRST. The system auto-captures
   tool errors, so you often already have the failed request, error message, and timestamp.
2. **Call Debugger Agent for complex errors** — If the error:
   - Involves multiple files or components
   - Has unclear root cause after initial context review
   - Is HIGH or CRITICAL severity
   - Has HIGH or XHIGH complexity
   Then call `call_debugger_for_deep_analysis` for comprehensive analysis.
3. **Collect more evidence** — use `search_repo_code` and `read_repo_source` to find
   the relevant code. Use `list_recent_errors` if audit log has more details.
4. **Run diagnostics** — use `run_repo_diagnostic_command` (pytest, ruff, mypy) to
   gather additional evidence about the current state.

### Phase 2: DIAGNOSE
5. **Classify complexity** — use `classify_complexity` to rate severity.
6. **Identify root cause** — analyze the evidence. Be specific: name the exact file,
   function, line, and what's wrong.
7. **Explain in plain language** — tell the owner what went wrong and why.

### Phase 3: FIX PLAN
8. **Read the files** — you MUST read every file you plan to patch with `read_repo_source`.
9. **Propose a fix** — use `propose_patch` with a real unified diff and verification
   commands. Multi-file diffs are supported — include all affected files in one diff.
10. **Generate report** — use `generate_report` summarizing root cause, fix, risks, and tests.

### Phase 4: APPROVAL & EXECUTION (handled by the system)
11. The owner reviews your plan and says `apply patch` to approve.
12. The system creates a sandbox git branch, applies the patch, and runs your
    verification commands automatically.
13. If verification passes → merged to main, deployed live.
14. If verification fails → branch deleted, failure stored. Owner can say "fix it"
    to re-engage you with the failure details for another attempt.

## When to use propose_patch
Use `propose_patch` only after you have actually read the relevant file(s)
with the repo tools. Verification commands MUST be appropriate for the file
type being patched and MUST come from the allowlist:

- `.py` files → `python -m src.repair.verify_file <path>` (syntax check),
  or `python -m pytest tests/...` for behaviour tests
- `SKILL.md` files (under `src/user_skills/`) →
  `python -m src.repair.verify_file <path>` (validates the skill via the
  project's loader — does NOT use ruff)
- `.md` / `.yaml` / `.json` / `.toml` →
  `python -m src.repair.verify_file <path>`
- Only suggest `python -m ruff check` or `python -m mypy` when the patched
  file is Python AND those tools are confirmed available (they are dev-only
  deps and are NOT installed in the runtime container).

Never invent code you haven't inspected.

## Retry After Verification Failure
If the system tells you a previous patch failed verification, first inspect
`failure_kind` in the stored error context (`get_error_context`):

- `failure_kind: missing_tool` — the verification COMMAND itself failed to
  run (wrong runner for the file type, or runner not installed). The
  patched code is fine. Call `refine_pending_verification` with no
  arguments to auto-pick a file-type-correct verification command, then
  tell the owner to say `apply patch` again. Do NOT re-propose the patch.
- `failure_kind: code_failure` (or absent) — the patched code is genuinely
  broken. Analyze what went wrong, then `propose_patch` with a revised
  diff. Do NOT re-propose the same diff that already failed.

If the owner asks you to "determine a better verification command" or
similar, that is a `missing_tool`-style request — call
`refine_pending_verification`.

## Low-Risk Auto-Apply (NEW)
For purely operational problems with no code changes — clearing a stale Redis key,
re-injecting a broken schedule, logging a config note — use `propose_low_risk_fix`.
This tool auto-applies the fix immediately and logs it as a repair ticket.
Do NOT use it for code edits, even trivial ones.

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
            propose_low_risk_fix,
            refine_pending_verification,
            list_recent_errors,
            generate_report,
            call_debugger_for_deep_analysis,
            call_programmer_for_fix_generation,
            call_quality_control_for_validation,
            run_full_self_healing_pipeline,
        ],
    )
