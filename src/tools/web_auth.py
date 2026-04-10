"""Generic authenticated web scraping for dynamic tools.

Uses crawl4ai (LLM-friendly web crawler built on Playwright) to:
1. Login to any website using configurable credentials
2. Scrape any page into clean LLM-ready markdown
3. Cache session cookies in the credential vault for reuse

No per-site custom scrapers needed — the LLM parses the markdown itself.

Usage:
    from src.tools.web_auth import authenticated_scrape

    # Scrape any page after login
    result = await authenticated_scrape(
        site="linkedin",
        target_url="https://www.linkedin.com/in/john-doe/",
    )
    print(result.markdown)   # Clean markdown for LLM consumption

    # Or scrape without auth (public pages)
    result = await authenticated_scrape(
        target_url="https://example.com/public-page",
    )
"""

import asyncio
import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


# ── Site login configurations ─────────────────────────────────────
# Add new sites here — no custom scraper code needed.

@dataclass
class SiteLoginConfig:
    """Configuration for automated login to a website."""
    login_url: str
    username_selector: str
    password_selector: str
    submit_selector: str
    success_indicator: str                   # URL substring after login
    cookie_names: list[str] = field(default_factory=list)  # cookies to cache
    credential_keys: tuple[str, str] = ("email", "password")  # vault key names
    wait_after_login_sec: float = 3.0


SITE_CONFIGS: dict[str, SiteLoginConfig] = {
    "linkedin": SiteLoginConfig(
        login_url="https://www.linkedin.com/login",
        username_selector="input#username",
        password_selector="input#password",
        submit_selector='button[type="submit"]',
        success_indicator="/feed",
        cookie_names=["li_at", "JSESSIONID"],
        credential_keys=("linkedin_email", "linkedin_password"),
        wait_after_login_sec=5.0,
    ),
    "indeed": SiteLoginConfig(
        login_url="https://secure.indeed.com/auth",
        username_selector='input[type="email"]',
        password_selector='input[type="password"]',
        submit_selector='button[type="submit"]',
        success_indicator="/",
        credential_keys=("indeed_email", "indeed_password"),
    ),
    # Add more sites as needed — zero scraper code required
}


# ── Scrape result ─────────────────────────────────────────────────

@dataclass
class ScrapeResult:
    """Result of an authenticated (or public) web scrape."""
    url: str
    markdown: str                # LLM-ready markdown content
    raw_markdown: str = ""       # Unfiltered markdown (before noise removal)
    success: bool = True
    error: str = ""
    links: list[dict] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)


# ── Core scraping function ────────────────────────────────────────

async def authenticated_scrape(
    target_url: str,
    site: str | None = None,
    username: str | None = None,
    password: str | None = None,
    scroll: bool = True,
    wait_for: str | None = None,
    timeout_ms: int = 30000,
    cache_cookies: bool = True,
) -> ScrapeResult:
    """Scrape any web page, optionally with authenticated login.

    This is the single entry point for all web scraping in the project.
    Uses crawl4ai for LLM-ready markdown output. No per-site scrapers needed.

    Args:
        target_url: The page URL to scrape.
        site: Site key from SITE_CONFIGS (e.g. "linkedin", "indeed").
            If None, scrapes without authentication.
        username: Override username (otherwise loaded from credential vault).
        password: Override password (otherwise loaded from credential vault).
        scroll: Whether to scroll the page to trigger lazy-loading.
        wait_for: Optional CSS selector to wait for before scraping.
        timeout_ms: Page load timeout in milliseconds.
        cache_cookies: Whether to cache session cookies in the vault.

    Returns:
        ScrapeResult with clean markdown content.
    """
    try:
        from crawl4ai import (
            AsyncWebCrawler,
            BrowserConfig,
            CrawlerRunConfig,
            CacheMode,
        )
        from crawl4ai.content_filter_strategy import PruningContentFilter
        from crawl4ai.markdown_generation_strategy import DefaultMarkdownGenerator
    except ImportError:
        return ScrapeResult(
            url=target_url, markdown="", success=False,
            error="crawl4ai not installed. Add crawl4ai>=0.8.0 to requirements.txt",
        )

    # Resolve credentials if site auth is needed
    site_config: SiteLoginConfig | None = None
    cred_user = username
    cred_pass = password

    if site:
        site_config = SITE_CONFIGS.get(site)
        if not site_config:
            return ScrapeResult(
                url=target_url, markdown="", success=False,
                error=f"Unknown site '{site}'. Available: {list(SITE_CONFIGS.keys())}",
            )
        if not cred_user or not cred_pass:
            cred_user, cred_pass = await _load_credentials(site, site_config)
        if not cred_user or not cred_pass:
            return ScrapeResult(
                url=target_url, markdown="", success=False,
                error=(
                    f"Credentials for '{site}' not configured. Run:\n"
                    f"  /tools credentials set {site} {site_config.credential_keys[0]} <value>\n"
                    f"  /tools credentials set {site} {site_config.credential_keys[1]} <value>"
                ),
            )

    # Build crawl4ai login hook if auth is needed
    login_hook = None
    if site_config and cred_user and cred_pass:
        login_hook = _build_login_hook(site, site_config, cred_user, cred_pass, cache_cookies)

    # Configure browser
    browser_config = BrowserConfig(
        headless=True,
        verbose=False,
        browser_type="chromium",
    )

    # Configure crawl run
    js_scroll = (
        "window.scrollTo(0, document.body.scrollHeight);"
        if scroll else ""
    )

    run_config = CrawlerRunConfig(
        cache_mode=CacheMode.BYPASS,
        page_timeout=max(timeout_ms, 60000),
        wait_for=f"css:{wait_for}" if wait_for else None,
        js_code=js_scroll if js_scroll else None,
        scan_full_page=scroll,
        remove_overlay_elements=True,
        delay_before_return_html=3.0,
        markdown_generator=DefaultMarkdownGenerator(
            content_filter=PruningContentFilter(
                threshold=0.48,
                threshold_type="fixed",
                min_word_threshold=0,
            )
        ),
    )

    try:
        crawler = AsyncWebCrawler(config=browser_config)

        # Attach login hook if needed
        if login_hook:
            crawler.crawler_strategy.set_hook("on_page_context_created", login_hook)

        await crawler.start()

        result = await crawler.arun(url=target_url, config=run_config)

        await crawler.close()

        if not result.success:
            return ScrapeResult(
                url=target_url, markdown="", success=False,
                error=result.error_message or "Crawl failed",
            )

        # Extract clean markdown (prefer fit, fall back to raw)
        md = ""
        raw_md = ""
        if result.markdown:
            if hasattr(result.markdown, "fit_markdown"):
                md = result.markdown.fit_markdown or ""
                raw_md = result.markdown.raw_markdown or ""
            else:
                md = str(result.markdown)
                raw_md = md

        # fit_markdown can be empty if heuristic filtering is too aggressive
        if not md.strip() and raw_md.strip():
            md = raw_md

        # Strip JSON tracking blobs and other noise from JS-heavy pages
        md = _clean_markdown(md)

        # Truncate very long pages to stay within LLM context
        if len(md) > 30000:
            md = md[:30000] + "\n\n[... content truncated at 30,000 chars ...]"

        return ScrapeResult(
            url=target_url,
            markdown=md,
            raw_markdown=raw_md,
            success=True,
            links=_extract_links(result) if hasattr(result, "links") else [],
            metadata={"title": getattr(result, "title", "")},
        )

    except Exception as e:
        logger.exception("Scrape failed for %s", target_url)
        return ScrapeResult(
            url=target_url, markdown="", success=False,
            error=f"Scrape error: {e}",
        )


# ── Helpers ───────────────────────────────────────────────────────

async def _load_credentials(site: str, config: SiteLoginConfig) -> tuple[str, str]:
    """Load credentials from the vault for a site."""
    try:
        from src.tools.credentials import get_credentials
        creds = await get_credentials(site)
        user_key, pass_key = config.credential_keys
        return creds.get(user_key, ""), creds.get(pass_key, "")
    except Exception as e:
        logger.warning("Failed to load credentials for %s: %s", site, e)
        return "", ""


def _build_login_hook(
    site: str,
    config: SiteLoginConfig,
    username: str,
    password: str,
    cache_cookies: bool,
):
    """Build a crawl4ai hook that performs login before crawling."""

    async def on_page_context_created(page, context, **kwargs):
        """Login to the site before the main page is crawled."""
        logger.info("Login hook: navigating to %s", config.login_url)
        await page.goto(config.login_url, wait_until="domcontentloaded", timeout=60000)
        await page.fill(config.username_selector, username)
        await page.fill(config.password_selector, password)
        await page.click(config.submit_selector)

        # Wait for successful login
        try:
            await page.wait_for_url(
                f"**{config.success_indicator}**", timeout=30000
            )
        except Exception:
            await asyncio.sleep(config.wait_after_login_sec)

        current_url = page.url
        if "challenge" in current_url.lower():
            logger.error("Security challenge detected at %s", current_url)
            raise RuntimeError(
                f"Security challenge detected for {site}. "
                f"Please log in manually at {config.login_url} first, then retry."
            )

        logger.info("Login hook: authenticated at %s", current_url)

        # Cache cookies in vault
        if cache_cookies and config.cookie_names:
            try:
                all_cookies = await context.cookies()
                to_cache = {}
                for cookie in all_cookies:
                    if cookie["name"] in config.cookie_names:
                        to_cache[cookie["name"]] = cookie["value"]
                if to_cache:
                    from src.tools.credentials import store_credentials
                    await store_credentials(site, to_cache)
                    logger.info("Cached %d cookies for %s", len(to_cache), site)
            except Exception as e:
                logger.warning("Failed to cache cookies for %s: %s", site, e)

        return page

    return on_page_context_created


def _clean_markdown(md: str) -> str:
    """Post-process markdown to strip noise from JS-heavy pages.

    Removes:
    - Inline code blocks containing JSON objects (tracking/config data)
    - Image markdown tags (not useful for LLM text extraction)
    - Excessive blank lines
    """
    import re

    lines = md.split("\n")
    cleaned = []
    skip_block = False

    for line in lines:
        stripped = line.strip()

        # Skip lines that are just backtick-wrapped JSON blobs
        if stripped.startswith("`") and stripped.endswith("`"):
            inner = stripped.strip("`").strip()
            if inner.startswith("{") or inner.startswith("["):
                continue

        # Skip fenced code blocks containing JSON
        if stripped.startswith("```"):
            skip_block = not skip_block
            continue
        if skip_block:
            continue

        # Skip standalone JSON objects
        if stripped.startswith("{") and stripped.endswith("}"):
            continue

        # Skip image-only lines (not useful for LLM text extraction)
        if re.match(r"^!\[.*\]\(https?://.*\)$", stripped):
            continue

        cleaned.append(line)

    # Collapse excessive blank lines
    result = "\n".join(cleaned)
    result = re.sub(r"\n{4,}", "\n\n\n", result)
    return result.strip()


def _extract_links(result) -> list[dict]:
    """Safely extract links from a crawl result."""
    try:
        links = getattr(result, "links", None)
        if links and isinstance(links, dict):
            return [
                {"url": link.get("href", ""), "text": link.get("text", "")}
                for link in links.get("internal", [])[:20]
            ]
    except Exception:
        pass
    return []


# ── Convenience wrappers ──────────────────────────────────────────

async def scrape_linkedin_profile(
    profile_url: str | None = None,
) -> ScrapeResult:
    """Scrape a LinkedIn profile page into LLM-ready markdown.

    If profile_url is None, scrapes the authenticated user's own profile.
    """
    # If no URL given, discover own profile URL via /me redirect
    url = profile_url or "https://www.linkedin.com/in/me/"
    return await authenticated_scrape(target_url=url, site="linkedin", scroll=True)
