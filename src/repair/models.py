"""Pydantic models for the repair pipeline structured output contracts.

These models define the inter-agent communication contracts for the self-healing loop:
Debugger → Programmer → Quality Control → Sandbox → Deploy

Using Pydantic for:
- Runtime validation
- Type safety
- JSON serialization for persistence
- Clear API contracts between agents
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, field_validator


class TicketStatus(str, Enum):
    """DB-level repair ticket status values (matches RepairTicket.status column)."""
    OPEN = "open"
    DEBUG_ANALYSIS_READY = "debug_analysis_ready"
    PLAN_READY = "plan_ready"
    VERIFYING = "verifying"
    VERIFICATION_FAILED = "verification_failed"
    READY_FOR_DEPLOY = "ready_for_deploy"
    DEPLOYED = "deployed"
    CLOSED = "closed"


class Severity(str, Enum):
    """Error severity levels."""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class Complexity(str, Enum):
    """Repair complexity levels."""
    LOW = "low"  # Single file, mechanical fix
    MEDIUM = "medium"  # 2-3 files, straightforward
    HIGH = "high"  # Cross-module changes
    XHIGH = "xhigh"  # Architectural changes


class PipelineStage(str, Enum):
    """Stages in the self-healing repair pipeline."""
    ERROR_DETECTED = "error_detected"
    DEBUGGING = "debugging"
    DEBUG_ANALYSIS_READY = "debug_analysis_ready"
    TICKET_CREATED = "ticket_created"
    PROGRAMMING = "programming"
    FIX_GENERATED = "fix_generated"
    QA_VALIDATION = "qa_validation"
    QA_PASSED = "qa_passed"
    QA_FAILED = "qa_failed"
    SANDBOX_TESTING = "sandbox_testing"
    SANDBOX_PASSED = "sandbox_passed"
    SANDBOX_FAILED = "sandbox_failed"
    AWAITING_APPROVAL = "awaiting_approval"
    APPROVED = "approved"
    DEPLOYED = "deployed"
    FAILED = "failed"


class DebugAnalysisModel(BaseModel):
    """Structured output from DebuggerAgent analysis.
    
    This is the contract between DebuggerAgent and the rest of the pipeline.
    """
    
    error_summary: str = Field(..., description="One-line summary of the error")
    root_cause: str = Field(..., description="Detailed explanation of WHY the error occurred")
    affected_components: list[str] = Field(default_factory=list, description="Affected subsystems")
    affected_files: list[str] = Field(default_factory=list, description="Files that likely need changes")
    reproduction_steps: list[str] = Field(default_factory=list, description="Steps to reproduce the error")
    confidence_score: float = Field(..., ge=0.0, le=1.0, description="Confidence in this analysis (0.0-1.0)")
    severity: Severity = Field(default=Severity.MEDIUM)
    complexity: Complexity = Field(default=Complexity.MEDIUM)
    related_errors: list[dict] = Field(default_factory=list, description="Related historical errors")
    diagnostic_evidence: dict = Field(default_factory=dict, description="Evidence gathered (test outputs, logs)")
    recommended_next_step: str = Field(default="", description="What should happen next")
    analyzed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    
    @field_validator("confidence_score")
    @classmethod
    def clamp_confidence(cls, v: float) -> float:
        return max(0.0, min(1.0, v))


class FixProposalModel(BaseModel):
    """Structured output from ProgrammerAgent fix generation.
    
    This is the contract between ProgrammerAgent and Quality Control.
    """
    
    ticket_id: int = Field(..., description="The repair ticket this fix is for")
    description: str = Field(..., description="What the fix does and why")
    unified_diff: str = Field(..., description="The actual unified diff patch")
    affected_files: list[str] = Field(..., description="Files modified by this patch")
    test_plan: list[str] = Field(default_factory=list, description="Commands to verify the fix")
    risk_assessment: Complexity = Field(..., description="Risk level of the fix")
    rollback_plan: str = Field(default="", description="How to undo this fix if needed")
    confidence_score: float = Field(..., ge=0.0, le=1.0, description="Confidence in this fix")
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    
    @field_validator("confidence_score")
    @classmethod
    def clamp_confidence(cls, v: float) -> float:
        return max(0.0, min(1.0, v))


class QAValidationResult(BaseModel):
    """Structured output from Quality Control Agent validation.
    
    This is the contract between Quality Control and the sandbox.
    """
    
    ticket_id: int = Field(..., description="The repair ticket being validated")
    fix_proposal_id: str = Field(..., description="Identifier for the fix proposal")
    
    # Validation checks
    patch_applies_cleanly: bool = Field(..., description="Does patch apply without conflicts?")
    no_security_issues: bool = Field(..., description="No eval, exec, shell=True, hardcoded secrets?")
    tests_are_allowlisted: bool = Field(..., description="Are test commands in allowlist?")
    no_unrelated_files: bool = Field(..., description="Only expected files modified?")
    
    # Details
    security_issues_found: list[str] = Field(default_factory=list)
    unrelated_files_found: list[str] = Field(default_factory=list)
    test_command_issues: list[str] = Field(default_factory=list)
    patch_dry_run_output: str = Field(default="")
    
    # Decision
    decision: str = Field(..., description="GO, NO_GO, or NEEDS_REVISION")
    revision_feedback: str = Field(default="", description="Feedback if NEEDS_REVISION")
    
    # Metadata
    validated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    confidence_score: float = Field(default=0.0, ge=0.0, le=1.0)


class SandboxTestResult(BaseModel):
    """Result from sandbox testing phase."""
    
    ticket_id: int
    branch_name: str
    patch_applied: bool
    test_results: list[dict] = Field(default_factory=list)
    all_tests_passed: bool = False
    failed_commands: list[str] = Field(default_factory=list)
    tested_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    error_output: str = Field(default="")


class RepairPipelineState(BaseModel):
    """Complete state of a repair through the self-healing pipeline."""
    
    ticket_id: int
    current_stage: PipelineStage = PipelineStage.ERROR_DETECTED
    
    # Analysis phase
    debug_analysis: Optional[DebugAnalysisModel] = None
    
    # Fix generation phase
    fix_proposal: Optional[FixProposalModel] = None
    
    # Validation phase
    qa_result: Optional[QAValidationResult] = None
    
    # Testing phase
    sandbox_result: Optional[SandboxTestResult] = None
    
    # Metadata
    started_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    retry_count: int = Field(default=0)
    max_retries: int = Field(default=3)
    error_message: str = Field(default="")
    
    def mark_stage(self, stage: PipelineStage, error: str = "") -> None:
        """Update the pipeline stage."""
        self.current_stage = stage
        self.updated_at = datetime.now(timezone.utc)
        if error:
            self.error_message = error
    
    def can_retry(self) -> bool:
        """Check if this pipeline can be retried."""
        return self.retry_count < self.max_retries
    
    def increment_retry(self) -> None:
        """Increment retry counter."""
        self.retry_count += 1
        self.updated_at = datetime.now(timezone.utc)


# ── Conversion helpers ───────────────────────────────────────────────────

def debug_analysis_to_model(analysis) -> DebugAnalysisModel:
    """Convert DebuggerAgent DebugAnalysis dataclass to Pydantic model.
    
    Args:
        analysis: DebugAnalysis dataclass from debugger_agent.py
        
    Returns:
        DebugAnalysisModel for persistence and inter-agent communication
    """
    from src.agents.debugger_agent import DebugAnalysis
    
    if isinstance(analysis, DebugAnalysisModel):
        return analysis
    
    if isinstance(analysis, DebugAnalysis):
        return DebugAnalysisModel(
            error_summary=analysis.error_summary,
            root_cause=analysis.root_cause,
            affected_components=analysis.affected_components,
            affected_files=analysis.affected_files,
            reproduction_steps=analysis.reproduction_steps,
            confidence_score=analysis.confidence_score,
            severity=Severity(analysis.severity) if analysis.severity in [s.value for s in Severity] else Severity.MEDIUM,
            complexity=Complexity(analysis.complexity) if analysis.complexity in [c.value for c in Complexity] else Complexity.MEDIUM,
            related_errors=analysis.related_errors,
            diagnostic_evidence=analysis.diagnostic_evidence,
            recommended_next_step=analysis.recommended_next_step,
        )
    
    # Fallback for dict
    if isinstance(analysis, dict):
        return DebugAnalysisModel(**analysis)
    
    raise ValueError(f"Cannot convert {type(analysis)} to DebugAnalysisModel")


def fix_proposal_to_model(proposal) -> FixProposalModel:
    """Convert ProgrammerAgent FixProposal dataclass to Pydantic model.

    Args:
        proposal: FixProposal dataclass from programmer_agent.py

    Returns:
        FixProposalModel for persistence and inter-agent communication
    """
    from src.agents.programmer_agent import FixProposal
    from datetime import datetime, timezone

    # Map string risk_assessment to Complexity enum
    risk_map = {
        "low": Complexity.LOW,
        "medium": Complexity.MEDIUM,
        "high": Complexity.HIGH,
        "xhigh": Complexity.XHIGH,
    }

    if isinstance(proposal, FixProposalModel):
        return proposal

    if isinstance(proposal, FixProposal):
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

    # Fallback for dict
    if isinstance(proposal, dict):
        # Map risk_assessment string to enum if present
        if "risk_assessment" in proposal and isinstance(proposal["risk_assessment"], str):
            proposal = proposal.copy()
            proposal["risk_assessment"] = risk_map.get(
                proposal["risk_assessment"].lower(), Complexity.MEDIUM
            )
        return FixProposalModel(**proposal)

    raise ValueError(f"Cannot convert {type(proposal)} to FixProposalModel")


def validation_decision_to_model(validation) -> QAValidationResult:
    """Convert QualityControlAgent ValidationDecision to Pydantic model.

    Args:
        validation: ValidationDecision dataclass

    Returns:
        QAValidationResult for persistence
    """
    from src.agents.quality_control_agent import ValidationDecision
    from datetime import datetime, timezone

    if isinstance(validation, QAValidationResult):
        return validation

    if isinstance(validation, ValidationDecision):
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

    # Fallback for dict
    if isinstance(validation, dict):
        return QAValidationResult(**validation)

    raise ValueError(f"Cannot convert {type(validation)} to QAValidationResult")
