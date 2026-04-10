"""Browser automation function-type tool — Playwright-based.

Maintains a singleton browser session so agents can chain multiple
actions (navigate → fill → click → extract) across tool calls.

Exposes ``tool_functions`` list for the ToolRegistry multi-tool loader.
"""

import base64
import json
import logging
from typing import Optional

from agents import function_tool

logger = logging.getLogger(__name__)

# Singleton browser state
_playwright = None
_browser = None
_page = None
_init_error: Optional[str] = None


async def _ensure_browser():
    """Lazy-init Playwright browser + page. Reuses across calls."""
    global _playwright, _browser, _page, _init_error

    if _page is not None:
        return _page
    if _init_error is not None:
        raise RuntimeError(_init_error)

    try:
        from playwright.async_api import async_playwright

        _playwright = await async_playwright().start()
        _browser = await _playwright.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )
        _page = await _browser.new_page()
        # Reasonable defaults
        _page.set_default_timeout(30000)
        await _page.set_viewport_size({"width": 1280, "height": 720})
        logger.info("Browser session started (Chromium headless)")
        return _page

    except ImportError:
        _init_error = (
            "playwright not installed. Add playwright>=1.40.0 to requirements "
            "and run: playwright install chromium"
        )
        raise RuntimeError(_init_error)
    except Exception as e:
        _init_error = f"Browser launch failed: {e}"
        raise RuntimeError(_init_error)


async def _close_browser():
    """Close the browser and cleanup."""
    global _playwright, _browser, _page, _init_error
    if _page:
        try:
            await _page.close()
        except Exception:
            pass
    if _browser:
        try:
            await _browser.close()
        except Exception:
            pass
    if _playwright:
        try:
            await _playwright.stop()
        except Exception:
            pass
    _playwright = None
    _browser = None
    _page = None
    _init_error = None


def _safe_json(obj: object, max_len: int = 4000) -> str:
    try:
        text = json.dumps(obj, indent=2, default=str)
    except (TypeError, ValueError):
        text = str(obj)
    if len(text) > max_len:
        text = text[:max_len] + "\n... (truncated)"
    return text


# ── Navigation ───────────────────────────────────────────────────


async def _navigate_impl(url: str) -> str:
    """Navigate to a URL and return page info."""
    try:
        page = await _ensure_browser()
        resp = await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        status = resp.status if resp else "unknown"
        title = await page.title()
        return _safe_json({
            "status": "navigated",
            "url": page.url,
            "title": title,
            "http_status": status,
        })
    except Exception as e:
        return f"Navigation error: {e}"


@function_tool
async def browser_navigate(url: str) -> str:
    """Navigate the browser to a URL.

    Args:
        url: The full URL to navigate to (e.g. 'https://www.linkedin.com/login').
    """
    return await _navigate_impl(url)


# ── Content Extraction ───────────────────────────────────────────


async def _get_page_text_impl(selector: str = "body") -> str:
    """Extract visible text from the page or a specific element."""
    try:
        page = await _ensure_browser()
        element = await page.query_selector(selector)
        if not element:
            return f"No element found for selector: {selector}"
        text = await element.inner_text()
        # Truncate long pages
        if len(text) > 3000:
            text = text[:3000] + "\n... (truncated)"
        return _safe_json({
            "selector": selector,
            "text": text,
            "url": page.url,
        })
    except Exception as e:
        return f"Extract error: {e}"


@function_tool
async def browser_get_text(selector: str = "body") -> str:
    """Extract visible text from the current page or a specific CSS selector.

    Args:
        selector: CSS selector to extract text from (default: 'body' for full page).
    """
    return await _get_page_text_impl(selector)


async def _get_page_html_impl(selector: str = "body") -> str:
    """Get the HTML of the page or a specific element."""
    try:
        page = await _ensure_browser()
        element = await page.query_selector(selector)
        if not element:
            return f"No element found for selector: {selector}"
        html = await element.inner_html()
        if len(html) > 3000:
            html = html[:3000] + "\n... (truncated)"
        return _safe_json({
            "selector": selector,
            "html": html,
            "url": page.url,
        })
    except Exception as e:
        return f"HTML extract error: {e}"


@function_tool
async def browser_get_html(selector: str = "body") -> str:
    """Get the inner HTML of the current page or a specific CSS selector.

    Args:
        selector: CSS selector (default: 'body').
    """
    return await _get_page_html_impl(selector)


# ── Interaction ──────────────────────────────────────────────────


async def _click_impl(selector: str) -> str:
    """Click an element on the page."""
    try:
        page = await _ensure_browser()
        await page.click(selector, timeout=10000)
        await page.wait_for_load_state("domcontentloaded", timeout=10000)
        title = await page.title()
        return _safe_json({
            "status": "clicked",
            "selector": selector,
            "url": page.url,
            "title": title,
        })
    except Exception as e:
        return f"Click error: {e}"


@function_tool
async def browser_click(selector: str) -> str:
    """Click an element on the page using a CSS selector.

    Args:
        selector: CSS selector of the element to click (e.g. 'button[type=submit]', '#login-btn').
    """
    return await _click_impl(selector)


async def _fill_impl(selector: str, value: str) -> str:
    """Fill a form field with text."""
    try:
        page = await _ensure_browser()
        await page.fill(selector, value, timeout=10000)
        return _safe_json({
            "status": "filled",
            "selector": selector,
            "length": len(value),
        })
    except Exception as e:
        return f"Fill error: {e}"


@function_tool
async def browser_fill(selector: str, value: str) -> str:
    """Fill a form input field with text.

    Args:
        selector: CSS selector of the input field (e.g. '#username', 'input[name=email]').
        value: Text to fill into the field.
    """
    return await _fill_impl(selector, value)


async def _type_impl(selector: str, text: str, delay: int = 50) -> str:
    """Type text character by character (simulates keyboard input)."""
    try:
        page = await _ensure_browser()
        await page.click(selector, timeout=10000)
        await page.type(selector, text, delay=delay)
        return _safe_json({
            "status": "typed",
            "selector": selector,
            "length": len(text),
        })
    except Exception as e:
        return f"Type error: {e}"


@function_tool
async def browser_type(selector: str, text: str, delay: int = 50) -> str:
    """Type text character by character into a field (simulates real typing).

    Args:
        selector: CSS selector of the input field.
        text: Text to type.
        delay: Delay between keystrokes in milliseconds (default 50).
    """
    return await _type_impl(selector, text, delay)


# ── Screenshot ───────────────────────────────────────────────────


async def _screenshot_impl(full_page: bool = False) -> str:
    """Take a screenshot and return as base64."""
    try:
        page = await _ensure_browser()
        screenshot_bytes = await page.screenshot(full_page=full_page)
        b64 = base64.b64encode(screenshot_bytes).decode("ascii")
        # Truncate if too large for LLM context
        if len(b64) > 50000:
            return _safe_json({
                "status": "screenshot_too_large",
                "url": page.url,
                "size_bytes": len(screenshot_bytes),
                "message": "Screenshot is too large to include. Use browser_get_text to extract content instead.",
            })
        return _safe_json({
            "status": "screenshot",
            "url": page.url,
            "base64_png": b64[:200] + "... (truncated for display)",
            "size_bytes": len(screenshot_bytes),
        })
    except Exception as e:
        return f"Screenshot error: {e}"


@function_tool
async def browser_screenshot(full_page: bool = False) -> str:
    """Take a screenshot of the current page.

    Args:
        full_page: If True, capture the entire scrollable page (default: viewport only).
    """
    return await _screenshot_impl(full_page)


# ── Page Info ────────────────────────────────────────────────────


async def _get_page_info_impl() -> str:
    """Get current page URL, title, and form elements."""
    try:
        page = await _ensure_browser()
        title = await page.title()
        url = page.url

        # Find interactive elements
        inputs = await page.eval_on_selector_all(
            "input, textarea, select, button, a[href]",
            """elements => elements.slice(0, 30).map(el => ({
                tag: el.tagName.toLowerCase(),
                type: el.type || '',
                name: el.name || '',
                id: el.id || '',
                placeholder: el.placeholder || '',
                text: (el.textContent || '').trim().substring(0, 80),
                href: el.href || '',
                selector: el.id ? '#' + el.id : 
                          el.name ? el.tagName.toLowerCase() + '[name=' + el.name + ']' :
                          el.className ? el.tagName.toLowerCase() + '.' + el.className.split(' ')[0] : 
                          el.tagName.toLowerCase()
            }))""",
        )

        return _safe_json({
            "url": url,
            "title": title,
            "interactive_elements": inputs,
        })
    except Exception as e:
        return f"Page info error: {e}"


@function_tool
async def browser_page_info() -> str:
    """Get information about the current page: URL, title, and interactive elements (inputs, buttons, links).
    Useful for understanding page structure before interacting."""
    return await _get_page_info_impl()


# ── Session Management ───────────────────────────────────────────


async def _close_session_impl() -> str:
    """Close the browser session."""
    try:
        await _close_browser()
        return "Browser session closed."
    except Exception as e:
        return f"Close error: {e}"


@function_tool
async def browser_close() -> str:
    """Close the browser session and free resources. Call when done with browser automation."""
    return await _close_session_impl()


async def _wait_impl(selector: str, timeout: int = 10000) -> str:
    """Wait for an element to appear on the page."""
    try:
        page = await _ensure_browser()
        await page.wait_for_selector(selector, timeout=timeout)
        return _safe_json({
            "status": "found",
            "selector": selector,
            "url": page.url,
        })
    except Exception as e:
        return f"Wait error: {e}"


@function_tool
async def browser_wait(selector: str, timeout: int = 10000) -> str:
    """Wait for an element to appear on the page.

    Args:
        selector: CSS selector to wait for.
        timeout: Maximum wait time in milliseconds (default 10000).
    """
    return await _wait_impl(selector, timeout)


# ── Login Helper ─────────────────────────────────────────────────


async def _login_with_credentials_impl(
    url: str,
    tool_name: str,
    email_selector: str,
    password_selector: str,
    submit_selector: str,
    email_key: str = "",
    password_key: str = "",
) -> str:
    """Navigate to a login page and fill credentials from the vault."""
    try:
        from src.tools.credentials import get_credentials

        page = await _ensure_browser()

        # Navigate to login page
        await page.goto(url, wait_until="domcontentloaded", timeout=30000)

        # Get credentials from vault
        creds = await get_credentials(tool_name)
        if not creds:
            return f"No credentials found for tool '{tool_name}'. Use /tools credentials set {tool_name} <key> <value>"

        # Find email/password credential keys
        email_val = creds.get(email_key) or ""
        password_val = creds.get(password_key) or ""

        # Try common key patterns if specific keys not found
        if not email_val:
            for k in creds:
                if "email" in k.lower() or "user" in k.lower():
                    email_val = creds[k]
                    break
        if not password_val:
            for k in creds:
                if "password" in k.lower() or "pass" in k.lower():
                    password_val = creds[k]
                    break

        if not email_val or not password_val:
            return f"Credentials incomplete for '{tool_name}'. Found keys: {list(creds.keys())}"

        # Fill and submit
        await page.fill(email_selector, email_val, timeout=10000)
        await page.fill(password_selector, password_val, timeout=10000)
        await page.click(submit_selector, timeout=10000)

        # Wait for navigation
        await page.wait_for_load_state("domcontentloaded", timeout=15000)
        title = await page.title()

        return _safe_json({
            "status": "login_submitted",
            "url": page.url,
            "title": title,
        })
    except Exception as e:
        return f"Login error: {e}"


@function_tool
async def browser_login(
    url: str,
    tool_name: str,
    email_selector: str,
    password_selector: str,
    submit_selector: str,
    email_key: str = "",
    password_key: str = "",
) -> str:
    """Log into a website using credentials from the tool vault.

    Args:
        url: Login page URL (e.g. 'https://www.linkedin.com/login').
        tool_name: Tool name whose credentials to use (e.g. 'linkedin').
        email_selector: CSS selector for the email/username field.
        password_selector: CSS selector for the password field.
        submit_selector: CSS selector for the login/submit button.
        email_key: Specific credential key for email (auto-detected if empty).
        password_key: Specific credential key for password (auto-detected if empty).
    """
    return await _login_with_credentials_impl(
        url, tool_name, email_selector, password_selector,
        submit_selector, email_key, password_key,
    )


# ── Generic Web Scraping (crawl4ai) ─────────────────────────────


async def _web_scrape_impl(url: str, site: str = "") -> str:
    """Scrape any web page into LLM-ready markdown using crawl4ai.

    If site is provided and matches a known login config (e.g. 'linkedin',
    'indeed'), the scraper will authenticate first using stored credentials.
    """
    try:
        from src.tools.web_auth import authenticated_scrape

        result = await authenticated_scrape(
            target_url=url,
            site=site if site else None,
            scroll=True,
        )

        if not result.success:
            return f"Scrape failed: {result.error}"

        if not result.markdown.strip():
            return f"Page at {url} was loaded but no content was extracted."

        return result.markdown

    except Exception as e:
        return f"Error scraping page: {e}"


@function_tool
async def browser_scrape_page(url: str, site: str = "") -> str:
    """Scrape any web page into clean readable text (LLM-ready markdown).
    Works for any website — news, docs, profiles, job listings, etc.

    For sites requiring login (e.g. LinkedIn, Indeed), pass the site name
    and credentials will be loaded from the vault automatically.

    Args:
        url: Full URL to scrape (e.g. 'https://example.com/page').
        site: Optional site name for authenticated scraping
            (e.g. 'linkedin', 'indeed'). Leave empty for public pages.
    """
    return await _web_scrape_impl(url, site)


# ── Exported tool list for ToolRegistry ──────────────────────────

tool_functions = [
    browser_navigate,
    browser_get_text,
    browser_get_html,
    browser_click,
    browser_fill,
    browser_type,
    browser_screenshot,
    browser_page_info,
    browser_wait,
    browser_login,
    browser_close,
    browser_scrape_page,
]
