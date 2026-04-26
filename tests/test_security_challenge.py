"""Tests for the security challenge gate."""

from __future__ import annotations

import json
import sys
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

# Ensure redis.asyncio is mockable when not installed locally.
# Track what we add so we can clean up and avoid poisoning other tests.
_INJECTED_MOCKS: list[str] = []
for _mod in ("redis", "redis.asyncio"):
    if _mod not in sys.modules:
        _INJECTED_MOCKS.append(_mod)
        sys.modules[_mod] = MagicMock()

from src.security.challenge import (
    _hash_answer,
    has_pending_challenge,
    hash_pin,
    hash_security_answer,
    issue_challenge,
    verify_challenge,
)


@pytest.fixture(autouse=True, scope="module")
def _cleanup_mocked_modules():
    """Remove mocked modules after this test module completes."""
    yield
    for mod_name in _INJECTED_MOCKS:
        sys.modules.pop(mod_name, None)
    stale = [k for k in sys.modules if k.startswith("src.security.challenge")]
    for k in stale:
        sys.modules.pop(k, None)


class TestHashHelpers:
    """Test hashing utilities."""

    def test_hash_pin_deterministic(self):
        assert hash_pin("1234") == hash_pin("1234")

    def test_hash_pin_strips_whitespace(self):
        assert hash_pin("  1234  ") == hash_pin("1234")

    def test_hash_pin_case_insensitive(self):
        assert hash_security_answer("MyPet") == hash_security_answer("mypet")

    def test_different_pins_different_hashes(self):
        assert hash_pin("1234") != hash_pin("5678")


class TestIssueChallenge:
    """Test issue_challenge()."""

    @pytest.mark.asyncio
    async def test_raises_if_no_config(self):
        with pytest.raises(ValueError, match="not configured"):
            await issue_challenge(user_id=1, pin_hash=None, security_qa=None)

    @pytest.mark.asyncio
    async def test_pin_challenge(self):
        mock_redis = AsyncMock()
        mock_redis.set = AsyncMock()
        mock_redis.aclose = AsyncMock()

        with patch("src.security.challenge.get_redis", new=AsyncMock(return_value=mock_redis)):
            result = await issue_challenge(
                user_id=1,
                pin_hash=hash_pin("1234"),
                ttl=30,
            )

        assert result["type"] == "pin"
        assert "PIN" in result["prompt"]
        assert result["expires_in"] == 30
        mock_redis.set.assert_called_once()

    @pytest.mark.asyncio
    async def test_security_question_challenge(self):
        mock_redis = AsyncMock()
        mock_redis.set = AsyncMock()
        mock_redis.aclose = AsyncMock()

        qa = [{"q": "What is your pet's name?", "a_hash": hash_security_answer("fluffy")}]

        with patch("src.security.challenge.get_redis", new=AsyncMock(return_value=mock_redis)):
            result = await issue_challenge(
                user_id=1,
                security_qa=qa,
                ttl=60,
            )

        assert result["type"] == "security_question"
        assert "pet's name" in result["prompt"]


class TestVerifyChallenge:
    """Test verify_challenge()."""

    @pytest.mark.asyncio
    async def test_correct_pin(self):
        pin = "1234"
        challenge_data = json.dumps({
            "type": "pin",
            "expected_hash": _hash_answer(pin),
        })

        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(return_value=challenge_data)
        mock_redis.delete = AsyncMock()
        mock_redis.aclose = AsyncMock()

        with patch("src.security.challenge.get_redis", new=AsyncMock(return_value=mock_redis)):
            assert await verify_challenge(user_id=1, answer="1234") is True

        mock_redis.delete.assert_called_once()

    @pytest.mark.asyncio
    async def test_wrong_pin(self):
        challenge_data = json.dumps({
            "type": "pin",
            "expected_hash": _hash_answer("1234"),
        })

        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(return_value=challenge_data)
        mock_redis.aclose = AsyncMock()

        with patch("src.security.challenge.get_redis", new=AsyncMock(return_value=mock_redis)):
            assert await verify_challenge(user_id=1, answer="9999") is False

    @pytest.mark.asyncio
    async def test_expired_challenge(self):
        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(return_value=None)
        mock_redis.aclose = AsyncMock()

        with patch("src.security.challenge.get_redis", new=AsyncMock(return_value=mock_redis)):
            assert await verify_challenge(user_id=1, answer="1234") is False

    @pytest.mark.asyncio
    async def test_correct_security_answer(self):
        answer = "fluffy"
        challenge_data = json.dumps({
            "type": "security_question",
            "question": "Pet name?",
            "expected_hash": _hash_answer(answer),
        })

        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(return_value=challenge_data)
        mock_redis.delete = AsyncMock()
        mock_redis.aclose = AsyncMock()

        with patch("src.security.challenge.get_redis", new=AsyncMock(return_value=mock_redis)):
            assert await verify_challenge(user_id=1, answer="Fluffy") is True


class TestHasPendingChallenge:
    """Test has_pending_challenge()."""

    @pytest.mark.asyncio
    async def test_no_pending(self):
        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(return_value=None)
        mock_redis.aclose = AsyncMock()

        with patch("src.security.challenge.get_redis", new=AsyncMock(return_value=mock_redis)):
            assert await has_pending_challenge(user_id=1) is False

    @pytest.mark.asyncio
    async def test_has_pending(self):
        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(return_value='{"type":"pin"}')
        mock_redis.aclose = AsyncMock()

        with patch("src.security.challenge.get_redis", new=AsyncMock(return_value=mock_redis)):
            assert await has_pending_challenge(user_id=1) is True
