"""Thin async wrapper around the `browser-use` library.

browser-use drives a real Chromium instance via Chrome DevTools Protocol
with an LLM in the loop. This wrapper:

  - Lazy-imports the browser-use package so a missing dep can't crash
    Atlas startup. Module imports succeed; only `BrowserRunner.run()`
    actually requires the dep.
  - Serializes runs through a module-level Semaphore(1). Atlas is
    single-user; running two browser sessions concurrently in a 3GB
    container is asking for OOM. (If the project ever needs parallel
    browsing, bump the semaphore + container memory together.)
  - Wraps `agent.run()` in `asyncio.wait_for` with the wall-clock cap
    from settings so a stuck Chromium can never starve the assistant.
  - Threads the action-gate callback through to browser-use so every
    write action (form submit, click apply, checkout) gets a Telegram
    approval prompt before it fires. See src/security/browser_action_gate.py.

Usage:
    from src.integrations.browser_use_runner import BrowserRunner
    runner = BrowserRunner()
    text = await runner.run("Go to example.com and tell me the headline",
                            user_telegram_id=12345)
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)


# Single concurrency-1 semaphore at module scope. Acquired inside .run()
# so multiple coroutines stack up as await-points rather than racing for
# Chromium. Bumping this requires also bumping the container memory limit.
_BROWSER_SEMAPHORE = asyncio.Semaphore(1)


class BrowserUseUnavailable(RuntimeError):
    """browser-use is not installed or its dependencies are missing."""


class BrowserRunTimeout(RuntimeError):
    """The browser-use run exceeded `browser_use_total_timeout_seconds`."""


class BrowserRunner:
    """Async runner that executes a single browser-use task and returns
    the agent's final text result.

    The runner is intentionally stateless — each `run()` builds a fresh
    Agent. browser-use itself owns the Chromium instance + persistent
    profile (via `user_data_dir`), so cookies survive between runs.
    """

    async def run(
        self,
        task: str,
        *,
        user_telegram_id: int,
        max_steps: Optional[int] = None,
        use_vision: bool = False,
        output_schema: Optional[type] = None,
    ) -> str:
        """Execute one browser-use task with the action-gate enforced,
        Atlas's bridge tools registered, sensitive PII masked, and
        unhandled failures auto-converted to RepairTickets.

        Args:
            task: Natural-language goal for the browser-use Agent.
            user_telegram_id: Owner ID — receives Telegram approval
                prompts for any write actions.
            max_steps: Override the default max_steps (settings.browser_use_max_steps).
            use_vision: Pass-through to browser-use Agent. Doubles LLM
                cost; off by default. Auto-flipped on for the ELEMENT_DRIFT
                retry path.
            output_schema: Optional Pydantic model class. When provided,
                browser-use validates the agent's final answer against
                this schema and returns it as JSON; the runner returns
                the JSON string. Lets callers (org_task worker, etc.)
                consume typed results.

        Returns:
            The agent's final text result (or JSON if output_schema set).

        Raises:
            BrowserUseUnavailable: package not installed.
            BrowserRunTimeout: ran past total-timeout.
            ApprovalDenied / ApprovalTimeout (from browser_action_gate)
                if the user rejects a write action or doesn't respond.
            BrowserRunFailed: unhandled failure after the targeted-retry
                strategy ran. A RepairTicket has already been opened.
        """
        from src.settings import settings

        if not settings.browser_use_enabled:
            raise BrowserUseUnavailable(
                "BROWSER_USE_ENABLED is false. Set it in .env and restart."
            )

        try:
            # Lazy import. browser-use pulls Playwright + a chunk of LLM
            # adapters; we don't want to pay that cost (or block a missing
            # dep) on every Atlas import.
            from browser_use import Agent  # type: ignore[import-not-found]
        except ImportError as exc:
            raise BrowserUseUnavailable(
                f"browser-use package not importable: {exc}. "
                "Install via `pip install browser-use>=0.7,<0.8`."
            ) from exc

        from src.security.browser_action_gate import gate

        async def _action_hook(action_name: str, action_args: dict[str, Any] | None = None) -> bool:
            # browser-use calls this synchronously per step. The gate
            # returns True for reads, awaits Telegram for writes. Any
            # non-True return / raise aborts the run.
            return await gate(
                user_telegram_id=user_telegram_id,
                action_name=action_name,
                action_args=action_args or {},
                summary=_summarize_action(action_name, action_args, task),
            )

        async with _BROWSER_SEMAPHORE:
            logger.info(
                "browser-use run starting user=%d task=%r profile=%s headless=%s",
                user_telegram_id, task[:100], settings.browser_use_profile_dir,
                settings.browser_use_headless,
            )
            return await _run_with_targeted_retry(
                runner=self,
                Agent=Agent,
                task=task,
                user_telegram_id=user_telegram_id,
                max_steps=max_steps or settings.browser_use_max_steps,
                use_vision=use_vision,
                output_schema=output_schema,
                action_hook=_action_hook,
            )

    async def _run_agent(
        self,
        *,
        Agent: type,
        task: str,
        user_telegram_id: int,
        max_steps: int,
        use_vision: bool,
        output_schema: Optional[type],
        action_hook: Callable[[str, dict | None], Any],
        step_timeout_override: Optional[int] = None,
    ) -> tuple[str, Any]:
        """Invoke the browser-use Agent and return (final_text, history).

        Split out so test code can monkeypatch the actual agent
        construction without re-implementing the semaphore + timeout
        wrapping above.

        Returns the AgentHistoryList alongside the final text so the
        caller's failure-handler has the action history to ticket on
        partial success or to retry against on transient failure.

        Implementation notes (browser-use 0.7 API specifics):
        - `max_steps` and `on_step_end` go to `agent.run(...)`, not
          `Agent(...)`. Pre-0.7 alphas accepted them on the constructor;
          stable 0.7 moved them.
        - Profile config is `browser_profile=BrowserProfile(user_data_dir=,
          headless=)`, not `browser_config={}`.
        - `step_timeout` is a top-level Agent kwarg, not nested in profile.
        - There is NO per-action callback in browser-use 0.7. The closest
          hook is `register_new_step_callback`, which fires AFTER the LLM
          decided what to do but BEFORE Chromium executes it. We inspect
          `agent_output.action` there and gate any write action; the
          existing `gate()` raises `ApprovalDenied` if the user clicks
          ❌, which propagates out of `agent.run()` and into the
          targeted-retry handler.
        """
        from src.settings import settings
        from src.integrations.browser_use_bridge import (
            build_atlas_browser_tools,
            collect_sensitive_data,
            build_atlas_system_extension,
        )
        from src.integrations.browser_use_failures import build_step_audit_hook

        # Build BrowserProfile — the persistent-cookies + headless config.
        #
        # Three things matter here that the obvious code drops:
        #
        # 1. `executable_path` — without this, browser-use 0.7's
        #    LocalBrowserWatchdog tries `uvx playwright install` to
        #    download its OWN chromium, even though Atlas's Dockerfile
        #    already installed playwright's chromium at
        #    `/opt/playwright/chromium-*/chrome-linux64/chrome`. We glob
        #    that path at runtime and hand it over so no second install
        #    is attempted.
        # 2. `allowed_domains` — when `sensitive_data` is set without
        #    a domain whitelist, browser-use logs a prompt-injection
        #    warning. We extract URLs from the task and whitelist their
        #    hostnames so leaks can't fan out.
        # 3. `user_data_dir` — persistent profile (cookies survive
        #    container restarts).
        chromium_path = _find_chromium_binary()
        allowed = _extract_allowed_domains(task)

        try:
            from browser_use import BrowserProfile  # type: ignore[import-not-found]
            profile_kwargs: dict[str, Any] = {
                "user_data_dir": settings.browser_use_profile_dir,
                "headless": settings.browser_use_headless,
                # Docker-required chrome flags. Without these, Chromium
                # launches and binds its CDP port but crashes within ~5
                # seconds because of two container constraints:
                #   - /dev/shm is only 64 MB by default (Chrome wants
                #     hundreds of MB; --disable-dev-shm-usage falls back
                #     to /tmp, slightly slower but actually works)
                #   - no GPU device, no setuid bit on chrome → sandbox
                #     can't initialize → chrome dies
                # The result was a 30s timeout on the CDP connect with
                # no useful error in the logs. Pin these explicitly.
                "args": [
                    "--disable-dev-shm-usage",
                    "--no-sandbox",
                    "--disable-gpu",
                    "--disable-setuid-sandbox",
                ],
            }
            if chromium_path:
                profile_kwargs["executable_path"] = chromium_path
            if allowed:
                profile_kwargs["allowed_domains"] = allowed
            browser_profile = BrowserProfile(**profile_kwargs)
        except ImportError:
            browser_profile = None

        agent_kwargs: dict[str, Any] = {
            "task": task,
            "use_vision": use_vision,
            "step_timeout": step_timeout_override or settings.browser_use_step_timeout_seconds,
        }
        if browser_profile is not None:
            agent_kwargs["browser_profile"] = browser_profile

        # Wrap the per-action gate as a per-STEP callback. browser-use
        # 0.7 only fires step-level hooks; this callback gets the
        # `AgentOutput` containing the LIST of planned actions for the
        # step, so we walk them and gate any writes before Chromium
        # runs them.
        async def _step_gate_hook(browser_state, agent_output, step_number):
            try:
                actions = getattr(agent_output, "action", None) or []
            except Exception:
                actions = []
            for raw_action in actions:
                # ActionModel is a Pydantic model with one key per action
                # type. `model_dump(exclude_unset=True)` gives us
                # `{action_name: action_args_or_None}`.
                try:
                    if hasattr(raw_action, "model_dump"):
                        dumped = raw_action.model_dump(exclude_unset=True)
                    else:
                        dumped = dict(raw_action) if raw_action else {}
                except Exception:
                    dumped = {}
                for action_name, action_args in dumped.items():
                    args_dict = action_args if isinstance(action_args, dict) else {}
                    # Calling `action_hook` (= gate) raises ApprovalDenied
                    # if the user clicks ❌; it bubbles out of agent.run()
                    # and into _run_with_targeted_retry's exception path.
                    await action_hook(action_name, args_dict)

        agent_kwargs["register_new_step_callback"] = _step_gate_hook

        # Tier 1.1 — bridge tools (memory read, drive save, activity log).
        bridge_tools = build_atlas_browser_tools(user_telegram_id=user_telegram_id)
        if bridge_tools is not None:
            agent_kwargs["tools"] = bridge_tools

        # Tier 1.2 — sensitive-data dictionary (PII masked from LLM context).
        sensitive = await collect_sensitive_data(user_telegram_id=user_telegram_id)
        if sensitive:
            agent_kwargs["sensitive_data"] = sensitive

        # Tier 1.4 — Atlas system message extension.
        agent_kwargs["extend_system_message"] = build_atlas_system_extension()

        # Tier 1.3 — structured output validation when the caller asked.
        if output_schema is not None:
            agent_kwargs["output_model_schema"] = output_schema

        # LLM choice — explicitly hand browser-use Atlas's OpenAI key.
        # The 0.7 import path is `browser_use.llm.ChatOpenAI` (NOT
        # `browser_use.ChatOpenAI`, which silently fails and lets
        # browser-use fall back to its default model). Atlas's
        # `model_general` (e.g. `gpt-5.4-mini`) isn't in browser-use's
        # supported model list, so we use a known-good model
        # (`gpt-4.1-mini`) for the browser agent specifically — it's
        # cheaper than the orchestrator's reasoning model and well-
        # suited to short reactive browser-loop calls.
        try:
            from browser_use.llm import ChatOpenAI  # type: ignore[import-not-found]
            agent_kwargs["llm"] = ChatOpenAI(
                model="gpt-4.1-mini",
                api_key=settings.openai_api_key,
            )
        except ImportError:
            # Older/newer browser-use versions may name this differently.
            # Falling back to letting browser-use pick its default — it
            # reads OPENAI_API_KEY from the environment.
            pass

        # Tier 2.3 — per-step audit log so the dashboard sees live progress.
        step_audit_hook = build_step_audit_hook(user_telegram_id=user_telegram_id, task=task)

        agent = Agent(**agent_kwargs)
        # max_steps + on_step_end go to .run(), not the constructor
        history = await agent.run(max_steps=max_steps, on_step_end=step_audit_hook)

        # Prefer structured output when the caller asked for one
        if output_schema is not None and hasattr(history, "structured_output"):
            try:
                structured = history.structured_output
                if structured is not None:
                    if hasattr(structured, "model_dump_json"):
                        return structured.model_dump_json(), history
                    return str(structured), history
            except Exception:
                pass

        # browser-use returns an AgentHistoryList. .final_result() returns
        # the agent's final text answer; older versions used .result.
        for attr in ("final_result", "final_answer", "result"):
            if hasattr(history, attr):
                value = getattr(history, attr)
                if callable(value):
                    value = value()
                if value:
                    return str(value), history
        return str(history), history


class BrowserRunFailed(RuntimeError):
    """The browser run failed all retries; a RepairTicket has been opened."""


async def _run_with_targeted_retry(
    *,
    runner: "BrowserRunner",
    Agent: type,
    task: str,
    user_telegram_id: int,
    max_steps: int,
    use_vision: bool,
    output_schema: Optional[type],
    action_hook: Callable,
) -> str:
    """Run the agent once. On classifiable failure, retry once with the
    bucket-appropriate strategy. On unhandled failure, open a
    RepairTicket and raise BrowserRunFailed.

    Strategies (Tier 2.1):
      - LOGIN_EXPIRED   → no retry (re-seed needed); ticket + raise.
      - ELEMENT_DRIFT   → one retry with use_vision=True.
      - TIMEOUT         → one retry with 2x step_timeout.
      - UNKNOWN         → no retry; ticket + raise.
    """
    from src.settings import settings
    from src.integrations.browser_use_failures import (
        FailureKind,
        classify_failure,
        open_repair_ticket_for_browser_failure,
    )

    last_history: Any = None
    last_exc: Optional[BaseException] = None
    attempts = 0
    use_vision_now = use_vision
    step_timeout_override: Optional[int] = None

    for attempts in range(1, 3):  # at most 2 attempts
        try:
            result, history = await asyncio.wait_for(
                runner._run_agent(
                    Agent=Agent,
                    task=task,
                    user_telegram_id=user_telegram_id,
                    max_steps=max_steps,
                    use_vision=use_vision_now,
                    output_schema=output_schema,
                    action_hook=action_hook,
                    step_timeout_override=step_timeout_override,
                ),
                timeout=settings.browser_use_total_timeout_seconds,
            )
            logger.info(
                "browser-use run finished user=%d attempts=%d",
                user_telegram_id, attempts,
            )
            return result
        except asyncio.TimeoutError as exc:
            last_exc = BrowserRunTimeout(
                f"browser-use run exceeded {settings.browser_use_total_timeout_seconds}s"
            )
            kind = FailureKind.TIMEOUT
        except (BrowserUseUnavailable, BrowserRunTimeout):
            raise  # not retryable
        except Exception as exc:
            last_exc = exc
            last_history = getattr(exc, "history", None) or last_history
            kind = classify_failure(exc, last_history)

        # Decide whether to retry once with a tailored strategy.
        if attempts >= 2:
            break  # already used our one retry
        if kind == FailureKind.ELEMENT_DRIFT:
            logger.info("browser failure ELEMENT_DRIFT — retrying with vision on")
            use_vision_now = True
            continue
        if kind == FailureKind.TIMEOUT:
            logger.info("browser failure TIMEOUT — retrying with 2x step_timeout")
            step_timeout_override = settings.browser_use_step_timeout_seconds * 2
            continue
        # LOGIN_EXPIRED + UNKNOWN: no retry helps
        break

    # Exhausted retries — open a ticket and raise typed
    final_kind = classify_failure(last_exc, last_history)
    ticket_id = await open_repair_ticket_for_browser_failure(
        user_telegram_id=user_telegram_id,
        task=task,
        failure_kind=final_kind,
        exc=last_exc,
        history=last_history,
    )
    msg_parts = [f"browser-use failed after {attempts} attempt(s) [{final_kind.value}]"]
    if last_exc:
        msg_parts.append(str(last_exc)[:200])
    if ticket_id is not None:
        msg_parts.append(f"RepairTicket #{ticket_id} opened")
    if final_kind == FailureKind.LOGIN_EXPIRED:
        msg_parts.append(
            "Run `docker compose run --rm -e BROWSER_USE_HEADLESS=false "
            "assistant python -m scripts.seed_browser_profile` to re-seed."
        )
    raise BrowserRunFailed(" — ".join(msg_parts))


def _summarize_action(
    action_name: str,
    action_args: Optional[dict],
    task: str,
) -> str:
    """Build the human-readable summary that goes into the Telegram prompt."""
    target = ""
    if isinstance(action_args, dict):
        for key in ("url", "label", "text", "selector", "target"):
            v = action_args.get(key)
            if isinstance(v, str) and v:
                target = f" → `{v[:80]}`"
                break
    return f"{action_name}{target}\n_Goal: {task[:160]}_"


def _find_chromium_binary() -> Optional[str]:
    """Locate the Playwright-installed Chromium binary.

    Atlas's Dockerfile sets PLAYWRIGHT_BROWSERS_PATH=/opt/playwright and
    runs `playwright install --with-deps chromium` at build time — the
    binary lands at `/opt/playwright/chromium-<version>/chrome-linux64/chrome`.
    We glob to tolerate the version suffix changing between Playwright
    upgrades. Returns None on any miss; the caller falls back to letting
    browser-use try its own install (which fails on a slim image, but
    keeping the path optional means dev environments without a baked-in
    Playwright still error gracefully rather than crashing on import).
    """
    import os
    import glob

    candidate_roots = []
    env_root = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    if env_root:
        candidate_roots.append(env_root)
    candidate_roots.extend(["/opt/playwright", "/ms-playwright"])

    for root in candidate_roots:
        for suffix in ("chromium-*/chrome-linux64/chrome", "chromium-*/chrome-linux/chrome"):
            matches = glob.glob(os.path.join(root, suffix))
            if matches:
                # Newest version wins (sort lexicographically — chromium-1217
                # > chromium-1093, etc., works for the version-numbered
                # Playwright bundles).
                matches.sort(reverse=True)
                return matches[0]
    return None


_URL_REGEX = None  # lazy-compile


def _extract_allowed_domains(task: str) -> list[str]:
    """Pull URL hostnames out of the task text.

    browser-use logs a "prompt-injection risk" warning whenever
    `sensitive_data` is set without `allowed_domains`. We can't know the
    exhaustive set ahead of time (the agent may follow links during the
    task), but the task itself usually names at least one URL — locking
    the browser to those hostnames blocks the worst-case fan-out.

    Returns an empty list if no URLs found. Caller skips the
    `allowed_domains` kwarg in that case so browser-use doesn't reject
    an empty whitelist.
    """
    global _URL_REGEX
    if _URL_REGEX is None:
        import re
        _URL_REGEX = re.compile(r"https?://([A-Za-z0-9.\-]+)", re.IGNORECASE)

    domains: set[str] = set()
    for match in _URL_REGEX.finditer(task or ""):
        host = match.group(1).strip().lower().rstrip(".")
        if host:
            domains.add(host)
    return sorted(domains)
