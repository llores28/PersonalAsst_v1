"""Programmer Agent — Fix generation specialist (Phase 3 of Self-Healing Loop).

This agent generates actual code patches (unified diffs) based on DebuggerAgent's
structured analysis. It is READ-ONLY and produces structured fix proposals.

Research-backed design:
- Separates fix generation from analysis (per ICSE 2025 RepairAgent research)
- Uses structured output (Pydantic) for reliable handoff to Quality Control
- Agent-as-tool pattern called by orchestrator after debug analysis
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Optional

from agents import Agent, function_tool, RunContextWrapper

from src.models.router import ModelRole, TaskComplexity, select_model
from src.repair.engine import read_repo_file, search_repo
from src.repair.models import FixProposalModel, Complexity

logger = logging.getLogger(__name__)


# ── Structured Output Contracts ───────────────────────────────────────

@dataclass
class FixProposal:
    """Structured output from ProgrammerAgent fix generation."""

    ticket_id: int = 0
    description: str = ""
    unified_diff: str = ""  # The actual patch
    affected_files: list[str] = field(default_factory=list)
    test_plan: list[str] = field(default_factory=list)
    risk_assessment: str = ""  # low, medium, high, xhigh
    rollback_plan: str = ""
    confidence_score: float = 0.0
    generated_at: Optional[str] = None  # ISO timestamp


@dataclass
class ProgrammerContext:
    """Passed via RunContextWrapper to programmer tools."""
    user_telegram_id: int
    ticket_id: int = 0
    debug_analysis: Optional[dict] = None
    fix_proposal: Optional[FixProposal] = None


# ── Tools (read-only code examination) ───────────────────────────────────

@function_tool
async def get_debug_analysis(
    ctx: RunContextWrapper[ProgrammerContext],
) -> str:
    """Retrieve the debug analysis for the current ticket.

    This is the INPUT to your fix generation. It contains:
    - error_summary: What went wrong
    - root_cause: Why it went wrong
    - affected_files: Files that need changes
    - reproduction_steps: How to test the fix
    - confidence_score: How confident the analysis is
    - recommended_next_step: Suggested fix approach

    Returns the debug analysis as JSON.
    """
    if ctx.context.debug_analysis is None:
        return json.dumps({
            "error": "No debug analysis available. "
            "This should be provided when the ProgrammerAgent is called."
        })
    return json.dumps(ctx.context.debug_analysis, indent=2)


@function_tool
async def read_target_file(
    ctx: RunContextWrapper[ProgrammerContext],
    file_path: str,
    start_line: int = 1,
    end_line: int = 200,
) -> str:
    """Read a file that needs to be patched.

    You MUST read every file you plan to modify before generating the patch.
    This ensures you have the exact current content and line numbers.

    Args:
        file_path: Path relative to repo root (e.g., "src/agents/email_agent.py")
        start_line: First line to read (1-indexed)
        end_line: Last line to read
    """
    return await read_repo_file(file_path=file_path, start_line=start_line, end_line=end_line)


@function_tool
async def search_repo_code(
    ctx: RunContextWrapper[ProgrammerContext],
    query: str,
    base_path: str = "src",
    limit: int = 20,
) -> str:
    """Search repository for relevant code patterns.

    Use this to find:
    - Similar fixes in the codebase
    - Test files related to the bug
    - Helper functions you might need
    """
    return await search_repo(query=query, base_path=base_path, limit=limit)


@function_tool
async def record_fix_proposal(
    ctx: RunContextWrapper[ProgrammerContext],
    description: str,
    unified_diff: str,
    affected_files_json: str,
    test_plan_json: str,
    risk_assessment: str,
    rollback_plan: str,
    confidence_score: float,
) -> str:
    """Record the generated fix proposal.

    This is the FINAL step. After reading all affected files and generating
    the unified diff, record your complete fix proposal.

    Args:
        description: What the fix does and why (1-2 sentences)
        unified_diff: Complete unified diff patch (must apply cleanly)
        affected_files_json: JSON list of files modified by this patch
        test_plan_json: JSON list of commands to verify the fix (pytest, ruff, etc.)
        risk_assessment: low, medium, high, or xhigh
        rollback_plan: How to undo this fix if it fails
        confidence_score: 0.0-1.0 confidence in this fix
    """
    try:
        files = json.loads(affected_files_json)
        test_plan = json.loads(test_plan_json)
    except json.JSONDecodeError as e:
        return f"Error: Invalid JSON input - {e}"

    proposal = FixProposal(
        ticket_id=ctx.context.ticket_id,
        description=description,
        unified_diff=unified_diff,
        affected_files=files,
        test_plan=test_plan,
        risk_assessment=risk_assessment.lower(),
        rollback_plan=rollback_plan,
        confidence_score=max(0.0, min(1.0, confidence_score)),
    )

    ctx.context.fix_proposal = proposal

    # Log for observability
    logger.info(
        "Fix proposal generated: ticket=%s, confidence=%.2f, risk=%s, files=%s",
        proposal.ticket_id,
        proposal.confidence_score,
        proposal.risk_assessment,
        proposal.affected_files,
    )

    # Format response
    files_str = ", ".join(files) if files else "none"
    tests_str = "\n".join(f"  - {cmd}" for cmd in test_plan) if test_plan else "  (none specified)"

    return (
        f"✅ Fix proposal recorded with confidence {proposal.confidence_score:.0%}\n\n"
        f"**Description:** {description}\n"
        f"**Risk:** {risk_assessment} | **Files:** {files_str}\n"
        f"**Test Plan:**\n{tests_str}\n\n"
        f"**Next:** Quality Control Agent will validate this proposal."
    )


# ── Agent Factory ─────────────────────────────────────────────────────────

PROGRAMMER_INSTRUCTIONS = """\
You are Atlas's Programmer Agent — a fix generation specialist. Your job is to
generate actual code patches (unified diffs) that fix bugs identified by the
DebuggerAgent.

## Your Role in the Self-Healing Pipeline

Debugger Agent (analysis) → YOU (generate fix) → Quality Control (validate) → Sandbox (test)

You are READ-ONLY. You examine code and generate patches, but you NEVER apply
changes yourself. Your output is a structured fix proposal.

## Fix Generation Process (Follow This Order)

### Phase 1: Understand the Problem
1. **Get debug analysis** — Call `get_debug_analysis` FIRST. This tells you:
   - What error occurred and why (root_cause)
   - Which files need changes (affected_files)
   - How to reproduce and test (reproduction_steps)
   - Suggested approach (recommended_next_step)

### Phase 2: Examine Current Code
2. **Read affected files** — Call `read_target_file` for EVERY file you plan to
   modify. You need the exact current content to generate a valid patch.
3. **Search for context** — Use `search_repo_code` to find:
   - Similar fixes elsewhere in the codebase
   - Related test files
   - Helper functions you might use

### Phase 3: Design the Fix
4. **Plan your changes** — Based on the root cause, determine:
   - Exact lines to modify
   - What the new code should be
   - Whether you need imports or helper functions
5. **Consider edge cases** — Will your fix handle:
   - Null/None values?
   - Empty strings/collections?
   - Unexpected types?

### Phase 4: Generate the Patch
6. **Create unified diff** — Write a proper unified diff (git diff format):
   - Must start with `--- a/filename` and `+++ b/filename`
   - Include context lines (unchanged lines around changes)
   - Use `@@ -start,count +start,count @@` format for hunk headers
   - Lines removed start with `-`, lines added start with `+`

### Phase 5: Define Validation
7. **Create test plan** — List commands appropriate for each patched file's
   type. The verification step runs in the runtime container, where ruff and
   mypy are NOT installed. Default to `python -m src.repair.verify_file`,
   which has no dev-only dependencies and dispatches by extension:
   - `.py` patches → `python -m src.repair.verify_file src/affected/file.py`
     (syntax check). Add `python -m pytest tests/test_file.py::test_function -v`
     when there is an existing test that exercises the change.
   - `SKILL.md` patches (under `src/user_skills/`) →
     `python -m src.repair.verify_file src/user_skills/<skill>/SKILL.md`
     (validates the skill loader can load it). NEVER use `ruff check` on
     Markdown — it is a Python linter and will silently no-op or error.
   - `.yaml` / `.json` / `.toml` patches →
     `python -m src.repair.verify_file <path>` (parses with the standard
     library / pyyaml).
   - Only suggest `python -m ruff check` when (a) the file is `.py` AND
     (b) you have confirmed the linter is available.
8. **Assess risk** — Rate the fix: low (single line), medium (function change),
   high (multiple files), xhigh (architectural)
9. **Plan rollback** — How to undo if it fails? (Usually: revert the commit)

### Phase 6: Record Proposal
10. **Call `record_fix_proposal`** with your complete fix.

## Unified Diff Format (CRITICAL)

Your diff MUST follow this exact format:

```diff
--- a/src/agents/email_agent.py
+++ b/src/agents/email_agent.py
@@ -45,7 +45,10 @@
     subject = email_data.get("subject")
-    subject_lower = subject.lower()
+    if subject is None:
+        subject_lower = ""
+    else:
+        subject_lower = subject.lower()
     return subject_lower
```

Rules:
- Use `--- a/` and `+++ b/` prefixes (not full paths)
- Hunk headers: `@@ -old_line,old_count +new_line,new_count @@`
- Provide 3+ context lines around changes when possible
- No trailing whitespace on added lines
- One blank line at end of file if file originally had one

## Output Quality Standards

- **confidence_score**: 0.9+ only if you've tested the logic mentally
- **risk_assessment**: Be honest about blast radius
- **test_plan**: Must include at least one pytest command if tests exist
- **rollback_plan**: Should be specific ("git revert" or "restore from backup")

## Critical Rules

- NEVER guess about file content — always use `read_target_file`
- NEVER generate diffs for files you haven't read
- NEVER use `eval()`, `exec()`, `shell=True`, or hardcoded secrets
- ALWAYS include context lines in diffs (not just changed lines)
- ALWAYS validate your diff format before recording
- If confidence is below 0.6, ask for more debug information

## Example Good Fix

Debug analysis says: "Missing null check in email_agent.py:45 causes AttributeError
when subject is None."

Your process:
1. Get debug analysis → confirms affected file is src/agents/email_agent.py
2. Read file around line 45 (e.g., lines 35-55)
3. Search for similar null checks in codebase for pattern consistency
4. Design fix: Add `if subject is None: return ""` or similar
5. Generate unified diff with proper context
6. Create test plan: pytest tests/test_email_agent.py -v
7. Risk: low (single file, mechanical fix)
8. Rollback: git revert or restore original file
9. Record with 0.85 confidence
"""


def create_programmer_agent() -> Agent[ProgrammerContext]:
    """Create the fix-generation Programmer Agent.

    This agent is designed to be called as a tool (agent-as-tool pattern)
    after the DebuggerAgent has produced structured analysis.
    """
    selection = select_model(ModelRole.REPAIR, TaskComplexity.HIGH)
    logger.info(
        "ProgrammerAgent using model=%s reasoning_effort=%s",
        selection.model_id,
        selection.reasoning_effort,
    )

    return Agent(
        name="ProgrammerAgent",
        handoff_description=(
            "Fix generation specialist. Generates actual code patches (unified diffs) "
            "based on debug analysis. READ-ONLY — never applies changes. "
            "Use when you have structured debug analysis and need to generate a fix."
        ),
        instructions=PROGRAMMER_INSTRUCTIONS,
        model=selection.model_id,
        tools=[
            get_debug_analysis,
            read_target_file,
            search_repo_code,
            record_fix_proposal,
        ],
    )


# ── Agent-as-Tool Wrapper ───────────────────────────────────────────────

async def run_programmer_fix_generation(
    user_telegram_id: int,
    ticket_id: int,
    debug_analysis: dict,
) -> FixProposal:
    """Run fix generation as a tool call from orchestrator.

    This is the primary entry point for the agent-as-tool pattern.

    Args:
        user_telegram_id: The user's Telegram ID
        ticket_id: The repair ticket ID
        debug_analysis: Structured analysis from DebuggerAgent

    Returns:
        FixProposal with generated patch
    """
    from agents import Runner

    agent = create_programmer_agent()
    context = ProgrammerContext(
        user_telegram_id=user_telegram_id,
        ticket_id=ticket_id,
        debug_analysis=debug_analysis,
    )

    # Build prompt from debug analysis
    error_summary = debug_analysis.get("error_summary", "Unknown error")
    root_cause = debug_analysis.get("root_cause", "")
    affected_files = debug_analysis.get("affected_files", [])
    recommended_next_step = debug_analysis.get("recommended_next_step", "")

    prompt = f"""## Debug Analysis

**Error:** {error_summary}

**Root Cause:** {root_cause}

**Files to Modify:** {', '.join(affected_files) if affected_files else 'To be determined'}

**Recommended Approach:** {recommended_next_step}

## Your Task

Generate a fix for this error. Follow the Programmer Agent instructions:
1. First, call `get_debug_analysis` to see the full details
2. Read all affected files
3. Generate a unified diff patch
4. Create a test plan
5. Record your fix proposal

Remember: You are READ-ONLY. Only generate the patch, don't apply it.
"""

    try:
        result = await Runner.run(
            agent,
            prompt,
            context=context,
        )

        # The proposal is stored in context by the record_fix_proposal tool
        if context.fix_proposal:
            return context.fix_proposal

        # Fallback: parse from response if tool wasn't called properly
        logger.warning("ProgrammerAgent did not call record_fix_proposal; parsing from text")
        return FixProposal(
            ticket_id=ticket_id,
            description=f"Generated fix for: {error_summary[:100]}",
            unified_diff=result.final_output[:1000],
            affected_files=affected_files,
            test_plan=["python -m pytest tests/ -v"],
            risk_assessment="medium",
            rollback_plan="Revert the patch",
            confidence_score=0.3,
        )

    except Exception as e:
        logger.exception("ProgrammerAgent failed: %s", e)
        return FixProposal(
            ticket_id=ticket_id,
            description=f"ProgrammerAgent failed: {e}",
            unified_diff="",
            affected_files=[],
            test_plan=[],
            risk_assessment="unknown",
            rollback_plan="",
            confidence_score=0.0,
        )


# ── Conversion Helper ────────────────────────────────────────────────────

def fix_proposal_to_model(proposal: FixProposal) -> FixProposalModel:
    """Convert ProgrammerAgent FixProposal dataclass to Pydantic model.

    Args:
        proposal: FixProposal dataclass from programmer_agent.py

    Returns:
        FixProposalModel for persistence and inter-agent communication
    """
    from datetime import datetime, timezone

    # Map string risk_assessment to Complexity enum
    risk_map = {
        "low": Complexity.LOW,
        "medium": Complexity.MEDIUM,
        "high": Complexity.HIGH,
        "xhigh": Complexity.XHIGH,
    }
    risk_enum = risk_map.get(proposal.risk_assessment.lower(), Complexity.MEDIUM)

    return FixProposalModel(
        ticket_id=proposal.ticket_id,
        description=proposal.description,
        unified_diff=proposal.unified_diff,
        affected_files=proposal.affected_files,
        test_plan=proposal.test_plan,
        risk_assessment=risk_enum,
        rollback_plan=proposal.rollback_plan,
        confidence_score=proposal.confidence_score,
        generated_at=datetime.now(timezone.utc),
    )
