"""
Webscraping Tool — fetch and extract content from external URLs.

Subcommands:
  page   — fetch page, extract main content as clean markdown
  api    — fetch JSON endpoint, pretty-print with schema inference
  links  — extract and categorize all links from a page
  docs   — crawl documentation site (max depth 3) as local markdown
"""

import hashlib
import json
import re
import time
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urljoin, urlparse

from bootstrap.cli.utils import (
    OutputFormat, Status, emit, make_result, truncate_output,
)
from bootstrap.cli.security import validate_url


# --- Cache ---

_CACHE_DIR = Path(".cache") / "scrape"
_CACHE_TTL_PAGE = 3600      # 1 hour for pages
_CACHE_TTL_DOCS = 86400     # 24 hours for doc crawls


def _cache_key(url: str) -> Path:
    h = hashlib.sha256(url.encode()).hexdigest()[:16]
    return _CACHE_DIR / f"{h}.json"


def _cache_get(key: Path, ttl: int) -> Optional[dict]:
    try:
        if key.exists():
            if time.time() - key.stat().st_mtime < ttl:
                return json.loads(key.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        pass
    return None


def _cache_set(key: Path, data: dict) -> None:
    try:
        key.parent.mkdir(parents=True, exist_ok=True)
        key.write_text(json.dumps(data, default=str), encoding="utf-8")
    except OSError:
        pass


# --- Rate limiting ---

_last_request_time = 0.0
_MIN_INTERVAL = 0.5  # 2 requests/second max


def _rate_limit() -> None:
    global _last_request_time
    now = time.time()
    elapsed = now - _last_request_time
    if elapsed < _MIN_INTERVAL:
        time.sleep(_MIN_INTERVAL - elapsed)
    _last_request_time = time.time()


# --- Fetching ---

_MAX_RESPONSE_SIZE = 5 * 1024 * 1024  # 5MB
_REQUEST_TIMEOUT = 30


def _fetch(url: str) -> tuple[str, int, dict]:
    """Fetch URL content. Returns (body, status_code, headers)."""
    import httpx

    validate_url(url)
    _rate_limit()

    resp = httpx.get(
        url,
        timeout=_REQUEST_TIMEOUT,
        follow_redirects=True,
        headers={"User-Agent": "bs-cli-scrape/0.1 (bootstrap toolkit)"},
    )

    # Check response size
    content_length = resp.headers.get("content-length")
    if content_length and int(content_length) > _MAX_RESPONSE_SIZE:
        raise ValueError(f"Response too large: {content_length} bytes (max {_MAX_RESPONSE_SIZE})")

    body = resp.text
    if len(body) > _MAX_RESPONSE_SIZE:
        body = body[:_MAX_RESPONSE_SIZE]

    headers = dict(resp.headers)
    return body, resp.status_code, headers


# --- HTML to Markdown ---

def _html_to_markdown(html: str) -> str:
    """Extract main content from HTML, strip chrome, return as clean text."""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")

    # Remove noise elements
    for tag in soup.find_all(["nav", "header", "footer", "aside", "script",
                              "style", "noscript", "iframe", "svg"]):
        tag.decompose()

    # Remove elements with common ad/nav classes
    noise_classes = ["nav", "menu", "sidebar", "footer", "header", "ad", "banner",
                     "cookie", "modal", "popup"]
    for cls in noise_classes:
        for el in soup.find_all(class_=re.compile(cls, re.I)):
            el.decompose()

    # Try to find main content
    main = (
        soup.find("main")
        or soup.find("article")
        or soup.find(attrs={"role": "main"})
        or soup.find(id=re.compile(r"content|main", re.I))
        or soup.body
        or soup
    )

    # Convert to text with basic markdown formatting
    lines: list[str] = []
    for el in main.descendants:
        if el.name in ("h1", "h2", "h3", "h4", "h5", "h6"):
            level = int(el.name[1])
            text = el.get_text(strip=True)
            if text:
                lines.append(f"\n{'#' * level} {text}\n")
        elif el.name == "p":
            text = el.get_text(strip=True)
            if text:
                lines.append(f"\n{text}\n")
        elif el.name == "li":
            text = el.get_text(strip=True)
            if text:
                lines.append(f"- {text}")
        elif el.name == "pre":
            code = el.get_text()
            if code.strip():
                lines.append(f"\n```\n{code.strip()}\n```\n")
        elif el.name == "code" and el.parent.name != "pre":
            text = el.get_text(strip=True)
            if text:
                lines.append(f"`{text}`")

    result = "\n".join(lines)
    # Clean up excessive whitespace
    result = re.sub(r"\n{3,}", "\n\n", result)
    return result.strip()


# --- Subcommands ---

def _scrape_page(url: str) -> dict[str, Any]:
    """Fetch a page and extract main content as markdown."""
    cache_key = _cache_key(url)
    cached = _cache_get(cache_key, _CACHE_TTL_PAGE)
    if cached:
        cached["cached"] = True
        return cached

    body, status, headers = _fetch(url)
    content_type = headers.get("content-type", "")

    if "text/html" in content_type or "<html" in body[:500].lower():
        content = _html_to_markdown(body)
    else:
        content = truncate_output(body, 5000)

    result = {
        "url": url,
        "status_code": status,
        "content_type": content_type,
        "content": truncate_output(content, 5000),
        "content_length": len(content),
    }

    _cache_set(cache_key, result)
    return result


def _scrape_api(url: str) -> dict[str, Any]:
    """Fetch a JSON API endpoint."""
    cache_key = _cache_key(f"api:{url}")
    cached = _cache_get(cache_key, _CACHE_TTL_PAGE)
    if cached:
        cached["cached"] = True
        return cached

    body, status, headers = _fetch(url)

    try:
        data = json.loads(body)
        # Infer schema from first level
        schema = {}
        if isinstance(data, dict):
            schema = {k: type(v).__name__ for k, v in data.items()}
        elif isinstance(data, list) and data:
            if isinstance(data[0], dict):
                schema = {k: type(v).__name__ for k, v in data[0].items()}
            schema["_array_length"] = len(data)

        result = {
            "url": url,
            "status_code": status,
            "data": data if len(body) < 5000 else truncate_output(json.dumps(data, indent=2), 5000),
            "schema": schema,
        }
    except json.JSONDecodeError:
        result = {
            "url": url,
            "status_code": status,
            "error": "Response is not valid JSON",
            "body_preview": truncate_output(body, 500),
        }

    _cache_set(cache_key, result)
    return result


def _scrape_links(url: str) -> dict[str, Any]:
    """Extract and categorize all links from a page."""
    body, status, headers = _fetch(url)

    from bs4 import BeautifulSoup
    soup = BeautifulSoup(body, "html.parser")

    links: list[dict] = []
    seen = set()

    for a in soup.find_all("a", href=True):
        href = a["href"]
        if href in seen or href.startswith("#") or href.startswith("javascript:"):
            continue
        seen.add(href)

        # Resolve relative URLs
        absolute = urljoin(url, href)
        parsed = urlparse(absolute)
        base_parsed = urlparse(url)

        category = "external"
        if parsed.netloc == base_parsed.netloc:
            category = "internal"
        elif parsed.scheme == "mailto":
            category = "email"

        text = a.get_text(strip=True)[:100]

        links.append({
            "url": absolute,
            "text": text,
            "category": category,
        })

    internal = [l for l in links if l["category"] == "internal"]
    external = [l for l in links if l["category"] == "external"]

    return {
        "source_url": url,
        "total_links": len(links),
        "internal": len(internal),
        "external": len(external),
        "links": links[:100],  # Cap at 100
    }


def _scrape_docs(url: str, max_depth: int = 2) -> dict[str, Any]:
    """Crawl a documentation site and save as local markdown."""
    max_depth = min(max_depth, 3)  # Hard cap at 3
    max_pages = 50

    visited: set[str] = set()
    pages: list[dict] = []
    queue: list[tuple[str, int]] = [(url, 0)]

    base_parsed = urlparse(url)

    while queue and len(visited) < max_pages:
        current_url, depth = queue.pop(0)

        if current_url in visited:
            continue
        if depth > max_depth:
            continue

        visited.add(current_url)

        try:
            body, status, _ = _fetch(current_url)
            if status != 200:
                continue

            content = _html_to_markdown(body)
            pages.append({
                "url": current_url,
                "depth": depth,
                "content": truncate_output(content, 3000),
                "content_length": len(content),
            })

            # Extract links for crawling
            if depth < max_depth:
                from bs4 import BeautifulSoup
                soup = BeautifulSoup(body, "html.parser")
                for a in soup.find_all("a", href=True):
                    href = urljoin(current_url, a["href"])
                    parsed = urlparse(href)
                    # Only follow same-domain links
                    if parsed.netloc == base_parsed.netloc and href not in visited:
                        # Strip fragments
                        clean = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
                        if clean not in visited:
                            queue.append((clean, depth + 1))

        except Exception:
            continue

    # Save to cache
    cache_key = _cache_key(f"docs:{url}")
    result = {
        "source_url": url,
        "pages_crawled": len(pages),
        "max_depth": max_depth,
        "pages": pages,
    }
    _cache_set(cache_key, result)

    return result


# --- Main runner ---

def run_scrape(
    subcommand: str,
    url: str,
    output_format: str = "json",
    depth: int = 2,
) -> None:
    """Route to the appropriate scrape subcommand."""
    fmt = OutputFormat(output_format)

    try:
        validate_url(url)
    except ValueError as e:
        result = make_result("scrape", Status.FAIL, str(e))
        emit(result, fmt)
        return

    try:
        if subcommand == "page":
            data = _scrape_page(url)
            status = Status.PASS if data.get("content") else Status.WARN
            result = make_result("scrape.page", status, f"Fetched {data.get('content_length', 0)} chars")
            result.update(data)

        elif subcommand == "api":
            data = _scrape_api(url)
            status = Status.FAIL if data.get("error") else Status.PASS
            result = make_result("scrape.api", status)
            result.update(data)

        elif subcommand == "links":
            data = _scrape_links(url)
            result = make_result("scrape.links", Status.INFO, f"{data['total_links']} links found")
            result.update(data)

        elif subcommand == "docs":
            data = _scrape_docs(url, max_depth=depth)
            result = make_result("scrape.docs", Status.INFO, f"Crawled {data['pages_crawled']} pages")
            result.update(data)

        else:
            result = make_result("scrape", Status.FAIL, f"Unknown subcommand: {subcommand}")

    except ImportError as e:
        result = make_result("scrape", Status.FAIL, f"Missing dependency: {e}. Run: pip install httpx beautifulsoup4")
    except Exception as e:
        result = make_result("scrape", Status.FAIL, f"Scrape error: {e}")

    emit(result, fmt)
