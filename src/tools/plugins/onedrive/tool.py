"""OneDrive function-type tool — search, inspect, create folders, rename, and move items.

Uses Microsoft Graph delegated auth via the credential vault. Supports either:
- a preloaded ``onedrive_access_token``, or
- refresh-token based auth via ``microsoft_client_id`` + ``onedrive_refresh_token``
  (with optional ``microsoft_client_secret`` and ``microsoft_tenant_id``).

Exposes ``tool_functions`` list for the ToolRegistry multi-tool loader.
"""

from __future__ import annotations

import json
import logging
from typing import Any
from urllib.parse import quote

import httpx
from agents import function_tool

logger = logging.getLogger(__name__)

_GRAPH_BASE_URL = "https://graph.microsoft.com/v1.0"
_TOKEN_BASE_URL = "https://login.microsoftonline.com"
_DEFAULT_TIMEOUT = 30.0
_SELECT_FIELDS = ",".join(
    [
        "id",
        "name",
        "size",
        "webUrl",
        "createdDateTime",
        "lastModifiedDateTime",
        "parentReference",
        "folder",
        "file",
        "package",
        "remoteItem",
        "root",
    ]
)


class OneDriveAPIError(RuntimeError):
    """Structured error for Microsoft Graph request failures."""

    def __init__(self, status_code: int, message: str):
        super().__init__(message)
        self.status_code = status_code


def _safe_json(obj: object, max_len: int = 6000) -> str:
    """Serialize to JSON, truncating oversized payloads for tool output."""
    try:
        text = json.dumps(obj, indent=2, default=str)
    except (TypeError, ValueError):
        text = str(obj)
    if len(text) > max_len:
        text = text[:max_len] + "\n... (truncated)"
    return text


def _normalize_path(path: str) -> str:
    normalized = path.strip().replace("\\", "/")
    if normalized in {"", "/", "root", "root/"}:
        return ""
    normalized = normalized.removeprefix("root:/").removeprefix("root:")
    return normalized.strip("/")


def _encode_drive_path(path: str) -> str:
    return "/".join(quote(segment, safe="") for segment in _normalize_path(path).split("/") if segment)


def _item_path(item: dict[str, Any]) -> str:
    if item.get("root") is not None:
        return "/"

    parent_path = item.get("parentReference", {}).get("path", "")
    display_parent = ""
    if "root:" in parent_path:
        display_parent = parent_path.split("root:", 1)[1]

    display_parent = display_parent or ""
    display_parent = display_parent if display_parent.startswith("/") or not display_parent else f"/{display_parent}"
    name = item.get("name", "").strip()
    if not name:
        return display_parent or "/"
    if display_parent in {"", "/"}:
        return f"/{name}"
    return f"{display_parent.rstrip('/')}/{name}"


def _format_item(item: dict[str, Any]) -> dict[str, Any]:
    item_type = "item"
    if item.get("folder") is not None:
        item_type = "folder"
    elif item.get("file") is not None:
        item_type = "file"
    elif item.get("package") is not None:
        item_type = "package"
    elif item.get("remoteItem") is not None:
        item_type = "remote_item"

    formatted: dict[str, Any] = {
        "id": item.get("id", ""),
        "name": item.get("name", ""),
        "type": item_type,
        "path": _item_path(item),
        "size": item.get("size", 0),
        "created_at": item.get("createdDateTime", ""),
        "last_modified": item.get("lastModifiedDateTime", ""),
        "web_url": item.get("webUrl", ""),
        "parent_id": item.get("parentReference", {}).get("id", ""),
        "drive_id": item.get("parentReference", {}).get("driveId", ""),
    }
    if item_type == "folder":
        formatted["child_count"] = item.get("folder", {}).get("childCount", 0)
    return formatted


def _graph_error_message(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        payload = {}

    if isinstance(payload, dict):
        error = payload.get("error", {})
        if isinstance(error, dict):
            code = error.get("code", "")
            message = error.get("message", "")
            if code and message:
                return f"{code}: {message}"
            if message:
                return message

    text = response.text.strip()
    return text[:400] if text else f"HTTP {response.status_code}"


def _setup_instructions() -> str:
    return (
        "OneDrive credentials not configured. Set either:\n"
        "  /tools credentials set onedrive onedrive_access_token <access_token>\n\n"
        "Or delegated refresh-token credentials:\n"
        "  /tools credentials set onedrive microsoft_client_id <client_id>\n"
        "  /tools credentials set onedrive onedrive_refresh_token <refresh_token>\n"
        "  /tools credentials set onedrive microsoft_tenant_id common\n"
        "  /tools credentials set onedrive microsoft_client_secret <client_secret>  (optional for public clients)"
    )


async def _refresh_access_token(creds: dict[str, str]) -> str:
    from src.tools.credentials import store_credentials

    refresh_token = creds.get("onedrive_refresh_token", "").strip()
    client_id = creds.get("microsoft_client_id", "").strip()
    client_secret = creds.get("microsoft_client_secret", "").strip()
    tenant = creds.get("microsoft_tenant_id", "").strip() or "common"

    if not refresh_token or not client_id:
        raise RuntimeError(_setup_instructions())

    token_url = f"{_TOKEN_BASE_URL}/{quote(tenant, safe='')}/oauth2/v2.0/token"
    data = {
        "client_id": client_id,
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
    }
    if client_secret:
        data["client_secret"] = client_secret

    async with httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT) as client:
        response = await client.post(token_url, data=data)

    if response.status_code >= 400:
        raise RuntimeError(f"Microsoft token refresh failed: {_graph_error_message(response)}")

    payload = response.json()
    access_token = payload.get("access_token", "").strip()
    if not access_token:
        raise RuntimeError("Microsoft token refresh succeeded but did not return an access token.")

    updates = {"onedrive_access_token": access_token}
    rotated_refresh = payload.get("refresh_token", "").strip()
    if rotated_refresh:
        updates["onedrive_refresh_token"] = rotated_refresh
    await store_credentials("onedrive", updates)
    logger.info("OneDrive access token refreshed via Microsoft OAuth")
    return access_token


async def _get_access_token(*, force_refresh: bool = False) -> str:
    from src.tools.credentials import get_credentials

    creds = await get_credentials("onedrive")
    access_token = creds.get("onedrive_access_token", "").strip()
    has_refresh_flow = bool(
        creds.get("onedrive_refresh_token", "").strip() and creds.get("microsoft_client_id", "").strip()
    )

    if force_refresh and has_refresh_flow:
        return await _refresh_access_token(creds)
    if access_token and not force_refresh:
        return access_token
    if has_refresh_flow:
        return await _refresh_access_token(creds)
    if access_token:
        return access_token
    raise RuntimeError(_setup_instructions())


async def _graph_request(
    method: str,
    endpoint: str,
    *,
    params: dict[str, Any] | None = None,
    json_body: dict[str, Any] | None = None,
    retry_on_401: bool = True,
) -> dict[str, Any]:
    token = await _get_access_token()
    headers = {"Authorization": f"Bearer {token}"}

    async with httpx.AsyncClient(base_url=_GRAPH_BASE_URL, timeout=_DEFAULT_TIMEOUT) as client:
        response = await client.request(method, endpoint, params=params, json=json_body, headers=headers)

        if response.status_code == 401 and retry_on_401:
            refreshed = await _get_access_token(force_refresh=True)
            if refreshed != token:
                headers["Authorization"] = f"Bearer {refreshed}"
                response = await client.request(method, endpoint, params=params, json=json_body, headers=headers)

    if response.status_code >= 400:
        raise OneDriveAPIError(response.status_code, _graph_error_message(response))
    if not response.text.strip():
        return {}
    return response.json()


async def _get_root_item() -> dict[str, Any]:
    return await _graph_request("GET", "/me/drive/root", params={"$select": _SELECT_FIELDS})


async def _get_item_by_path(path: str) -> dict[str, Any]:
    normalized = _normalize_path(path)
    if not normalized:
        return await _get_root_item()
    encoded = _encode_drive_path(normalized)
    return await _graph_request("GET", f"/me/drive/root:/{encoded}", params={"$select": _SELECT_FIELDS})


async def _list_children_impl(folder_path: str = "", limit: int = 25) -> str:
    try:
        limit = min(max(limit, 1), 200)
        folder = await _get_item_by_path(folder_path)
        if folder.get("folder") is None:
            return f"Error: '{folder_path or '/'}' is not a folder."

        normalized = _normalize_path(folder_path)
        if normalized:
            endpoint = f"/me/drive/items/{quote(folder['id'], safe='')}/children"
        else:
            endpoint = "/me/drive/root/children"

        response = await _graph_request(
            "GET",
            endpoint,
            params={
                "$top": limit,
                "$select": _SELECT_FIELDS,
                "$orderby": "name",
            },
        )
        items = [_format_item(item) for item in response.get("value", [])]
        return _safe_json(
            {
                "folder": _format_item(folder),
                "count": len(items),
                "items": items,
                "next_link": response.get("@odata.nextLink", ""),
            }
        )
    except Exception as e:
        return f"Error listing OneDrive children: {e}"


@function_tool
async def onedrive_list_children(folder_path: str = "", limit: int = 25) -> str:
    """List files and folders in a OneDrive folder path.

    Args:
        folder_path: Folder path relative to drive root (blank means root).
        limit: Maximum number of child items to return (default 25, max 200).
    """
    return await _list_children_impl(folder_path=folder_path, limit=limit)


async def _search_items_impl(query: str, limit: int = 10) -> str:
    try:
        if not query.strip():
            return "Error: search query cannot be empty."

        limit = min(max(limit, 1), 100)
        encoded_query = quote(query.strip(), safe="")
        response = await _graph_request(
            "GET",
            f"/me/drive/root/search(q='{encoded_query}')",
            params={
                "$top": limit,
                "$select": _SELECT_FIELDS,
            },
        )
        items = [_format_item(item) for item in response.get("value", [])]
        return _safe_json(
            {
                "query": query,
                "count": len(items),
                "items": items,
                "next_link": response.get("@odata.nextLink", ""),
            }
        )
    except Exception as e:
        return f"Error searching OneDrive: {e}"


@function_tool
async def onedrive_search_items(query: str, limit: int = 10) -> str:
    """Search OneDrive for files and folders matching a query.

    Args:
        query: Search text matched against filename, metadata, and some file content.
        limit: Maximum number of results to return (default 10, max 100).
    """
    return await _search_items_impl(query=query, limit=limit)


async def _get_item_impl(item_path: str) -> str:
    try:
        item = await _get_item_by_path(item_path)
        return _safe_json(_format_item(item))
    except Exception as e:
        return f"Error getting OneDrive item: {e}"


@function_tool
async def onedrive_get_item(item_path: str) -> str:
    """Get metadata for a OneDrive item by path.

    Args:
        item_path: Item path relative to drive root (use '/' for root).
    """
    return await _get_item_impl(item_path=item_path)


async def _create_folder_impl(
    name: str,
    parent_path: str = "",
    conflict_behavior: str = "rename",
) -> str:
    try:
        folder_name = name.strip()
        if not folder_name:
            return "Error: folder name cannot be empty."
        if conflict_behavior not in {"fail", "rename", "replace"}:
            return "Error: conflict_behavior must be one of fail, rename, or replace."

        parent = await _get_item_by_path(parent_path)
        if parent.get("folder") is None:
            return f"Error: '{parent_path or '/'}' is not a folder."

        normalized_parent = _normalize_path(parent_path)
        if normalized_parent:
            endpoint = f"/me/drive/items/{quote(parent['id'], safe='')}/children"
        else:
            endpoint = "/me/drive/root/children"

        created = await _graph_request(
            "POST",
            endpoint,
            json_body={
                "name": folder_name,
                "folder": {},
                "@microsoft.graph.conflictBehavior": conflict_behavior,
            },
        )
        return _safe_json({"created": _format_item(created), "parent": _format_item(parent)})
    except Exception as e:
        return f"Error creating OneDrive folder: {e}"


@function_tool
async def onedrive_create_folder(
    name: str,
    parent_path: str = "",
    conflict_behavior: str = "rename",
) -> str:
    """Create a folder in OneDrive.

    Args:
        name: New folder name.
        parent_path: Parent folder path relative to drive root (blank means root).
        conflict_behavior: One of fail, rename, or replace.
    """
    return await _create_folder_impl(
        name=name,
        parent_path=parent_path,
        conflict_behavior=conflict_behavior,
    )


async def _ensure_folder_path_impl(folder_path: str) -> str:
    try:
        normalized = _normalize_path(folder_path)
        current_item = await _get_root_item()
        if not normalized:
            return _safe_json({"folder": _format_item(current_item), "created": []})

        created: list[dict[str, Any]] = []
        current_path = ""
        for segment in normalized.split("/"):
            current_path = f"{current_path}/{segment}" if current_path else segment
            try:
                current_item = await _get_item_by_path(current_path)
                if current_item.get("folder") is None:
                    return f"Error: '{current_path}' already exists and is not a folder."
            except OneDriveAPIError as e:
                if e.status_code != 404:
                    raise
                endpoint = (
                    f"/me/drive/items/{quote(current_item['id'], safe='')}/children"
                    if current_item.get("id")
                    else "/me/drive/root/children"
                )
                current_item = await _graph_request(
                    "POST",
                    endpoint,
                    json_body={
                        "name": segment,
                        "folder": {},
                        "@microsoft.graph.conflictBehavior": "fail",
                    },
                )
                created.append(_format_item(current_item))

        return _safe_json(
            {
                "folder": _format_item(current_item),
                "created_count": len(created),
                "created": created,
            }
        )
    except Exception as e:
        return f"Error ensuring OneDrive folder path: {e}"


@function_tool
async def onedrive_ensure_folder_path(folder_path: str) -> str:
    """Ensure a nested OneDrive folder path exists, creating missing segments.

    Args:
        folder_path: Nested folder path relative to drive root, e.g. 'Projects/2026/Receipts'.
    """
    return await _ensure_folder_path_impl(folder_path=folder_path)


async def _rename_item_impl(item_path: str, new_name: str) -> str:
    try:
        updated_name = new_name.strip()
        if not updated_name:
            return "Error: new_name cannot be empty."

        item = await _get_item_by_path(item_path)
        updated = await _graph_request(
            "PATCH",
            f"/me/drive/items/{quote(item['id'], safe='')}",
            json_body={"name": updated_name},
        )
        return _safe_json({"before": _format_item(item), "after": _format_item(updated)})
    except Exception as e:
        return f"Error renaming OneDrive item: {e}"


@function_tool
async def onedrive_rename_item(item_path: str, new_name: str) -> str:
    """Rename a OneDrive file or folder.

    Args:
        item_path: Existing item path relative to drive root.
        new_name: Replacement item name.
    """
    return await _rename_item_impl(item_path=item_path, new_name=new_name)


async def _move_item_impl(
    item_path: str,
    destination_folder_path: str = "",
    new_name: str = "",
) -> str:
    try:
        item = await _get_item_by_path(item_path)
        destination = await _get_item_by_path(destination_folder_path)
        if destination.get("folder") is None:
            return f"Error: '{destination_folder_path or '/'}' is not a folder."

        body: dict[str, Any] = {
            "parentReference": {"id": destination["id"]},
        }
        if new_name.strip():
            body["name"] = new_name.strip()

        updated = await _graph_request(
            "PATCH",
            f"/me/drive/items/{quote(item['id'], safe='')}",
            json_body=body,
        )
        return _safe_json(
            {
                "before": _format_item(item),
                "destination": _format_item(destination),
                "after": _format_item(updated),
            }
        )
    except Exception as e:
        return f"Error moving OneDrive item: {e}"


@function_tool
async def onedrive_move_item(
    item_path: str,
    destination_folder_path: str = "",
    new_name: str = "",
) -> str:
    """Move a OneDrive file or folder to another folder, optionally renaming it.

    Args:
        item_path: Existing item path relative to drive root.
        destination_folder_path: Destination folder path relative to drive root (blank means root).
        new_name: Optional new name to apply during the move.
    """
    return await _move_item_impl(
        item_path=item_path,
        destination_folder_path=destination_folder_path,
        new_name=new_name,
    )


tool_functions = [
    onedrive_list_children,
    onedrive_search_items,
    onedrive_get_item,
    onedrive_create_folder,
    onedrive_ensure_folder_path,
    onedrive_rename_item,
    onedrive_move_item,
]
