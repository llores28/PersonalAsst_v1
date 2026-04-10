"""Shared temporal parsing utilities for calendar and scheduler flows."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

from src.settings import settings

TemporalDomain = Literal["calendar", "scheduler"]
TemporalAction = Literal["read", "write", "manage"]
TemporalResolutionKind = Literal["range", "moment", "recurrence"]
ScheduleType = Literal["cron", "interval", "once"]

_WEEKDAY_INDEX = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}

_NUMBER_WORDS = {
    "a": 1,
    "an": 1,
    "one": 1,
    "two": 2,
    "three": 3,
    "tree": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
}

_CALENDAR_READ_PHRASES = (
    "what's on my calendar",
    "whats on my calendar",
    "what is on my calendar",
    "show my calendar",
    "check my calendar",
    "calendar today",
    "calendar tomorrow",
    "calendar this week",
    "calendar next week",
    "what's on my schedule",
    "whats on my schedule",
    "what is on my schedule",
    "show my schedule",
    "check my schedule",
    "my schedule today",
    "my schedule tomorrow",
    "my schedule this week",
    "my schedule next week",
    "what's on my schedual",
    "whats on my schedual",
    "what is on my schedual",
    "show my schedual",
    "check my schedual",
    "my schedual today",
    "my schedual tomorrow",
    "my schedual this week",
    "my schedual next week",
)

_CALENDAR_CONTEXT_KEYWORDS = ("calendar", "schedule", "schedual", "meeting", "event")
_EMAIL_CONTEXT_CUES = ("email", "gmail", "inbox", "draft", "compose", "reply", "forward")
_CALENDAR_WRITE_CUES = (
    "add to my calendar",
    "add to my schedule",
    "add to my schedual",
    "put on my calendar",
    "put on my schedule",
    "put on my schedual",
    "create event",
    "create calendar event",
    "schedule meeting",
    "book meeting",
)
_SCHEDULER_WRITE_CUES = (
    "remind me",
    "set a reminder",
    "create reminder",
    "schedule a reminder",
    "every ",
    "morning brief",
)


@dataclass(frozen=True)
class RecurrenceSpec:
    schedule_type: ScheduleType
    day_of_week: str = ""
    hour: int | None = None
    minute: int | None = None
    interval_minutes: int = 0
    interval_hours: int = 0


@dataclass(frozen=True)
class TemporalInterpretation:
    domain: TemporalDomain
    action: TemporalAction
    resolution_kind: TemporalResolutionKind
    label: str
    timezone: str
    start_at: str = ""
    end_at: str = ""
    recurrence: RecurrenceSpec | None = None
    assumptions: tuple[str, ...] = ()


def _get_timezone_name(timezone: str | None = None) -> str:
    return timezone or settings.default_timezone


def _get_now(reference: datetime | None = None, timezone: str | None = None) -> datetime:
    tz = ZoneInfo(_get_timezone_name(timezone))
    if reference is None:
        return datetime.now(tz)
    if reference.tzinfo is None:
        return reference.replace(tzinfo=tz)
    return reference.astimezone(tz)


def _parse_count(token: str) -> int | None:
    normalized = token.strip().lower()
    if normalized.isdigit():
        return int(normalized)
    return _NUMBER_WORDS.get(normalized)


def _add_months(base_date: date, months: int) -> date:
    month_index = base_date.month - 1 + months
    year = base_date.year + month_index // 12
    month = month_index % 12 + 1
    month_lengths = [31, 29 if year % 4 == 0 and (year % 100 != 0 or year % 400 == 0) else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
    day = min(base_date.day, month_lengths[month - 1])
    return date(year, month, day)


def _day_window(target_day: date, timezone: str) -> tuple[str, str]:
    tz = ZoneInfo(timezone)
    start = datetime.combine(target_day, datetime.min.time(), tzinfo=tz)
    end = start + timedelta(days=1)
    return start.isoformat(), end.isoformat()


def _contains_temporal_phrase(lowered: str) -> bool:
    if any(phrase in lowered for phrase in ("today", "tomorrow", "this week", "next week", "next ", "this ")):
        return True
    if re.search(r"\bin\s+[a-z0-9]+\s+(day|days|week|weeks|month|months)\b", lowered):
        return True
    return bool(re.search(r"\bat\s*\d{1,2}(?::\d{2})?\s*(am|pm)?\b", lowered))


def _is_calendar_read_intent(lowered: str) -> bool:
    if _is_calendar_write_intent(lowered):
        return False
    explicit_calendar_context = any(keyword in lowered for keyword in ("calendar", "schedule", "schedual"))
    if any(cue in lowered for cue in _EMAIL_CONTEXT_CUES) and not explicit_calendar_context:
        return False
    if any(phrase in lowered for phrase in _CALENDAR_READ_PHRASES):
        return True
    if any(keyword in lowered for keyword in _CALENDAR_CONTEXT_KEYWORDS) and _contains_temporal_phrase(lowered):
        return True
    return any(phrase in lowered for phrase in ("am i free", "check availability", "free busy"))


def _is_calendar_write_intent(lowered: str) -> bool:
    if any(cue in lowered for cue in _CALENDAR_WRITE_CUES):
        return True
    if any(keyword in lowered for keyword in ("calendar", "event", "meeting")) and any(verb in lowered for verb in ("add", "create", "schedule", "book", "put")):
        return True
    if any(token in lowered for token in ("my schedule", "my schedual")) and any(verb in lowered for verb in ("add", "put")):
        return True
    return False


def _is_scheduler_write_intent(lowered: str) -> bool:
    if any(cue in lowered for cue in _SCHEDULER_WRITE_CUES):
        return True
    if any(keyword in lowered for keyword in ("reminder", "task", "todo", "to do")) and any(verb in lowered for verb in ("add", "create", "set", "schedule")):
        return True
    return False


def _resolve_weekday_target(prefix: str, weekday_name: str, now: datetime) -> tuple[date, str, tuple[str, ...]]:
    target_index = _WEEKDAY_INDEX[weekday_name]
    if prefix == "next":
        days_ahead = (target_index - now.weekday()) % 7
        if days_ahead == 0:
            days_ahead = 7
        target_day = now.date() + timedelta(days=days_ahead)
        return target_day, f"next {weekday_name}", ()
    
    start_of_week = now.date() - timedelta(days=now.weekday())
    target_day = start_of_week + timedelta(days=target_index)
    if target_day < now.date():
        target_day = target_day + timedelta(days=7)
        return target_day, f"this {weekday_name}", (f'Interpreted "this {weekday_name}" as the next upcoming {weekday_name}.',)
    return target_day, f"this {weekday_name}", ()


def _resolve_week_relative_weekday(lowered: str, now: datetime) -> tuple[date, str, tuple[str, ...]] | None:
    weekday_match = re.search(r"\b(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b", lowered)
    if weekday_match is None:
        return None

    weekday_name = weekday_match.group(1)
    target_index = _WEEKDAY_INDEX[weekday_name]
    start_of_week = now.date() - timedelta(days=now.weekday())

    if "next week" in lowered:
        target_day = start_of_week + timedelta(days=7 + target_index)
        return target_day, f"next week {weekday_name}", ()

    if "this week" in lowered:
        target_day = start_of_week + timedelta(days=target_index)
        if target_day < now.date():
            target_day = target_day + timedelta(days=7)
            return target_day, f"this week {weekday_name}", (f'Interpreted "this week {weekday_name}" as the next upcoming {weekday_name}.',)
        return target_day, f"this week {weekday_name}", ()

    return None


def _resolve_day_reference(lowered: str, now: datetime) -> tuple[date, str, tuple[str, ...]] | None:
    if "today" in lowered:
        return now.date(), "today", ()
    if "tomorrow" in lowered:
        return now.date() + timedelta(days=1), "tomorrow", ()

    week_relative_weekday = _resolve_week_relative_weekday(lowered, now)
    if week_relative_weekday is not None:
        return week_relative_weekday

    weekday_match = re.search(r"\b(?P<prefix>this|next)\s+(?P<weekday>monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b", lowered)
    if weekday_match:
        return _resolve_weekday_target(
            weekday_match.group("prefix"),
            weekday_match.group("weekday"),
            now,
        )

    relative_match = re.search(
        r"\bin\s+(?P<count>[a-z0-9]+)\s+(?P<unit>day|days|week|weeks|month|months)\b",
        lowered,
    )
    if relative_match:
        count = _parse_count(relative_match.group("count"))
        if count is None:
            return None
        unit = relative_match.group("unit")
        if unit.startswith("day"):
            target_day = now.date() + timedelta(days=count)
        elif unit.startswith("week"):
            target_day = now.date() + timedelta(weeks=count)
        else:
            target_day = _add_months(now.date(), count)
        label = f"in {count} {unit}"
        return target_day, label, ()

    return None


def _extract_time_of_day(lowered: str) -> tuple[int, int, str] | None:
    meridiem_match = re.search(
        r"\bat\s*(?P<hour>\d{1,2})(?::(?P<minute>\d{2}))?\s*(?P<meridiem>am|pm)\b",
        lowered,
    )
    if meridiem_match:
        hour = int(meridiem_match.group("hour")) % 12
        minute = int(meridiem_match.group("minute") or 0)
        if meridiem_match.group("meridiem") == "pm":
            hour += 12
        label = datetime.combine(date.today(), time(hour=hour, minute=minute)).strftime("%I:%M %p").lstrip("0")
        return hour, minute, label

    twenty_four_match = re.search(r"\bat\s*(?P<hour>\d{1,2}):(?P<minute>\d{2})\b", lowered)
    if twenty_four_match:
        hour = int(twenty_four_match.group("hour"))
        minute = int(twenty_four_match.group("minute"))
        if hour > 23 or minute > 59:
            return None
        label = datetime.combine(date.today(), time(hour=hour, minute=minute)).strftime("%I:%M %p").lstrip("0")
        return hour, minute, label

    return None


def _parse_recurrence(lowered: str) -> tuple[RecurrenceSpec, str] | None:
    interval_match = re.search(r"\bevery\s+(?P<count>[a-z0-9]+)\s+(?P<unit>minute|minutes|hour|hours)\b", lowered)
    if interval_match:
        count = _parse_count(interval_match.group("count"))
        if count is None:
            return None
        unit = interval_match.group("unit")
        if unit.startswith("minute"):
            return RecurrenceSpec(schedule_type="interval", interval_minutes=count), f"every {count} minutes"
        return RecurrenceSpec(schedule_type="interval", interval_hours=count), f"every {count} hours"

    time_of_day = _extract_time_of_day(lowered)
    if time_of_day is None:
        return None
    hour, minute, time_label = time_of_day

    if "every weekday" in lowered:
        return RecurrenceSpec(schedule_type="cron", day_of_week="mon-fri", hour=hour, minute=minute), f"every weekday at {time_label}"
    if "every day" in lowered or "daily" in lowered:
        return RecurrenceSpec(schedule_type="cron", hour=hour, minute=minute), f"every day at {time_label}"

    weekday_match = re.search(r"\bevery\s+(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b", lowered)
    if weekday_match:
        weekday_name = weekday_match.group(1)
        day_of_week = weekday_name[:3].lower()
        return RecurrenceSpec(schedule_type="cron", day_of_week=day_of_week, hour=hour, minute=minute), f"every {weekday_name} at {time_label}"

    return None


def parse_calendar_time_range(
    user_message: str,
    *,
    timezone: str | None = None,
    reference: datetime | None = None,
) -> tuple[str, str, str] | None:
    lowered = user_message.strip().lower()
    if not _is_calendar_read_intent(lowered):
        return None

    timezone_name = _get_timezone_name(timezone)
    now = _get_now(reference, timezone_name)
    tz = ZoneInfo(timezone_name)

    week_relative_weekday = _resolve_week_relative_weekday(lowered, now)
    if week_relative_weekday is not None:
        target_day, label, _ = week_relative_weekday
        start, end = _day_window(target_day, timezone_name)
        return start, end, label

    if "next week" in lowered:
        start_of_week = now.date() - timedelta(days=now.weekday())
        start = datetime.combine(start_of_week, datetime.min.time(), tzinfo=tz) + timedelta(days=7)
        end = start + timedelta(days=7)
        return start.isoformat(), end.isoformat(), "next week"
    if "this week" in lowered:
        start_of_week = now.date() - timedelta(days=now.weekday())
        start = datetime.combine(start_of_week, datetime.min.time(), tzinfo=tz)
        end = start + timedelta(days=7)
        return start.isoformat(), end.isoformat(), "this week"

    resolved_day = _resolve_day_reference(lowered, now)
    if resolved_day is None:
        resolved_day = (now.date(), "today", ())
    target_day, label, _ = resolved_day
    start, end = _day_window(target_day, timezone_name)
    return start, end, label


def parse_temporal_interpretation(
    user_message: str,
    *,
    timezone: str | None = None,
    reference: datetime | None = None,
) -> TemporalInterpretation | None:
    lowered = user_message.strip().lower()
    timezone_name = _get_timezone_name(timezone)
    now = _get_now(reference, timezone_name)
    tz = ZoneInfo(timezone_name)

    calendar_range = parse_calendar_time_range(
        user_message,
        timezone=timezone_name,
        reference=now,
    )
    if calendar_range is not None:
        start_at, end_at, label = calendar_range
        return TemporalInterpretation(
            domain="calendar",
            action="read",
            resolution_kind="range",
            label=label,
            timezone=timezone_name,
            start_at=start_at,
            end_at=end_at,
        )

    recurrence = _parse_recurrence(lowered)
    if _is_calendar_write_intent(lowered):
        if recurrence is not None:
            recurrence_spec, label = recurrence
            return TemporalInterpretation(
                domain="calendar",
                action="write",
                resolution_kind="recurrence",
                label=label,
                timezone=timezone_name,
                recurrence=recurrence_spec,
                assumptions=("Event title and end time may still be missing.",),
            )

        day_reference = _resolve_day_reference(lowered, now)
        time_of_day = _extract_time_of_day(lowered)
        assumptions: list[str] = []
        if day_reference is not None:
            target_day, label, day_assumptions = day_reference
            assumptions.extend(day_assumptions)
            if time_of_day is not None:
                hour, minute, time_label = time_of_day
                start_at = datetime.combine(target_day, time(hour=hour, minute=minute), tzinfo=tz).isoformat()
                assumptions.append("End time is still missing for the calendar event.")
                return TemporalInterpretation(
                    domain="calendar",
                    action="write",
                    resolution_kind="moment",
                    label=f"{label} at {time_label}",
                    timezone=timezone_name,
                    start_at=start_at,
                    assumptions=tuple(assumptions),
                )
            start_at, end_at = _day_window(target_day, timezone_name)
            assumptions.append("Time is still missing for the calendar event.")
            return TemporalInterpretation(
                domain="calendar",
                action="write",
                resolution_kind="range",
                label=label,
                timezone=timezone_name,
                start_at=start_at,
                end_at=end_at,
                assumptions=tuple(assumptions),
            )

    if _is_scheduler_write_intent(lowered):
        if recurrence is not None:
            recurrence_spec, label = recurrence
            return TemporalInterpretation(
                domain="scheduler",
                action="write",
                resolution_kind="recurrence",
                label=label,
                timezone=timezone_name,
                recurrence=recurrence_spec,
            )

        day_reference = _resolve_day_reference(lowered, now)
        time_of_day = _extract_time_of_day(lowered)
        assumptions: list[str] = []
        if day_reference is not None:
            target_day, label, day_assumptions = day_reference
            assumptions.extend(day_assumptions)
            if time_of_day is not None:
                hour, minute, time_label = time_of_day
                start_at = datetime.combine(target_day, time(hour=hour, minute=minute), tzinfo=tz).isoformat()
                return TemporalInterpretation(
                    domain="scheduler",
                    action="write",
                    resolution_kind="moment",
                    label=f"{label} at {time_label}",
                    timezone=timezone_name,
                    start_at=start_at,
                    assumptions=tuple(assumptions),
                )
            start_at, end_at = _day_window(target_day, timezone_name)
            assumptions.append("Time is still missing for the reminder or scheduled task.")
            return TemporalInterpretation(
                domain="scheduler",
                action="write",
                resolution_kind="range",
                label=label,
                timezone=timezone_name,
                start_at=start_at,
                end_at=end_at,
                assumptions=tuple(assumptions),
            )

    return None


def build_temporal_context_block(
    user_message: str,
    *,
    timezone: str | None = None,
    reference: datetime | None = None,
) -> str:
    interpretation = parse_temporal_interpretation(
        user_message,
        timezone=timezone,
        reference=reference,
    )
    if interpretation is None:
        return ""

    lines = [
        "## Temporal Interpretation",
        f"Timezone: {interpretation.timezone}",
        f"Domain: {interpretation.domain}",
        f"Action: {interpretation.action}",
        f"Resolution: {interpretation.resolution_kind}",
        f"Label: {interpretation.label}",
    ]
    if interpretation.start_at:
        lines.append(f"Start: {interpretation.start_at}")
    if interpretation.end_at:
        lines.append(f"End: {interpretation.end_at}")
    if interpretation.recurrence is not None:
        lines.append(f"Schedule Type: {interpretation.recurrence.schedule_type}")
        if interpretation.recurrence.day_of_week:
            lines.append(f"Day Of Week: {interpretation.recurrence.day_of_week}")
        if interpretation.recurrence.hour is not None:
            lines.append(f"Hour: {interpretation.recurrence.hour}")
        if interpretation.recurrence.minute is not None:
            lines.append(f"Minute: {interpretation.recurrence.minute}")
        if interpretation.recurrence.interval_minutes:
            lines.append(f"Interval Minutes: {interpretation.recurrence.interval_minutes}")
        if interpretation.recurrence.interval_hours:
            lines.append(f"Interval Hours: {interpretation.recurrence.interval_hours}")
    if interpretation.assumptions:
        lines.append("Assumptions:")
        lines.extend(f"- {assumption}" for assumption in interpretation.assumptions)
    lines.append("Use this normalized timing if it matches the user's intent. Ask for any missing details before taking a write action.")
    return "\n".join(lines)


def append_temporal_context(
    user_message: str,
    *,
    timezone: str | None = None,
    reference: datetime | None = None,
) -> str:
    block = build_temporal_context_block(
        user_message,
        timezone=timezone,
        reference=reference,
    )
    if not block:
        return user_message
    return f"{user_message}\n\n{block}"
