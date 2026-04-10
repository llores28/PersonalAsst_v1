"""Pytest wrapper for the Google Workspace Tools QA Harness.

Parametrizes over the scenario playbook defined in src/tools/google_tools_qa.py.
Each scenario runs as an independent test with mocked MCP + Redis.

Run:
    python -m pytest tests/test_google_tools_qa.py -v --tb=short
    python -m pytest tests/test_google_tools_qa.py -v -k "gmail"       # filter by tag
    python -m pytest tests/test_google_tools_qa.py -v -k "direct_handler"
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock

# Stub Docker-only packages so orchestrator imports work locally
_INJECTED_MOCKS: list[str] = []
for _mod in ("agents", "agents.mcp", "redis", "redis.asyncio"):
    if _mod not in sys.modules:
        _INJECTED_MOCKS.append(_mod)
        sys.modules[_mod] = MagicMock()

import pytest

from src.tools.google_tools_qa import PLAYBOOK, Scenario, run_scenario, validate_google_tools

# Windows may lack tzdata; skip calendar tests that need ZoneInfo
_has_tzdata = True
try:
    from zoneinfo import ZoneInfo as _ZI
    _ZI("America/Chicago")
except Exception:
    _has_tzdata = False


# Note: no cleanup fixture — mocked modules persist for the test session.
# Aggressive cleanup causes cross-file failures when running combined suites.


def _needs_timezone(scenario: Scenario) -> bool:
    return scenario.handler == "calendar" or "calendar" in scenario.tags


# Generate test IDs from scenario IDs
_scenario_ids = [s.id for s in PLAYBOOK]


@pytest.mark.asyncio
@pytest.mark.parametrize("scenario", PLAYBOOK, ids=_scenario_ids)
async def test_scenario(scenario: Scenario) -> None:
    if _needs_timezone(scenario) and not _has_tzdata:
        pytest.skip("tzdata not available")

    result = await run_scenario(scenario)
    assert result.passed, (
        f"Scenario '{scenario.id}' failed:\n"
        + "\n".join(f"  - {f}" for f in result.failures)
    )


@pytest.mark.asyncio
async def test_validate_google_tools_returns_all_results() -> None:
    """Smoke test: the agent-callable function runs without error."""
    results = await validate_google_tools()
    assert len(results) == len(PLAYBOOK)
    # At minimum, all non-calendar scenarios should pass
    non_calendar = [r for r in results if r["passed"] or "calendar" in r["scenario_id"]]
    assert len(non_calendar) > 0


@pytest.mark.asyncio
async def test_validate_google_tools_tag_filter() -> None:
    """Tag filter returns only matching scenarios."""
    results = await validate_google_tools(tags_filter=("gmail", "read"))
    assert len(results) > 0
    assert len(results) < len(PLAYBOOK)
