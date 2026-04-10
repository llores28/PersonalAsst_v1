"""
Research Tool — structured dependency and documentation research.

Subcommands:
  docs      — search project docs/ folder with relevance ranking
  deps      — fetch package info from registry APIs
  changelog — fetch recent changelog/release notes
  compare   — side-by-side package comparison
"""

import hashlib
import json
import os
import re
import time
from pathlib import Path
from typing import Any, Optional

from bootstrap.cli.utils import (
    OutputFormat, Status, emit, make_result, truncate_output, find_project_root,
)
from bootstrap.cli.security import validate_package_name, validate_url


# --- Cache ---

_CACHE_DIR = Path(".cache") / "research"
_CACHE_TTL = 86400  # 24 hours


def _cache_key(prefix: str, query: str) -> Path:
    """Generate a cache file path."""
    h = hashlib.sha256(query.encode()).hexdigest()[:16]
    return _CACHE_DIR / f"{prefix}_{h}.json"


def _cache_get(key: Path) -> Optional[dict]:
    """Read from cache if fresh."""
    try:
        if key.exists():
            mtime = key.stat().st_mtime
            if time.time() - mtime < _CACHE_TTL:
                return json.loads(key.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        pass
    return None


def _cache_set(key: Path, data: dict) -> None:
    """Write to cache."""
    try:
        key.parent.mkdir(parents=True, exist_ok=True)
        key.write_text(json.dumps(data, default=str), encoding="utf-8")
    except OSError:
        pass


# --- docs subcommand ---

def _search_docs(query: str, project_dir: Optional[Path] = None) -> dict[str, Any]:
    """Search project docs/ folder for relevant content."""
    root = project_dir or find_project_root()
    docs_dir = root / "docs"

    if not docs_dir.exists():
        return {"results": [], "message": "No docs/ directory found"}

    query_terms = [t.lower() for t in query.split() if len(t) > 2]
    results: list[dict] = []

    for fpath in docs_dir.rglob("*.md"):
        try:
            content = fpath.read_text(encoding="utf-8", errors="replace")
            lines = content.splitlines()

            # Score by term frequency
            content_lower = content.lower()
            score = sum(content_lower.count(term) for term in query_terms)

            if score > 0:
                # Find best matching section
                best_line = 0
                best_line_score = 0
                for i, line in enumerate(lines):
                    line_lower = line.lower()
                    line_score = sum(1 for t in query_terms if t in line_lower)
                    if line_score > best_line_score:
                        best_line_score = line_score
                        best_line = i

                # Extract context around best match
                start = max(0, best_line - 2)
                end = min(len(lines), best_line + 5)
                context = "\n".join(lines[start:end])

                results.append({
                    "file": str(fpath.relative_to(root)),
                    "score": score,
                    "best_line": best_line + 1,
                    "context": truncate_output(context, 300),
                })
        except OSError:
            continue

    results.sort(key=lambda r: r["score"], reverse=True)

    return {
        "query": query,
        "results": results[:10],
        "total_matches": len(results),
    }


# --- deps subcommand ---

def _fetch_pypi(package: str) -> dict[str, Any]:
    """Fetch package info from PyPI."""
    try:
        import httpx

        url = f"https://pypi.org/pypi/{package}/json"
        validate_url(url)

        resp = httpx.get(url, timeout=15, follow_redirects=True)
        if resp.status_code == 404:
            return {"error": f"Package '{package}' not found on PyPI"}
        resp.raise_for_status()

        data = resp.json()
        info = data.get("info", {})

        return {
            "registry": "pypi",
            "name": info.get("name"),
            "version": info.get("version"),
            "summary": info.get("summary"),
            "license": info.get("license"),
            "author": info.get("author"),
            "home_page": info.get("home_page") or info.get("project_url"),
            "requires_python": info.get("requires_python"),
            "last_updated": None,  # PyPI doesn't expose this directly in info
        }
    except ImportError:
        return {"error": "httpx not installed — run: pip install httpx"}
    except Exception as e:
        return {"error": str(e)}


def _fetch_npm(package: str) -> dict[str, Any]:
    """Fetch package info from npm registry."""
    try:
        import httpx

        url = f"https://registry.npmjs.org/{package}"
        validate_url(url)

        resp = httpx.get(url, timeout=15, follow_redirects=True)
        if resp.status_code == 404:
            return {"error": f"Package '{package}' not found on npm"}
        resp.raise_for_status()

        data = resp.json()
        latest = data.get("dist-tags", {}).get("latest", "")
        latest_info = data.get("versions", {}).get(latest, {})

        return {
            "registry": "npm",
            "name": data.get("name"),
            "version": latest,
            "description": data.get("description"),
            "license": latest_info.get("license") or data.get("license"),
            "author": data.get("author", {}).get("name") if isinstance(data.get("author"), dict) else data.get("author"),
            "homepage": latest_info.get("homepage") or data.get("homepage"),
            "last_updated": data.get("time", {}).get(latest),
        }
    except ImportError:
        return {"error": "httpx not installed — run: pip install httpx"}
    except Exception as e:
        return {"error": str(e)}


def _research_deps(package: str) -> dict[str, Any]:
    """Fetch package info from the best-guess registry."""
    validate_package_name(package)

    cache_key = _cache_key("deps", package)
    cached = _cache_get(cache_key)
    if cached:
        cached["cached"] = True
        return cached

    # Try PyPI first, then npm
    result = _fetch_pypi(package)
    if result.get("error") and "not found" in result["error"].lower():
        result = _fetch_npm(package)

    if not result.get("error"):
        _cache_set(cache_key, result)

    return result


# --- changelog subcommand ---

def _research_changelog(package: str) -> dict[str, Any]:
    """Fetch recent changelog / release notes."""
    validate_package_name(package)

    cache_key = _cache_key("changelog", package)
    cached = _cache_get(cache_key)
    if cached:
        cached["cached"] = True
        return cached

    try:
        import httpx

        # Try GitHub releases via npm/PyPI metadata
        dep_info = _research_deps(package)
        homepage = dep_info.get("homepage") or dep_info.get("home_page") or ""

        # If GitHub, try releases API
        gh_match = re.search(r"github\.com/([^/]+/[^/]+)", homepage)
        if gh_match:
            repo = gh_match.group(1).rstrip("/")
            url = f"https://api.github.com/repos/{repo}/releases?per_page=5"
            validate_url(url)

            resp = httpx.get(url, timeout=15, follow_redirects=True)
            if resp.status_code == 200:
                releases = resp.json()
                entries = []
                for r in releases[:5]:
                    entries.append({
                        "tag": r.get("tag_name"),
                        "name": r.get("name"),
                        "date": r.get("published_at"),
                        "body": truncate_output(r.get("body", ""), 300),
                    })
                result = {
                    "package": package,
                    "source": f"github:{repo}",
                    "releases": entries,
                }
                _cache_set(cache_key, result)
                return result

        return {
            "package": package,
            "message": "Could not find changelog. Try checking the package homepage.",
            "homepage": homepage,
        }
    except ImportError:
        return {"error": "httpx not installed — run: pip install httpx"}
    except Exception as e:
        return {"error": str(e)}


# --- compare subcommand ---

def _research_compare(pkg_a: str, pkg_b: str) -> dict[str, Any]:
    """Side-by-side comparison of two packages."""
    info_a = _research_deps(pkg_a)
    info_b = _research_deps(pkg_b)

    return {
        "comparison": [
            {"field": "name", "a": info_a.get("name", pkg_a), "b": info_b.get("name", pkg_b)},
            {"field": "version", "a": info_a.get("version", "?"), "b": info_b.get("version", "?")},
            {"field": "license", "a": info_a.get("license", "?"), "b": info_b.get("license", "?")},
            {"field": "registry", "a": info_a.get("registry", "?"), "b": info_b.get("registry", "?")},
            {"field": "description", "a": truncate_output(info_a.get("summary") or info_a.get("description") or "?", 100), "b": truncate_output(info_b.get("summary") or info_b.get("description") or "?", 100)},
        ],
        "a_details": info_a,
        "b_details": info_b,
    }


# --- Main runner ---

def run_research(
    subcommand: str,
    args: tuple = (),
    output_format: str = "json",
) -> None:
    """Route to the appropriate research subcommand."""
    fmt = OutputFormat(output_format)

    if subcommand == "docs":
        if not args:
            result = make_result("research.docs", Status.FAIL, "Usage: research docs <query>")
            emit(result, fmt)
            return
        query = " ".join(args)
        data = _search_docs(query)
        status = Status.INFO if data["results"] else Status.WARN
        result = make_result("research.docs", status, f"{len(data['results'])} result(s)")
        result.update(data)

    elif subcommand == "deps":
        if not args:
            result = make_result("research.deps", Status.FAIL, "Usage: research deps <package>")
            emit(result, fmt)
            return
        data = _research_deps(args[0])
        status = Status.FAIL if data.get("error") else Status.INFO
        result = make_result("research.deps", status)
        result["package_info"] = data

    elif subcommand == "changelog":
        if not args:
            result = make_result("research.changelog", Status.FAIL, "Usage: research changelog <package>")
            emit(result, fmt)
            return
        data = _research_changelog(args[0])
        status = Status.FAIL if data.get("error") else Status.INFO
        result = make_result("research.changelog", status)
        result.update(data)

    elif subcommand == "compare":
        if len(args) < 2:
            result = make_result("research.compare", Status.FAIL, "Usage: research compare <pkg-a> <pkg-b>")
            emit(result, fmt)
            return
        data = _research_compare(args[0], args[1])
        result = make_result("research.compare", Status.INFO, f"Comparing {args[0]} vs {args[1]}")
        result.update(data)

    else:
        result = make_result("research", Status.FAIL, f"Unknown subcommand: {subcommand}")

    emit(result, fmt)
