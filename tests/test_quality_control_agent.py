"""Tests for the Quality Control Agent (Phase 4 of Self-Healing Loop)."""

# ruff: noqa: E402

from __future__ import annotations

import importlib
import sys
import pytest
from unittest.mock import MagicMock, AsyncMock, patch

# Ensure packages available only in Docker are mockable locally.
# Track what we add so we can clean up and avoid poisoning other tests.
_INJECTED_MOCKS: list[str] = []
for _mod in ("redis", "redis.asyncio"):
    if _mod not in sys.modules:
        _INJECTED_MOCKS.append(_mod)
        sys.modules[_mod] = MagicMock()

try:
    importlib.import_module("agents")
except Exception:
    if "agents" not in sys.modules:
        _INJECTED_MOCKS.append("agents")
        sys.modules["agents"] = MagicMock()

from src.agents.quality_control_agent import (
    ValidationDecision,
    QualityControlContext,
    create_quality_control_agent,
    check_security_issues,
    check_test_commands_allowlisted,
    check_no_unrelated_files,
)


@pytest.fixture(autouse=True, scope="module")
def _cleanup_mocked_modules():
    """Remove mocked modules after this test module completes."""
    yield
    for mod_name in _INJECTED_MOCKS:
        sys.modules.pop(mod_name, None)
    # Also remove cached src modules that imported the mock
    stale = [k for k in sys.modules if k.startswith("src.agents.quality_control_agent")]
    for k in stale:
        sys.modules.pop(k, None)


class TestValidationDecision:
    """Test ValidationDecision dataclass."""

    def test_defaults(self):
        decision = ValidationDecision()
        assert decision.ticket_id == 0
        assert decision.fix_proposal_id == ""
        assert decision.patch_applies_cleanly is False
        assert decision.no_security_issues is False
        assert decision.tests_are_allowlisted is False
        assert decision.no_unrelated_files is False
        assert decision.decision == ""
        assert decision.confidence_score == 0.0

    def test_full_construction(self):
        decision = ValidationDecision(
            ticket_id=123,
            fix_proposal_id="fp-123",
            patch_applies_cleanly=True,
            no_security_issues=True,
            tests_are_allowlisted=True,
            no_unrelated_files=True,
            decision="GO",
            confidence_score=0.95,
        )
        assert decision.ticket_id == 123
        assert decision.decision == "GO"
        assert decision.confidence_score == 0.95


class TestQualityControlContext:
    """Test QualityControlContext dataclass."""

    def test_defaults(self):
        ctx = QualityControlContext(user_telegram_id=12345)
        assert ctx.user_telegram_id == 12345
        assert ctx.ticket_id == 0
        assert ctx.fix_proposal is None
        assert ctx.validation_result is None

    def test_stores_fix_proposal(self):
        ctx = QualityControlContext(user_telegram_id=12345)
        proposal = {"description": "Test fix", "unified_diff": "test diff"}
        ctx.fix_proposal = proposal
        assert ctx.fix_proposal is proposal

    def test_stores_validation_result(self):
        ctx = QualityControlContext(user_telegram_id=12345)
        result = ValidationDecision(ticket_id=1, decision="GO")
        ctx.validation_result = result
        assert ctx.validation_result is result


class TestCreateQualityControlAgent:
    """Test agent factory — verify the agent is constructed correctly."""

    def test_creates_agent_with_correct_name(self):
        agent = create_quality_control_agent()
        assert agent.name == "QualityControlAgent"

    def test_agent_has_tools(self):
        agent = create_quality_control_agent()
        # Should have 6 tools: get_fix_proposal, check_patch_applies_cleanly,
        # check_security_issues, check_test_commands_allowlisted,
        # check_no_unrelated_files, record_validation_decision
        assert len(agent.tools) == 6

    def test_agent_uses_high_complexity_model(self):
        agent = create_quality_control_agent()
        # Should use HIGH complexity model (gpt-5.4 per router)
        assert "gpt-5.4" in agent.model

    def test_agent_has_handoff_description(self):
        agent = create_quality_control_agent()
        assert "Validation" in (agent.handoff_description or "")
        assert "GO/NO_GO" in (agent.handoff_description or "")


class TestQualityControlAgentIntegration:
    """Test QualityControlAgent integration with repair pipeline."""

    @pytest.mark.asyncio
    async def test_run_validation_returns_structured_output(self):
        """Test that run_quality_control_validation returns ValidationDecision."""
        from src.agents.quality_control_agent import run_quality_control_validation

        # Mock the Runner.run to avoid actual LLM calls
        mock_result = MagicMock()
        mock_result.final_output = "Validation complete"

        with patch("agents.Runner.run", new=AsyncMock(return_value=mock_result)):
            fix_proposal = {
                "description": "Fix null pointer",
                "unified_diff": "--- a/file.py\n+++ b/file.py\n@@ -1 +1 @@\n-old\n+new",
                "affected_files": ["src/agents/email_agent.py"],
            }

            result = await run_quality_control_validation(
                user_telegram_id=12345,
                ticket_id=1,
                fix_proposal=fix_proposal,
            )

            # Result should be ValidationDecision
            assert isinstance(result, ValidationDecision)
            assert result.ticket_id == 1
