"""Google Workspace MCP client — connects to the workspace-mcp sidecar container."""

import logging
from typing import Optional

from agents import MCPServerStreamableHttp

from src.settings import settings

logger = logging.getLogger(__name__)

# MCP server URL — points to the workspace-mcp Docker service
WORKSPACE_MCP_URL = "http://workspace-mcp:8080/mcp"


def is_google_configured() -> bool:
    """Check if Google OAuth credentials are configured."""
    return bool(settings.google_oauth_client_id and settings.google_oauth_client_secret)


def create_workspace_mcp_server() -> Optional[MCPServerStreamableHttp]:
    """Create the Google Workspace MCP server connection.

    Returns None if Google credentials are not configured.
    """
    if not is_google_configured():
        logger.info("Google Workspace not configured — skipping MCP server")
        return None

    return MCPServerStreamableHttp(
        name="google_workspace",
        params={"url": WORKSPACE_MCP_URL},
    )


def get_oauth_url(user_id: int) -> str:
    """Generate the Google OAuth authorization URL for a user.

    The workspace-mcp container handles the OAuth flow. This URL
    redirects the user to Google's consent screen.
    """
    return (
        f"http://localhost:8081/auth/google"
        f"?client_id={settings.google_oauth_client_id}"
        f"&state={user_id}"
    )
