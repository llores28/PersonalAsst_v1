"""Tests for shared temporal parsing utilities."""

from datetime import datetime

from src.temporal import append_temporal_context, parse_calendar_time_range, parse_temporal_interpretation

_REFERENCE = datetime(2026, 3, 17, 9, 0)
_TIMEZONE = "America/New_York"


class TestCalendarTimeRangeParser:
    """Calendar read-range normalization."""

    def test_parse_next_sunday_schedule_range(self) -> None:
        start, end, label = parse_calendar_time_range(
            "what's on my schedule next Sunday?",
            timezone=_TIMEZONE,
            reference=_REFERENCE,
        ) or ("", "", "")

        assert label == "next sunday"
        assert start == "2026-03-22T00:00:00-04:00"
        assert end == "2026-03-23T00:00:00-04:00"

    def test_parse_in_three_weeks_calendar_range(self) -> None:
        start, end, label = parse_calendar_time_range(
            "show my calendar in three weeks",
            timezone=_TIMEZONE,
            reference=_REFERENCE,
        ) or ("", "", "")

        assert label == "in 3 weeks"
        assert start == "2026-04-07T00:00:00-04:00"
        assert end == "2026-04-08T00:00:00-04:00"

    def test_parse_generic_calendar_read_defaults_to_today(self) -> None:
        start, end, label = parse_calendar_time_range(
            "show my calendar",
            timezone=_TIMEZONE,
            reference=_REFERENCE,
        ) or ("", "", "")

        assert label == "today"
        assert start == "2026-03-17T00:00:00-04:00"
        assert end == "2026-03-18T00:00:00-04:00"

    def test_email_draft_with_meeting_time_is_not_misparsed_as_calendar_read(self) -> None:
        parsed = parse_calendar_time_range(
            "draft an email to Alain Lores about having a meeting tomorrow at 10am about the project with Intel",
            timezone=_TIMEZONE,
            reference=_REFERENCE,
        )

        assert parsed is None


class TestTemporalInterpretation:
    """Calendar and scheduler write normalization."""

    def test_parse_one_shot_scheduler_request(self) -> None:
        interpretation = parse_temporal_interpretation(
            "remind me in three days at 4pm to pay rent",
            timezone=_TIMEZONE,
            reference=_REFERENCE,
        )

        assert interpretation is not None
        assert interpretation.domain == "scheduler"
        assert interpretation.action == "write"
        assert interpretation.resolution_kind == "moment"
        assert interpretation.start_at == "2026-03-20T16:00:00-04:00"
        assert interpretation.label == "in 3 days at 4:00 PM"

    def test_parse_recurring_scheduler_request(self) -> None:
        interpretation = parse_temporal_interpretation(
            "every Monday at 9am remind me to submit the report",
            timezone=_TIMEZONE,
            reference=_REFERENCE,
        )

        assert interpretation is not None
        assert interpretation.domain == "scheduler"
        assert interpretation.resolution_kind == "recurrence"
        assert interpretation.recurrence is not None
        assert interpretation.recurrence.schedule_type == "cron"
        assert interpretation.recurrence.day_of_week == "mon"
        assert interpretation.recurrence.hour == 9
        assert interpretation.recurrence.minute == 0

    def test_parse_calendar_write_with_missing_end_time(self) -> None:
        interpretation = parse_temporal_interpretation(
            "add lunch with Sam to my calendar next Sunday at 1pm",
            timezone=_TIMEZONE,
            reference=_REFERENCE,
        )

        assert interpretation is not None
        assert interpretation.domain == "calendar"
        assert interpretation.action == "write"
        assert interpretation.resolution_kind == "moment"
        assert interpretation.start_at == "2026-03-22T13:00:00-04:00"
        assert "End time is still missing for the calendar event." in interpretation.assumptions

    def test_parse_calendar_write_with_next_week_weekday_phrase(self) -> None:
        interpretation = parse_temporal_interpretation(
            "schedule a meeting with Alain Lores for next week at 9am monday",
            timezone=_TIMEZONE,
            reference=_REFERENCE,
        )

        assert interpretation is not None
        assert interpretation.domain == "calendar"
        assert interpretation.action == "write"
        assert interpretation.resolution_kind == "moment"
        assert interpretation.label == "next week monday at 9:00 AM"
        assert interpretation.start_at == "2026-03-23T09:00:00-04:00"
        assert "End time is still missing for the calendar event." in interpretation.assumptions

    def test_parse_task_request_as_scheduler_write(self) -> None:
        interpretation = parse_temporal_interpretation(
            "please add to my task for tomorrow to place grocery order at 9am on the app h-e-b",
            timezone=_TIMEZONE,
            reference=_REFERENCE,
        )

        assert interpretation is not None
        assert interpretation.domain == "scheduler"
        assert interpretation.action == "write"
        assert interpretation.resolution_kind == "moment"
        assert interpretation.label == "tomorrow at 9:00 AM"
        assert interpretation.start_at == "2026-03-18T09:00:00-04:00"


class TestTemporalContextFormatting:
    """Structured context appended for orchestrator routing."""

    def test_append_temporal_context_for_scheduler_request(self) -> None:
        prepared = append_temporal_context(
            "remind me in three days at 4pm to pay rent",
            timezone=_TIMEZONE,
            reference=_REFERENCE,
        )

        assert "## Temporal Interpretation" in prepared
        assert "Domain: scheduler" in prepared
        assert "Action: write" in prepared
        assert "Start: 2026-03-20T16:00:00-04:00" in prepared

    def test_append_temporal_context_leaves_unrelated_message_unchanged(self) -> None:
        message = "tell me a joke"
        assert append_temporal_context(message, timezone=_TIMEZONE, reference=_REFERENCE) == message
