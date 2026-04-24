"""Tests for repair engine functions."""

# ruff: noqa: E402

from __future__ import annotations

import sys
import pytest
from unittest.mock import MagicMock, AsyncMock, patch

# Mock redis before importing
sys.modules["redis"] = MagicMock()
sys.modules["redis.asyncio"] = MagicMock()

from src.repair.engine import create_structured_ticket


class TestCreateStructuredTicket:
    """Test create_structured_ticket function."""

    @pytest.mark.asyncio
    async def test_creates_ticket_with_high_confidence(self):
        """Test that high confidence analysis creates debug_analysis_ready ticket."""
        debug_analysis = {
            "error_summary": "Email sending fails",
            "root_cause": "Missing null check",
            "affected_components": ["email_skill"],
            "affected_files": ["src/agents/email_agent.py"],
            "reproduction_steps": ["Call with null subject"],
            "confidence_score": 0.85,
            "severity": "high",
            "complexity": "low",
            "recommended_next_step": "Generate null check patch",
        }

        # Mock database session
        mock_session = AsyncMock()
        mock_session.add = MagicMock()  # synchronous in SQLAlchemy
        mock_user = MagicMock()
        mock_user.id = 1
        mock_user.telegram_id = 12345

        # Mock the session execution
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_user
        mock_session.execute.return_value = mock_result

        with patch("src.db.session.async_session") as mock_async_session:
            mock_async_session.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_async_session.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await create_structured_ticket(
                user_telegram_id=12345,
                debug_analysis=debug_analysis,
                title="Test Ticket",
            )

            # Verify ticket was created with debug_analysis_ready status
            assert result["success"] is True
            assert result["status"] == "debug_analysis_ready"
            assert "Test Ticket" in result["message"]

    @pytest.mark.asyncio
    async def test_creates_ticket_with_low_confidence(self):
        """Test that low confidence analysis creates open ticket (needs more work)."""
        debug_analysis = {
            "error_summary": "Unknown error",
            "root_cause": "Unclear",
            "affected_components": [],
            "affected_files": [],
            "reproduction_steps": [],
            "confidence_score": 0.5,  # Below 0.7 threshold
            "severity": "medium",
            "complexity": "medium",
            "recommended_next_step": "Needs more investigation",
        }

        # Mock database session
        mock_session = AsyncMock()
        mock_session.add = MagicMock()  # synchronous in SQLAlchemy
        mock_user = MagicMock()
        mock_user.id = 1
        mock_user.telegram_id = 12345

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_user
        mock_session.execute.return_value = mock_result

        with patch("src.db.session.async_session") as mock_async_session:
            mock_async_session.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_async_session.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await create_structured_ticket(
                user_telegram_id=12345,
                debug_analysis=debug_analysis,
            )

            # Verify ticket was created with open status (needs more investigation)
            assert result["success"] is True
            assert result["status"] == "open"

    @pytest.mark.asyncio
    async def test_auto_generates_title(self):
        """Test that title is auto-generated from analysis if not provided."""
        debug_analysis = {
            "error_summary": "Email sending fails with null pointer",
            "root_cause": "Missing null check",
            "affected_components": ["email_skill"],
            "affected_files": ["src/agents/email_agent.py"],
            "confidence_score": 0.8,
            "severity": "high",
            "complexity": "low",
        }

        mock_session = AsyncMock()
        mock_session.add = MagicMock()  # synchronous in SQLAlchemy
        mock_user = MagicMock()
        mock_user.id = 1

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_user
        mock_session.execute.return_value = mock_result

        with patch("src.db.session.async_session") as mock_async_session:
            mock_async_session.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_async_session.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await create_structured_ticket(
                user_telegram_id=12345,
                debug_analysis=debug_analysis,
                # No title provided - should auto-generate
            )

            # Verify title was auto-generated from component and error summary
            assert result["success"] is True
            assert "[email_skill]" in result["title"]
            assert "Email sending fails" in result["title"]

    @pytest.mark.asyncio
    async def test_maps_severity_to_priority(self):
        """Test that severity maps correctly to priority."""
        test_cases = [
            ("low", "low"),
            ("medium", "medium"),
            ("high", "high"),
            ("critical", "high"),  # Critical maps to high priority
        ]

        for severity, expected_priority in test_cases:
            debug_analysis = {
                "error_summary": f"Test {severity} error",
                "root_cause": "Test",
                "confidence_score": 0.8,
                "severity": severity,
                "complexity": "low",
            }

            mock_session = AsyncMock()
            mock_session.add = MagicMock()  # synchronous in SQLAlchemy
            mock_user = MagicMock()
            mock_user.id = 1

            mock_result = MagicMock()
            mock_result.scalar_one_or_none.return_value = mock_user
            mock_session.execute.return_value = mock_result

            with patch("src.db.session.async_session") as mock_async_session:
                mock_async_session.return_value.__aenter__ = AsyncMock(return_value=mock_session)
                mock_async_session.return_value.__aexit__ = AsyncMock(return_value=False)

                await create_structured_ticket(
                    user_telegram_id=12345,
                    debug_analysis=debug_analysis,
                )

                # Verify ticket was created with correct priority
                ticket = mock_session.add.call_args[0][0]
                assert ticket.priority == expected_priority

    @pytest.mark.asyncio
    async def test_user_not_found_error(self):
        """Test error handling when user is not found."""
        debug_analysis = {
            "error_summary": "Test error",
            "confidence_score": 0.8,
        }

        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None  # User not found
        mock_session.execute.return_value = mock_result

        with patch("src.db.session.async_session") as mock_async_session:
            mock_async_session.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_async_session.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await create_structured_ticket(
                user_telegram_id=99999,  # Non-existent user
                debug_analysis=debug_analysis,
            )

            assert result["success"] is False
            assert result["ticket_id"] is None
            assert "not found" in result["message"]
