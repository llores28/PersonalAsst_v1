"""Tests for repair pipeline Pydantic models."""

from __future__ import annotations

import pytest
from datetime import datetime

from src.repair.models import (
    DebugAnalysisModel,
    FixProposalModel,
    QAValidationResult,
    SandboxTestResult,
    RepairPipelineState,
    Severity,
    Complexity,
    PipelineStage,
)


class TestDebugAnalysisModel:
    """Test DebugAnalysisModel Pydantic validation."""

    def test_basic_creation(self):
        model = DebugAnalysisModel(
            error_summary="Email sending fails",
            root_cause="Missing null check",
            confidence_score=0.85,
        )
        assert model.error_summary == "Email sending fails"
        assert model.confidence_score == 0.85

    def test_confidence_validation(self):
        """Test that confidence must be within 0.0-1.0."""
        # Valid values work
        model = DebugAnalysisModel(
            error_summary="Test",
            root_cause="Cause",
            confidence_score=0.85,
        )
        assert model.confidence_score == 0.85

        # Boundary values work
        model_max = DebugAnalysisModel(
            error_summary="Test",
            root_cause="Cause",
            confidence_score=1.0,
        )
        assert model_max.confidence_score == 1.0

        model_min = DebugAnalysisModel(
            error_summary="Test",
            root_cause="Cause",
            confidence_score=0.0,
        )
        assert model_min.confidence_score == 0.0

        # Invalid values raise validation error
        with pytest.raises(Exception):  # pydantic.ValidationError
            DebugAnalysisModel(
                error_summary="Test",
                root_cause="Cause",
                confidence_score=1.5,  # Invalid: > 1.0
            )

    def test_full_model(self):
        model = DebugAnalysisModel(
            error_summary="Test error",
            root_cause="Root cause",
            affected_components=["email_skill"],
            affected_files=["src/agents/email_agent.py"],
            reproduction_steps=["Step 1", "Step 2"],
            confidence_score=0.9,
            severity=Severity.HIGH,
            complexity=Complexity.LOW,
            recommended_next_step="Generate patch",
        )
        assert model.severity == Severity.HIGH
        assert model.complexity == Complexity.LOW
        assert len(model.affected_files) == 1


class TestFixProposalModel:
    """Test FixProposalModel validation."""

    def test_basic_creation(self):
        model = FixProposalModel(
            ticket_id=1,
            description="Fix null pointer",
            unified_diff="--- a/file.py\n+++ b/file.py\n@@ -1 +1 @@\n-old\n+new",
            affected_files=["src/agents/email_agent.py"],
            risk_assessment=Complexity.LOW,
            confidence_score=0.8,
        )
        assert model.ticket_id == 1
        assert model.confidence_score == 0.8


class TestQAValidationResult:
    """Test QAValidationResult validation."""

    def test_go_decision(self):
        model = QAValidationResult(
            ticket_id=1,
            fix_proposal_id="fp-123",
            patch_applies_cleanly=True,
            no_security_issues=True,
            tests_are_allowlisted=True,
            no_unrelated_files=True,
            decision="GO",
        )
        assert model.decision == "GO"
        assert model.patch_applies_cleanly is True

    def test_no_go_decision(self):
        model = QAValidationResult(
            ticket_id=1,
            fix_proposal_id="fp-123",
            patch_applies_cleanly=False,
            no_security_issues=True,
            tests_are_allowlisted=True,
            no_unrelated_files=True,
            decision="NO_GO",
            patch_dry_run_output="patch: **** malformed patch",
        )
        assert model.decision == "NO_GO"


class TestRepairPipelineState:
    """Test RepairPipelineState lifecycle management."""

    def test_initial_state(self):
        state = RepairPipelineState(ticket_id=1)
        assert state.ticket_id == 1
        assert state.current_stage == PipelineStage.ERROR_DETECTED
        assert state.retry_count == 0
        assert state.max_retries == 3

    def test_stage_transition(self):
        state = RepairPipelineState(ticket_id=1)
        state.mark_stage(PipelineStage.DEBUGGING)
        assert state.current_stage == PipelineStage.DEBUGGING
        assert state.updated_at > state.started_at

    def test_can_retry(self):
        state = RepairPipelineState(ticket_id=1)
        assert state.can_retry() is True

        state.retry_count = 3
        assert state.can_retry() is False

    def test_increment_retry(self):
        state = RepairPipelineState(ticket_id=1)
        state.increment_retry()
        assert state.retry_count == 1

    def test_error_message(self):
        state = RepairPipelineState(ticket_id=1)
        state.mark_stage(PipelineStage.FAILED, error="Test failure")
        assert state.error_message == "Test failure"
