"""Google Docs Agent — manages Google Docs via Google Workspace MCP."""

import logging

from agents import function_tool
from src.integrations.workspace_mcp import call_workspace_tool

logger = logging.getLogger(__name__)


def _build_connected_docs_tools(connected_google_email: str) -> list:
    """Build direct connected Google Docs skill tools bound to a Google email."""

    @function_tool(name_override="search_connected_docs")
    async def search_connected_docs(query: str) -> str:
        """Search Google Docs by title or content."""
        return await call_workspace_tool(
            "search_docs",
            {
                "user_google_email": connected_google_email,
                "query": query,
            },
        )

    @function_tool(name_override="get_connected_doc_content")
    async def get_connected_doc_content(document_id: str) -> str:
        """Get the full content of a Google Doc by its ID."""
        return await call_workspace_tool(
            "get_doc_content",
            {
                "user_google_email": connected_google_email,
                "document_id": document_id,
            },
        )

    @function_tool(name_override="get_connected_doc_as_markdown")
    async def get_connected_doc_as_markdown(document_id: str) -> str:
        """Get a Google Doc's content formatted as Markdown."""
        return await call_workspace_tool(
            "get_doc_as_markdown",
            {
                "user_google_email": connected_google_email,
                "document_id": document_id,
            },
        )

    @function_tool(name_override="create_connected_doc")
    async def create_connected_doc(
        title: str,
        content: str | None = None,
        folder_id: str | None = None,
    ) -> str:
        """Create a new Google Doc with optional initial content."""
        return await call_workspace_tool(
            "create_doc",
            {
                "user_google_email": connected_google_email,
                "title": title,
                "content": content,
                "folder_id": folder_id,
            },
        )

    @function_tool(name_override="modify_connected_doc_text")
    async def modify_connected_doc_text(
        document_id: str,
        text: str,
        location: str = "end",
    ) -> str:
        """Insert or append text to a Google Doc. Location: 'start', 'end', or an index."""
        return await call_workspace_tool(
            "modify_doc_text",
            {
                "user_google_email": connected_google_email,
                "document_id": document_id,
                "text": text,
                "location": location,
            },
        )

    @function_tool(name_override="find_and_replace_connected_doc")
    async def find_and_replace_connected_doc(
        document_id: str,
        find_text: str,
        replace_text: str,
    ) -> str:
        """Find and replace text in a Google Doc."""
        return await call_workspace_tool(
            "find_and_replace_doc",
            {
                "user_google_email": connected_google_email,
                "document_id": document_id,
                "find_text": find_text,
                "replace_text": replace_text,
            },
        )

    @function_tool(name_override="export_connected_doc_to_pdf")
    async def export_connected_doc_to_pdf(document_id: str) -> str:
        """Export a Google Doc as a PDF file."""
        return await call_workspace_tool(
            "export_doc_to_pdf",
            {
                "user_google_email": connected_google_email,
                "document_id": document_id,
            },
        )

    return [
        search_connected_docs,
        get_connected_doc_content,
        get_connected_doc_as_markdown,
        create_connected_doc,
        modify_connected_doc_text,
        find_and_replace_connected_doc,
        export_connected_doc_to_pdf,
    ]
