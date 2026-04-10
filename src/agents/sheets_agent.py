"""Google Sheets Agent — manages Google Sheets via Google Workspace MCP."""

import logging

from agents import function_tool
from src.integrations.workspace_mcp import call_workspace_tool

logger = logging.getLogger(__name__)


def _build_connected_sheets_tools(connected_google_email: str) -> list:
    """Build direct connected Google Sheets skill tools bound to a Google email."""

    @function_tool(name_override="create_connected_spreadsheet")
    async def create_connected_spreadsheet(
        title: str,
        sheet_names: str | None = None,
    ) -> str:
        """Create a new Google Spreadsheet. Optionally provide comma-separated sheet names."""
        args: dict = {
            "user_google_email": connected_google_email,
            "title": title,
        }
        if sheet_names:
            args["sheet_names"] = sheet_names
        return await call_workspace_tool("create_spreadsheet", args)

    @function_tool(name_override="get_connected_spreadsheet_info")
    async def get_connected_spreadsheet_info(spreadsheet_id: str) -> str:
        """Get metadata about a Google Spreadsheet (sheets, titles, row counts)."""
        return await call_workspace_tool(
            "get_spreadsheet_info",
            {
                "user_google_email": connected_google_email,
                "spreadsheet_id": spreadsheet_id,
            },
        )

    @function_tool(name_override="get_connected_sheet_values")
    async def get_connected_sheet_values(
        spreadsheet_id: str,
        range_name: str,
    ) -> str:
        """Read values from a Google Sheets range (e.g., 'Sheet1!A1:D10')."""
        return await call_workspace_tool(
            "read_sheet_values",
            {
                "user_google_email": connected_google_email,
                "spreadsheet_id": spreadsheet_id,
                "range_name": range_name,
            },
        )

    @function_tool(name_override="update_connected_sheet_values")
    async def update_connected_sheet_values(
        spreadsheet_id: str,
        range_name: str,
        values: str,
    ) -> str:
        """Update values in a Google Sheets range. Values should be a JSON array of arrays."""
        return await call_workspace_tool(
            "modify_sheet_values",
            {
                "user_google_email": connected_google_email,
                "spreadsheet_id": spreadsheet_id,
                "range_name": range_name,
                "values": values,
            },
        )

    @function_tool(name_override="append_connected_sheet_values")
    async def append_connected_sheet_values(
        spreadsheet_id: str,
        range_name: str,
        values: str,
    ) -> str:
        """Append rows to a Google Sheet. Values should be a JSON array of arrays."""
        return await call_workspace_tool(
            "modify_sheet_values",
            {
                "user_google_email": connected_google_email,
                "spreadsheet_id": spreadsheet_id,
                "range_name": range_name,
                "values": values,
            },
        )

    @function_tool(name_override="create_connected_sheet_tab")
    async def create_connected_sheet_tab(
        spreadsheet_id: str,
        sheet_name: str,
    ) -> str:
        """Add a new sheet tab to an existing Google Spreadsheet."""
        return await call_workspace_tool(
            "create_sheet",
            {
                "user_google_email": connected_google_email,
                "spreadsheet_id": spreadsheet_id,
                "sheet_name": sheet_name,
            },
        )

    return [
        create_connected_spreadsheet,
        get_connected_spreadsheet_info,
        get_connected_sheet_values,
        update_connected_sheet_values,
        append_connected_sheet_values,
        create_connected_sheet_tab,
    ]
