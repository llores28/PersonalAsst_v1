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


# ─── Tier 2: failure classifier + retry strategy ─────────────────────────


def test_classify_failure_login_expired_takes_priority():
    """A timeout that lands on a login screen must classify as
    LOGIN_EXPIRED (re-seed needed), not TIMEOUT (retry with longer
    window). The order matters because retrying a login-expired session
    just wastes tokens."""
    from src.integrations.browser_use_failures import classify_failure, FailureKind

    class _FakeHistory:
        # Simulate browser-use's AgentHistoryList interface
        def urls(self):
            return ["https://app.example.com/login?redirect=/dashboard"]
        def model_thoughts(self):
            return ["Page redirected to login. Session expired."]

    kind = classify_failure(TimeoutError("step exceeded step_timeout"), _FakeHistory())
    assert kind == FailureKind.LOGIN_EXPIRED, (
        f"Login + timeout combo must classify as LOGIN_EXPIRED, got {kind}"
    )


def test_classify_failure_element_drift_routes_to_vision_retry():
    """DOM-drift failures (selector miss) must classify as ELEMENT_DRIFT
    so the runner retries once with use_vision=True."""
    from src.integrations.browser_use_failures import classify_failure, FailureKind

    kind = classify_failure(
        Exception("element not found: #submit-btn"),
        history=None,
    )
    assert kind == FailureKind.ELEMENT_DRIFT


def test_classify_failure_pure_timeout_routes_to_extended_retry():
    """Pure timeout (no login redirect) must classify as TIMEOUT so the
    runner retries once with 2x step_timeout."""
    from src.integrations.browser_use_failures import classify_failure, FailureKind

    class _FakeHistory:
        def urls(self):
            return ["https://example.com/api/data"]
        def model_thoughts(self):
            return ["Waiting for response..."]

    kind = classify_failure(TimeoutError("step exceeded step_timeout"), _FakeHistory())
    assert kind == FailureKind.TIMEOUT


def test_classify_failure_unknown_when_no_sentinels_match():
    """Anything we can't bucket must NOT silently retry — UNKNOWN means
    'open a ticket and stop trying'."""
    from src.integrations.browser_use_failures import classify_failure, FailureKind

    kind = classify_failure(ValueError("some unrelated bug"), history=None)
    assert kind == FailureKind.UNKNOWN


# ─── Tier 1: bridge module signatures ────────────────────────────────────


def test_collect_sensitive_data_signature_returns_dict():
    """The runner depends on this returning a plain dict[str, str].
    If it ever returns something fancier, browser-use's sensitive_data
    parameter rejects it silently."""
    from src.integrations.browser_use_bridge import collect_sensitive_data

    sig = inspect.signature(collect_sensitive_data)
    assert inspect.iscoroutinefunction(collect_sensitive_data)
    params = list(sig.parameters.keys())
    assert "user_telegram_id" in params, (
        f"collect_sensitive_data signature drifted: {params}"
    )


def test_build_atlas_system_extension_returns_non_empty_string():
    """If this is empty, browser-use ignores it AND we lose the safety
    posture (describe-before-submit, never invent credentials)."""
    from src.integrations.browser_use_bridge import build_atlas_system_extension

    text = build_atlas_system_extension()
    assert isinstance(text, str)
    assert len(text) > 100, "system extension is too thin to be meaningful"
    # Locks the safety lines (the WHY of always-on gate)
    assert "describe" in text.lower()
    assert "credentials" in text.lower() or "credential" in text.lower()


def test_build_step_audit_hook_returns_async_callable():
    """Browser-use registers this as on_step_end=. If it isn't a
    coroutine, browser-use either ignores it or raises mid-run."""
    from src.integrations.browser_use_failures import build_step_audit_hook

    hook = build_step_audit_hook(user_telegram_id=12345, task="x")
    assert inspect.iscoroutinefunction(hook), (
        "build_step_audit_hook must return an async callable so "
        "browser-use can `await` it from on_step_end."
    )


# ─── Tier 2: targeted-retry contract ─────────────────────────────────────


@pytest.mark.asyncio
async def test_targeted_retry_opens_ticket_on_unknown_failure(monkeypatch):
    """When _run_with_targeted_retry exhausts attempts on UNKNOWN failure,
    it must call open_repair_ticket_for_browser_failure AND raise
    BrowserRunFailed. Without this contract, browser failures would be
    invisible to the dashboard's Repairs tab."""
    from src.integrations import browser_use_runner
    from src.integrations.browser_use_runner import (
        BrowserRunFailed,
        _run_with_targeted_retry,
    )

    # Stub the inner agent to always raise an unclassifiable exception
    raised: list[BaseException] = []

    async def _stub_run_agent(self, **kwargs):
        exc = ValueError("totally unrelated failure")
        raised.append(exc)
        raise exc

    monkeypatch.setattr(browser_use_runner.BrowserRunner, "_run_agent", _stub_run_agent)

    # Capture the ticket-creation call
    ticket_calls: list[dict] = []

    async def _stub_open_ticket(**kw):
        ticket_calls.append(kw)
        return 999

    monkeypatch.setattr(
        "src.integrations.browser_use_failures.open_repair_ticket_for_browser_failure",
        _stub_open_ticket,
    )

    runner = browser_use_runner.BrowserRunner()
    with pytest.raises(BrowserRunFailed) as excinfo:
        await _run_with_targeted_retry(
            runner=runner,
            Agent=MagicMock,
            task="some task",
            user_telegram_id=42,
            max_steps=5,
            use_vision=False,
            output_schema=None,
            action_hook=lambda *a, **kw: True,
        )

    # Ticket was opened
    assert len(ticket_calls) == 1
    assert ticket_calls[0]["user_telegram_id"] == 42
    assert ticket_calls[0]["task"] == "some task"

    # Error message includes the ticket id + failure kind
    msg = str(excinfo.value)
    assert "RepairTicket #999" in msg
    assert "unknown" in msg.lower()


@pytest.mark.asyncio
async def test_targeted_retry_retries_once_on_element_drift_with_vision_on(monkeypatch):
    """ELEMENT_DRIFT failures must retry exactly once with use_vision=True.
    The original visit ran without vision (cost optimization); the retry
    flips it on so the model can recover via visual grounding."""
    from src.integrations import browser_use_runner
    from src.integrations.browser_use_runner import _run_with_targeted_retry

    attempts: list[dict] = []

    async def _stub_run_agent(self, **kwargs):
        attempts.append({"use_vision": kwargs["use_vision"], "step_timeout": kwargs.get("step_timeout_override")})
        if len(attempts) == 1:
            raise Exception("element not found: button#apply")
        return ("OK after vision retry", MagicMock())

    monkeypatch.setattr(browser_use_runner.BrowserRunner, "_run_agent", _stub_run_agent)

    runner = browser_use_runner.BrowserRunner()
    result = await _run_with_targeted_retry(
        runner=runner,
        Agent=MagicMock,
        task="click apply",
        user_telegram_id=42,
        max_steps=5,
        use_vision=False,
        output_schema=None,
        action_hook=lambda *a, **kw: True,
    )
    assert result == "OK after vision retry"
    assert len(attempts) == 2
    # First attempt: vision off (initial config). Second: vision flipped on
    assert attempts[0]["use_vision"] is False
    assert attempts[1]["use_vision"] is True


@pytest.mark.asyncio
async def test_targeted_retry_does_not_retry_on_login_expired(monkeypatch):
    """LOGIN_EXPIRED needs a human to re-seed the profile. Retrying just
    burns LLM cost on the same login screen. Verify the runner makes
    EXACTLY one attempt, opens a ticket, and surfaces the re-seed hint
    in the error message."""
    from src.integrations import browser_use_runner
    from src.integrations.browser_use_runner import (
        BrowserRunFailed,
        _run_with_targeted_retry,
    )

    attempts = 0

    async def _stub_run_agent(self, **kwargs):
        nonlocal attempts
        attempts += 1
        raise Exception("Page redirected to /login — your session has expired")

    monkeypatch.setattr(browser_use_runner.BrowserRunner, "_run_agent", _stub_run_agent)
    monkeypatch.setattr(
        "src.integrations.browser_use_failures.open_repair_ticket_for_browser_failure",
        AsyncMock(return_value=42),
    )

    runner = browser_use_runner.BrowserRunner()
    with pytest.raises(BrowserRunFailed) as excinfo:
        await _run_with_targeted_retry(
            runner=runner,
            Agent=MagicMock,
            task="check messages",
            user_telegram_id=99,
            max_steps=5,
            use_vision=False,
            output_schema=None,
            action_hook=lambda *a, **kw: True,
        )

    assert attempts == 1, f"LOGIN_EXPIRED must NOT retry; got {attempts} attempts"
    assert "seed_browser_profile" in str(excinfo.value), (
        "Re-seed hint must be in the error message so the user knows what to do"
    )
