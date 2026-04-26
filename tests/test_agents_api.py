"""Tests for Agents Tab API endpoints."""

import importlib.util

import pytest
from datetime import datetime, timezone

from src.orchestration.system_agents import get_system_agents, get_system_agent_by_id, get_agents_by_category
from src.orchestration.agent_registry import Organization, OrgAgent, OrgTask, OrgActivity
from src.db.models import User


# The orchestration API ships in a separate container (Dockerfile.orchestration
# / requirements-orchestration.txt) with its own deps, including fastapi. The
# bot venv intentionally does NOT install fastapi. Tests that import api.py
# (which has a top-level `from fastapi import ...`) must skip gracefully when
# fastapi is absent. To run the full dashboard test suite locally:
#     pip install -r requirements-orchestration.txt
_HAS_FASTAPI = importlib.util.find_spec("fastapi") is not None
_requires_fastapi = pytest.mark.skipif(
    not _HAS_FASTAPI,
    reason="fastapi not installed (install requirements-orchestration.txt to run)",
)


class TestSystemAgentsRegistry:
    """Tests for the system agents registry."""

    def test_get_all_system_agents(self):
        """Test that we can get all system agents."""
        agents = get_system_agents()
        assert len(agents) > 0
        assert len(agents) >= 15  # We have at least 15 system agents

    def test_system_agent_has_required_fields(self):
        """Test that each system agent has all required fields."""
        agents = get_system_agents()
        for agent in agents:
            assert agent.id
            assert agent.name
            assert agent.description
            assert agent.category in ["google_workspace", "internal", "utility"]
            assert isinstance(agent.capabilities, list)
            assert isinstance(agent.tool_count, int)
            assert agent.status in ["active", "beta", "deprecated"]

    def test_get_system_agent_by_id(self):
        """Test fetching a specific agent by ID."""
        agent = get_system_agent_by_id("email_agent")
        assert agent is not None
        assert agent.id == "email_agent"
        assert agent.name == "Email Agent"
        assert agent.category == "google_workspace"

    def test_get_nonexistent_agent_returns_none(self):
        """Test that fetching a nonexistent agent returns None."""
        agent = get_system_agent_by_id("nonexistent_agent")
        assert agent is None

    def test_get_agents_by_category(self):
        """Test filtering agents by category."""
        google_agents = get_agents_by_category("google_workspace")
        assert len(google_agents) > 0
        for agent in google_agents:
            assert agent.category == "google_workspace"

        internal_agents = get_agents_by_category("internal")
        assert len(internal_agents) > 0
        for agent in internal_agents:
            assert agent.category == "internal"


@_requires_fastapi
class TestOrgAgentWithOrgInfo:
    """Tests for OrgAgentWithOrgInfo model validation."""

    def test_can_delete_when_org_paused(self):
        """Test that can_delete is True when org is paused."""
        from src.orchestration.api import OrgAgentWithOrgInfo
        
        agent = OrgAgentWithOrgInfo(
            id=1,
            org_id=1,
            org_name="Test Org",
            org_status="paused",
            name="Test Agent",
            role="assistant",
            model_tier="general",
            status="active",
            can_delete=True,
            delete_reason=None,
        )
        assert agent.can_delete is True
        assert agent.delete_reason is None

    def test_cannot_delete_when_org_active(self):
        """Test that can_delete is False when org is active."""
        from src.orchestration.api import OrgAgentWithOrgInfo
        
        agent = OrgAgentWithOrgInfo(
            id=1,
            org_id=1,
            org_name="Test Org",
            org_status="active",
            name="Test Agent",
            role="assistant",
            model_tier="general",
            status="active",
            can_delete=False,
            delete_reason="Attached to active organization 'Test Org'",
        )
        assert agent.can_delete is False
        assert agent.delete_reason is not None
        assert "Test Org" in agent.delete_reason


@_requires_fastapi
class TestAgentDeletionCheck:
    """Tests for AgentDeletionCheck model."""

    def test_can_delete_response(self):
        """Test the deletion check response model."""
        from src.orchestration.api import AgentDeletionCheck
        
        check = AgentDeletionCheck(
            can_delete=True,
            reason=None,
            attached_org=None,
            attached_org_status=None,
        )
        assert check.can_delete is True
        assert check.reason is None

    def test_cannot_delete_response(self):
        """Test the deletion check response when deletion is blocked."""
        from src.orchestration.api import AgentDeletionCheck
        
        check = AgentDeletionCheck(
            can_delete=False,
            reason="Cannot delete while attached to active organization 'My Org'",
            attached_org="My Org",
            attached_org_status="active",
        )
        assert check.can_delete is False
        assert check.attached_org == "My Org"
        assert check.attached_org_status == "active"
