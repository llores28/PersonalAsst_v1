"""Tests for the Programmer Agent (Phase 3 of Self-Healing Loop)."""

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

from src.agents.programmer_agent import (
    FixProposal,
    ProgrammerContext,
    create_programmer_agent,
)


@pytest.fixture(autouse=True, scope="module")
def _cleanup_mocked_modules():
    """Remove mocked modules after this test module completes."""
    yield
    for mod_name in _INJECTED_MOCKS:
        sys.modules.pop(mod_name, None)
    # Also remove cached src modules that imported the mock
    stale = [k for k in sys.modules if k.startswith("src.agents.programmer_agent")]
    for k in stale:
        sys.modules.pop(k, None)


class TestFixProposal:
    """Test FixProposal dataclass."""

    def test_defaults(self):
        proposal = FixProposal()
        assert proposal.ticket_id == 0
        assert proposal.description == ""
        assert proposal.unified_diff == ""
        assert proposal.affected_files == []
        assert proposal.test_plan == []
        assert proposal.risk_assessment == ""
        assert proposal.rollback_plan == ""
        assert proposal.confidence_score == 0.0

    def test_full_construction(self):
        proposal = FixProposal(
            ticket_id=123,
            description="Fix null pointer",
            unified_diff="--- a/file.py\n+++ b/file.py\n@@ -1 +1 @@\n-old\n+new",
            affected_files=["src/agents/email_agent.py"],
            test_plan=["python -m pytest tests/test_email.py -v"],
            risk_assessment="low",
            rollback_plan="git revert HEAD",
            confidence_score=0.85,
        )
        assert proposal.ticket_id == 123
        assert proposal.confidence_score == 0.85
        assert len(proposal.affected_files) == 1


class TestProgrammerContext:
    """Test ProgrammerContext dataclass."""

    def test_defaults(self):
        ctx = ProgrammerContext(user_telegram_id=12345)
        assert ctx.user_telegram_id == 12345
        assert ctx.ticket_id == 0
        assert ctx.debug_analysis is None
        assert ctx.fix_proposal is None

    def test_stores_debug_analysis(self):
        ctx = ProgrammerContext(user_telegram_id=12345)
        analysis = {"error_summary": "Test error", "root_cause": "Test cause"}
        ctx.debug_analysis = analysis
        assert ctx.debug_analysis is analysis

    def test_stores_fix_proposal(self):
        ctx = ProgrammerContext(user_telegram_id=12345)
        proposal = FixProposal(ticket_id=1, description="Test fix")
        ctx.fix_proposal = proposal
        assert ctx.fix_proposal is proposal


class TestCreateProgrammerAgent:
    """Test agent factory — verify the agent is constructed correctly."""

    def test_creates_agent_with_correct_name(self):
        agent = create_programmer_agent()
        assert agent.name == "ProgrammerAgent"

    def test_agent_has_tools(self):
        agent = create_programmer_agent()
        # Should have 4 tools: get_debug_analysis, read_target_file,
        # search_repo_code, record_fix_proposal
        assert len(agent.tools) == 4

    def test_agent_uses_high_complexity_model(self):
        agent = create_programmer_agent()
        # Should use HIGH complexity model (gpt-5.4 per router)
        assert "gpt-5.4" in agent.model

    def test_agent_has_handoff_description(self):
        agent = create_programmer_agent()
        assert "Fix generation" in (agent.handoff_description or "")
        assert "READ-ONLY" in (agent.handoff_description or "")

    def test_agent_is_read_only(self):
        """Programmer agent should never write files."""
        from src.agents.programmer_agent import PROGRAMMER_INSTRUCTIONS
        assert "READ-ONLY" in PROGRAMMER_INSTRUCTIONS
        assert "never" in PROGRAMMER_INSTRUCTIONS.lower() or "NEVER" in PROGRAMMER_INSTRUCTIONS
        assert "record_fix_proposal" in PROGRAMMER_INSTRUCTIONS


class TestProgrammerAgentIntegration:
    """Test ProgrammerAgent integration with repair pipeline."""

    @pytest.mark.asyncio
    async def test_run_programmer_fix_generation_returns_structured_output(self):
        """Test that run_programmer_fix_generation returns FixProposal."""
        from src.agents.programmer_agent import run_programmer_fix_generation

        # Mock the Runner.run to avoid actual LLM calls
        mock_result = MagicMock()
        mock_result.final_output = "Fix generated"

        with patch("agents.Runner.run", new=AsyncMock(return_value=mock_result)):
            debug_analysis = {
                "error_summary": "Email sending fails",
                "root_cause": "Missing null check",
                "affected_files": ["src/agents/email_agent.py"],
                "confidence_score": 0.85,
            }

            result = await run_programmer_fix_generation(
                user_telegram_id=12345,
                ticket_id=1,
                debug_analysis=debug_analysis,
            )

            # Result should be FixProposal (even with fallback values on mock)
            assert isinstance(result, FixProposal)
            assert result.ticket_id == 1


class TestFixProposalToModelConversion:
    """Test conversion from dataclass to Pydantic model."""

    def test_conversion_to_model(self):
        """Test FixProposal can be converted to FixProposalModel."""
        from src.repair.models import fix_proposal_to_model, FixProposalModel

        dataclass_proposal = FixProposal(
            ticket_id=1,
            description="Fix null pointer",
            unified_diff="--- a/file.py\n+++ b/file.py\n@@ -1 +1 @@\n-old\n+new",
            affected_files=["src/agents/email_agent.py"],
            test_plan=["python -m pytest tests/ -v"],
            risk_assessment="low",
            rollback_plan="git revert",
            confidence_score=0.85,
        )

        model = fix_proposal_to_model(dataclass_proposal)

        assert isinstance(model, FixProposalModel)
        assert model.ticket_id == 1
        assert model.description == "Fix null pointer"
        assert model.confidence_score == 0.85
        assert model.risk_assessment.value == "low"
