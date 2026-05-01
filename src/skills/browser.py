"""Browser-use skill — gives the orchestrator a `browse_web` tool that
drives a real Chromium instance to interact with arbitrary websites.

Architecture:
  - One `function_tool` per skill: `browse_web(task: str)`. The orchestrator
    routes here when the user asks for navigation, form-filling, or any
    action that needs an authenticated browser session.
  - The tool body delegates to `BrowserRunner` (src/integrations/browser_use_runner.py),
    which serializes runs through a single Semaphore and enforces a
    wall-clock cap from settings.
  - BrowserRunner threads every browser-use action through the gate
    (src/security/browser_action_gate.py); writes (form submit, click
    apply, checkout) emit a Telegram approval prompt and block until the
    user clicks ✅ or ❌.

Skill is opt-in: only registered when settings.browser_use_enabled=True
AND the persistent profile dir exists. Otherwise the orchestrator never
sees `browse_web` as an option.
"""
from __future__ import annotations

import logging
from pathlib import Path

from agents import function_tool

from src.skills.definition import SkillDefinition, SkillGroup

logger = logging.getLogger(__name__)


def _build_browser_use_tools(bound_user_id: int) -> list:
    """Build the browse_web tool with the user's Telegram ID closed over,
    matching the pattern of other skills (openrouter, gmail, etc.).

    Args:
        bound_user_id: Owner's Telegram ID — receives the per-action
            approval prompts from the gate.
    """

    @function_tool(name_override="browse_web")
    async def browse_web_tool(task: str) -> str:
        """Drive a real Chromium browser to accomplish a task that needs
        navigation, form-filling, or interaction with an authenticated
        website (LinkedIn, banking dashboards, multi-step forms, etc.).

        The browser uses a persistent profile, so cookies from the user's
        prior logins persist between calls.

        ALL write actions (form submit, click apply, checkout, post,
        purchase) require the user to approve via a Telegram inline button
        before they fire. Read actions (navigate, scrape, screenshot) run
        autonomously.

        Args:
            task: A natural-language description of what to accomplish.
                Be specific about the target site and the exact goal,
                e.g. "Go to https://news.ycombinator.com and tell me the
                title of the top story" or "Open my LinkedIn feed and
                summarize the top 3 posts."

        Returns:
            The agent's final text answer, or an error message if the
            task could not be completed (timeout, approval rejected,
            etc.).
        """
        from src.integrations.browser_use_runner import (
            BrowserRunner,
            BrowserUseUnavailable,
            BrowserRunTimeout,
            BrowserRunFailed,
        )
        from src.security.browser_action_gate import ApprovalDenied, ApprovalTimeout

        runner = BrowserRunner()
        try:
            result = await runner.run(task=task, user_telegram_id=bound_user_id)
            return result
        except BrowserUseUnavailable as exc:
            logger.warning("browse_web unavailable: %s", exc)
            return (
                "The browser skill is not currently available. "
                f"Reason: {exc}. Ask the owner to enable it via "
                "BROWSER_USE_ENABLED=true and seed the profile."
            )
        except BrowserRunTimeout as exc:
            logger.warning("browse_web timeout: %s", exc)
            return (
                "The browser ran past its wall-clock limit before "
                f"finishing. {exc}. Try breaking the task into smaller "
                "steps or increase BROWSER_USE_TOTAL_TIMEOUT_SECONDS."
            )
        except ApprovalDenied as exc:
            return f"Browser action cancelled by user: {exc}"
        except ApprovalTimeout as exc:
            return f"Browser action cancelled — no approval received: {exc}"
        except BrowserRunFailed as exc:
            # Targeted-retry path already exhausted + RepairTicket opened.
            # Surface a useful message; the dashboard's Repairs tab will
            # show the ticket with full action_history captured.
            logger.warning("browse_web failed after retries: %s", exc)
            return f"Browser run failed and a repair ticket was opened: {exc}"
        except Exception as exc:
            logger.exception("browse_web unexpected failure")
            return f"The browser run failed: {exc}"

    return [browse_web_tool]


def build_browser_skill(user_id: int) -> SkillDefinition:
    """Construct the browser skill. Caller must pass the owner Telegram ID
    so the action-gate prompts target the right chat."""
    return SkillDefinition(
        id="browser",
        group=SkillGroup.INTERNAL,
        description=(
            "Drive a real Chromium browser to interact with websites that need "
            "navigation, form-filling, or an authenticated session. Cookies "
            "persist via a seeded profile. Write actions require Telegram approval."
        ),
        tools=_build_browser_use_tools(user_id),
        instructions=(
            "Use `browse_web` when the user asks Atlas to:\n"
            "- Open a specific URL and read/scrape/summarize its content "
            "(use this when the built-in WebSearchTool isn't enough — e.g., "
            "the page requires login, or the user wants you to navigate "
            "through a multi-step flow).\n"
            "- Log into a site and check something behind the auth wall "
            "(LinkedIn feed, bank statement, private dashboard).\n"
            "- Fill out a form on the user's behalf. ALL submit actions "
            "will pause for Telegram approval before firing — describe "
            "what you intend to submit so the user can decide quickly.\n"
            "- Click through a checkout/apply/purchase flow.\n\n"
            "Pass a single, specific natural-language task. The browser "
            "agent will navigate autonomously; you don't need to break it "
            "into steps. Sample good tasks:\n"
            "  - 'Go to https://news.ycombinator.com and tell me the top story title'\n"
            "  - 'Open my LinkedIn feed and summarize the latest 3 posts'\n"
            "  - 'Fill out the contact form at example.com/contact with "
            "name=Lannys, email=lannys.lores@gmail.com, message=Hello'\n\n"
            "Sample BAD task (too vague): 'browse the web'.\n\n"
            "Do NOT use `browse_web` when:\n"
            "- The user asks a search-style question that the WebSearchTool "
            "or grounded LLM can answer without navigating.\n"
            "- The action only needs Gmail / Drive / Calendar — those have "
            "dedicated workspace skills.\n"
        ),
        routing_hints=[
            "open this URL and: 'open https://...', 'go to this site and...'",
            "interact with a website: 'log in to', 'click', 'fill out form on'",
            "scrape/read a logged-in page: 'check my LinkedIn feed', 'show my dashboard at'",
            "navigate a multi-step flow: 'go through the checkout', 'walk through the apply form'",
            "NOT for plain web search — use the WebSearchTool for that",
        ],
        requires_connection=True,
        read_only=False,
        tags=[
            "browser", "browse", "web", "automation", "navigate",
            "click", "form", "submit", "scrape", "login",
        ],
    )


def is_browser_skill_available() -> tuple[bool, str]:
    """Return (available, reason). Used by the orchestrator's registry
    builder to decide whether to register the skill, and by main.py at
    startup to log a clear warning if the profile is unseeded.
    """
    from src.settings import settings

    if not settings.browser_use_enabled:
        return False, "BROWSER_USE_ENABLED is false"
    profile = Path(settings.browser_use_profile_dir)
    if not profile.exists():
        return False, (
            f"profile directory does not exist at {profile}. "
            "Run `docker compose run --rm -e BROWSER_USE_HEADLESS=false "
            "assistant python -m scripts.seed_browser_profile` to seed it."
        )
    try:
        import browser_use  # type: ignore[import-not-found]  # noqa: F401
    except ImportError as exc:
        return False, f"browser-use package not importable: {exc}"
    return True, "ok"
