"""Tests for startup behavior in main module."""

from unittest.mock import patch

import pytest


class TestRunMigrations:
    @pytest.mark.asyncio
    async def test_run_migrations_skips_when_disabled(self) -> None:
        from src import main

        with (
            patch.object(main.settings, "startup_migrations_enabled", False),
            patch("src.main.logger.info") as mock_info,
        ):
            await main.run_migrations()

        logged_messages = [call.args[0] for call in mock_info.call_args_list if call.args]
        assert any("Skipping startup migrations" in message for message in logged_messages)

    @pytest.mark.asyncio
    async def test_run_migrations_upgrades_head_when_enabled(self) -> None:
        from src import main

        with (
            patch.object(main.settings, "startup_migrations_enabled", True),
            patch("alembic.config.Config", return_value="fake_cfg") as mock_config,
            patch("alembic.command.upgrade") as mock_upgrade,
        ):
            await main.run_migrations()

        mock_config.assert_called_once_with("alembic.ini")
        mock_upgrade.assert_called_once_with("fake_cfg", "head")
