"""LinkedIn function-type tool — profile, search, jobs, messaging, posts.

Uses the unofficial ``linkedin-api`` package (Voyager endpoints).
Credentials are loaded from the credential vault at first use.

Exposes ``tool_functions`` list for the ToolRegistry multi-tool loader.
"""

import json
import logging
from typing import Optional

from agents import function_tool

logger = logging.getLogger(__name__)

# Lazy-initialized LinkedIn client (singleton per process)
_client = None
_client_error: Optional[str] = None


def _build_cookie_jar(li_at: str, jsessionid: str):
    """Build a requests CookieJar from LinkedIn session cookies."""
    from requests.cookies import RequestsCookieJar
    cookies = RequestsCookieJar()
    cookies.set("li_at", li_at, domain=".linkedin.com", path="/")
    cookies.set("JSESSIONID", jsessionid, domain=".linkedin.com", path="/")
    return cookies


async def _get_client():
    """Lazy-init the LinkedIn client from credential vault.

    Auth flow (user only provides email + password):
    1. Check vault for cached session cookies (li_at, JSESSIONID).
       If found → authenticate with cookies (fast, no CHALLENGE).
    2. If no cached cookies → use Playwright browser to automate login,
       extract li_at + JSESSIONID, store in vault, then authenticate.
    3. If email/password also missing → error with setup instructions.
    """
    global _client, _client_error

    if _client is not None:
        return _client
    if _client_error is not None:
        raise RuntimeError(_client_error)

    try:
        from linkedin_api import Linkedin
        from src.tools.credentials import get_credentials

        creds = await get_credentials("linkedin")
        li_at = creds.get("li_at", "")
        jsessionid = creds.get("JSESSIONID", "")
        email = creds.get("linkedin_email", "")
        password = creds.get("linkedin_password", "")

        # Step 1: Try cached cookies
        if li_at and jsessionid:
            cookies = _build_cookie_jar(li_at, jsessionid)
            _client = Linkedin("", "", cookies=cookies)
            logger.info("LinkedIn client authenticated via cached cookies")
            return _client

        # Step 2: Need email + password to acquire cookies
        if not email or not password:
            _client_error = (
                "LinkedIn credentials not configured. Run:\n"
                "  /tools credentials set linkedin linkedin_email <your_email>\n"
                "  /tools credentials set linkedin linkedin_password <your_password>"
            )
            raise RuntimeError(_client_error)

        # Step 3: Try direct email/password auth first (fastest)
        try:
            _client = Linkedin(email, password)
            logger.info("LinkedIn client authenticated via email/password for %s", email)
            return _client
        except Exception as direct_err:
            err_msg = str(direct_err).upper()
            if "CHALLENGE" not in err_msg:
                # Real auth error (bad password, network, etc.)
                _client_error = f"LinkedIn auth failed: {direct_err}"
                raise RuntimeError(_client_error)
            logger.warning(
                "LinkedIn CHALLENGE detected — falling back to browser login for %s",
                email,
            )

        # Step 4: Browser-based cookie acquisition via crawl4ai (handles CHALLENGE)
        try:
            from src.tools.web_auth import authenticated_scrape
            # Trigger a lightweight authenticated scrape — this logs in and
            # caches li_at + JSESSIONID in the credential vault automatically.
            result = await authenticated_scrape(
                target_url="https://www.linkedin.com/feed/",
                site="linkedin",
                username=email,
                password=password,
                scroll=False,
            )
            if not result.success:
                raise RuntimeError(f"Browser login failed: {result.error}")

            # Read cached cookies back from the vault
            creds = await get_credentials("linkedin")
            li_at = creds.get("li_at", "")
            jsessionid = creds.get("JSESSIONID", "")

            if not li_at or not jsessionid:
                _client_error = (
                    "Browser login succeeded but session cookies not found. "
                    "LinkedIn may require manual verification."
                )
                raise RuntimeError(_client_error)

            cookies = _build_cookie_jar(li_at, jsessionid)
            _client = Linkedin("", "", cookies=cookies)
            logger.info("LinkedIn client authenticated via browser-acquired cookies")
            return _client

        except RuntimeError:
            raise
        except Exception as browser_err:
            _client_error = (
                f"Both direct and browser login failed.\n"
                f"Direct: CHALLENGE\n"
                f"Browser: {browser_err}\n\n"
                f"Try logging into LinkedIn manually in your browser first, "
                f"then retry."
            )
            raise RuntimeError(_client_error)

    except ImportError:
        _client_error = "linkedin-api package not installed. Add linkedin-api>=2.0.0 to requirements."
        raise RuntimeError(_client_error)
    except RuntimeError:
        raise
    except Exception as e:
        if _client_error is None:
            _client_error = f"LinkedIn auth failed: {e}"
        raise RuntimeError(_client_error)


def _reset_client():
    """Reset the client so next call re-authenticates."""
    global _client, _client_error
    _client = None
    _client_error = None


def _safe_json(obj: object, max_len: int = 4000) -> str:
    """Serialize to JSON, truncate if too long."""
    try:
        text = json.dumps(obj, indent=2, default=str)
    except (TypeError, ValueError):
        text = str(obj)
    if len(text) > max_len:
        text = text[:max_len] + "\n... (truncated)"
    return text


# ── Profile Tools ────────────────────────────────────────────────


async def _get_profile_impl(public_id: str) -> str:
    """Core implementation for getting a LinkedIn profile."""
    try:
        api = await _get_client()
        profile = api.get_profile(public_id)
        contact = api.get_profile_contact_info(public_id)
        skills = api.get_profile_skills(public_id)

        result = {
            "name": f"{profile.get('firstName', '')} {profile.get('lastName', '')}".strip(),
            "headline": profile.get("headline", ""),
            "summary": profile.get("summary", ""),
            "location": profile.get("locationName", ""),
            "industry": profile.get("industryName", ""),
            "experience": [
                {
                    "title": exp.get("title", ""),
                    "company": exp.get("companyName", ""),
                    "description": exp.get("description", ""),
                }
                for exp in profile.get("experience", [])[:5]
            ],
            "education": [
                {
                    "school": edu.get("schoolName", ""),
                    "degree": edu.get("degreeName", ""),
                    "field": edu.get("fieldOfStudy", ""),
                }
                for edu in profile.get("education", [])[:5]
            ],
            "skills": [s.get("name", "") for s in (skills or [])[:15]],
            "contact": {
                "email": contact.get("email_address", ""),
                "websites": contact.get("websites", []),
            },
        }
        return _safe_json(result)
    except Exception as e:
        return f"Error getting profile: {e}"


@function_tool
async def linkedin_get_profile(public_id: str) -> str:
    """Get a LinkedIn profile by public ID (the slug in their profile URL).

    Args:
        public_id: LinkedIn public profile ID (e.g. 'john-doe-123456').
    """
    return await _get_profile_impl(public_id)


async def _get_my_profile_impl() -> str:
    """Core implementation for getting own profile with full details.

    Uses crawl4ai generic authenticated scraper — logs in via Playwright,
    scrapes the profile page, and returns LLM-ready markdown.
    No per-site custom DOM parsing needed.
    """
    try:
        from src.tools.web_auth import scrape_linkedin_profile

        result = await scrape_linkedin_profile()

        if not result.success:
            return f"Error getting own profile: {result.error}"

        if not result.markdown.strip():
            return "Profile page was scraped but no content was extracted. The page may require manual login first."

        return result.markdown

    except Exception as e:
        return f"Error getting own profile: {e}"


@function_tool
async def linkedin_get_my_profile() -> str:
    """Get the authenticated user's own LinkedIn profile."""
    return await _get_my_profile_impl()


async def _get_profile_views_impl() -> str:
    try:
        api = await _get_client()
        views = api.get_current_profile_views()
        return _safe_json(views)
    except Exception as e:
        return f"Error getting profile views: {e}"


@function_tool
async def linkedin_get_profile_views() -> str:
    """Get view statistics for the authenticated user's profile."""
    return await _get_profile_views_impl()


# ── Search Tools ─────────────────────────────────────────────────


async def _search_people_impl(keywords: str, limit: int = 10) -> str:
    try:
        api = await _get_client()
        limit = min(max(limit, 1), 50)
        results = api.search_people(
            keywords=keywords,
            limit=limit,
        )
        people = []
        for p in results[:limit]:
            people.append({
                "name": p.get("name", ""),
                "headline": p.get("jobtitle", ""),
                "location": p.get("location", ""),
                "public_id": p.get("public_id", ""),
                "urn_id": p.get("urn_id", ""),
            })
        return _safe_json({"count": len(people), "results": people})
    except Exception as e:
        return f"Error searching people: {e}"


@function_tool
async def linkedin_search_people(
    keywords: str,
    limit: int = 10,
) -> str:
    """Search for people on LinkedIn.

    Args:
        keywords: Search keywords (e.g. 'software engineer San Francisco').
        limit: Maximum number of results (default 10, max 50).
    """
    return await _search_people_impl(keywords, limit)


async def _search_jobs_impl(keywords: str, location: str = "", limit: int = 10) -> str:
    try:
        api = await _get_client()
        limit = min(max(limit, 1), 50)
        results = api.search_jobs(
            keywords=keywords,
            location_name=location if location else None,
            limit=limit,
        )
        jobs = []
        for j in results[:limit]:
            jobs.append({
                "title": j.get("title", ""),
                "company": j.get("companyName", ""),
                "location": j.get("formattedLocation", ""),
                "job_id": j.get("dashEntityUrn", "").split(":")[-1] if j.get("dashEntityUrn") else "",
                "listed_at": j.get("listedAt", ""),
            })
        return _safe_json({"count": len(jobs), "results": jobs})
    except Exception as e:
        return f"Error searching jobs: {e}"


@function_tool
async def linkedin_search_jobs(
    keywords: str,
    location: str = "",
    limit: int = 10,
) -> str:
    """Search for jobs on LinkedIn.

    Args:
        keywords: Job search keywords (e.g. 'python developer').
        location: Location filter (e.g. 'New York').
        limit: Maximum number of results (default 10, max 50).
    """
    return await _search_jobs_impl(keywords, location, limit)


async def _get_job_impl(job_id: str) -> str:
    try:
        api = await _get_client()
        job = api.get_job(job_id)
        result = {
            "title": job.get("title", ""),
            "company": job.get("companyDetails", {})
                .get("com.linkedin.voyager.deco.jobs.web.shared.model.JobPostingCompany", {})
                .get("companyResolutionResult", {})
                .get("name", ""),
            "description": job.get("description", {}).get("text", ""),
            "location": job.get("formattedLocation", ""),
            "work_remote": job.get("workRemoteAllowed", False),
            "applies": job.get("applies", 0),
            "views": job.get("views", 0),
        }
        return _safe_json(result)
    except Exception as e:
        return f"Error getting job details: {e}"


@function_tool
async def linkedin_get_job(job_id: str) -> str:
    """Get details about a specific LinkedIn job posting.

    Args:
        job_id: LinkedIn job ID (numeric string from search results).
    """
    return await _get_job_impl(job_id)


# ── Messaging Tools ──────────────────────────────────────────────


async def _get_conversations_impl(limit: int = 10) -> str:
    try:
        api = await _get_client()
        convos = api.get_conversations()
        limit = min(max(limit, 1), 50)
        results = []
        elements = convos.get("elements", []) if isinstance(convos, dict) else convos
        for c in elements[:limit]:
            participants = []
            for p in c.get("participants", []):
                mini = p.get("com.linkedin.voyager.messaging.MessagingMember", {}).get("miniProfile", {})
                participants.append(
                    f"{mini.get('firstName', '')} {mini.get('lastName', '')}".strip()
                )
            results.append({
                "conversation_id": c.get("entityUrn", "").split(":")[-1],
                "participants": participants,
                "last_activity": c.get("lastActivityAt", ""),
            })
        return _safe_json({"count": len(results), "conversations": results})
    except Exception as e:
        return f"Error getting conversations: {e}"


@function_tool
async def linkedin_get_conversations(limit: int = 10) -> str:
    """Get recent LinkedIn message conversations.

    Args:
        limit: Maximum number of conversations (default 10, max 50).
    """
    return await _get_conversations_impl(limit)


async def _send_message_impl(recipient_public_id: str, message: str) -> str:
    try:
        api = await _get_client()
        profile = api.get_profile(recipient_public_id)
        profile_urn = profile.get("profile_id") or profile.get("entityUrn", "").split(":")[-1]

        if not profile_urn:
            return f"Could not resolve profile URN for '{recipient_public_id}'"

        err = api.send_message(
            message_body=message,
            recipients=[profile_urn],
        )
        if err:
            return f"Failed to send message to {recipient_public_id}"
        return f"Message sent to {recipient_public_id} successfully."
    except Exception as e:
        return f"Error sending message: {e}"


@function_tool
async def linkedin_send_message(
    recipient_public_id: str,
    message: str,
) -> str:
    """Send a LinkedIn message to someone.

    Args:
        recipient_public_id: Recipient's LinkedIn public ID (e.g. 'john-doe-123').
        message: Message text to send.
    """
    return await _send_message_impl(recipient_public_id, message)


# ── Posting Tools ────────────────────────────────────────────────


async def _create_post_impl(text: str) -> str:
    try:
        api = await _get_client()
        result = api.post(text)
        if result:
            return _safe_json({"status": "posted", "detail": str(result)})
        return "Post created successfully on LinkedIn."
    except AttributeError:
        return (
            "Posting is not supported by the current linkedin-api version. "
            "This feature may require a newer version or direct Voyager call."
        )
    except Exception as e:
        return f"Error creating post: {e}"


@function_tool
async def linkedin_create_post(
    text: str,
) -> str:
    """Create a new LinkedIn post (text only).

    Args:
        text: The post content text.
    """
    return await _create_post_impl(text)


# ── Connection Tools ─────────────────────────────────────────────


async def _get_invitations_impl() -> str:
    try:
        api = await _get_client()
        invitations = api.get_invitations()
        results = []
        for inv in (invitations or [])[:20]:
            from_profile = inv.get("fromMember", {})
            results.append({
                "name": f"{from_profile.get('firstName', '')} {from_profile.get('lastName', '')}".strip(),
                "headline": from_profile.get("occupation", ""),
                "invitation_urn": inv.get("entityUrn", ""),
                "shared_secret": inv.get("sharedSecret", ""),
            })
        return _safe_json({"count": len(results), "invitations": results})
    except Exception as e:
        return f"Error getting invitations: {e}"


@function_tool
async def linkedin_get_invitations() -> str:
    """Get pending LinkedIn connection invitations."""
    return await _get_invitations_impl()


# ── Generic Scraping Tool ────────────────────────────────────────


async def _scrape_page_impl(url: str) -> str:
    """Scrape any LinkedIn page into LLM-ready markdown."""
    try:
        from src.tools.web_auth import authenticated_scrape

        result = await authenticated_scrape(
            target_url=url,
            site="linkedin",
            scroll=True,
        )

        if not result.success:
            return f"Error scraping {url}: {result.error}"

        if not result.markdown.strip():
            return f"Page at {url} was loaded but no content was extracted."

        return result.markdown

    except Exception as e:
        return f"Error scraping page: {e}"


@function_tool
async def linkedin_scrape_page(url: str) -> str:
    """Scrape any LinkedIn page into readable text. Works for profiles,
    job postings, company pages, articles, etc.

    Args:
        url: Full LinkedIn URL to scrape (e.g. 'https://www.linkedin.com/in/john-doe/').
    """
    return await _scrape_page_impl(url)


# ── Exported tool list for ToolRegistry ──────────────────────────

tool_functions = [
    linkedin_get_profile,
    linkedin_get_my_profile,
    linkedin_get_profile_views,
    linkedin_search_people,
    linkedin_search_jobs,
    linkedin_get_job,
    linkedin_get_conversations,
    linkedin_send_message,
    linkedin_create_post,
    linkedin_get_invitations,
    linkedin_scrape_page,
]
