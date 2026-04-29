"""Tests for voice-mode summaries and TTS text cleanup.

Background: when a user sends a voice message, ``handlers.py`` sets
``wants_audio_reply=true`` on the session and the reply is also synthesized
as TTS audio. Two failure modes have shown up in production:

1. The deterministic calendar / Gmail short-circuits emit the long numbered
   "1) Date: ... Time: ... Event: ..." block, which is ~4-8x longer than
   appropriate for a spoken reply (voice-UX research suggests 75-150 words
   ≈ 30-60s of audio). Voice users get a 3-minute drone instead of a quick
   summary.

2. When the raw Google Calendar event description contains HTML (Zoom and
   Meet auto-invites are full of ``<p>``, ``<br/>``, and a URL/Meeting ID
   block), the formatter and TTS cleaner each pass it through verbatim —
   the TTS engine reads "less than P greater than" out loud and recites
   the meeting ID digit-by-digit. Brutal.

These tests pin both fixes.
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock

import pytest

if "agents" not in sys.modules:
    fake_agents = MagicMock()
    fake_agents.Agent = MagicMock
    fake_agents.function_tool = lambda *a, **kw: (lambda f: f) if (a and not callable(a[0])) else (a[0] if a else (lambda f: f))
    fake_agents.Runner = MagicMock()
    fake_agents.WebSearchTool = MagicMock
    sys.modules["agents"] = fake_agents
    sys.modules["agents.mcp"] = MagicMock()


# Real-world ``get_events`` payload shape (lines from the workspace MCP),
# including the HTML-laden Zoom invite description that broke voice replies
# on 2026-04-28.
RAW_GET_EVENTS = """\
Found 2 events.
- "Investment Sync Zoom Meeting" (Starts: 2026-04-30T17:00:00+00:00, Ends: 2026-04-30T18:00:00+00:00)
  Location: https://us02web.zoom.us/j/81189437956?pwd=u5djux0ddkygZeXQelAmitg5padHL7.1
  Description: <p>──────────<br/>Joseph Areas is inviting you to a scheduled Zoom meeting.<br/>Join Zoom Meeting<br/>https://us02web.zoom.us/j/81189437956?pwd=u5djux0ddkygZeXQelAmitg5padHL7.1<br/><br/>Meeting ID: 811 8943 7956<br/>Passcode: 757756<br/><br/>One tap mobile<br/>+13052241968,,81189437956#,,,,757756# US</p>
- "Mortgage(AutoP)" (Starts: 2026-04-30T19:00:00+00:00, Ends: 2026-05-01T19:00:00+00:00)
  Location: No Location
  Description: No description
"""


# --------------------------------------------------------------------------
# Calendar formatter
# --------------------------------------------------------------------------


class TestCalendarFormatterVoiceMode:
    def test_voice_mode_produces_short_paragraph_no_numbered_list(self) -> None:
        from src.agents.orchestrator import _format_connected_calendar_summary

        out = _format_connected_calendar_summary("this week", RAW_GET_EVENTS, voice_mode=True)

        # Conversational format: starts with "You have ... event"
        assert out.lower().startswith("you have ")
        # No numbered list / structured bullets
        assert "1)" not in out
        assert "Date:" not in out
        assert "Time:" not in out
        assert "Event:" not in out
        # No HTML, no URLs, no Zoom dial-in noise
        assert "<" not in out and ">" not in out
        assert "https://" not in out
        assert "Meeting ID" not in out
        assert "Passcode" not in out
        # Both events mentioned
        assert "Investment Sync" in out
        assert "Mortgage" in out
        # Reasonable length — voice replies should be tight
        word_count = len(out.split())
        assert 8 <= word_count <= 80, f"Voice summary length out of range: {word_count} words"

    def test_text_mode_strips_html_from_descriptions(self) -> None:
        from src.agents.orchestrator import _format_connected_calendar_summary

        out = _format_connected_calendar_summary("this week", RAW_GET_EVENTS, voice_mode=False)

        # Numbered list still present in text mode
        assert "1)" in out
        # But the HTML is gone
        assert "<p>" not in out and "<br/>" not in out
        # Zoom noise dropped
        assert "Meeting ID" not in out
        assert "Passcode" not in out
        # URL collapsed to a friendly label
        assert "Zoom link" in out
        assert "https://us02web.zoom.us" not in out

    def test_voice_mode_caps_at_five_events_with_more_indicator(self) -> None:
        from src.agents.orchestrator import _format_connected_calendar_summary

        # Build a payload with 8 events
        lines = ["Found 8 events."]
        for i in range(8):
            lines.append(
                f'- "Event {i + 1}" (Starts: 2026-04-30T{10 + i:02d}:00:00+00:00, '
                f'Ends: 2026-04-30T{11 + i:02d}:00:00+00:00)'
            )
            lines.append("  Location: No Location")
            lines.append("  Description: No description")
        payload = "\n".join(lines) + "\n"

        out = _format_connected_calendar_summary("today", payload, voice_mode=True)
        assert "Plus 3 more" in out
        assert "Event 1" in out
        assert "Event 5" in out
        assert "Event 6" not in out  # capped


# --------------------------------------------------------------------------
# Description cleaner
# --------------------------------------------------------------------------


class TestCleanEventDescription:
    @pytest.fixture(autouse=True)
    def _import(self):
        from src.agents.orchestrator import _clean_event_description

        self.clean = _clean_event_description

    def test_strips_html_tags(self) -> None:
        assert "<p>" not in self.clean("<p>Hello <br/>world</p>")

    def test_drops_zoom_dial_in_noise(self) -> None:
        text = self.clean(
            "<p>Join Zoom Meeting<br/>Meeting ID: 811 8943 7956<br/>Passcode: 757756</p>"
        )
        assert "Meeting ID" not in text
        assert "Passcode" not in text

    def test_voice_mode_caps_at_eighty_chars(self) -> None:
        long_desc = (
            "Quarterly product review. Agenda: roadmap, blockers, hiring, customer escalations, "
            "Q3 OKRs, and an open discussion before we wrap. Bring slides."
        )
        out = self.clean(long_desc, voice_mode=True)
        assert len(out) <= 81  # +1 for the ellipsis
        assert out.endswith("…")

    def test_returns_empty_for_no_description(self) -> None:
        assert self.clean("No description") == ""
        assert self.clean("") == ""


# --------------------------------------------------------------------------
# TTS text cleaner
# --------------------------------------------------------------------------


class TestStripMarkdownForTTS:
    @pytest.fixture(autouse=True)
    def _import(self):
        from src.bot.handler_utils import _strip_markdown

        self.strip = _strip_markdown

    def test_strips_basic_markdown(self) -> None:
        assert "bold" in self.strip("**bold**")
        assert "*" not in self.strip("**bold**")
        assert self.strip("# Header text") == "Header text"

    def test_strips_html_tags(self) -> None:
        out = self.strip("<p>Hello <br/>world</p>")
        assert "<" not in out and ">" not in out

    def test_drops_raw_urls(self) -> None:
        out = self.strip("Click https://us02web.zoom.us/j/81189437956 to join.")
        assert "https://" not in out
        assert "us02web.zoom.us" not in out

    def test_drops_meeting_id_blocks(self) -> None:
        out = self.strip("Meeting ID: 811 8943 7956 Passcode: 757756")
        assert "811" not in out
        assert "Passcode" not in out

    def test_real_calendar_dump_becomes_short_clean_text(self) -> None:
        # Roughly the actual reply that the user reported on 2026-04-28 —
        # a TTS engine would have read this verbatim, including HTML tags
        # and meeting IDs. After cleanup, those should all be gone.
        dump = (
            "Here's your schedule for this week:\n\n"
            "1)\n"
            "Date: Thu, Apr 30, 2026\n"
            "Time: 12:00 PM - 1:00 PM\n"
            "Event: Investment Sync' Zoom Meeting\n"
            "Location: https://us02web.zoom.us/j/81189437956?pwd=abc\n"
            "Details: <p>──────────<br/>Joseph Areas is inviting you to a scheduled Zoom meeting.<br/>"
            "Join Zoom Meeting<br/>https://us02web.zoom.us/j/81189437956<br/><br/>"
            "Meeting ID: 811 8943 7956<br/>Passcode: 757756</p>"
        )
        out = self.strip(dump)
        assert "<" not in out and ">" not in out
        assert "https://" not in out
        assert "Meeting ID" not in out
        assert "Passcode" not in out
        # The actual schedule wording should survive
        assert "Investment Sync" in out
