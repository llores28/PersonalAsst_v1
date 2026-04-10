"""Tests for the self-healing repair agent."""

# ruff: noqa: E402

from __future__ import annotations

import importlib
import sys
import pytest
from unittest.mock import MagicMock

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

from src.agents.repair_agent import (
    RepairContext,
    create_repair_agent,
)


@pytest.fixture(autouse=True, scope="module")
def _cleanup_mocked_modules():
    """Remove mocked modules after this test module completes."""
    yield
    for mod_name in _INJECTED_MOCKS:
        sys.modules.pop(mod_name, None)
    # Also remove cached src modules that imported the mock
    stale = [k for k in sys.modules if k.startswith("src.agents.repair_agent")]
    for k in stale:
        sys.modules.pop(k, None)


class TestRepairContext:
    """Test RepairContext dataclass."""

    def test_defaults(self):
        ctx = RepairContext(user_telegram_id=12345)
        assert ctx.user_telegram_id == 12345
        assert ctx.error_logs == ""
        assert ctx.stack_trace == ""
        assert ctx.relevant_source == ""
        assert ctx.proposed_patch is None
        assert ctx.patch_approved is False

    def test_stores_patch(self):
        ctx = RepairContext(user_telegram_id=12345)
        ctx.proposed_patch = "--- a/file.py\n+++ b/file.py\n@@ -1 +1 @@\n-old\n+new"
        assert "old" in ctx.proposed_patch
        assert "new" in ctx.proposed_patch

    def test_approval_default_false(self):
        ctx = RepairContext(user_telegram_id=12345)
        assert ctx.patch_approved is False


class TestCreateRepairAgent:
    """Test agent factory — verify the agent is constructed correctly."""

    def test_creates_agent_with_correct_name(self):
        agent = create_repair_agent()
        assert agent.name == "RepairAgent"

    def test_agent_has_tools(self):
        agent = create_repair_agent()
        assert len(agent.tools) == 9

    def test_agent_uses_router_model(self):
        agent = create_repair_agent()
        # Default routing for REPAIR at MEDIUM → gpt-5.4-mini
        assert agent.model == "gpt-5.4-mini"

    def test_agent_has_handoff_description(self):
        agent = create_repair_agent()
        assert "Read-only diagnostics" in (agent.handoff_description or "")
        assert "Not for routine file organization" in (agent.handoff_description or "")

    def test_agent_instructions_contain_safety_rules(self):
        from src.agents.repair_agent import REPAIR_AGENT_INSTRUCTIONS
        assert "NEVER apply changes directly" in REPAIR_AGENT_INSTRUCTIONS
        assert "never" in REPAIR_AGENT_INSTRUCTIONS.lower() or "NEVER" in REPAIR_AGENT_INSTRUCTIONS

    def test_agent_instructions_contain_workflow(self):
        from src.agents.repair_agent import REPAIR_AGENT_INSTRUCTIONS
        assert "Full Repair Pipeline" in REPAIR_AGENT_INSTRUCTIONS
        assert "propose_patch" in REPAIR_AGENT_INSTRUCTIONS

    def test_agent_instructions_exclude_routine_file_work(self):
        from src.agents.repair_agent import REPAIR_AGENT_INSTRUCTIONS

        assert "routine third-party work" in REPAIR_AGENT_INSTRUCTIONS
        assert "organizing files in OneDrive" in REPAIR_AGENT_INSTRUCTIONS

    def test_agent_instructions_allow_broken_integration_repairs(self):
        from src.agents.repair_agent import REPAIR_AGENT_INSTRUCTIONS

        assert "OneDrive, Drive, Gmail" in REPAIR_AGENT_INSTRUCTIONS
        assert "broken, failing" in REPAIR_AGENT_INSTRUCTIONS
