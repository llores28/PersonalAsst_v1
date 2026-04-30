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
    ) -> str:
        """Execute one browser-use task with the action-gate enforced.

        Args:
            task: Natural-language goal for the browser-use Agent.
            user_telegram_id: Owner ID — receives Telegram approval
                prompts for any write actions.
            max_steps: Override the default max_steps (settings.browser_use_max_steps).
            use_vision: Pass-through to browser-use Agent. Doubles LLM
                cost; off by default.

        Returns:
            The agent's final text result.

        Raises:
            BrowserUseUnavailable: package not installed.
            BrowserRunTimeout: ran past total-timeout.
            ApprovalDenied / ApprovalTimeout (from browser_action_gate)
                if the user rejects a write action or doesn't respond.
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
            try:
                result = await asyncio.wait_for(
                    self._run_agent(
                        Agent=Agent,
                        task=task,
                        max_steps=max_steps or settings.browser_use_max_steps,
                        use_vision=use_vision,
                        action_hook=_action_hook,
                    ),
                    timeout=settings.browser_use_total_timeout_seconds,
                )
            except asyncio.TimeoutError as exc:
                raise BrowserRunTimeout(
                    f"browser-use run exceeded {settings.browser_use_total_timeout_seconds}s"
                ) from exc
            logger.info("browser-use run finished user=%d", user_telegram_id)
            return result

    async def _run_agent(
        self,
        *,
        Agent: type,
        task: str,
        max_steps: int,
        use_vision: bool,
        action_hook: Callable[[str, dict | None], Any],
    ) -> str:
        """Invoke the browser-use Agent and return its final text.

        Split out so test code can monkeypatch the actual agent
        construction without re-implementing the semaphore + timeout
        wrapping above.
        """
        from src.settings import settings

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
            "step_timeout": settings.browser_use_step_timeout_seconds,
        }
        # Wire the per-step action gate. browser-use's API has evolved;
        # the most stable hook is `pre_action_callback` — we pass it
        # under both names so the same code works across 0.7.x.
        agent_kwargs["pre_action_callback"] = action_hook
        agent_kwargs["on_action"] = action_hook

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
        # browser-use returns an AgentHistoryList. .final_result() returns
        # the agent's final text answer; older versions used .result.
        for attr in ("final_result", "final_answer", "result"):
            if hasattr(history, attr):
                value = getattr(history, attr)
                if callable(value):
                    value = value()
                if value:
                    return str(value)
        return str(history)


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
