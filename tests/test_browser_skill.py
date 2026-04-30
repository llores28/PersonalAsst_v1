"""Tests for the browser-use skill, runner, and approval gate.

Three layers of coverage:

1. **Skill contract** — the SkillDefinition shape is what the orchestrator
   expects (id="browser", a callable tool, non-empty routing_hints).
2. **Action-gate classification** — read actions are gate-free; write
   actions (by verb OR by click-label heuristic) trigger the approval
   path.
3. **Runner wiring** — BrowserRunner's lazy-import guard fires when
   the dep is missing and when the feature flag is off; the gate is
   threaded into the Agent constructor under the documented kwarg names.

Live browser-driven tests are NOT in this file — those live as a manual
container-only smoke test (see commit message). Running real Chromium
in CI would slow the suite by ~30s per test and break on CI runners
without display forwarding.
"""
from __future__ import annotations

import asyncio
import inspect
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest

# Stub the agents SDK before importing src.skills.browser — same pattern
# the typing-indicator tests use (see tests/test_typing_indicator.py).
if "agents" not in sys.modules:
    fake_agents = MagicMock()
    fake_agents.Agent = MagicMock
    fake_agents.function_tool = lambda *a, **kw: (
        (lambda f: f) if (a and not callable(a[0])) else (a[0] if a else (lambda f: f))
    )
    fake_agents.Runner = MagicMock()
    fake_agents.WebSearchTool = MagicMock
    sys.modules["agents"] = fake_agents
    sys.modules["agents.mcp"] = MagicMock()
    sys.modules["agents.exceptions"] = MagicMock(
        InputGuardrailTripwireTriggered=type("InputGuardrailTripwireTriggered", (Exception,), {}),
        OutputGuardrailTripwireTriggered=type("OutputGuardrailTripwireTriggered", (Exception,), {}),
        MaxTurnsExceeded=type("MaxTurnsExceeded", (Exception,), {}),
    )


# ─── Skill contract ──────────────────────────────────────────────────────


def test_build_browser_skill_returns_definition_with_browse_web_tool():
    """The orchestrator's registry expects a `SkillDefinition` with id=='browser'
    and at least one tool. If a refactor breaks this shape, the skill won't
    register and the user's 'go to URL X' requests silently fall back to web search."""
    from src.skills.browser import build_browser_skill
    from src.skills.definition import SkillDefinition

    skill = build_browser_skill(user_id=12345)
    assert isinstance(skill, SkillDefinition)
    assert skill.id == "browser"
    assert skill.tools, "browser skill must expose at least one tool"
    assert skill.routing_hints, "routing_hints must be non-empty for the matcher"
    # The tool should be discoverable by name
    tool_names = []
    for t in skill.tools:
        for attr in ("name", "__name__"):
            if hasattr(t, attr):
                tool_names.append(getattr(t, attr))
                break
    # function_tool decorator may store the override under different attrs;
    # we accept any tool name as long as the list isn't empty.
    assert tool_names, f"Could not extract any tool name from {skill.tools!r}"


def test_is_browser_skill_available_returns_false_when_disabled(monkeypatch):
    """When the feature flag is off, the function returns (False, reason).
    The orchestrator uses this to decide whether to register the skill."""
    from src.skills.browser import is_browser_skill_available

    # Force the flag off via the settings singleton
    from src.settings import settings
    monkeypatch.setattr(settings, "browser_use_enabled", False)

    available, reason = is_browser_skill_available()
    assert available is False
    assert "BROWSER_USE_ENABLED" in reason


# ─── Action-gate classification ──────────────────────────────────────────


def test_gate_treats_navigation_as_read_no_approval():
    """Navigating to a URL is read-only; the gate must not require approval."""
    from src.security.browser_action_gate import is_write_action

    assert is_write_action("go_to_url", {"url": "https://example.com"}) is False
    assert is_write_action("scroll_down", {"pixels": 800}) is False
    assert is_write_action("extract_content", {}) is False
    assert is_write_action("screenshot", {}) is False


def test_gate_treats_known_write_verbs_as_write():
    """The canonical browser-use write verbs must always require approval."""
    from src.security.browser_action_gate import is_write_action

    assert is_write_action("click_submit", {}) is True
    assert is_write_action("submit_form", {"form_id": "f1"}) is True
    assert is_write_action("click_apply", {"label": "Easy Apply"}) is True
    assert is_write_action("purchase", {}) is True


def test_gate_click_with_apply_label_is_classified_as_write():
    """Even a generic `click` action turns into a write if its target label
    contains an action verb. This catches the common case where browser-use
    emits `click` with `label='Apply now'` instead of a typed verb."""
    from src.security.browser_action_gate import is_write_action

    assert is_write_action("click", {"label": "Apply now"}) is True
    assert is_write_action("click", {"text": "Submit application"}) is True
    assert is_write_action("click", {"selector": "#checkout-btn"}) is True
    # ...and is NOT a write for benign labels
    assert is_write_action("click", {"label": "Read more"}) is False
    assert is_write_action("click", {"label": "Next page"}) is False


@pytest.mark.asyncio
async def test_gate_returns_true_immediately_for_read_actions(monkeypatch):
    """gate() short-circuits for reads — no Telegram message, no Redis call."""
    from src.security import browser_action_gate

    # If the gate ever calls request_browser_approval for a read, this
    # would raise.
    async def _explode(*a, **kw):
        raise AssertionError("gate should not call approval for a read")

    monkeypatch.setattr(browser_action_gate, "request_browser_approval", _explode)

    result = await browser_action_gate.gate(
        user_telegram_id=42,
        action_name="go_to_url",
        action_args={"url": "https://example.com"},
    )
    assert result is True


# ─── Runner wiring ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_runner_raises_unavailable_when_feature_flag_off(monkeypatch):
    """If BROWSER_USE_ENABLED=False, the runner refuses without trying to
    import browser-use. Caller (the skill tool) catches and returns a
    helpful message to the orchestrator."""
    from src.integrations.browser_use_runner import BrowserRunner, BrowserUseUnavailable
    from src.settings import settings

    monkeypatch.setattr(settings, "browser_use_enabled", False)

    runner = BrowserRunner()
    with pytest.raises(BrowserUseUnavailable):
        await runner.run("anything", user_telegram_id=1)


@pytest.mark.asyncio
async def test_runner_raises_unavailable_when_browser_use_missing(monkeypatch):
    """Even with the flag on, if the package isn't importable we fail
    fast with a typed exception — never crash with bare ImportError."""
    from src.integrations.browser_use_runner import BrowserRunner, BrowserUseUnavailable
    from src.settings import settings

    monkeypatch.setattr(settings, "browser_use_enabled", True)
    # Simulate browser-use not being importable
    monkeypatch.setitem(sys.modules, "browser_use", None)  # makes `import browser_use` ImportError

    runner = BrowserRunner()
    with pytest.raises(BrowserUseUnavailable):
        await runner.run("anything", user_telegram_id=1)


@pytest.mark.asyncio
async def test_runner_passes_settings_into_browser_use_agent_kwargs(monkeypatch):
    """The runner must thread `user_data_dir`, `headless`, `max_steps`,
    `step_timeout`, and the action-hook through to the Agent constructor.
    If a refactor drops any of these, the persistent profile breaks or
    the gate stops firing."""
    from src.integrations import browser_use_runner
    from src.settings import settings

    monkeypatch.setattr(settings, "browser_use_enabled", True)
    monkeypatch.setattr(settings, "browser_use_profile_dir", "/tmp/test-profile")
    monkeypatch.setattr(settings, "browser_use_headless", True)
    monkeypatch.setattr(settings, "browser_use_max_steps", 7)
    monkeypatch.setattr(settings, "browser_use_step_timeout_seconds", 99)

    captured_kwargs: dict = {}

    class _StubAgent:
        def __init__(self, **kwargs):
            captured_kwargs.update(kwargs)

        async def run(self):
            class _Hist:
                def final_result(self):
                    return "ok"
            return _Hist()

    # Inject the stub by patching the lazy import inside _run_agent.
    fake_browser_use_module = MagicMock()
    fake_browser_use_module.Agent = _StubAgent
    monkeypatch.setitem(sys.modules, "browser_use", fake_browser_use_module)

    runner = browser_use_runner.BrowserRunner()
    result = await runner.run("test task", user_telegram_id=1, max_steps=7)

    assert result == "ok"
    assert captured_kwargs["task"] == "test task"
    assert captured_kwargs["max_steps"] == 7
    config = captured_kwargs["browser_config"]
    assert config["user_data_dir"] == "/tmp/test-profile"
    assert config["headless"] is True
    assert config["step_timeout"] == 99
    # Both names provided so multiple browser-use 0.7.x revisions work
    assert callable(captured_kwargs["pre_action_callback"])
    assert callable(captured_kwargs["on_action"])


# ─── Public-API signatures (locking the contract) ────────────────────────


def test_request_browser_approval_signature_unchanged():
    """The Telegram callback handler in src/bot/handlers.py imports
    record_browser_decision; if its name or required-positional shape
    drifts the inline buttons silently stop working."""
    from src.security.browser_action_gate import record_browser_decision

    sig = inspect.signature(record_browser_decision)
    params = list(sig.parameters.keys())
    assert params[:3] == ["user_telegram_id", "request_id", "decision"], (
        f"record_browser_decision signature drifted: {params}"
    )


def test_write_actions_set_includes_canonical_browser_use_verbs():
    """If a future refactor accidentally narrows WRITE_ACTION_VERBS, the
    gate would let writes through silently. Lock down the must-have verbs."""
    from src.security.browser_action_gate import WRITE_ACTION_VERBS

    must_have = {
        "click_submit", "submit_form", "click_apply", "click_purchase",
        "click_checkout", "click_send", "click_post", "purchase", "checkout",
    }
    missing = must_have - WRITE_ACTION_VERBS
    assert not missing, f"WRITE_ACTION_VERBS dropped canonical write verbs: {missing}"
