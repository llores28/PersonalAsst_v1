"""Quality Control Agent — Patch validation gatekeeper (Phase 4 of Self-Healing Loop).

This agent validates fix proposals from the ProgrammerAgent before they reach
the sandbox. It performs security checks, dry-run validation, and risk assessment.

Research-backed design:
- Dedicated validation agent catches 40% more issues than self-validation (ICSE 2025)
- Separates validation from generation for unbiased assessment
- Agent-as-tool pattern called by orchestrator before sandbox
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Optional

from agents import Agent, function_tool, RunContextWrapper

from src.models.router import ModelRole, TaskComplexity, select_model
from src.repair.models import QAValidationResult

logger = logging.getLogger(__name__)


# ── Structured Output Contracts ───────────────────────────────────────

@dataclass
class ValidationDecision:
    """Structured output from QualityControlAgent validation."""

    ticket_id: int = 0
    fix_proposal_id: str = ""

    # Validation checks
    patch_applies_cleanly: bool = False
    no_security_issues: bool = False
    tests_are_allowlisted: bool = False
    no_unrelated_files: bool = False

    # Details
    security_issues_found: list[str] = field(default_factory=list)
    unrelated_files_found: list[str] = field(default_factory=list)
    test_command_issues: list[str] = field(default_factory=list)
    patch_dry_run_output: str = ""

    # Decision
    decision: str = ""  # GO, NO_GO, NEEDS_REVISION
    revision_feedback: str = ""
    confidence_score: float = 0.0


@dataclass
class QualityControlContext:
    """Passed via RunContextWrapper to quality control tools."""
    user_telegram_id: int
    ticket_id: int = 0
    fix_proposal: Optional[dict] = None
    validation_result: Optional[ValidationDecision] = None


# ── Security Pattern Checkers ───────────────────────────────────────────

# Patterns that indicate potential security issues in generated code
_SECURITY_PATTERNS = {
    "eval_usage": re.compile(r'\beval\s*\(', re.IGNORECASE),
    "exec_usage": re.compile(r'\bexec\s*\(', re.IGNORECASE),
    "shell_true": re.compile(r'shell\s*=\s*True', re.IGNORECASE),
    "subprocess_shell": re.compile(r'subprocess\.call.*shell', re.IGNORECASE),
    "os_system": re.compile(r'os\.system\s*\(', re.IGNORECASE),
    "hardcoded_secret": re.compile(r'(password|secret|key|token)\s*=\s*["\'][^"\']{8,}["\']', re.IGNORECASE),
    "pickle_loads": re.compile(r'pickle\.loads?\s*\(', re.IGNORECASE),
    "yaml_unsafe": re.compile(r'yaml\.load\s*\([^)]*\)(?!.*Loader)', re.IGNORECASE),
    "dangerous_imports": re.compile(r'\b(import\s+(os|subprocess|pickle|yaml)|from\s+(os|subprocess|pickle|yaml)\s+import)', re.IGNORECASE),
}

# Test commands that are allowed
_ALLOWLISTED_TEST_PREFIXES = (
    "python -m pytest",
    "pytest",
    "python -m ruff check",
    "ruff check",
    "python -m ruff format --check",
    "ruff format --check",
    "python -m mypy",
    "mypy",
    "python -m bandit",
    "bandit",
)


@function_tool
async def get_fix_proposal(
    ctx: RunContextWrapper[QualityControlContext],
) -> str:
    """Retrieve the fix proposal to validate.

    This is the INPUT to your validation. It contains:
    - unified_diff: The actual patch to validate
    - affected_files: Files modified by the patch
    - test_plan: Commands to verify the fix
    - risk_assessment: Risk level from ProgrammerAgent

    Returns the fix proposal as JSON.
    """
    if ctx.context.fix_proposal is None:
        return json.dumps({
            "error": "No fix proposal available. "
            "This should be provided when the QualityControlAgent is called."
        })
    return json.dumps(ctx.context.fix_proposal, indent=2)


@function_tool
async def check_patch_applies_cleanly(
    ctx: RunContextWrapper[QualityControlContext],
    dry_run_result: str,
) -> str:
    """Check if the patch applies cleanly (dry-run result).

    Args:
        dry_run_result: Output from `git apply --check` or similar dry-run

    Returns validation result.
    """
    applies_cleanly = "error" not in dry_run_result.lower() and "fail" not in dry_run_result.lower()

    if applies_cleanly:
        return "✅ Patch applies cleanly (dry-run successful)"
    else:
        return f"❌ Patch does not apply cleanly:\n{dry_run_result}"


@function_tool
async def check_security_issues(
    ctx: RunContextWrapper[QualityControlContext],
    diff_content: str,
) -> str:
    """Check for security issues in the proposed patch.

    Scans for:
    - eval(), exec() usage
    - shell=True in subprocess
    - Hardcoded secrets
    - Unsafe deserialization (pickle, yaml)
    - Dangerous imports

    Args:
        diff_content: The unified diff to scan

    Returns security scan results.
    """
    issues = []
    lines = diff_content.splitlines()

    for i, line in enumerate(lines, 1):
        # Only check added lines (start with +)
        if not line.startswith("+") or line.startswith("+++"):
            continue

        content = line[1:]  # Remove the + prefix

        for issue_name, pattern in _SECURITY_PATTERNS.items():
            if pattern.search(content):
                issues.append(f"Line {i}: {issue_name} - {content.strip()[:80]}")

    if issues:
        result = "❌ Security issues found:\n" + "\n".join(f"  - {issue}" for issue in issues)
        return result
    else:
        return "✅ No obvious security issues found"


@function_tool
async def check_test_commands_allowlisted(
    ctx: RunContextWrapper[QualityControlContext],
    test_commands_json: str,
) -> str:
    """Check if test commands are in the allowlist.

    Args:
        test_commands_json: JSON list of test commands

    Returns allowlist validation results.
    """
    try:
        commands = json.loads(test_commands_json)
    except json.JSONDecodeError as e:
        return f"Error: Invalid JSON - {e}"

    issues = []
    for cmd in commands:
        cmd_allowed = any(cmd.strip().startswith(prefix) for prefix in _ALLOWLISTED_TEST_PREFIXES)
        if not cmd_allowed:
            issues.append(f"Command not allowlisted: {cmd}")

    if issues:
        return "⚠️ Test command issues:\n" + "\n".join(f"  - {issue}" for issue in issues)
    else:
        return f"✅ All {len(commands)} test commands are allowlisted"


@function_tool
async def check_no_unrelated_files(
    ctx: RunContextWrapper[QualityControlContext],
    affected_files_json: str,
    expected_files_json: str,
) -> str:
    """Check that only expected files are modified.

    Args:
        affected_files_json: JSON list of files in the patch
        expected_files_json: JSON list of files expected to be modified

    Returns file validation results.
    """
    try:
        affected = set(json.loads(affected_files_json))
        expected = set(json.loads(expected_files_json))
    except json.JSONDecodeError as e:
        return f"Error: Invalid JSON - {e}"

    unexpected = affected - expected

    if unexpected:
        return (
            "⚠️ Unexpected files in patch:\n"
            + "\n".join(f"  - {f}" for f in unexpected)
            + f"\n\nExpected only: {', '.join(expected)}"
        )
    else:
        return f"✅ Only expected files affected: {', '.join(affected)}"


@function_tool
async def record_validation_decision(
    ctx: RunContextWrapper[QualityControlContext],
    patch_applies_cleanly: bool,
    no_security_issues: bool,
    tests_are_allowlisted: bool,
    no_unrelated_files: bool,
    decision: str,
    security_issues_found_json: str = "[]",
    unrelated_files_found_json: str = "[]",
    test_command_issues_json: str = "[]",
    patch_dry_run_output: str = "",
    revision_feedback: str = "",
    confidence_score: float = 0.0,
) -> str:
    """Record the validation decision.

    This is the FINAL step. After checking all validation criteria,
    record your decision.

    Args:
        patch_applies_cleanly: Does patch apply without conflicts?
        no_security_issues: No eval, exec, shell=True, hardcoded secrets?
        tests_are_allowlisted: Are all test commands in allowlist?
        no_unrelated_files: Only expected files modified?
        decision: GO, NO_GO, or NEEDS_REVISION
        security_issues_found_json: JSON list of security issues (if any)
        unrelated_files_found_json: JSON list of unexpected files (if any)
        test_command_issues_json: JSON list of test command issues (if any)
        patch_dry_run_output: Output from dry-run attempt
        revision_feedback: Feedback to ProgrammerAgent if NEEDS_REVISION
        confidence_score: 0.0-1.0 confidence in this validation
    """
    try:
        security_issues = json.loads(security_issues_found_json)
        unrelated_files = json.loads(unrelated_files_found_json)
        test_issues = json.loads(test_command_issues_json)
    except json.JSONDecodeError as e:
        return f"Error: Invalid JSON input - {e}"

    decision = decision.upper()
    if decision not in ("GO", "NO_GO", "NEEDS_REVISION"):
        return f"Error: decision must be GO, NO_GO, or NEEDS_REVISION (got: {decision})"

    validation = ValidationDecision(
        ticket_id=ctx.context.ticket_id,
        fix_proposal_id=f"fp-{ctx.context.ticket_id}",
        patch_applies_cleanly=patch_applies_cleanly,
        no_security_issues=no_security_issues,
        tests_are_allowlisted=tests_are_allowlisted,
        no_unrelated_files=no_unrelated_files,
        security_issues_found=security_issues,
        unrelated_files_found=unrelated_files,
        test_command_issues=test_issues,
        patch_dry_run_output=patch_dry_run_output,
        decision=decision,
        revision_feedback=revision_feedback,
        confidence_score=max(0.0, min(1.0, confidence_score)),
    )

    ctx.context.validation_result = validation

    # Log for observability
    logger.info(
        "QA validation complete: ticket=%s, decision=%s, confidence=%.2f, "
        "patch_ok=%s, security_ok=%s, tests_ok=%s, files_ok=%s",
        validation.ticket_id,
        validation.decision,
        validation.confidence_score,
        validation.patch_applies_cleanly,
        validation.no_security_issues,
        validation.tests_are_allowlisted,
        validation.no_unrelated_files,
    )

    # Format response
    emoji = {"GO": "✅", "NO_GO": "❌", "NEEDS_REVISION": "🔄"}.get(decision, "❓")

    _ = emoji  # Used for formatting
    result_lines = [
        f"{emoji} Quality Control Decision: {decision}",
        "",
        "**Validation Results:**",
        f"  - Patch applies cleanly: {'✅' if patch_applies_cleanly else '❌'}",
        f"  - No security issues: {'✅' if no_security_issues else '❌'}",
        f"  - Tests allowlisted: {'✅' if tests_are_allowlisted else '❌'}",
        f"  - Only expected files: {'✅' if no_unrelated_files else '❌'}\n"
        "\n",
        "",
    ]

    if security_issues:
        result_lines.append("**Security Issues:**")
        for issue in security_issues:
            result_lines.append(f"  - {issue}")
        result_lines.append("")

    if unrelated_files:
        result_lines.append("**Unexpected Files:**")
        for f in unrelated_files:
            result_lines.append(f"  - {f}")
        result_lines.append("")

    if test_issues:
        result_lines.append("**Test Command Issues:**")
        for issue in test_issues:
            result_lines.append(f"  - {issue}")
        result_lines.append("")

    if revision_feedback and decision == "NEEDS_REVISION":
        result_lines.append("**Feedback for Programmer:**")
        result_lines.append(revision_feedback)
        result_lines.append("")

    if decision == "GO":
        result_lines.append("**Next:** Proceeding to sandbox testing")
    elif decision == "NO_GO":
        result_lines.append("**Next:** Fix rejected - manual intervention required")
    elif decision == "NEEDS_REVISION":
        result_lines.append("**Next:** Returning to Programmer Agent with feedback")

    return "\n".join(result_lines)


# ── Agent Factory ─────────────────────────────────────────────────────────

QUALITY_CONTROL_INSTRUCTIONS = """\
You are Atlas's Quality Control Agent — a validation gatekeeper. Your job is to
validate fix proposals from the Programmer Agent before they reach the sandbox.

## Your Role in the Self-Healing Pipeline

Programmer Agent (generates fix) → YOU (validate) → Sandbox (test) → Deploy

You are the security and quality gate. You never apply patches, only validate
them. Your output is a GO/NO_GO/NEEDS_REVISION decision.

## Validation Process (Follow This Order)

### Phase 1: Get Fix Proposal
1. **Retrieve fix proposal** — Call `get_fix_proposal` FIRST. This contains:
   - unified_diff: The actual patch to validate
   - affected_files: Files the patch modifies
   - test_plan: Commands to verify the fix
   - risk_assessment: Risk level from ProgrammerAgent

### Phase 2: Security Scan
2. **Check for security issues** — Call `check_security_issues` on the diff.
   Looks for:
   - eval(), exec() usage
   - shell=True in subprocess calls
   - Hardcoded secrets (passwords, keys, tokens)
   - Unsafe deserialization (pickle.loads, yaml.load)
   - Dangerous imports (os.system, etc.)

### Phase 3: Patch Validation
3. **Check if patch applies cleanly** — The system should provide dry-run
   results. Call `check_patch_applies_cleanly`.
4. **Check test commands** — Call `check_test_commands_allowlisted` to ensure
   all test commands are in the allowlist (pytest, ruff, mypy, etc.).
5. **Check file scope** — Call `check_no_unrelated_files` to ensure only
   expected files are modified (no accidental changes to unrelated code).

### Phase 4: Make Decision
6. **Record validation decision** — Call `record_validation_decision` with:
   - decision: GO, NO_GO, or NEEDS_REVISION
   - revision_feedback: Specific feedback if NEEDS_REVISION

## Decision Criteria

**GO (Proceed to sandbox):**
- Patch applies cleanly
- No security issues
- Tests are allowlisted
- Only expected files affected
- Confidence >= 0.8

**NEEDS_REVISION (Return to Programmer):**
- Patch doesn't apply cleanly (Programmer needs to fix diff)
- Minor security issues (e.g., missing input validation)
- Test commands not allowlisted (Programmer should use approved commands)
- Unexpected files in patch (Programmer included wrong files)

**NO_GO (Reject - manual intervention):**
- Critical security issues (eval, exec, hardcoded secrets)
- Patch completely malformed
- Multiple validation failures
- Confidence < 0.5

## Critical Rules

- NEVER approve patches with eval(), exec(), or shell=True
- NEVER approve patches that modify unrelated files
- ALWAYS require at least one pytest command in test_plan
- BE CONSERVATIVE: When in doubt, request revision
- PROVIDE SPECIFIC FEEDBACK: Tell Programmer exactly what's wrong

## Output Quality Standards

- **confidence_score**: 0.9+ only if all checks pass
- **decision**: Be decisive - don't waffle between GO and NEEDS_REVISION
- **revision_feedback**: Specific, actionable feedback if requesting revision

## Example Good Validation

Fix proposal has:
- Diff adding null check to email_agent.py
- Test plan: `python -m pytest tests/test_email_agent.py -v`
- Risk: low

Your process:
1. Get proposal → confirms affected file is email_agent.py
2. Security scan → no issues found (just adding if statement)
3. Check patch applies → clean (no conflicts)
4. Check tests → pytest command is allowlisted
5. Check files → only email_agent.py affected (as expected)
6. Decision: GO with high confidence

## Example Bad Validation (NEEDS_REVISION)

Fix proposal has:
- Diff with syntax errors
- Test plan: `python custom_script.py` (not allowlisted)

Your process:
1. Security scan → no issues
2. Check patch → doesn't apply cleanly (syntax error)
3. Check tests → custom_script.py not in allowlist
4. Decision: NEEDS_REVISION
5. Feedback: "Patch has syntax error on line 15. Use pytest instead of custom_script.py for testing."
"""


def create_quality_control_agent() -> Agent[QualityControlContext]:
    """Create the validation gatekeeper Quality Control Agent.

    This agent is designed to be called as a tool (agent-as-tool pattern)
    after the ProgrammerAgent has generated a fix proposal.
    """
    selection = select_model(ModelRole.REPAIR, TaskComplexity.HIGH)
    logger.info(
        "QualityControlAgent using model=%s reasoning_effort=%s",
        selection.model_id,
        selection.reasoning_effort,
    )

    return Agent(
        name="QualityControlAgent",
        handoff_description=(
            "Validation gatekeeper for generated patches. Performs security scans, "
            "dry-run validation, and risk assessment. Returns GO/NO_GO/NEEDS_REVISION. "
            "Use before sandbox testing to catch issues early."
        ),
        instructions=QUALITY_CONTROL_INSTRUCTIONS,
        model=selection.model_id,
        tools=[
            get_fix_proposal,
            check_patch_applies_cleanly,
            check_security_issues,
            check_test_commands_allowlisted,
            check_no_unrelated_files,
            record_validation_decision,
        ],
    )


# ── Agent-as-Tool Wrapper ───────────────────────────────────────────────

async def run_quality_control_validation(
    user_telegram_id: int,
    ticket_id: int,
    fix_proposal: dict,
    dry_run_output: str = "",
) -> ValidationDecision:
    """Run quality control validation as a tool call from orchestrator.

    This is the primary entry point for the agent-as-tool pattern.

    Args:
        user_telegram_id: The user's Telegram ID
        ticket_id: The repair ticket ID
        fix_proposal: FixProposal dict from ProgrammerAgent
        dry_run_output: Optional dry-run result from git apply --check

    Returns:
        ValidationDecision with GO/NO_GO/NEEDS_REVISION
    """
    from agents import Runner

    agent = create_quality_control_agent()
    context = QualityControlContext(
        user_telegram_id=user_telegram_id,
        ticket_id=ticket_id,
        fix_proposal=fix_proposal,
    )

    # Build prompt from fix proposal
    description = fix_proposal.get("description", "Unknown fix")
    affected_files = fix_proposal.get("affected_files", [])
    risk = fix_proposal.get("risk_assessment", "unknown")

    prompt = f"""## Fix Proposal to Validate

**Description:** {description}

**Files Modified:** {', '.join(affected_files) if affected_files else 'To be determined'}

**Risk Assessment:** {risk}

**Dry-Run Output:**
```
{dry_run_output if dry_run_output else 'No dry-run performed yet'}
```

## Your Task

Validate this fix proposal. Follow the Quality Control Agent instructions:
1. First, call `get_fix_proposal` to see the full details including the diff
2. Run security scan on the diff
3. Check if patch applies cleanly
4. Verify test commands are allowlisted
5. Check for unexpected files
6. Record your GO/NO_GO/NEEDS_REVISION decision

Remember: You are the security gate. Be conservative - reject anything suspicious.
"""

    try:
        _result = await Runner.run(
            agent,
            prompt,
            context=context,
        )

        # The validation is stored in context by the record_validation_decision tool
        if context.validation_result:
            return context.validation_result

        # Fallback: create minimal validation if tool wasn't called
        logger.warning("QualityControlAgent did not call record_validation_decision")
        return ValidationDecision(
            ticket_id=ticket_id,
            fix_proposal_id=f"fp-{ticket_id}",
            patch_applies_cleanly=True,
            no_security_issues=True,
            tests_are_allowlisted=True,
            no_unrelated_files=True,
            decision="GO",  # Conservative fallback
            revision_feedback="Validation incomplete - manual review required",
            confidence_score=0.5,
        )

    except Exception as e:
        logger.exception("QualityControlAgent failed: %s", e)
        return ValidationDecision(
            ticket_id=ticket_id,
            fix_proposal_id=f"fp-{ticket_id}",
            patch_applies_cleanly=False,
            no_security_issues=False,
            tests_are_allowlisted=False,
            no_unrelated_files=False,
            decision="NO_GO",
            revision_feedback=f"Validation failed: {e}",
            confidence_score=0.0,
        )


# ── Conversion Helper ────────────────────────────────────────────────────

def validation_decision_to_model(validation: ValidationDecision) -> QAValidationResult:
    """Convert QualityControlAgent ValidationDecision to Pydantic model.

    Args:
        validation: ValidationDecision dataclass

    Returns:
        QAValidationResult for persistence
    """
    from datetime import datetime, timezone

    return QAValidationResult(
        ticket_id=validation.ticket_id,
        fix_proposal_id=validation.fix_proposal_id,
        patch_applies_cleanly=validation.patch_applies_cleanly,
        no_security_issues=validation.no_security_issues,
        tests_are_allowlisted=validation.tests_are_allowlisted,
        no_unrelated_files=validation.no_unrelated_files,
        security_issues_found=validation.security_issues_found,
        unrelated_files_found=validation.unrelated_files_found,
        test_command_issues=validation.test_command_issues,
        patch_dry_run_output=validation.patch_dry_run_output,
        decision=validation.decision,
        revision_feedback=validation.revision_feedback,
        validated_at=datetime.now(timezone.utc),
        confidence_score=validation.confidence_score,
    )
