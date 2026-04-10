"""Google Slides Agent — manages Google Slides via Google Workspace MCP."""

import logging

from agents import function_tool
from src.integrations.workspace_mcp import call_workspace_tool

logger = logging.getLogger(__name__)


def _build_connected_slides_tools(connected_google_email: str) -> list:
    """Build direct connected Google Slides skill tools bound to a Google email."""

    @function_tool(name_override="create_connected_presentation")
    async def create_connected_presentation(title: str = "Untitled Presentation") -> str:
        """Create a new Google Slides presentation."""
        return await call_workspace_tool(
            "create_presentation",
            {
                "user_google_email": connected_google_email,
                "title": title,
            },
        )

    @function_tool(name_override="get_connected_presentation")
    async def get_connected_presentation(presentation_id: str) -> str:
        """Get details about a Google Slides presentation including slide content."""
        return await call_workspace_tool(
            "get_presentation",
            {
                "user_google_email": connected_google_email,
                "presentation_id": presentation_id,
            },
        )

    @function_tool(name_override="update_connected_presentation")
    async def update_connected_presentation(
        presentation_id: str,
        requests: str,
    ) -> str:
        """Apply batch updates to a Google Slides presentation. Requests should be a JSON array of Slides API request objects (createSlide, insertText, etc.)."""
        return await call_workspace_tool(
            "batch_update_presentation",
            {
                "user_google_email": connected_google_email,
                "presentation_id": presentation_id,
                "requests": requests,
            },
        )

    @function_tool(name_override="get_connected_slide_page")
    async def get_connected_slide_page(
        presentation_id: str,
        page_object_id: str,
    ) -> str:
        """Get details about a specific slide page including elements and layout."""
        return await call_workspace_tool(
            "get_page",
            {
                "user_google_email": connected_google_email,
                "presentation_id": presentation_id,
                "page_object_id": page_object_id,
            },
        )

    @function_tool(name_override="get_connected_slide_thumbnail")
    async def get_connected_slide_thumbnail(
        presentation_id: str,
        page_object_id: str,
        thumbnail_size: str = "MEDIUM",
    ) -> str:
        """Generate a thumbnail URL for a specific slide. Size: LARGE, MEDIUM, or SMALL."""
        return await call_workspace_tool(
            "get_page_thumbnail",
            {
                "user_google_email": connected_google_email,
                "presentation_id": presentation_id,
                "page_object_id": page_object_id,
                "thumbnail_size": thumbnail_size,
            },
        )

    return [
        create_connected_presentation,
        get_connected_presentation,
        update_connected_presentation,
        get_connected_slide_page,
        get_connected_slide_thumbnail,
    ]
