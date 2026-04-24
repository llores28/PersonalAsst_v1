"""Tests for the Debugger Agent (Phase 1 of Self-Healing Loop)."""

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

from src.agents.debugger_agent import (
    DebugAnalysis,
    DebuggerContext,
    create_debugger_agent,
)


@pytest.fixture(autouse=True, scope="module")
def _cleanup_mocked_modules():
    """Remove mocked modules after this test module completes."""
    yield
    for mod_name in _INJECTED_MOCKS:
        sys.modules.pop(mod_name, None)
    # Also remove cached src modules that imported the mock
    stale = [k for k in sys.modules if k.startswith("src.agents.debugger_agent")]
    for k in stale:
        sys.modules.pop(k, None)


class TestDebugAnalysis:
    """Test DebugAnalysis dataclass."""

    def test_defaults(self):
        analysis = DebugAnalysis(
            error_summary="Test error",
            root_cause="Test cause",
            confidence_score=0.8,
        )
        assert analysis.error_summary == "Test error"
        assert analysis.root_cause == "Test cause"
        assert analysis.affected_components == []
        assert analysis.affected_files == []
        assert analysis.reproduction_steps == []
        assert analysis.confidence_score == 0.8
        assert analysis.severity == ""  # Default empty
        assert analysis.complexity == ""  # Default empty
        assert analysis.related_errors == []
        assert analysis.diagnostic_evidence == {}
        assert analysis.recommended_next_step == ""

    def test_full_construction(self):
        analysis = DebugAnalysis(
            error_summary="Email sending fails",
            root_cause="Missing null check in email_agent.py:45",
            affected_components=["email_skill", "orchestrator"],
            affected_files=["src/agents/email_agent.py"],
            reproduction_steps=["Call manage_email with null subject"],
            confidence_score=0.85,
            severity="high",
            complexity="low",
            related_errors=[{"timestamp": "2026-01-01", "error": "similar"}],
            diagnostic_evidence={"test_output": "AssertionError"},
            recommended_next_step="Generate patch for null check",
        )
        assert analysis.error_summary == "Email sending fails"
        assert len(analysis.affected_components) == 2
        assert analysis.confidence_score == 0.85


class TestDebuggerContext:
    """Test DebuggerContext dataclass."""

    def test_defaults(self):
        ctx = DebuggerContext(user_telegram_id=12345)
        assert ctx.user_telegram_id == 12345
        assert ctx.error_logs == ""
        assert ctx.stack_trace == ""
        assert ctx.analysis is None

    def test_stores_analysis(self):
        ctx = DebuggerContext(user_telegram_id=12345)
        analysis = DebugAnalysis(
            error_summary="Test",
            root_cause="Test cause",
            confidence_score=0.8,
        )
        ctx.analysis = analysis
        assert ctx.analysis is analysis


class TestCreateDebuggerAgent:
    """Test agent factory — verify the agent is constructed correctly."""

    def test_creates_agent_with_correct_name(self):
        agent = create_debugger_agent()
        assert agent.name == "DebuggerAgent"

    def test_agent_has_tools(self):
        agent = create_debugger_agent()
        # Should have 7 tools: retrieve_error_context, search_codebase,
        # read_source_file, run_diagnostic_command, list_recent_audit_errors,
        # record_analysis, create_ticket_from_analysis
        assert len(agent.tools) == 7

    def test_agent_uses_high_complexity_model(self):
        agent = create_debugger_agent()
        # Should use HIGH complexity model (gpt-5.4 per router)
        assert "gpt-5.4" in agent.model

    def test_agent_has_handoff_description(self):
        agent = create_debugger_agent()
        assert "Deep diagnostic" in (agent.handoff_description or "")
        assert "READ-ONLY" in (agent.handoff_description or "")

    def test_agent_is_read_only(self):
        """Debugger agent should never write files."""
        from src.agents.debugger_agent import DEBUGGER_INSTRUCTIONS
        assert "READ-ONLY" in DEBUGGER_INSTRUCTIONS
        assert "never" in DEBUGGER_INSTRUCTIONS.lower() or "NEVER" in DEBUGGER_INSTRUCTIONS
        assert "record_analysis" in DEBUGGER_INSTRUCTIONS


class TestDebuggerAgentIntegration:
    """Test DebuggerAgent integration with RepairAgent."""

    @pytest.mark.asyncio
    async def test_run_debugger_analysis_returns_structured_output(self):
        """Test that run_debugger_analysis returns DebugAnalysis."""
        from src.agents.debugger_agent import run_debugger_analysis

        # Mock the Runner.run to avoid actual LLM calls
        mock_result = MagicMock()
        mock_result.final_output = "Analysis complete"

        with patch("agents.Runner.run", new=AsyncMock(return_value=mock_result)):
            with patch.object(DebuggerContext, "__init__", return_value=None) as mock_ctx:
                # The analysis gets stored in context by record_analysis tool
                # But since we're mocking, we just verify the function runs
                result = await run_debugger_analysis(
                    user_telegram_id=12345,
                    error_description="Test error: email sending fails",
                )

                # Result should be DebugAnalysis (even with fallback values on mock)
                assert isinstance(result, DebugAnalysis)
                # With mock, confidence should be low (0.3 fallback)
                assert result.confidence_score <= 0.5

    def test_create_ticket_tool_registered(self):
        """Test that create_ticket_from_analysis tool is registered."""
        agent = create_debugger_agent()
        tool_names = []
        for t in agent.tools:
            name = getattr(t, "name", None) or getattr(t, "__name__", str(t))
            tool_names.append(name)

        # Verify the ticket creation tool is present
        assert any("create_ticket" in name for name in tool_names)


class TestDebuggerAnalysisToModelConversion:
    """Test conversion from dataclass to Pydantic model."""

    def test_conversion_to_model(self):
        """Test DebugAnalysis can be converted to DebugAnalysisModel."""
        from src.repair.models import debug_analysis_to_model, DebugAnalysisModel

        dataclass_analysis = DebugAnalysis(
            error_summary="Test error",
            root_cause="Test root cause",
            affected_components=["email_skill"],
            affected_files=["src/agents/email_agent.py"],
            reproduction_steps=["Step 1", "Step 2"],
            confidence_score=0.85,
            severity="high",
            complexity="low",
            recommended_next_step="Generate patch",
        )

        model = debug_analysis_to_model(dataclass_analysis)

        assert isinstance(model, DebugAnalysisModel)
        assert model.error_summary == "Test error"
        assert model.root_cause == "Test root cause"
        assert model.confidence_score == 0.85
        assert model.severity.value == "high"
        assert model.complexity.value == "low"

    def test_conversion_already_model(self):
        """Test that passing a model returns the same model."""
        from src.repair.models import debug_analysis_to_model, DebugAnalysisModel

        model = DebugAnalysisModel(
            error_summary="Already model",
            root_cause="Cause",
            confidence_score=0.9,
        )

        result = debug_analysis_to_model(model)
        assert result is model
