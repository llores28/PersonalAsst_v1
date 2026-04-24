"""Tests for repair planning + approval/execution flow."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest


class TestRepairRepoHelpers:
    """Read-only repo helper coverage."""

    @pytest.mark.asyncio
    async def test_search_repo_finds_matches(self, tmp_path: Path) -> None:
        from src.repair import engine

        target = tmp_path / "src" / "sample.py"
        target.parent.mkdir(parents=True)
        target.write_text("def hello():\n    return 'world'\n")

        with patch.object(engine, "REPO_ROOT", tmp_path):
            result = await engine.search_repo("return", base_path="src", limit=5)

        assert "sample.py" in result
        assert '"count": 1' in result

    @pytest.mark.asyncio
    async def test_read_repo_file_returns_numbered_lines(self, tmp_path: Path) -> None:
        from src.repair import engine

        target = tmp_path / "src" / "sample.py"
        target.parent.mkdir(parents=True)
        target.write_text("line1\nline2\nline3\n")

        with patch.object(engine, "REPO_ROOT", tmp_path):
            result = await engine.read_repo_file("src/sample.py", start_line=2, end_line=3)

        assert "2: line2" in result
        assert "3: line3" in result


class TestRepairPlanStorage:
    """Repair plan persistence and validation."""

    @pytest.mark.asyncio
    async def test_store_repair_plan_records_payload(self, tmp_path: Path) -> None:
        from src.repair import engine

        target = tmp_path / "src" / "feature.py"
        target.parent.mkdir(parents=True)
        target.write_text("old = True\n")

        diff = """--- a/src/feature.py
+++ b/src/feature.py
@@ -1 +1 @@
-old = True
+old = False
"""

        with (
            patch.object(engine, "REPO_ROOT", tmp_path),
            patch.object(engine, "store_pending_repair", new=AsyncMock()) as mock_store,
        ):
            payload = await engine.store_repair_plan(
                123,
                file_path="src/feature.py",
                description="Flip the feature flag default.",
                diff=diff,
                verification_commands="python -m pytest tests/test_feature.py -q",
            )

        assert payload["file_path"] == "src/feature.py"
        assert payload["verification_commands"] == ["python -m pytest tests/test_feature.py -q"]
        mock_store.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_store_repair_plan_rejects_mismatched_patch_path(self, tmp_path: Path) -> None:
        from src.repair import engine

        target = tmp_path / "src" / "feature.py"
        target.parent.mkdir(parents=True)
        target.write_text("old = True\n")

        diff = """--- a/src/other.py
+++ b/src/other.py
@@ -1 +1 @@
-old = True
+old = False
"""

        with patch.object(engine, "REPO_ROOT", tmp_path):
            with pytest.raises(ValueError, match="is not referenced in the diff"):
                await engine.store_repair_plan(
                    123,
                    file_path="src/feature.py",
                    description="Bad diff",
                    diff=diff,
                )


class TestRepairApprovalFlow:
    """Approval + execution behavior around the stored repair plan."""

    def test_is_repair_approval_request_requires_explicit_repair_language(self) -> None:
        from src.repair import engine

        assert engine.is_repair_approval_request("apply patch") is True
        assert engine.is_repair_approval_request("yes apply it") is True
        assert engine.is_repair_approval_request("yes") is False
        assert engine.is_repair_approval_request("go ahead") is False

    @pytest.mark.asyncio
    async def test_execute_pending_repair_success(self) -> None:
        from src.repair import engine

        payload = {
            "file_path": "src/example.py",
            "affected_files": ["src/example.py"],
            "description": "Apply the fix",
            "diff": "--- a/src/example.py\n+++ b/src/example.py\n@@ -1 +1 @@\n-old\n+new\n",
            "verification_commands": ["python -m pytest tests/test_example.py -q"],
        }

        with (
            patch.object(engine, "get_pending_repair", new=AsyncMock(return_value=payload)),
            patch.object(engine, "clear_pending_repair", new=AsyncMock()) as mock_clear,
            patch.object(engine, "_write_patch_file", new=AsyncMock(return_value="repair.patch")),
            patch.object(engine, "_maybe_trigger_deploy", new=AsyncMock(return_value=None)),
            patch.object(
                engine,
                "_run_command_parts",
                new=AsyncMock(
                    side_effect=[
                        (0, "main\n", ""),       # git rev-parse HEAD
                        (0, "", ""),              # git apply --check
                        (0, "", ""),              # git checkout -b repair/...
                        (0, "", ""),              # git apply
                        (0, "", ""),              # git add
                        (0, "", ""),              # git commit
                        (0, "ok", ""),            # verification command
                        (0, "", ""),              # git checkout main (back to original)
                    ]
                ),
            ),
        ):
            result = await engine.execute_pending_repair(123)

        assert "Patch Verified" in result or "ready" in result.lower()
        assert "src/example.py" in result
        mock_clear.assert_awaited_once_with(123)

    @pytest.mark.asyncio
    async def test_maybe_handle_pending_repair_issues_security_challenge(self) -> None:
        from src.repair import engine

        payload = {"file_path": "src/example.py"}

        with (
            patch.object(engine, "get_pending_repair", new=AsyncMock(return_value=payload)),
            patch.object(engine, "has_pending_challenge", new=AsyncMock(return_value=False)),
            patch.object(engine, "_load_owner_security_config", new=AsyncMock(return_value=("pinhash", None, 60))),
            patch.object(engine, "issue_challenge", new=AsyncMock(return_value={"prompt": "enter pin"})),
        ):
            result = await engine.maybe_handle_pending_repair(123, "apply patch")

        assert result is not None
        assert "enter pin" in result

    @pytest.mark.asyncio
    async def test_maybe_handle_pending_repair_reports_missing_security_setup(self) -> None:
        from src.repair import engine

        payload = {"file_path": "src/example.py"}

        with (
            patch.object(engine, "get_pending_repair", new=AsyncMock(return_value=payload)),
            patch.object(engine, "has_pending_challenge", new=AsyncMock(return_value=False)),
            patch.object(engine, "_load_owner_security_config", new=AsyncMock(return_value=(None, None, 60))),
            patch.object(
                engine,
                "issue_challenge",
                new=AsyncMock(side_effect=ValueError("Configure security first.")),
            ),
        ):
            result = await engine.maybe_handle_pending_repair(123, "apply patch")

        assert result is not None
        assert "Configure security first." in result
        assert "/settings security" in result

    @pytest.mark.asyncio
    async def test_maybe_handle_pending_repair_executes_after_verification(self) -> None:
        from src.repair import engine

        payload = {"file_path": "src/example.py"}

        with (
            patch.object(engine, "get_pending_repair", new=AsyncMock(return_value=payload)),
            patch.object(engine, "has_pending_challenge", new=AsyncMock(return_value=True)),
            patch.object(engine, "verify_challenge", new=AsyncMock(return_value=True)),
            patch.object(engine, "execute_pending_repair", new=AsyncMock(return_value="applied")),
        ):
            result = await engine.maybe_handle_pending_repair(123, "1234")

        assert result == "applied"
