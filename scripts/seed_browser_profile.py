"""One-shot seeder: open a HEADED Chromium pointed at the persistent
browser-use profile so the user can log into the sites Atlas should be
able to access.

Usage (from a host shell):

    docker compose run --rm \\
        -e BROWSER_USE_HEADLESS=false \\
        assistant python -m scripts.seed_browser_profile

The container's named volume `browser_profile` is mounted at
`/data/browser_profile`. Cookies persist across container rebuilds; you
only need to re-seed when a site itself expires the session (LinkedIn
~30d, banks days–weeks).

Atlas NEVER sees passwords. The login happens entirely in the user's
browser; only cookies + local storage land in the volume.

Implementation notes:
    - We use Playwright directly (already installed in the container) +
      the same `user_data_dir` browser-use uses, so the seeded cookies
      are visible to browser-use's Agent on its next run.
    - Headed mode requires X server / WSLg / Docker Desktop's display
      forwarding. On Windows + Docker Desktop, the default works.
    - The script blocks on input() — close the browser window OR press
      Enter in the terminal to exit.
"""
from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

logger = logging.getLogger("seed_browser_profile")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")


async def seed() -> int:
    from src.settings import settings

    profile_dir = Path(settings.browser_use_profile_dir)
    profile_dir.mkdir(parents=True, exist_ok=True)

    if settings.browser_use_headless:
        logger.error(
            "BROWSER_USE_HEADLESS is true. Seeding requires a HEADED browser. "
            "Re-run with `-e BROWSER_USE_HEADLESS=false` (the docker compose "
            "form in the module docstring sets this for you)."
        )
        return 2

    try:
        from playwright.async_api import async_playwright
    except ImportError as exc:
        logger.error("Playwright is not installed in this image: %s", exc)
        return 3

    logger.info("Opening a headed Chromium at user_data_dir=%s", profile_dir)
    logger.info("Log in to the sites you want Atlas to access (LinkedIn, etc.).")
    logger.info("Press Enter in this terminal when you're done.")

    async with async_playwright() as pw:
        # `launch_persistent_context` is the API that uses a persistent
        # user-data-dir — same path browser-use uses. Cookies/localStorage
        # written here are seen by browser-use on its next Agent run.
        try:
            context = await pw.chromium.launch_persistent_context(
                str(profile_dir),
                headless=False,
                args=["--no-first-run", "--no-default-browser-check"],
            )
        except Exception as exc:
            logger.error(
                "Could not launch headed Chromium: %s. "
                "If you're inside Docker, the host needs display forwarding "
                "(Docker Desktop on Windows enables this by default; on "
                "Linux you may need DISPLAY + an X11 socket mount).",
                exc,
            )
            return 4

        # Open a starter tab with a help message
        page = context.pages[0] if context.pages else await context.new_page()
        await page.goto("about:blank")
        await page.set_content(
            """
            <!doctype html><meta charset="utf-8">
            <title>Atlas browser-use seed</title>
            <style>
              body { font-family: system-ui; max-width: 720px; margin: 80px auto;
                     padding: 0 24px; color: #222; }
              h1 { color: #2a6df4; }
              code { background: #f3f3f3; padding: 2px 6px; border-radius: 4px; }
            </style>
            <h1>👋 Atlas browser seeder</h1>
            <p>This Chromium uses a persistent profile at
               <code>{profile}</code>. Cookies and localStorage you create here
               will be available to Atlas's browser-use skill.</p>
            <ol>
              <li>Open a new tab and navigate to any site you want Atlas to
                  access (e.g., <a href="https://www.linkedin.com">LinkedIn</a>,
                  Indeed, your bank dashboard).</li>
              <li>Log in normally. Click "remember me" / "stay logged in" if
                  the site offers it.</li>
              <li>Repeat for each site.</li>
              <li>When done, close this window OR press Enter in the terminal
                  that started this script.</li>
            </ol>
            <p><strong>Atlas never sees your passwords.</strong> Only cookies
               and local storage land in the volume.</p>
            """.replace("{profile}", str(profile_dir)),
        )

        # Block until the user is done. Two exit paths:
        #   1. They press Enter in the terminal -> input() returns.
        #   2. They close the browser window     -> wait_for_event raises.
        loop = asyncio.get_running_loop()
        try:
            close_task = loop.create_task(context.wait_for_event("close"))
            input_task = loop.run_in_executor(None, sys.stdin.readline)
            done, pending = await asyncio.wait(
                {close_task, input_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            for t in pending:
                t.cancel()
        except KeyboardInterrupt:
            logger.info("Interrupted.")
        finally:
            try:
                await context.close()
            except Exception:
                pass

    logger.info("Seed complete. Cookies persisted at %s.", profile_dir)
    logger.info("Set BROWSER_USE_ENABLED=true (and BROWSER_USE_HEADLESS=true) and restart the assistant.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(seed()))
