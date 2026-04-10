"""Google Contacts Agent — manages Google Contacts via People API through Google Workspace MCP."""

import logging

from agents import function_tool
from src.integrations.workspace_mcp import call_workspace_tool

logger = logging.getLogger(__name__)


def _build_connected_contacts_tools(connected_google_email: str) -> list:
    """Build direct connected Google Contacts skill tools bound to a Google email."""

    @function_tool(name_override="list_connected_contacts")
    async def list_connected_contacts(
        page_size: int = 50,
        page_token: str | None = None,
    ) -> str:
        """List the user's Google Contacts with names, emails, and phone numbers."""
        args: dict = {
            "user_google_email": connected_google_email,
            "page_size": page_size,
        }
        if page_token:
            args["page_token"] = page_token
        return await call_workspace_tool("list_contacts", args)

    @function_tool(name_override="get_connected_contact")
    async def get_connected_contact(contact_id: str) -> str:
        """Get detailed information about a specific contact by ID."""
        return await call_workspace_tool(
            "get_contact",
            {
                "user_google_email": connected_google_email,
                "contact_id": contact_id,
            },
        )

    @function_tool(name_override="search_connected_contacts")
    async def search_connected_contacts(query: str, page_size: int = 20) -> str:
        """Search contacts by name, email, phone number, or other fields."""
        return await call_workspace_tool(
            "search_contacts",
            {
                "user_google_email": connected_google_email,
                "query": query,
                "page_size": page_size,
            },
        )

    @function_tool(name_override="manage_connected_contact")
    async def manage_connected_contact(
        action: str,
        given_name: str | None = None,
        family_name: str | None = None,
        email: str | None = None,
        phone: str | None = None,
        organization: str | None = None,
        job_title: str | None = None,
        notes: str | None = None,
        contact_id: str | None = None,
    ) -> str:
        """Create, update, or delete a Google Contact. Actions: create, update, delete. contact_id required for update/delete."""
        args: dict = {
            "user_google_email": connected_google_email,
            "action": action,
        }
        if contact_id:
            args["contact_id"] = contact_id
        if given_name:
            args["given_name"] = given_name
        if family_name:
            args["family_name"] = family_name
        if email:
            args["email"] = email
        if phone:
            args["phone"] = phone
        if organization:
            args["organization"] = organization
        if job_title:
            args["job_title"] = job_title
        if notes:
            args["notes"] = notes
        return await call_workspace_tool("manage_contact", args)

    return [
        list_connected_contacts,
        get_connected_contact,
        search_connected_contacts,
        manage_connected_contact,
    ]
