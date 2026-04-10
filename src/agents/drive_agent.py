"""Drive Agent — manages Google Drive via Google Workspace MCP (as_tool per AD-3)."""

import logging
from typing import Any

from agents import Agent, function_tool
from src.agents.persona_mode import PersonaMode, build_persona_mode_addendum
from src.integrations.workspace_mcp import (
    call_workspace_tool,
    get_workspace_tool_argument_names,
)
from src.models.router import ModelRole, select_model

logger = logging.getLogger(__name__)

DRIVE_INSTRUCTIONS = """\
You are a file management specialist. You help the user manage their Google Drive.

## Capabilities
- Search for files and folders by name, type, or content
- List files in specific folders
- Download file contents or metadata
- Upload new files
- Share files with specific people (ALWAYS confirm before sharing)
- Create new folders
- Move and organize files

## Rules
- When sharing files, ALWAYS confirm with the user who to share with and what permissions.
- When deleting files, ALWAYS ask for explicit confirmation.
- For search results, show file name, type, last modified date, and location.
- When uploading, confirm the file name and destination folder.
- If you encounter an auth error, tell the user to run /connect google.

## Output Format
- Use markdown for readability.
- List files with name, type, and last modified date.
- For file details, show name, type, size, location, sharing status.
"""


def _build_connected_drive_tools(connected_google_email: str) -> list:
    """Build direct connected Drive skill tools bound to a Google email."""

    async def _call_connected_drive_tool(
        tool_name: str,
        logical_arguments: dict[str, Any],
        *,
        argument_aliases: dict[str, tuple[str, ...]] | None = None,
    ) -> str:
        """Map stable local argument names onto the live upstream MCP schema."""
        available_argument_names = await get_workspace_tool_argument_names(tool_name)
        aliases = argument_aliases or {}

        args: dict[str, Any] = {
            "user_google_email": connected_google_email,
        }
        for logical_name, value in logical_arguments.items():
            if value is None:
                continue

            candidate_names = aliases.get(logical_name, (logical_name,))
            selected_name = candidate_names[0]
            if available_argument_names:
                for candidate_name in candidate_names:
                    if candidate_name in available_argument_names:
                        selected_name = candidate_name
                        break

            args[selected_name] = value

        return await call_workspace_tool(tool_name, args)

    @function_tool(name_override="search_connected_drive_files")
    async def search_connected_drive_files(
        query: str,
        page_size: int = 10,
    ) -> str:
        """Search Google Drive files by name, content, or type."""
        return await call_workspace_tool(
            "search_drive_files",
            {
                "user_google_email": connected_google_email,
                "query": query,
                "page_size": page_size,
            },
        )

    @function_tool(name_override="list_connected_drive_items")
    async def list_connected_drive_items(
        folder_id: str = "root",
        page_size: int = 20,
    ) -> str:
        """List files and folders in a Google Drive folder."""
        return await call_workspace_tool(
            "list_drive_items",
            {
                "user_google_email": connected_google_email,
                "folder_id": folder_id,
                "page_size": page_size,
            },
        )

    @function_tool(name_override="get_connected_drive_file_content")
    async def get_connected_drive_file_content(file_id: str) -> str:
        """Get the content of a Google Drive file by its ID."""
        return await call_workspace_tool(
            "get_drive_file_content",
            {
                "user_google_email": connected_google_email,
                "file_id": file_id,
            },
        )

    @function_tool(name_override="create_connected_drive_file")
    async def create_connected_drive_file(
        file_name: str,
        content: str | None = None,
        mime_type: str = "text/plain",
        folder_id: str = "root",
        source_url: str | None = None,
    ) -> str:
        """Create a new file on Google Drive."""
        return await _call_connected_drive_tool(
            "create_drive_file",
            {
                "file_name": file_name,
                "content": content,
                "mime_type": mime_type,
                "folder_id": folder_id,
                "source_url": source_url,
            },
            argument_aliases={
                "file_name": ("file_name", "name"),
                "folder_id": ("folder_id", "parent_folder_id"),
                "source_url": ("fileUrl", "file_url", "source_url"),
            },
        )

    @function_tool(name_override="create_connected_drive_folder")
    async def create_connected_drive_folder(
        folder_name: str,
        parent_folder_id: str = "root",
    ) -> str:
        """Create a new folder on Google Drive."""
        return await _call_connected_drive_tool(
            "create_drive_folder",
            {
                "folder_name": folder_name,
                "parent_folder_id": parent_folder_id,
            },
            argument_aliases={
                "folder_name": ("folder_name", "name"),
            },
        )

    # ── Internal helpers (plain async functions, safe for cross-calling) ──

    async def _move_impl(
        file_id: str,
        destination_folder_id: str,
        current_parent_folder_id: str | None = None,
    ) -> str:
        """Internal move implementation — can be called from other helpers."""
        return await _call_connected_drive_tool(
            "update_drive_file",
            {
                "file_id": file_id,
                "destination_folder_id": destination_folder_id,
                "current_parent_folder_id": current_parent_folder_id,
            },
            argument_aliases={
                "destination_folder_id": ("add_parents", "destination_folder_id", "parent_folder_id"),
                "current_parent_folder_id": ("remove_parents", "current_parent_folder_id", "source_folder_id"),
            },
        )

    async def _check_ownership_impl(file_id: str) -> str:
        """Internal ownership check — can be called from other helpers."""
        try:
            result = await _call_connected_drive_tool(
                "get_drive_shareable_link",
                {
                    "user_google_email": connected_google_email,
                    "file_id": file_id,
                },
            )
            is_folder = "application/vnd.google-apps.folder" in result
            if "Shared: True" in result and "Shared: False" not in result:
                if is_folder:
                    return f"Folder {file_id} is SHARED. Cannot rename or copy. Alternative: Create a new folder and manually move contents if you have edit access."
                else:
                    return f"File {file_id} is SHARED (view-only). Cannot rename. Alternative: Create a copy with 'copy_drive_file' to your own folder, then rename the copy."
            elif "Shared: False" in result:
                return f"File {file_id} is OWNED by {connected_google_email}. Can rename, move, and organize."
            else:
                if "owned by" in result.lower() and connected_google_email in result.lower():
                    return f"File {file_id} is OWNED by {connected_google_email}. Can rename, move, and organize."
                return f"File {file_id} ownership unclear. Assume shared - limited operations available."
        except Exception as e:
            return f"Error checking ownership: {str(e)[:100]}"

    async def _rename_impl(file_id: str, new_name: str) -> str:
        """Internal rename implementation — can be called from other helpers."""
        ownership = await _check_ownership_impl(file_id)
        if "SHARED" in ownership or "cannot rename" in ownership.lower():
            return f"Cannot rename: {ownership}\n\nSuggestion: Use create_organized_copy to make an editable copy."
        return await _call_connected_drive_tool(
            "update_drive_file",
            {
                "user_google_email": connected_google_email,
                "file_id": file_id,
                "name": new_name,
            },
        )

    # ── Tool wrappers (thin shells that delegate to helpers) ──

    @function_tool(name_override="move_connected_drive_file")
    async def move_connected_drive_file(
        file_id: str,
        destination_folder_id: str,
        current_parent_folder_id: str | None = None,
    ) -> str:
        """Move a Drive file or folder by ID into a destination folder."""
        return await _move_impl(file_id, destination_folder_id, current_parent_folder_id)

    @function_tool(name_override="check_drive_file_ownership")
    async def check_drive_file_ownership(
        file_id: str,
    ) -> str:
        """Check if the connected user owns or can edit a file, and suggest alternatives."""
        return await _check_ownership_impl(file_id)

    @function_tool(name_override="create_organized_copy")
    async def create_organized_copy(
        file_id: str,
        new_name: str,
        destination_folder_id: str | None = None,
    ) -> str:
        """Create a copy of a shared file in your own Drive with a new name."""
        try:
            # Check ownership first (use internal helper, not FunctionTool)
            ownership = await _check_ownership_impl(file_id)
            if "OWNED" in ownership:
                return f"File is already owned by you. No need to copy."
            
            # Use default folder if none specified
            if not destination_folder_id:
                # Find or create a "Copied from Shared" folder
                destination_folder_id = await _get_or_create_copied_folder()
            
            # Attempt to copy
            result = await _call_connected_drive_tool(
                "copy_drive_file",
                {
                    "user_google_email": connected_google_email,
                    "file_id": file_id,
                    "parent_folder_id": destination_folder_id,
                    "new_name": new_name,
                },
            )
            
            if "Successfully copied" in result:
                return f"✅ {result}\n\nThe copy is now owned by you and can be renamed, moved, and organized freely."
            else:
                return f"Copy failed: {result}"
                
        except Exception as e:
            return f"Error creating copy: {str(e)[:200]}"

    async def _get_or_create_copied_folder() -> str:
        """Get or create a folder for copied shared files."""
        folder_name = "Copied from Shared"
        
        # Search for existing folder
        search_result = await _call_connected_drive_tool(
            "search_drive_files",
            {
                "user_google_email": connected_google_email,
                "query": f"name='{folder_name}' and mimeType='application/vnd.google-apps.folder'",
                "page_size": 5,
            },
        )
        
        if "Found 1 files" in search_result:
            # Extract folder ID
            lines = search_result.split('\n')
            for line in lines:
                if folder_name in line and 'ID:' in line:
                    return line.split('ID:')[1].split(',')[0].strip()
        
        # Create new folder
        create_result = await _call_connected_drive_tool(
            "create_drive_folder",
            {
                "user_google_email": connected_google_email,
                "name": folder_name,
            },
        )
        
        # Extract new folder ID
        if "Folder created" in create_result:
            lines = create_result.split('\n')
            for line in lines:
                if 'ID:' in line:
                    return line.split('ID:')[1].strip()
        
        # Fallback to root
        return "root"

    @function_tool(name_override="batch_move_drive_files")
    async def batch_move_drive_files(
        file_moves_json: str,
    ) -> str:
        """Batch move multiple Drive files with enhanced error recovery.

        `file_moves_json` must be a JSON array of objects, each with keys:
        `file_id`, `file_name`, `destination_folder_id`.
        Example:
        [{"file_id":"1AB…","file_name":"jobesearch","destination_folder_id":"1CD…"}]
        """
        import json as _json

        try:
            file_moves = _json.loads(file_moves_json)
        except _json.JSONDecodeError as exc:
            return f"[ERROR] Could not parse file_moves_json: {exc}"

        if not isinstance(file_moves, list) or not file_moves:
            return "[ERROR] file_moves_json must be a non-empty JSON array."

        # Process moves one-by-one using internal helper (not FunctionTool)
        results: list[str] = []
        for move in file_moves:
            fid = move.get("file_id", "")
            fname = move.get("file_name", fid[:12])
            dest = move.get("destination_folder_id", "")
            if not fid or not dest:
                results.append(f"❌ {fname}: missing file_id or destination_folder_id")
                continue
            try:
                result = await _move_impl(fid, dest)
                results.append(f"✅ {fname}: {result}")
            except Exception as e:
                results.append(f"❌ {fname}: {str(e)[:200]}")

        return "\n".join(results)

    @function_tool(name_override="rename_connected_drive_file")
    async def rename_connected_drive_file(
        file_id: str,
        new_name: str,
    ) -> str:
        """Rename a file or folder in Google Drive."""
        return await _rename_impl(file_id, new_name)

    @function_tool(name_override="compare_drive_items")
    async def compare_drive_items(
        item1_id: str,
        item2_id: str,
        item1_name: str,
        item2_name: str,
    ) -> str:
        """Safely compare two Drive items by content and metadata.
        
        This tool fetches the actual content/metadata of both items and provides:
        - Content comparison (for files)
        - Folder listing comparison (for folders)
        - Metadata comparison (size, type, modified date)
        - Recommendations for deduplication
        """
        results = []
        
        # Get basic info for both items
        try:
            info1 = await _call_connected_drive_tool(
                "get_drive_shareable_link",
                {
                    "user_google_email": connected_google_email,
                    "file_id": item1_id,
                },
            )
        except Exception as e:
            results.append(f"❌ {item1_name}: Could not fetch info - {str(e)[:100]}")
            info1 = None
        
        try:
            info2 = await _call_connected_drive_tool(
                "get_drive_shareable_link",
                {
                    "user_google_email": connected_google_email,
                    "file_id": item2_id,
                },
            )
        except Exception as e:
            results.append(f"❌ {item2_name}: Could not fetch info - {str(e)[:100]}")
            info2 = None
        
        if not info1 or not info2:
            return "\n".join(results)
        
        # Determine item types
        is_folder1 = "application/vnd.google-apps.folder" in info1
        is_folder2 = "application/vnd.google-apps.folder" in info2
        
        # Compare based on types
        if is_folder1 and is_folder2:
            # Both are folders - compare listings
            results.append(f"## Folder Comparison: {item1_name} vs {item2_name}")
            
            try:
                list1 = await _call_connected_drive_tool(
                    "list_drive_items",
                    {
                        "user_google_email": connected_google_email,
                        "folder_id": item1_id,
                        "page_size": 50,
                    },
                )
                list2 = await _call_connected_drive_tool(
                    "list_drive_items",
                    {
                        "user_google_email": connected_google_email,
                        "folder_id": item2_id,
                        "page_size": 50,
                    },
                )
                
                # Count items in each
                count1 = list1.count("Name:") if "Name:" in list1 else 0
                count2 = list2.count("Name:") if "Name:" in list2 else 0
                
                results.append(f"📁 {item1_name}: {count1} items")
                results.append(f"📁 {item2_name}: {count2} items")
                
                if count1 == 0 and count2 == 0:
                    results.append("✅ Both folders are empty - safe to delete one")
                elif count1 == count2:
                    results.append("⚠️ Same number of items - need manual review")
                else:
                    results.append("📊 Different item counts - likely different folders")
                
                # Show first few items from each for comparison
                if count1 > 0:
                    results.append(f"\n{item1_name} contents:")
                    lines1 = list1.split('\n')
                    for line in lines1[:5]:
                        if "Name:" in line:
                            results.append(f"  - {line.strip()}")
                
                if count2 > 0:
                    results.append(f"\n{item2_name} contents:")
                    lines2 = list2.split('\n')
                    for line in lines2[:5]:
                        if "Name:" in line:
                            results.append(f"  - {line.strip()}")
                
            except Exception as e:
                results.append(f"❌ Could not compare folder contents: {str(e)[:100]}")
        
        elif not is_folder1 and not is_folder2:
            # Both are files - compare content
            results.append(f"## File Comparison: {item1_name} vs {item2_name}")
            
            # Try to get content for text-based files
            text_extensions = ['.txt', '.docx', '.xlsx', '.csv', '.md', '.py', '.js', '.html']
            is_text1 = any(ext in item1_name.lower() for ext in text_extensions)
            is_text2 = any(ext in item2_name.lower() for ext in text_extensions)
            
            if is_text1 and is_text2:
                try:
                    content1 = await _call_connected_drive_tool(
                        "get_drive_file_content",
                        {
                            "user_google_email": connected_google_email,
                            "file_id": item1_id,
                        },
                    )
                    content2 = await _call_connected_drive_tool(
                        "get_drive_file_content",
                        {
                            "user_google_email": connected_google_email,
                            "file_id": item2_id,
                        },
                    )
                    
                    # Simple content comparison
                    if content1 == content2:
                        results.append("✅ Files have identical content")
                        results.append("💡 Recommendation: Keep one, delete the other")
                    elif len(content1) == len(content2):
                        results.append("⚠️ Files have same length but different content")
                        results.append("💡 Recommendation: Manual review needed")
                    else:
                        results.append(f"📏 Different sizes: {len(content1)} vs {len(content2)} chars")
                        results.append("💡 Recommendation: Likely different files")
                        
                except Exception as e:
                    results.append(f"❌ Could not compare file contents: {str(e)[:100]}")
            else:
                results.append("📄 File types may not support text comparison")
                results.append("💡 Recommendation: Compare manually or use specialized tools")
        
        else:
            # One is folder, one is file
            results.append(f"## Type Mismatch: {item1_name} vs {item2_name}")
            results.append(f"📁 {item1_name if is_folder1 else item2_name} is a folder")
            results.append(f"📄 {item2_name if is_folder1 else item1_name} is a file")
            results.append("💡 Recommendation: These are different types - keep both")
        
        # Add metadata comparison
        results.append("\n## Metadata Comparison")
        results.append(f"📋 {item1_name}: {info1[:200]}...")
        results.append(f"📋 {item2_name}: {info2[:200]}...")
        
        return "\n".join(results)

    @function_tool(name_override="batch_compare_drive_items")
    async def batch_compare_drive_items(
        comparisons_json: str,
    ) -> str:
        """Batch compare multiple Drive item pairs.
        
        `comparisons_json` must be a JSON array of objects, each with keys:
        `item1_id`, `item2_id`, `item1_name`, `item2_name`.
        Example:
        [{"item1_id":"1AB…","item2_id":"1CD…","item1_name":"Programa","item2_name":"Programa.docx"}]
        """
        import json as _json

        try:
            comparisons = _json.loads(comparisons_json)
        except _json.JSONDecodeError as exc:
            return f"[ERROR] Could not parse comparisons_json: {exc}"

        if not isinstance(comparisons, list) or not comparisons:
            return "[ERROR] comparisons_json must be a non-empty JSON array."

        results = []
        results.append("# Batch Drive Item Comparison Report\n")
        
        for i, comp in enumerate(comparisons, 1):
            item1_id = comp.get("item1_id", "")
            item2_id = comp.get("item2_id", "")
            item1_name = comp.get("item1_name", "Item 1")
            item2_name = comp.get("item2_name", "Item 2")
            
            if not item1_id or not item2_id:
                results.append(f"## Comparison {i}: ❌ Missing IDs")
                continue
            
            results.append(f"## Comparison {i}: {item1_name} vs {item2_name}")
            
            try:
                comparison_result = await compare_drive_items(
                    item1_id, item2_id, item1_name, item2_name
                )
                results.append(comparison_result)
            except Exception as e:
                results.append(f"❌ Comparison failed: {str(e)[:100]}")
            
            results.append("\n---\n")
        
        return "\n".join(results)

    @function_tool(name_override="get_connected_drive_shareable_link")
    async def get_connected_drive_shareable_link(file_id: str) -> str:
        """Get a shareable link for a Google Drive file."""
        return await call_workspace_tool(
            "get_drive_shareable_link",
            {
                "user_google_email": connected_google_email,
                "file_id": file_id,
            },
        )

    @function_tool(name_override="manage_connected_drive_access")
    async def manage_connected_drive_access(
        file_id: str,
        action: str,
        share_with: str | None = None,
        role: str | None = None,
        share_type: str = "user",
        permission_id: str | None = None,
        send_notification: bool = True,
        email_message: str | None = None,
        expiration_time: str | None = None,
        allow_file_discovery: bool | None = None,
        new_owner_email: str | None = None,
        move_to_new_owners_root: bool = False,
    ) -> str:
        """Manage sharing permissions for a Google Drive file.

        Actions include `grant`, `update`, `revoke`, and `transfer_owner`.
        """
        return await _call_connected_drive_tool(
            "manage_drive_access",
            {
                "file_id": file_id,
                "action": action,
                "share_with": share_with,
                "role": role,
                "share_type": share_type,
                "permission_id": permission_id,
                "send_notification": send_notification,
                "email_message": email_message,
                "expiration_time": expiration_time,
                "allow_file_discovery": allow_file_discovery,
                "new_owner_email": new_owner_email,
                "move_to_new_owners_root": move_to_new_owners_root,
            },
            argument_aliases={
                "share_with": ("share_with", "email"),
            },
        )

    return [
        search_connected_drive_files,
        list_connected_drive_items,
        get_connected_drive_file_content,
        create_connected_drive_file,
        create_connected_drive_folder,
        move_connected_drive_file,
        batch_move_drive_files,
        check_drive_file_ownership,
        create_organized_copy,
        rename_connected_drive_file,
        compare_drive_items,
        batch_compare_drive_items,
        get_connected_drive_shareable_link,
        manage_connected_drive_access,
    ]


def create_drive_agent(
    mcp_servers: list = None,
    connected_google_email: str | None = None,
    mode: PersonaMode = "workspace",
) -> Agent:
    """Create the drive specialist agent."""
    instructions = f"{DRIVE_INSTRUCTIONS}\n\n{build_persona_mode_addendum(mode)}"
    tools = []
    if connected_google_email:
        instructions = (
            f"{instructions}\n\n"
            f"## Connected Google Account\n"
            f"The user's connected Google email is `{connected_google_email}`. "
            f"For file/folder operations, prefer the connected-account tools "
            f"(`search_connected_drive_files`, `list_connected_drive_items`, "
            f"`create_connected_drive_folder`, `move_connected_drive_file`, "
            f"`rename_connected_drive_file`, etc.). "
            f"When calling Google Workspace tools, pass `user_google_email` as `{connected_google_email}` unless the user explicitly asks for a different connected Google account."
        )
        tools = _build_connected_drive_tools(connected_google_email)

    # When connected tools are available, they use call_workspace_tool() which
    # manages its own MCP connection per call. Passing the shared mcp_servers
    # instance to sub-agents causes lifecycle conflicts when used via as_tool().
    effective_mcp = [] if connected_google_email else (mcp_servers or [])

    selection = select_model(ModelRole.GENERAL)
    return Agent(
        name="DriveAgent",
        instructions=instructions,
        model=selection.model_id,
        tools=tools,
        mcp_servers=effective_mcp,
    )
