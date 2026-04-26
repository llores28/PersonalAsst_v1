"""Tests for the integrated self-healing pipeline (Phase 5)."""

# ruff: noqa: E402

from __future__ import annotations

import sys
import pytest
from unittest.mock import MagicMock, AsyncMock, patch

# Mock redis before importing
sys.modules["redis"] = MagicMock()
sys.modules["redis.asyncio"] = MagicMock()


@pytest.fixture(autouse=True)
def _reset_pipeline_attempt_counts():
    """Clear the global ``_PIPELINE_ATTEMPT_COUNTS`` cache before each test.

    All TestSelfHealingPipeline cases drive `run_self_healing_pipeline` with
    the same `(user_telegram_id, error_description)` fingerprint, so the
    in-memory retry counter accumulates across tests in the same session and
    the third test gets blocked with `MAX_RETRIES_EXCEEDED` instead of running
    its assertion. Resetting before every test makes ordering irrelevant.
    """
    try:
        from src.repair import engine
        engine._PIPELINE_ATTEMPT_COUNTS.clear()
    except Exception:
        pass
    yield
    try:
        from src.repair import engine
        engine._PIPELINE_ATTEMPT_COUNTS.clear()
    except Exception:
        pass


class TestSelfHealingPipeline:
    """Test the integrated self-healing pipeline."""

    @pytest.mark.asyncio
    async def test_pipeline_runs_all_stages_on_success(self):
        """Test that successful pipeline runs through all stages."""
        from src.repair.engine import run_self_healing_pipeline
        from src.agents.debugger_agent import DebugAnalysis
        from src.agents.programmer_agent import FixProposal
        from src.agents.quality_control_agent import ValidationDecision

        # Create actual dataclass instances
        mock_debug_result = DebugAnalysis(
            error_summary="Test error",
            root_cause="Test cause",
            affected_components=["test"],
            affected_files=["src/test.py"],
            confidence_score=0.85,
        )

        mock_fix_result = FixProposal(
            ticket_id=123,
            description="Fix test",
            unified_diff="--- a/test.py\n+++ b/test.py\n@@ -1 +1 @@\n-old\n+new",
            affected_files=["src/test.py"],
            confidence_score=0.85,
        )

        mock_qa_result = ValidationDecision(
            ticket_id=123,
            decision="GO",
            patch_applies_cleanly=True,
            no_security_issues=True,
            tests_are_allowlisted=True,
            no_unrelated_files=True,
            confidence_score=0.95,
        )

        with patch("src.agents.debugger_agent.run_debugger_analysis", new=AsyncMock(return_value=mock_debug_result)):
            with patch("src.repair.engine.create_structured_ticket", new=AsyncMock(return_value={"success": True, "ticket_id": 123})):
                with patch("src.agents.programmer_agent.run_programmer_fix_generation", new=AsyncMock(return_value=mock_fix_result)):
                    with patch("src.agents.quality_control_agent.run_quality_control_validation", new=AsyncMock(return_value=mock_qa_result)):
                        with patch("src.repair.engine._dry_run_patch", new=AsyncMock(return_value="Patch applies cleanly")):
                            with patch("src.repair.engine._run_sandbox_test", new=AsyncMock(return_value={"success": True})):
                                with patch("src.repair.engine._update_ticket_status", new=AsyncMock()):
                                    result = await run_self_healing_pipeline(
                                        user_telegram_id=12345,
                                        error_description="Test error",
                                    )

        assert result["success"] is True
        assert result["ticket_id"] == 123
        assert result["decision"] == "AWAITING_APPROVAL"

    @pytest.mark.asyncio
    async def test_pipeline_stops_at_debugger_on_low_confidence(self):
        """Test pipeline stops if debugger confidence is too low."""
        from src.repair.engine import run_self_healing_pipeline
        from src.agents.debugger_agent import DebugAnalysis

        mock_debug_result = DebugAnalysis(
            error_summary="Unclear",
            confidence_score=0.2,  # Too low
        )

        with patch("src.agents.debugger_agent.run_debugger_analysis", new=AsyncMock(return_value=mock_debug_result)):
            result = await run_self_healing_pipeline(
                user_telegram_id=12345,
                error_description="Test error",
            )

        assert result["success"] is False
        assert result["stage_reached"] == "debugger"
        assert result["decision"] == "NEEDS_REVISION"
        assert "confidence" in result["message"].lower()

    @pytest.mark.asyncio
    async def test_pipeline_stops_at_qa_on_no_go(self):
        """Test pipeline stops if QA rejects the fix."""
        from src.repair.engine import run_self_healing_pipeline
        from src.agents.debugger_agent import DebugAnalysis
        from src.agents.programmer_agent import FixProposal
        from src.agents.quality_control_agent import ValidationDecision

        mock_debug_result = DebugAnalysis(
            error_summary="Test error",
            confidence_score=0.85,
        )

        mock_fix_result = FixProposal(
            ticket_id=123,
            unified_diff="test diff",
            confidence_score=0.85,
        )

        mock_qa_result = ValidationDecision(
            ticket_id=123,
            decision="NO_GO",
            revision_feedback="Security issues found",
        )

        with patch("src.agents.debugger_agent.run_debugger_analysis", new=AsyncMock(return_value=mock_debug_result)):
            with patch("src.repair.engine.create_structured_ticket", new=AsyncMock(return_value={"success": True, "ticket_id": 123})):
                with patch("src.agents.programmer_agent.run_programmer_fix_generation", new=AsyncMock(return_value=mock_fix_result)):
                    with patch("src.agents.quality_control_agent.run_quality_control_validation", new=AsyncMock(return_value=mock_qa_result)):
                        with patch("src.repair.engine._dry_run_patch", new=AsyncMock(return_value="Patch applies cleanly")):
                            with patch("src.repair.engine._store_qa_results", new=AsyncMock()):
                                with patch("src.repair.engine._update_ticket_status", new=AsyncMock()):
                                    result = await run_self_healing_pipeline(
                                        user_telegram_id=12345,
                                        error_description="Test error",
                                    )

        assert result["success"] is False
        assert result["stage_reached"] == "qa"
        assert result["decision"] == "NO_GO"

    @pytest.mark.asyncio
    async def test_pipeline_returns_needs_revision_when_qa_requests_it(self):
        """Test pipeline returns NEEDS_REVISION when QA requests changes."""
        from src.repair.engine import run_self_healing_pipeline
        from src.agents.debugger_agent import DebugAnalysis
        from src.agents.programmer_agent import FixProposal
        from src.agents.quality_control_agent import ValidationDecision

        mock_debug_result = DebugAnalysis(
            error_summary="Test error",
            confidence_score=0.85,
        )

        mock_fix_result = FixProposal(
            ticket_id=123,
            unified_diff="test diff",
            confidence_score=0.85,
        )

        mock_qa_result = ValidationDecision(
            ticket_id=123,
            decision="NEEDS_REVISION",
            revision_feedback="Fix syntax error on line 5",
        )

        with patch("src.agents.debugger_agent.run_debugger_analysis", new=AsyncMock(return_value=mock_debug_result)):
            with patch("src.repair.engine.create_structured_ticket", new=AsyncMock(return_value={"success": True, "ticket_id": 123})):
                with patch("src.agents.programmer_agent.run_programmer_fix_generation", new=AsyncMock(return_value=mock_fix_result)):
                    with patch("src.agents.quality_control_agent.run_quality_control_validation", new=AsyncMock(return_value=mock_qa_result)):
                        with patch("src.repair.engine._dry_run_patch", new=AsyncMock(return_value="Patch applies cleanly")):
                            with patch("src.repair.engine._store_qa_results", new=AsyncMock()):
                                with patch("src.repair.engine._update_ticket_status", new=AsyncMock()):
                                    result = await run_self_healing_pipeline(
                                        user_telegram_id=12345,
                                        error_description="Test error",
                                    )

        assert result["success"] is False
        assert result["decision"] == "NEEDS_REVISION"
        assert "revision" in result["message"].lower()


class TestDryRunPatch:
    """Test the dry-run patch helper."""

    @pytest.mark.asyncio
    async def test_dry_run_reports_clean_patch(self):
        """Test dry-run reports success for clean patch."""
        from src.repair.engine import _dry_run_patch

        mock_rc = 0
        mock_stdout = ""
        mock_stderr = ""

        with patch("src.repair.engine._run_command_parts", new=AsyncMock(return_value=(mock_rc, mock_stdout, mock_stderr))):
            result = await _dry_run_patch("test diff content")

        assert "applies cleanly" in result.lower()

    @pytest.mark.asyncio
    async def test_dry_run_reports_failed_patch(self):
        """Dry-run must detect a context-mismatch hunk.

        The implementation switched from `git apply --check` (subprocess) to a
        pure-Python read-only simulation that parses each hunk and compares
        the context lines against the actual file content. To exercise the
        failure path we feed a diff that targets `requirements.txt` (an
        always-present file in this repo) with deliberately bogus context —
        the hunk's first three lines won't match the real file, so the
        function returns a "does not apply" message.
        """
        from src.repair.engine import _dry_run_patch

        bad_diff = (
            "diff --git a/requirements.txt b/requirements.txt\n"
            "--- a/requirements.txt\n"
            "+++ b/requirements.txt\n"
            "@@ -1,3 +1,3 @@\n"
            " __THIS_LINE_DOES_NOT_EXIST_IN_REQUIREMENTS_TXT__\n"
            " __NEITHER_DOES_THIS_ONE__\n"
            "-__OR_THIS_THIRD_BOGUS_LINE__\n"
            "+__REPLACEMENT__\n"
        )
        result = await _dry_run_patch(bad_diff)
        assert "does not apply" in result.lower()


class TestSandboxTest:
    """Test the sandbox test helper."""

    @pytest.mark.asyncio
    async def test_sandbox_test_success(self):
        """Test sandbox test reports success."""
        from src.repair.engine import _run_sandbox_test
        from src.repair.models import FixProposalModel

        mock_model = MagicMock(spec=FixProposalModel)
        mock_model.affected_files = ["src/test.py"]
        mock_model.description = "Test fix"
        mock_model.unified_diff = "test diff"
        mock_model.test_plan = ["pytest tests/"]

        # `_run_sandbox_test` matches against the EXACT success-marker
        # substrings produced by execute_pending_repair, including
        # "Patch Verified in Sandbox" (not just "Patch Verified"). The mock
        # must echo one of those markers verbatim.
        with patch("src.repair.engine.store_pending_repair", new=AsyncMock()):
            with patch(
                "src.repair.engine.execute_pending_repair",
                new=AsyncMock(return_value="✅ Patch Verified in Sandbox - Awaiting Deploy Approval"),
            ):
                result = await _run_sandbox_test(
                    user_telegram_id=12345,
                    ticket_id=1,
                    fix_proposal=mock_model,
                )

        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_sandbox_test_failure(self):
        """Test sandbox test reports failure."""
        from src.repair.engine import _run_sandbox_test
        from src.repair.models import FixProposalModel

        mock_model = MagicMock(spec=FixProposalModel)
        mock_model.affected_files = ["src/test.py"]
        mock_model.description = "Test fix"
        mock_model.unified_diff = "test diff"
        mock_model.test_plan = ["pytest tests/"]

        with patch("src.repair.engine.store_pending_repair", new=AsyncMock()):
            with patch("src.repair.engine.execute_pending_repair", new=AsyncMock(return_value="Verification failed")):
                result = await _run_sandbox_test(
                    user_telegram_id=12345,
                    ticket_id=1,
                    fix_proposal=mock_model,
                )

        assert result["success"] is False


class TestUpdateTicketStatus:
    """Test ticket status updates."""

    @pytest.mark.asyncio
    async def test_updates_ticket_status(self):
        """Test that ticket status is updated."""
        from src.repair.engine import _update_ticket_status

        mock_session = AsyncMock()
        mock_ticket = MagicMock()
        mock_ticket.plan = {}

        with patch("src.db.session.async_session") as mock_async_session:
            mock_async_session.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_async_session.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_session.get.return_value = mock_ticket

            await _update_ticket_status(123, "ready_for_deploy", "Tests passed")

            assert mock_ticket.status == "ready_for_deploy"
            assert mock_ticket.plan.get("pipeline_note") == "Tests passed"


class TestStoreQAResults:
    """Test QA results storage."""

    @pytest.mark.asyncio
    async def test_stores_qa_results_in_ticket(self):
        """Test that QA results are stored in ticket."""
        from src.repair.engine import _store_qa_results

        mock_session = AsyncMock()
        mock_ticket = MagicMock()
        mock_ticket.verification_results = {}

        with patch("src.db.session.async_session") as mock_async_session:
            mock_async_session.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_async_session.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_session.get.return_value = mock_ticket

            qa_results = {"decision": "GO", "confidence": 0.95}
            await _store_qa_results(123, qa_results)

            assert "qa_validation" in mock_ticket.verification_results
            assert mock_ticket.verification_results["qa_validation"] == qa_results
