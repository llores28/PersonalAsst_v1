"""Debugger Agent — Deep error analysis specialist (Phase 1 of Self-Healing Loop).

This agent performs comprehensive root-cause analysis for errors detected in Atlas.
It is READ-ONLY and produces structured analysis output that feeds into the repair pipeline.

Research-backed design:
- Separates deep analysis from fix generation (per ICSE 2025 RepairAgent research)
- Uses structured output (Pydantic) for reliable inter-agent communication
- Agent-as-tool pattern called by RepairAgent for complex errors
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Optional

from agents import Agent, function_tool, RunContextWrapper

from src.models.router import ModelRole, TaskComplexity, select_model
from src.repair.engine import (
    list_recent_errors_for_user,
    read_repo_file,
    run_repo_diagnostics,
    search_repo,
)
from src.memory.conversation import get_last_tool_error

logger = logging.getLogger(__name__)


# ── Structured Output Contracts ───────────────────────────────────────

@dataclass
class DebugAnalysis:
    """Structured output from DebuggerAgent analysis."""
    
    error_summary: str = ""
    root_cause: str = ""
    affected_components: list[str] = field(default_factory=list)
    affected_files: list[str] = field(default_factory=list)
    reproduction_steps: list[str] = field(default_factory=list)
    confidence_score: float = 0.0  # 0.0-1.0
    severity: str = ""  # low, medium, high, critical
    complexity: str = ""  # low, medium, high, xhigh
    related_errors: list[dict] = field(default_factory=list)
    diagnostic_evidence: dict = field(default_factory=dict)
    recommended_next_step: str = ""


@dataclass
class DebuggerContext:
    """Passed via RunContextWrapper to debugger tools."""
    user_telegram_id: int
    error_logs: str = ""
    stack_trace: str = ""
    analysis: Optional[DebugAnalysis] = None


# ── Tools (read-only diagnostics) ───────────────────────────────────────

@function_tool
async def retrieve_error_context(
    ctx: RunContextWrapper[DebuggerContext],
) -> str:
    """Retrieve the most recent tool error context stored by Atlas.
    
    This is auto-captured when a tool call fails. Returns the original user
    request, Atlas's error response, and the timestamp. Use this FIRST.
    """
    error = await get_last_tool_error(ctx.context.user_telegram_id)
    if not error:
        return "No stored error context found. Ask the user to describe the error."
    return json.dumps(error, indent=2)


@function_tool
async def search_codebase(
    ctx: RunContextWrapper[DebuggerContext],
    query: str,
    base_path: str = "src",
    limit: int = 20,
) -> str:
    """Search repository files for matching text to find relevant code."""
    return await search_repo(query=query, base_path=base_path, limit=limit)


@function_tool
async def read_source_file(
    ctx: RunContextWrapper[DebuggerContext],
    file_path: str,
    start_line: int = 1,
    end_line: int = 200,
) -> str:
    """Read a repository file with line numbers to examine suspicious code."""
    return await read_repo_file(file_path=file_path, start_line=start_line, end_line=end_line)


@function_tool
async def run_diagnostic_command(
    ctx: RunContextWrapper[DebuggerContext],
    command: str,
) -> str:
    """Run an allowlisted read-only diagnostic command (pytest, ruff, mypy).
    
    Allowed: pytest, ruff check, mypy
    """
    return await run_repo_diagnostics(command)


@function_tool
async def list_recent_audit_errors(
    ctx: RunContextWrapper[DebuggerContext],
    limit: int = 5,
) -> str:
    """List recent errors from the audit log to find patterns."""
    return await list_recent_errors_for_user(ctx.context.user_telegram_id, limit=limit)


@function_tool
async def record_analysis(
    ctx: RunContextWrapper[DebuggerContext],
    error_summary: str,
    root_cause: str,
    affected_components_json: str,
    affected_files_json: str,
    reproduction_steps_json: str,
    confidence_score: float,
    severity: str,
    complexity: str,
    recommended_next_step: str,
) -> str:
    """Record the structured analysis findings.

    This is the FINAL step. After gathering all evidence, record your
    comprehensive analysis using this tool.

    Args:
        error_summary: One-line summary of the error
        root_cause: Detailed explanation of WHY the error occurred
        affected_components_json: JSON list of affected subsystems (e.g., ["gmail_skill", "orchestrator"])
        affected_files_json: JSON list of file paths that likely need changes
        reproduction_steps_json: JSON list of steps to reproduce the error
        confidence_score: 0.0-1.0 confidence in this analysis
        severity: low, medium, high, or critical
        complexity: low, medium, high, or xhigh (repair complexity)
        recommended_next_step: What should happen next (e.g., "Generate patch for email_agent.py")
    """
    try:
        components = json.loads(affected_components_json)
        files = json.loads(affected_files_json)
        steps = json.loads(reproduction_steps_json)
    except json.JSONDecodeError as e:
        return f"Error: Invalid JSON input - {e}"

    analysis = DebugAnalysis(
        error_summary=error_summary,
        root_cause=root_cause,
        affected_components=components,
        affected_files=files,
        reproduction_steps=steps,
        confidence_score=max(0.0, min(1.0, confidence_score)),
        severity=severity.lower(),
        complexity=complexity.lower(),
        recommended_next_step=recommended_next_step,
    )

    ctx.context.analysis = analysis

    # Log for observability
    logger.info(
        "Debugger analysis complete: confidence=%.2f, severity=%s, complexity=%s, components=%s",
        analysis.confidence_score,
        analysis.severity,
        analysis.complexity,
        analysis.affected_components,
    )

    return (
        f"✅ Analysis recorded with confidence {analysis.confidence_score:.0%}\n\n"
        f"**Summary:** {error_summary}\n"
        f"**Root Cause:** {root_cause}\n"
        f"**Severity:** {severity} | **Complexity:** {complexity}\n"
        f"**Next Step:** {recommended_next_step}"
    )


@function_tool
async def create_ticket_from_analysis(
    ctx: RunContextWrapper[DebuggerContext],
    custom_title: str = "",
) -> str:
    """Create a RepairTicket from the recorded analysis.

    Use this AFTER calling `record_analysis` to persist the findings
    as a durable repair ticket. The ticket will be visible in the
    dashboard and can be tracked through the repair pipeline.

    The ticket status will be:
    - "debug_analysis_ready" if confidence >= 70%
    - "open" if confidence < 70% (needs more investigation)

    Args:
        custom_title: Optional custom title for the ticket.
                     If not provided, title is auto-generated from analysis.

    Returns:
        Ticket creation result with ticket_id, title, and next steps.
    """
    if ctx.context.analysis is None:
        return (
            "❌ No analysis recorded yet. "
            "You must call `record_analysis` before creating a ticket."
        )

    from src.repair.engine import create_structured_ticket
    from src.repair.models import debug_analysis_to_model

    analysis = ctx.context.analysis

    # Convert dataclass to dict for ticket creation
    try:
        model = debug_analysis_to_model(analysis)
        analysis_dict = model.model_dump()
    except Exception:
        # Fallback: manually build dict from dataclass
        analysis_dict = {
            "error_summary": analysis.error_summary,
            "root_cause": analysis.root_cause,
            "affected_components": analysis.affected_components,
            "affected_files": analysis.affected_files,
            "reproduction_steps": analysis.reproduction_steps,
            "confidence_score": analysis.confidence_score,
            "severity": analysis.severity,
            "complexity": analysis.complexity,
            "recommended_next_step": analysis.recommended_next_step,
        }

    result = await create_structured_ticket(
        user_telegram_id=ctx.context.user_telegram_id,
        debug_analysis=analysis_dict,
        title=custom_title if custom_title else None,
        source="telegram",
    )

    if result["success"]:
        return result["message"]
    else:
        return f"❌ Failed to create ticket: {result['message']}"


# ── Agent Factory ─────────────────────────────────────────────────────────

DEBUGGER_INSTRUCTIONS = """\
You are Atlas's Debugger Agent — a deep diagnostic specialist. Your job is to
perform comprehensive root-cause analysis on errors, NOT to propose fixes.

## Your Role in the Self-Healing Pipeline

Error → YOU (deep analysis) → Programmer Agent (generates fix) → Quality Control (validates)

You are READ-ONLY. You gather evidence, analyze code, and produce structured
findings. You NEVER write files or propose code changes.

## Analysis Process (Follow This Order)

### Phase 1: Gather Evidence
1. **Retrieve error context** — Call `retrieve_error_context` FIRST. This gives you
   the failed user request, error message, and timestamp.
2. **Search codebase** — Use `search_codebase` to find relevant files mentioned in
   the error or stack trace.
3. **Read source files** — Use `read_source_file` to examine suspicious code paths.
4. **Check recent errors** — Use `list_recent_audit_errors` to see if this is a
   recurring pattern.

### Phase 2: Run Diagnostics
5. **Run tests** — If you find relevant test files, use `run_diagnostic_command`
   with `python -m pytest path/to/test.py -v` to see current state.
6. **Run linter** — Use `python -m ruff check src/` to catch obvious issues.
7. **Run type checker** — Use `python -m mypy src/path/to/file.py` if types seem off.

### Phase 3: Synthesize Findings
8. **Record analysis** — Call `record_analysis` with your comprehensive findings.
   Be specific: name exact files, functions, and lines. Include reproduction steps.

### Phase 4: Create Ticket (Optional but Recommended)
9. **Create repair ticket** — If your analysis has confidence >= 0.7, call
   `create_ticket_from_analysis` to persist the findings as a durable ticket.
   This makes the issue trackable in the dashboard and enables the repair pipeline.
   If confidence is lower, note what additional information would be needed.

## Output Quality Standards

- **confidence_score**: Be honest. 0.9+ only if you've reproduced the issue.
- **severity**: critical = system down, high = major feature broken,
  medium = partial functionality affected, low = minor/cosmetic
- **complexity**: low = single file change, medium = 2-3 files, 
  high = cross-module changes, xhigh = architectural changes needed
- **affected_files**: Only list files you've actually examined
- **reproduction_steps**: Another agent should be able to follow these

## Critical Rules

- NEVER guess about file paths or function names — use search/read tools
- NEVER propose code changes — that's the Programmer Agent's job
- ALWAYS use `record_analysis` as your final tool call
- If confidence is below 0.6, say what additional information you need
- If the error is in external APIs (Google, LinkedIn, etc.), note that
  in your analysis

## Example Good Analysis

Error: "'NoneType' object has no attribute 'lower' in email_agent.py"

Your analysis should:
1. Retrieve error context
2. Search for the function causing the error
3. Read the relevant lines (+- 10 lines for context)
4. Identify the variable that's None and why
5. Check if there's validation missing
6. Record: affected_files=["src/agents/email_agent.py"], 
   affected_components=["email_skill"],
   reproduction_steps=["Call manage_email with null subject"],
   confidence_score=0.85, severity="high", complexity="low"
"""


def create_debugger_agent() -> Agent[DebuggerContext]:
    """Create the deep-analysis Debugger Agent.
    
    This agent is designed to be called as a tool (agent-as-tool pattern)
    by the RepairAgent when complex errors require deep analysis.
    """
    selection = select_model(ModelRole.REPAIR, TaskComplexity.HIGH)
    logger.info(
        "DebuggerAgent using model=%s reasoning_effort=%s",
        selection.model_id,
        selection.reasoning_effort,
    )
    
    return Agent(
        name="DebuggerAgent",
        handoff_description=(
            "Deep diagnostic specialist for complex errors. Performs comprehensive "
            "root-cause analysis across the codebase. READ-ONLY — never writes files. "
            "Use when the error requires multi-file investigation or the root cause is unclear."
        ),
        instructions=DEBUGGER_INSTRUCTIONS,
        model=selection.model_id,
        tools=[
            retrieve_error_context,
            search_codebase,
            read_source_file,
            run_diagnostic_command,
            list_recent_audit_errors,
            record_analysis,
            create_ticket_from_analysis,
        ],
    )


# ── Agent-as-Tool Wrapper ───────────────────────────────────────────────

async def run_debugger_analysis(
    user_telegram_id: int,
    error_description: str,
    error_context: Optional[dict] = None,
) -> DebugAnalysis:
    """Run debugger analysis as a tool call from RepairAgent.
    
    This is the primary entry point for the agent-as-tool pattern.
    
    Args:
        user_telegram_id: The user's Telegram ID
        error_description: Human-readable description of the error
        error_context: Optional additional context dict
        
    Returns:
        DebugAnalysis with structured findings
    """
    from agents import Runner
    
    agent = create_debugger_agent()
    context = DebuggerContext(user_telegram_id=user_telegram_id)
    
    # Enrich the prompt with any additional context
    enriched_input = error_description
    if error_context:
        context_block = (
            "## Additional Error Context\n"
            f"```json\n{json.dumps(error_context, indent=2)}\n```\n\n"
        )
        enriched_input = f"{context_block}## Error to Analyze\n{error_description}"
    
    try:
        result = await Runner.run(
            agent,
            enriched_input,
            context=context,
        )
        
        # The analysis is stored in context by the record_analysis tool
        if context.analysis:
            return context.analysis
        
        # Fallback: parse from response if tool wasn't called properly
        logger.warning("DebuggerAgent did not call record_analysis; parsing from text")
        return DebugAnalysis(
            error_summary=error_description[:200],
            root_cause=result.final_output[:500],
            affected_components=["unknown"],
            affected_files=[],
            reproduction_steps=[],
            confidence_score=0.3,
            severity="unknown",
            complexity="unknown",
            recommended_next_step="Re-run with proper tool calling",
        )
        
    except Exception as e:
        logger.exception("DebuggerAgent failed: %s", e)
        return DebugAnalysis(
            error_summary=f"Debugger failed: {error_description[:200]}",
            root_cause=f"DebuggerAgent error: {e}",
            affected_components=["debugger_failure"],
            affected_files=[],
            reproduction_steps=[],
            confidence_score=0.0,
            severity="unknown",
            complexity="unknown",
            recommended_next_step="Check debugger logs and retry",
        )
