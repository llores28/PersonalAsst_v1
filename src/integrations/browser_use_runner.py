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
        """
        from src.settings import settings
        from src.integrations.browser_use_bridge import (
            build_atlas_browser_tools,
            collect_sensitive_data,
            build_atlas_system_extension,
        )
        from src.integrations.browser_use_failures import build_step_audit_hook

        agent_kwargs: dict[str, Any] = {
            "task": task,
            "max_steps": max_steps,
            "use_vision": use_vision,
        }
        # browser-use accepts either explicit Browser config or the
        # `user_data_dir` shortcut. We always use the persistent profile.
        agent_kwargs["browser_config"] = {
            "user_data_dir": settings.browser_use_profile_dir,
            "headless": settings.browser_use_headless,
            "step_timeout": step_timeout_override or settings.browser_use_step_timeout_seconds,
        }
        # Pre-action gate (writes require Telegram approval). browser-use's
        # API has evolved; the most stable hook is `pre_action_callback` —
        # we pass it under both names so the same code works across 0.7.x.
        agent_kwargs["pre_action_callback"] = action_hook
        agent_kwargs["on_action"] = action_hook

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

        # Tier 2.3 — per-step audit log so the dashboard sees live progress.
        step_hook = build_step_audit_hook(user_telegram_id=user_telegram_id, task=task)
        agent_kwargs["on_step_end"] = step_hook

        # LLM choice — explicitly hand browser-use Atlas's OpenAI key
        # via the documented `llm=` arg if available, otherwise rely on
        # the OPENAI_API_KEY env var (already set in the assistant
        # container) and browser-use's default.
        try:
            from browser_use import ChatOpenAI  # type: ignore[import-not-found]
            agent_kwargs["llm"] = ChatOpenAI(
                model=settings.model_general,
                api_key=settings.openai_api_key,
            )
        except ImportError:
            # Older/newer browser-use versions may name this differently.
            # Falling back to letting browser-use pick its default — it
            # reads OPENAI_API_KEY from the environment.
            pass

        agent = Agent(**agent_kwargs)
        history = await agent.run()

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
