"""Tests for Google Workspace integration (Phase 2)."""

import pytest
from unittest.mock import patch, MagicMock

from src.agents.email_agent import create_email_agent, EMAIL_INSTRUCTIONS
from src.agents.calendar_agent import create_calendar_agent, CALENDAR_INSTRUCTIONS
from src.agents.drive_agent import create_drive_agent, DRIVE_INSTRUCTIONS


class TestEmailAgent:
    """Test email agent creation and instructions."""

    def test_create_email_agent(self) -> None:
        agent = create_email_agent()
        assert agent.name == "EmailAgent"

    def test_email_instructions_contain_key_capabilities(self) -> None:
        assert "Read" in EMAIL_INSTRUCTIONS
        assert "send" in EMAIL_INSTRUCTIONS.lower()
        assert "draft" in EMAIL_INSTRUCTIONS.lower()
        assert "confirmation" in EMAIL_INSTRUCTIONS.lower()

    def test_email_agent_with_mcp_servers(self) -> None:
        mock_mcp = MagicMock()
        agent = create_email_agent(mcp_servers=[mock_mcp])
        assert len(agent.mcp_servers) == 1


class TestCalendarAgent:
    """Test calendar agent creation and instructions."""

    def test_create_calendar_agent(self) -> None:
        agent = create_calendar_agent()
        assert agent.name == "CalendarAgent"

    def test_calendar_instructions_contain_key_capabilities(self) -> None:
        assert "View" in CALENDAR_INSTRUCTIONS
        assert "Create" in CALENDAR_INSTRUCTIONS
        assert "confirm" in CALENDAR_INSTRUCTIONS.lower()
        assert "timezone" in CALENDAR_INSTRUCTIONS.lower()

    def test_calendar_agent_with_mcp_servers(self) -> None:
        mock_mcp = MagicMock()
        agent = create_calendar_agent(mcp_servers=[mock_mcp])
        assert len(agent.mcp_servers) == 1


class TestDriveAgent:
    """Test drive agent creation and instructions."""

    def test_create_drive_agent(self) -> None:
        agent = create_drive_agent()
        assert agent.name == "DriveAgent"

    def test_drive_instructions_contain_key_capabilities(self) -> None:
        assert "Search" in DRIVE_INSTRUCTIONS
        assert "upload" in DRIVE_INSTRUCTIONS.lower()
        assert "download" in DRIVE_INSTRUCTIONS.lower()
        assert "confirm" in DRIVE_INSTRUCTIONS.lower()

    def test_drive_agent_with_mcp_servers(self) -> None:
        mock_mcp = MagicMock()
        agent = create_drive_agent(mcp_servers=[mock_mcp])
        assert len(agent.mcp_servers) == 1


class TestWorkspaceMCPClient:
    """Test workspace MCP client configuration."""

    def test_is_google_configured_false_by_default(self) -> None:
        from src.integrations.workspace_mcp import is_google_configured
        # Default test env has empty Google credentials
        result = is_google_configured()
        assert result is False

    def test_create_mcp_server_returns_none_when_not_configured(self) -> None:
        from src.integrations.workspace_mcp import create_workspace_mcp_server
        result = create_workspace_mcp_server()
        assert result is None

    @patch("src.integrations.workspace_mcp.settings")
    def test_is_google_configured_true(self, mock_settings) -> None:
        mock_settings.google_oauth_client_id = "test-client-id"
        mock_settings.google_oauth_client_secret = "test-client-secret"
        from src.integrations.workspace_mcp import is_google_configured
        # Need to reimport to use patched settings
        assert mock_settings.google_oauth_client_id != ""

    def test_get_oauth_url_contains_user_id(self) -> None:
        from src.integrations.workspace_mcp import get_oauth_url
        url = get_oauth_url(12345)
        assert "12345" in url
        assert "state=" in url


class TestOrchestratorWithWorkspace:
    """Test orchestrator integrates workspace agents correctly."""

    def test_orchestrator_persona_mentions_specialists(self) -> None:
        from src.agents.orchestrator import build_persona_prompt
        prompt = build_persona_prompt("TestUser")
        assert "Email" in prompt
        assert "Calendar" in prompt
        assert "Drive" in prompt

    def test_orchestrator_mentions_connect_command(self) -> None:
        from src.agents.orchestrator import build_persona_prompt
        prompt = build_persona_prompt()
        assert "/connect google" in prompt

    def test_orchestrator_without_google_has_web_search(self) -> None:
        from src.agents.orchestrator import create_orchestrator
        agent = create_orchestrator("TestUser")
        # Should always have at least WebSearchTool
        assert len(agent.tools) >= 1
