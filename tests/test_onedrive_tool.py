"""Tests for the OneDrive function-type tool and routing copy."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest


class TestOneDriveManifest:
    """Validate the shipped OneDrive manifest."""

    def test_onedrive_manifest_loads(self) -> None:
        from src.tools.manifest import ToolManifest

        manifest_path = Path("src/tools/plugins/onedrive/manifest.json")
        manifest = ToolManifest.model_validate_json(manifest_path.read_text())

        assert manifest.name == "onedrive"
        assert manifest.type == "function"
        assert manifest.requires_network is True
        assert "onedrive_access_token" in manifest.credentials
        assert "onedrive_refresh_token" in manifest.credentials
        assert "microsoft_client_id" in manifest.credentials


class TestOneDriveToolWrappers:
    """Exercise the OneDrive wrapper module without hitting the network."""

    def test_tool_functions_list_exists(self) -> None:
        from src.tools.plugins.onedrive.tool import tool_functions

        assert isinstance(tool_functions, list)
        assert len(tool_functions) == 7
        names = [getattr(f, "name", None) for f in tool_functions]
        assert "onedrive_search_items" in names
        assert "onedrive_list_children" in names
        assert "onedrive_move_item" in names

    @pytest.mark.asyncio
    async def test_search_items_success(self) -> None:
        import src.tools.plugins.onedrive.tool as od

        mock_response = {
            "value": [
                {
                    "id": "item-123",
                    "name": "Taxes.pdf",
                    "size": 2048,
                    "webUrl": "https://example.com/file",
                    "lastModifiedDateTime": "2026-03-22T10:00:00Z",
                    "createdDateTime": "2026-03-21T10:00:00Z",
                    "parentReference": {"path": "/drive/root:/Finances", "id": "parent-1"},
                    "file": {"mimeType": "application/pdf"},
                }
            ],
            "@odata.nextLink": "",
        }

        with patch.object(od, "_graph_request", new=AsyncMock(return_value=mock_response)):
            result = await od._search_items_impl("tax", limit=5)

        data = json.loads(result)
        assert data["query"] == "tax"
        assert data["count"] == 1
        assert data["items"][0]["name"] == "Taxes.pdf"
        assert data["items"][0]["path"] == "/Finances/Taxes.pdf"

    @pytest.mark.asyncio
    async def test_list_children_rejects_file_parent(self) -> None:
        import src.tools.plugins.onedrive.tool as od

        with patch.object(
            od,
            "_get_item_by_path",
            new=AsyncMock(
                return_value={
                    "id": "file-1",
                    "name": "readme.txt",
                    "parentReference": {"path": "/drive/root:/Docs"},
                    "file": {"mimeType": "text/plain"},
                }
            ),
        ):
            result = await od._list_children_impl("Docs/readme.txt")

        assert "is not a folder" in result

    @pytest.mark.asyncio
    async def test_move_item_success(self) -> None:
        import src.tools.plugins.onedrive.tool as od

        item = {
            "id": "item-1",
            "name": "receipt.pdf",
            "parentReference": {"path": "/drive/root:/Inbox", "id": "parent-inbox"},
            "file": {"mimeType": "application/pdf"},
        }
        destination = {
            "id": "folder-2",
            "name": "Receipts",
            "parentReference": {"path": "/drive/root:/Finance", "id": "parent-finance"},
            "folder": {"childCount": 3},
        }
        updated = {
            "id": "item-1",
            "name": "receipt.pdf",
            "parentReference": {"path": "/drive/root:/Finance/Receipts", "id": "folder-2"},
            "file": {"mimeType": "application/pdf"},
        }

        with (
            patch.object(od, "_get_item_by_path", new=AsyncMock(side_effect=[item, destination])),
            patch.object(od, "_graph_request", new=AsyncMock(return_value=updated)),
        ):
            result = await od._move_item_impl(
                item_path="Inbox/receipt.pdf",
                destination_folder_path="Finance/Receipts",
            )

        data = json.loads(result)
        assert data["before"]["path"] == "/Inbox/receipt.pdf"
        assert data["destination"]["path"] == "/Finance/Receipts"
        assert data["after"]["path"] == "/Finance/Receipts/receipt.pdf"

    @pytest.mark.asyncio
    async def test_get_access_token_prefers_seeded_access_token(self) -> None:
        import src.tools.plugins.onedrive.tool as od

        with patch("src.tools.credentials.get_credentials", new=AsyncMock(return_value={"onedrive_access_token": "token-123"})):
            token = await od._get_access_token()

        assert token == "token-123"

    @pytest.mark.asyncio
    async def test_get_access_token_missing_credentials(self) -> None:
        import src.tools.plugins.onedrive.tool as od

        with patch("src.tools.credentials.get_credentials", new=AsyncMock(return_value={})):
            with pytest.raises(RuntimeError, match="OneDrive credentials not configured"):
                await od._get_access_token()


class TestOneDriveRoutingCopy:
    """Keep the prompt contract aligned with the installed OneDrive tools."""

    def test_persona_mode_mentions_onedrive_tools(self) -> None:
        from src.agents.persona_mode import build_persona_mode_addendum

        text = build_persona_mode_addendum("conversation")
        assert "ONEDRIVE TOOL ROUTING" in text
        assert "Do NOT claim you lack OneDrive access" in text
