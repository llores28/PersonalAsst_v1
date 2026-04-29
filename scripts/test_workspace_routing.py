"""Friendly CLI runner for the Workspace routing harness.

Drives the same routing tests as ``pytest tests/test_workspace_routing_harness.py``
but with a compact terminal table so you can eyeball regressions at a
glance instead of scrolling through pytest output.

Usage:
    # Fast, mock-only routing checks (default)
    python scripts/test_workspace_routing.py

    # Add live, sandboxed probes against the connected workspace
    LIVE_WORKSPACE_TEST=1 LIVE_WORKSPACE_EMAIL=you@gmail.com \\
        python scripts/test_workspace_routing.py --live

    # Run only one section
    python scripts/test_workspace_routing.py --only gmail

Inside the assistant container (where the MCP creds + Redis already exist):

    docker compose exec -w /app assistant python scripts/test_workspace_routing.py
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
from typing import Any, Awaitable, Callable
from unittest.mock import MagicMock

# ── Path + lightweight import bootstrap ────────────────────────────────
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# Provide sane defaults for required settings so the script runs outside
# the container too. These are obvious test stubs and never reach a real
# service when the deterministic harness is active. The live path uses
# real env vars from the user's shell.
os.environ.setdefault("OPENAI_API_KEY", "sk-test-routing-harness")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "123456:routing-harness")
os.environ.setdefault("OWNER_TELEGRAM_ID", "999999999")
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://x:x@localhost/x")
os.environ.setdefault("DB_PASSWORD", "x")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("QDRANT_URL", "http://localhost:6333")

# Stub the agents SDK before importing the orchestrator IFF it isn't already
# installed (e.g., running this script outside the assistant container).
# Inside the container the real SDK is present — stubbing there would break
# the live path because ``call_workspace_tool`` actually instantiates
# ``MCPServerStreamableHttp``.
try:
    import agents  # type: ignore  # noqa: F401
    import agents.mcp  # type: ignore  # noqa: F401
except ImportError:
    fake_agents = MagicMock()
    fake_agents.Agent = MagicMock
    fake_agents.function_tool = lambda *a, **kw: (lambda f: f) if (a and not callable(a[0])) else (a[0] if a else (lambda f: f))
    fake_agents.Runner = MagicMock()
    fake_agents.WebSearchTool = MagicMock
    sys.modules["agents"] = fake_agents
    sys.modules["agents.mcp"] = MagicMock()


# ── Test cases — kept in lockstep with tests/test_workspace_routing_harness.py
GMAIL_CASES: list[tuple[str, str]] = [
    ("what was my last email i got today", "search_gmail_messages"),
    ("check my unread emails", "search_gmail_messages"),
    ("show my latest email", "search_gmail_messages"),
    ("what's in my inbox", "search_gmail_messages"),
    ("do i have any new mail today?", "search_gmail_messages"),
]

CALENDAR_CASES: list[tuple[str, str]] = [
    ("what is on my calendar today", "get_events"),
    ("what's on my schedule tomorrow", "get_events"),
    ("what is on my calendar this morning", "get_events"),
]

TASKS_CASES: list[tuple[str, str]] = [
    ("list my tasks", "list_tasks"),
    ("what are my tasks", "list_tasks"),
    ("show my todo list", "list_tasks"),
]

NON_SHORTCIRCUIT_CASES: list[str] = [
    "hello",
    "what is the weather today",
    "I sent an email to bob earlier",
    "create a new spreadsheet",
    "find a file in my drive",
]

SKILL_ROUTING_CASES: list[tuple[str, str]] = [
    ("what was my last email i got today", "gmail"),
    ("check my unread email", "gmail"),
    ("what is on my calendar today", "calendar"),
    ("schedule a meeting tomorrow at 3pm", "calendar"),
    ("list my tasks", "google_tasks"),
    ("find a file in my drive", "drive"),
    ("create a new google doc", "google_docs"),
    ("update my budget spreadsheet", "google_sheets"),
    ("make a slide deck about our roadmap", "google_slides"),
    ("look up phone number for jane in my contacts", "google_contacts"),
]


TEST_EMAIL = "atlas-test@example.com"
TEST_USER_ID = 999_999_999


# ── ANSI color helpers (cheap, no external dep) ─────────────────────────
def _ansi(code: str, text: str) -> str:
    return f"\033[{code}m{text}\033[0m" if sys.stdout.isatty() else text


GREEN = lambda s: _ansi("32", s)
RED = lambda s: _ansi("31", s)
YELLOW = lambda s: _ansi("33", s)
DIM = lambda s: _ansi("2", s)
BOLD = lambda s: _ansi("1", s)


class Reporter:
    def __init__(self) -> None:
        self.passed = 0
        self.failed = 0
        self.failures: list[tuple[str, str]] = []
        self.t0 = time.monotonic()

    def section(self, title: str) -> None:
        print(f"\n{BOLD(title)}")
        # ASCII-only divider — Windows cp1252 console can't encode U+2500.
        print(DIM("-" * len(title)))

    def case(self, name: str, ok: bool, detail: str = "") -> None:
        tag = GREEN("PASS") if ok else RED("FAIL")
        line = f"  {tag}  {name}"
        if detail and not ok:
            line += DIM(f"  — {detail}")
        print(line)
        if ok:
            self.passed += 1
        else:
            self.failed += 1
            self.failures.append((name, detail))

    def summary(self) -> int:
        elapsed = time.monotonic() - self.t0
        total = self.passed + self.failed
        print()
        if self.failed == 0:
            print(BOLD(GREEN(f"All {total} checks passed")) + DIM(f" in {elapsed:.2f}s"))
            return 0
        print(BOLD(RED(f"{self.failed} of {total} checks failed")) + DIM(f" in {elapsed:.2f}s"))
        for name, detail in self.failures:
            print(RED(f"  • {name}"))
            if detail:
                print(DIM(f"    {detail}"))
        return 1


# ── Mock harness for the deterministic layer ────────────────────────────


def _install_workspace_mocks() -> list[tuple[str, dict[str, Any]]]:
    """Patch the orchestrator's external touchpoints. Returns the call log."""
    from src.agents import orchestrator
    from src.memory import conversation as conv

    calls: list[tuple[str, dict[str, Any]]] = []

    async def fake_call(tool_name: str, args: dict[str, Any]) -> str:
        calls.append((tool_name, args))
        if tool_name == "search_gmail_messages":
            return "Found 0 messages"
        if tool_name == "get_events":
            return "No events found"
        if tool_name == "list_tasks":
            return "0 tasks"
        return "OK"

    async def fake_email(_user_id: int) -> str:
        return TEST_EMAIL

    async def fake_pending_task(_user_id: int):
        return None

    async def fake_history(_user_id: int):
        return []

    async def fake_noop(*_a, **_kw):
        return None

    orchestrator.call_workspace_tool = fake_call  # type: ignore[assignment]
    orchestrator.get_connected_google_email = fake_email  # type: ignore[assignment]
    conv.get_pending_google_task = fake_pending_task  # type: ignore[assignment]
    conv.store_pending_google_task = fake_noop  # type: ignore[assignment]
    conv.clear_pending_google_task = fake_noop  # type: ignore[assignment]
    conv.get_conversation_history = fake_history  # type: ignore[assignment]
    conv.cache_task_list = fake_noop  # type: ignore[assignment]
    conv.get_cached_task_list = fake_noop  # type: ignore[assignment]
    return calls


# ── Section runners ────────────────────────────────────────────────────


async def _run_gmail_section(report: Reporter, calls: list) -> None:
    from src.agents.orchestrator import _maybe_handle_connected_gmail_check

    report.section("Gmail short-circuit")
    for msg, expected_tool in GMAIL_CASES:
        calls.clear()
        result = await _maybe_handle_connected_gmail_check(TEST_USER_ID, msg)
        if result is None:
            report.case(msg, False, "short-circuit returned None — would fall through to LLM")
            continue
        if not calls:
            report.case(msg, False, "no MCP call recorded")
            continue
        first_tool, first_args = calls[0]
        if first_tool != expected_tool:
            report.case(msg, False, f"expected {expected_tool!r}, got {first_tool!r}")
            continue
        if first_args.get("user_google_email") != TEST_EMAIL:
            report.case(msg, False, f"missing/wrong user_google_email in {first_args}")
            continue
        report.case(msg, True)


async def _run_calendar_section(report: Reporter, calls: list) -> None:
    from src.agents.orchestrator import _maybe_handle_connected_calendar_check

    report.section("Calendar short-circuit")
    for msg, expected_tool in CALENDAR_CASES:
        calls.clear()
        result = await _maybe_handle_connected_calendar_check(TEST_USER_ID, msg)
        if result is None:
            report.case(msg, False, "short-circuit returned None")
            continue
        if not calls or calls[0][0] != expected_tool:
            report.case(msg, False, f"expected {expected_tool!r}, calls={[c[0] for c in calls]}")
            continue
        report.case(msg, True)


async def _run_tasks_section(report: Reporter, calls: list) -> None:
    from src.agents.orchestrator import _maybe_handle_connected_google_tasks_flow

    report.section("Google Tasks short-circuit")
    for msg, expected_tool in TASKS_CASES:
        calls.clear()
        result = await _maybe_handle_connected_google_tasks_flow(TEST_USER_ID, msg)
        if result is None:
            report.case(msg, False, "short-circuit returned None")
            continue
        if not calls or calls[0][0] != expected_tool:
            report.case(msg, False, f"expected {expected_tool!r}, calls={[c[0] for c in calls]}")
            continue
        report.case(msg, True)


async def _run_no_false_positives(report: Reporter, calls: list) -> None:
    from src.agents.orchestrator import (
        _maybe_handle_connected_gmail_check,
        _maybe_handle_connected_google_tasks_flow,
    )

    report.section("No false positives (non-workspace messages)")
    for msg in NON_SHORTCIRCUIT_CASES:
        calls.clear()
        gmail = await _maybe_handle_connected_gmail_check(TEST_USER_ID, msg)
        tasks = await _maybe_handle_connected_google_tasks_flow(TEST_USER_ID, msg)
        if gmail is not None:
            report.case(msg, False, "Gmail short-circuit fired — would steal a non-workspace turn")
            continue
        if tasks is not None:
            report.case(msg, False, "Tasks short-circuit fired — would steal a non-workspace turn")
            continue
        report.case(msg, True)


def _run_skill_routing(report: Reporter) -> None:
    from src.skills.google_workspace import (
        build_calendar_skill,
        build_contacts_skill,
        build_docs_skill,
        build_drive_skill,
        build_gmail_skill,
        build_sheets_skill,
        build_slides_skill,
        build_tasks_skill,
    )
    from src.skills.registry import SkillRegistry

    reg = SkillRegistry()
    for builder in (
        build_gmail_skill, build_calendar_skill, build_tasks_skill, build_drive_skill,
        build_docs_skill, build_sheets_skill, build_slides_skill, build_contacts_skill,
    ):
        reg.register(builder(TEST_EMAIL))

    report.section("SkillRegistry routing")
    for msg, expected_skill in SKILL_ROUTING_CASES:
        matched = reg.match_skills(msg)
        if expected_skill not in matched:
            report.case(msg, False, f"expected {expected_skill!r} in matched={sorted(matched)}")
            continue
        report.case(msg, True, f"matched={sorted(matched)}")


# ── Live section ───────────────────────────────────────────────────────


async def _run_live_smoke(report: Reporter) -> None:
    """Tiny live probe — just assert that workspace-mcp answers a benign
    read query for each surface. Detailed live tests live in
    ``tests/integration/test_live_workspace_smoke.py``; run them with
    pytest when you want full coverage.
    """
    email = os.environ.get("LIVE_WORKSPACE_EMAIL", "").strip()
    if not email:
        report.section("Live workspace probes (skipped)")
        report.case("LIVE_WORKSPACE_EMAIL not set", False, "set LIVE_WORKSPACE_EMAIL=you@gmail.com")
        return

    from src.integrations.workspace_mcp import call_workspace_tool
    from datetime import datetime, timedelta, timezone

    report.section(f"Live workspace probes (email={email})")

    async def probe(name: str, tool: str, args: dict[str, Any]) -> None:
        try:
            res = await call_workspace_tool(tool, args)
        except Exception as exc:  # noqa: BLE001
            report.case(name, False, f"raised: {exc}")
            return
        if "[AUTH ERROR]" in res:
            report.case(name, False, "auth error — run /connect google again")
            return
        if "[CONNECTION ERROR]" in res:
            report.case(name, False, "workspace-mcp sidecar unreachable")
            return
        report.case(name, True, f"{len(res)} bytes")

    now = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    tomorrow_start = today_start + timedelta(days=1)

    await probe(
        "Gmail - search label:atlas-test",
        "search_gmail_messages",
        {"query": "label:atlas-test", "user_google_email": email, "page_size": 1},
    )
    await probe(
        "Calendar - get_events today",
        "get_events",
        {
            "user_google_email": email, "calendar_id": "primary",
            "time_min": today_start.isoformat(), "time_max": tomorrow_start.isoformat(),
            "max_results": 5, "detailed": False,
        },
    )
    await probe(
        "Tasks - list @default (open)",
        "list_tasks",
        {"user_google_email": email, "task_list_id": "@default",
         "show_completed": False, "max_results": 5},
    )
    await probe(
        "Drive - search Atlas-Test folder",
        "search_drive_files",
        {"user_google_email": email,
         "query": "name = 'Atlas-Test' and mimeType = 'application/vnd.google-apps.folder'",
         "page_size": 1},
    )


# ── Entrypoint ─────────────────────────────────────────────────────────


async def _main_async(args: argparse.Namespace) -> int:
    report = Reporter()
    sections = set(args.only) if args.only else {"gmail", "calendar", "tasks", "false-pos", "skill"}

    calls = _install_workspace_mocks()

    print(BOLD("Atlas - Workspace routing harness"))
    print(DIM(f"connected_email={TEST_EMAIL}  user_id={TEST_USER_ID}  (mock)"))

    if "gmail" in sections:
        await _run_gmail_section(report, calls)
    if "calendar" in sections:
        await _run_calendar_section(report, calls)
    if "tasks" in sections:
        await _run_tasks_section(report, calls)
    if "false-pos" in sections:
        await _run_no_false_positives(report, calls)
    if "skill" in sections:
        _run_skill_routing(report)

    if args.live:
        await _run_live_smoke(report)

    return report.summary()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--only",
        nargs="+",
        choices=["gmail", "calendar", "tasks", "false-pos", "skill"],
        help="Run only the named section(s).",
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="Also run live, sandboxed probes against the connected workspace. "
             "Requires LIVE_WORKSPACE_EMAIL to be set.",
    )
    args = parser.parse_args()
    return asyncio.run(_main_async(args))


if __name__ == "__main__":
    sys.exit(main())
