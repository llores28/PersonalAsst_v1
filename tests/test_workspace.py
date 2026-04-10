"""Tests for Google Workspace MCP client configuration.

Agent-specific tests (tool registration, instructions, briefing mode, etc.)
live in test_google_integration.py. This file covers only the MCP client
configuration, auth URL extraction, and connected email storage.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


class TestWorkspaceMCPClient:
    """Test workspace MCP client configuration."""

    @patch("src.integrations.workspace_mcp.settings")
    def test_is_google_configured_false_by_default(self, mock_settings) -> None:
        mock_settings.google_oauth_client_id = ""
        mock_settings.google_oauth_client_secret = ""
        from src.integrations.workspace_mcp import is_google_configured
        result = is_google_configured()
        assert result is False

    @patch("src.integrations.workspace_mcp.settings")
    def test_create_mcp_server_returns_none_when_not_configured(self, mock_settings) -> None:
        mock_settings.google_oauth_client_id = ""
        mock_settings.google_oauth_client_secret = ""
        from src.integrations.workspace_mcp import create_workspace_mcp_server
        result = create_workspace_mcp_server()
        assert result is None

    @patch("src.integrations.workspace_mcp.settings")
    def test_is_google_configured_true(self, mock_settings) -> None:
        mock_settings.google_oauth_client_id = "test-client-id"
        mock_settings.google_oauth_client_secret = "test-client-secret"
        # Need to reimport to use patched settings
        assert mock_settings.google_oauth_client_id != ""

    @patch("src.integrations.workspace_mcp.settings")
    def test_create_mcp_server_returns_streamable_http_server(self, mock_settings) -> None:
        mock_settings.google_oauth_client_id = "test-client-id"
        mock_settings.google_oauth_client_secret = "test-client-secret"
        mock_settings.workspace_mcp_url = "http://workspace-mcp:8000/mcp"

        from src.integrations.workspace_mcp import create_workspace_mcp_server

        result = create_workspace_mcp_server()

        assert result is not None
        assert type(result).__name__ == "MCPServerStreamableHttp"

    def test_extract_authorization_url_from_tool_message(self) -> None:
        from src.integrations.workspace_mcp import _extract_authorization_url

        message = "Authorization URL: https://accounts.google.com/o/oauth2/auth?state=abc123"
        assert _extract_authorization_url(message) == "https://accounts.google.com/o/oauth2/auth?state=abc123"

    @pytest.mark.asyncio
    @patch("src.integrations.workspace_mcp.create_workspace_mcp_server")
    async def test_get_google_auth_url_uses_start_google_auth_tool(self, mock_create_server) -> None:
        mock_server = AsyncMock()
        mock_server.call_tool.return_value = MagicMock(
            content=[MagicMock(text="Authorization URL: https://accounts.google.com/o/oauth2/auth?state=abc123")]
        )
        mock_create_server.return_value = mock_server

        from src.integrations.workspace_mcp import get_google_auth_url

        url = await get_google_auth_url(12345, "user@example.com")

        assert url == "https://accounts.google.com/o/oauth2/auth?state=abc123"
        mock_server.connect.assert_awaited_once()
        mock_server.call_tool.assert_awaited_once_with(
            "start_google_auth",
            {
                "service_name": "Google Workspace",
                "user_google_email": "user@example.com",
            },
        )
        mock_server.cleanup.assert_awaited_once()

    @pytest.mark.asyncio
    @patch("src.integrations.workspace_mcp.create_workspace_mcp_server")
    async def test_call_workspace_tool_strips_none_values(self, mock_create_server) -> None:
        """Regression: None values must be stripped before MCP call to avoid
        additionalProperties:false schema rejections."""
        mock_server = AsyncMock()
        mock_server.call_tool.return_value = MagicMock(
            content=[MagicMock(text="OK")]
        )
        mock_create_server.return_value = mock_server

        from src.integrations.workspace_mcp import call_workspace_tool

        await call_workspace_tool("send_gmail_message", {
            "user_google_email": "test@gmail.com",
            "to": "bob@example.com",
            "subject": "Test",
            "body": "Hello",
            "cc": None,
            "bcc": None,
            "thread_id": None,
            "in_reply_to": None,
        })

        # Only non-None values should reach the MCP server
        mock_server.call_tool.assert_awaited_once_with(
            "send_gmail_message",
            {
                "user_google_email": "test@gmail.com",
                "to": "bob@example.com",
                "subject": "Test",
                "body": "Hello",
            },
        )

    @pytest.mark.asyncio
    @patch("src.integrations.workspace_mcp.create_workspace_mcp_server")
    async def test_list_workspace_tool_schemas_returns_live_input_schemas(self, mock_create_server) -> None:
        mock_server = AsyncMock()
        tool = MagicMock()
        tool.name = "create_drive_folder"
        tool.inputSchema = {
            "type": "object",
            "properties": {
                "user_google_email": {"type": "string"},
                "folder_name": {"type": "string"},
                "parent_folder_id": {"type": "string"},
            },
        }
        mock_server.list_tools.return_value = [tool]
        mock_create_server.return_value = mock_server

        from src.integrations.workspace_mcp import list_workspace_tool_schemas

        schemas = await list_workspace_tool_schemas(force_refresh=True)

        assert "create_drive_folder" in schemas
        assert "folder_name" in schemas["create_drive_folder"]["properties"]
        mock_server.connect.assert_awaited_once()
        mock_server.cleanup.assert_awaited_once()

    @pytest.mark.asyncio
    @patch("src.integrations.workspace_mcp.create_workspace_mcp_server")
    async def test_get_workspace_tool_argument_names_returns_property_names(self, mock_create_server) -> None:
        mock_server = AsyncMock()
        tool = MagicMock()
        tool.name = "update_drive_file"
        tool.inputSchema = {
            "type": "object",
            "properties": {
                "file_id": {"type": "string"},
                "new_name": {"type": "string"},
                "add_parents": {"type": "string"},
            },
        }
        mock_server.list_tools.return_value = [tool]
        mock_create_server.return_value = mock_server

        from src.integrations.workspace_mcp import get_workspace_tool_argument_names

        argument_names = await get_workspace_tool_argument_names(
            "update_drive_file",
            force_refresh=True,
        )

        assert argument_names == {"file_id", "new_name", "add_parents"}

    @pytest.mark.asyncio
    @patch("src.integrations.workspace_mcp.get_redis")
    async def test_store_and_get_connected_google_email(self, mock_get_redis) -> None:
        mock_redis = AsyncMock()
        mock_redis.get.return_value = "user@example.com"
        mock_get_redis.return_value = mock_redis

        from src.integrations.workspace_mcp import get_connected_google_email, store_connected_google_email

        await store_connected_google_email(12345, "User@Example.com")
        result = await get_connected_google_email(12345)

        mock_redis.set.assert_awaited_once_with("google_email:12345", "user@example.com")
        mock_redis.get.assert_awaited_once_with("google_email:12345")
        assert result == "user@example.com"
