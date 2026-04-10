import argparse
import asyncio
import json
import re
import time
from datetime import datetime, timedelta, timezone as datetime_timezone
from typing import Any
from zoneinfo import ZoneInfo

import yaml


_DIRECTLY_AUDITED_SERVICES = (
    ("gmail", "Direct MCP-backed read audit is implemented."),
    ("calendar", "Direct MCP-backed read audit is implemented."),
    ("tasks", "Direct MCP-backed read audit is implemented."),
)

_LLM_ROUTED_ONLY_SERVICES = (
    ("drive", "Repo currently exposes Drive through the LLM-routed `manage_drive` tool, not verified direct contracts."),
)

_NOT_INTEGRATED_SERVICES = (
    ("chat", "Granted by Google consent, but not wired in this repo."),
    ("contacts", "Granted by Google consent, but not wired in this repo."),
    ("docs", "Granted by Google consent, but not wired in this repo."),
    ("sheets", "Granted by Google consent, but not wired in this repo."),
    ("slides", "Granted by Google consent, but not wired in this repo."),
    ("forms", "Granted by Google consent, but not wired in this repo."),
    ("apps_script", "Granted by Google consent, but not wired in this repo."),
    ("custom_search", "Granted by Google consent, but not wired in this repo."),
    ("profile", "Granted by Google consent, but not audited as an app capability."),
)


def _tool_result_is_error(result_text: str) -> bool:
    lowered = result_text.strip().lower()
    if not lowered:
        return False
    return (
        lowered.startswith("error calling tool")
        or "userinputerror:" in lowered
        or "input error in " in lowered
        or "traceback (most recent call last)" in lowered
    )


def _extract_gmail_message_ids(search_results: str) -> list[str]:
    message_ids: list[str] = []
    seen: set[str] = set()
    for message_id in re.findall(r"Message ID:\s*([A-Za-z0-9]+)", search_results):
        if message_id not in seen:
            seen.add(message_id)
            message_ids.append(message_id)
    return message_ids


def _build_coverage_items(audit_mode: str = "read_only") -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    for service, note in _DIRECTLY_AUDITED_SERVICES:
        item_audit_mode = "direct_read"
        item_note = note
        if audit_mode == "canary" and service == "tasks":
            item_audit_mode = "direct_read_plus_canary"
            item_note = "Direct MCP-backed read audit and cleanup-safe canary write audit are implemented."
        items.append(
            {
                "service": service,
                "audit_mode": item_audit_mode,
                "repo_status": "covered",
                "note": item_note,
            }
        )
    for service, note in _LLM_ROUTED_ONLY_SERVICES:
        items.append(
            {
                "service": service,
                "audit_mode": "llm_routed_only",
                "repo_status": "partial",
                "note": note,
            }
        )
    for service, note in _NOT_INTEGRATED_SERVICES:
        items.append(
            {
                "service": service,
                "audit_mode": "not_integrated",
                "repo_status": "uncovered",
                "note": note,
            }
        )
    return items


def _extract_task_id(result_text: str) -> str | None:
    match = re.search(r"^\s*-\s*ID:\s*(\S+)\s*$", result_text, re.MULTILINE)
    if match is None:
        return None
    return match.group(1)


def _extract_task_block(results: str, title: str) -> str | None:
    normalized_title = title.strip().lower()
    lines = results.splitlines()
    for index, raw_line in enumerate(lines):
        if normalized_title not in raw_line.lower():
            continue

        block_lines = [raw_line.rstrip()]
        cursor = index + 1
        while cursor < len(lines):
            next_line = lines[cursor]
            stripped = next_line.strip()
            if not stripped:
                break
            if stripped.startswith("-") and "(ID:" in stripped:
                break
            block_lines.append(next_line.rstrip())
            cursor += 1
        return "\n".join(block_lines).strip()
    return None


def _classify_issue_type(
    *,
    status: str,
    service: str,
    step: str,
    tool_name: str | None = None,
    message: str = "",
) -> str:
    lowered = message.strip().lower()

    if service == "auth" or any(
        token in lowered
        for token in (
            "invalid_grant",
            "authentication",
            "authorization",
            "connect google",
            "connected google email",
        )
    ):
        return "auth"

    if step.endswith("cleanup"):
        return "cleanup"

    if status == "skip":
        if any(
            token in lowered
            for token in (
                "llm-routed",
                "not wired",
                "not integrated",
                "no deterministic direct tool contract",
            )
        ):
            return "coverage_gap"
        return "read_verification"

    if any(
        token in lowered
        for token in (
            "invalid action",
            "invalid payload",
            "userinputerror",
            "input error in",
            "must be one of",
            "traceback (most recent call last)",
        )
    ):
        return "tool_contract"

    if step.startswith("tasks.canary") or tool_name == "manage_task":
        return "write_verification"

    return "read_verification"


def _summarize_issue_types(steps: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for step in steps:
        if step.get("status") == "pass":
            continue
        issue_type = step.get("issue_type")
        if not issue_type:
            continue
        counts[issue_type] = counts.get(issue_type, 0) + 1
    return counts


def _build_tasks_canary_title() -> str:
    stamp = datetime.now(datetime_timezone.utc).strftime("%Y%m%d%H%M%S")
    return f"[AUDIT] PersonalAsst Google Tasks Canary {stamp}"


def _truncate(text: str, max_chars: int = 500) -> str:
    text = text.strip()
    if len(text) <= max_chars:
        return text
    half = max_chars // 2
    return text[:half] + f"\n... [truncated {len(text) - max_chars} chars] ...\n" + text[-half:]


def _today_window(timezone_name: str) -> tuple[str, str]:
    now = datetime.now(ZoneInfo(timezone_name))
    start = datetime.combine(now.date(), datetime.min.time(), tzinfo=now.tzinfo)
    end = start + timedelta(days=1)
    return start.isoformat(), end.isoformat()


def _default_user_id() -> int:
    from src.settings import settings

    return settings.owner_telegram_id


def _default_timezone() -> str:
    from src.settings import settings

    return settings.default_timezone


def _emit(result: dict[str, Any], output_format: str) -> None:
    if output_format == "json":
        print(json.dumps(result, indent=2, default=str))
        return
    if output_format == "yaml":
        print(yaml.dump(result, default_flow_style=False, sort_keys=False))
        return

    print(f"google-audit: {result['status']}")
    print(result.get("message", ""))
    print()
    if result.get("details"):
        for key, value in result["details"].items():
            print(f"{key}: {value}")
        print()
    if result.get("issue_summary"):
        print("issue_summary:")
        for key, value in result["issue_summary"].items():
            print(f"- {key}: {value}")
        print()
    print("steps:")
    for step in result.get("steps", []):
        line = f"- [{step['status']}"
        if step.get("issue_type"):
            line += f"/{step['issue_type']}"
        line += f"] {step['step']}"
        if step.get("message"):
            line += f" — {step['message']}"
        print(line)
    print()
    print("coverage:")
    for item in result.get("coverage", []):
        print(f"- {item['service']}: {item['repo_status']} ({item['audit_mode']})")


async def _call_workspace_tool_checked(tool_name: str, arguments: dict[str, Any]) -> str:
    from src.integrations.workspace_mcp import call_workspace_tool

    result = await call_workspace_tool(tool_name, arguments)
    if _tool_result_is_error(result):
        raise RuntimeError(result.strip())
    return result


def _make_step(
    *,
    step: str,
    service: str,
    status: str,
    duration_ms: int,
    tool: str | None = None,
    message: str = "",
    details: dict[str, Any] | None = None,
    raw_output: str | None = None,
    issue_type: str | None = None,
) -> dict[str, Any]:
    item: dict[str, Any] = {
        "step": step,
        "service": service,
        "status": status,
        "duration_ms": duration_ms,
        "issue_type": issue_type or _classify_issue_type(
            status=status,
            service=service,
            step=step,
            tool_name=tool,
            message=message,
        ),
    }
    if tool:
        item["tool"] = tool
    if message:
        item["message"] = message
    if details is not None:
        item["details"] = details
    if raw_output:
        item["raw_output"] = _truncate(raw_output)
    return item


async def _run_read_step(
    *,
    step: str,
    service: str,
    tool_name: str,
    arguments: dict[str, Any],
    success_message: str,
    details_builder,
    issue_type: str = "read_verification",
) -> tuple[dict[str, Any], str | None]:
    started = time.time()
    try:
        result = await _call_workspace_tool_checked(tool_name, arguments)
        details = details_builder(result)
        return (
            _make_step(
                step=step,
                service=service,
                status="pass",
                duration_ms=int((time.time() - started) * 1000),
                tool=tool_name,
                message=success_message,
                details=details,
                raw_output=result,
                issue_type=issue_type,
            ),
            result,
        )
    except Exception as exc:
        return (
            _make_step(
                step=step,
                service=service,
                status="fail",
                duration_ms=int((time.time() - started) * 1000),
                tool=tool_name,
                message=str(exc),
            ),
            None,
        )


def _audit_gmail_local_contracts() -> list[dict[str, Any]]:
    from src.action_policy import classify_action_request
    from src.agents.email_agent import _normalize_gmail_subject
    from src.agents.orchestrator import _build_pending_gmail_send_payload
    from src.temporal import parse_calendar_time_range

    steps: list[dict[str, Any]] = []
    draft_request = "draft an email to Alain Lores about having a meeting tomorrow at 10am about the project with Intel"
    send_request = "send email to lannys.lores@gmail.com running 10min late"
    follow_up_send_request = "send it"
    draft_response = (
        "Here’s a draft reminder email for your wife about the upcoming Electric bill:\n\n"
        "---\n"
        "Subject: Reminder: Upcoming Electric Bill\n\n"
        "Hi love,\n\n"
        "Just a quick reminder that the electric bill is coming up soon. Let me know if you have any questions or if you need the details!\n\n"
        "Thanks!\n"
        "---"
    )

    draft_started = time.time()
    draft_calendar_range = parse_calendar_time_range(draft_request)
    steps.append(
        _make_step(
            step="gmail.routing_draft_not_calendar_read",
            service="gmail",
            status="pass" if draft_calendar_range is None else "fail",
            duration_ms=int((time.time() - draft_started) * 1000),
            message=(
                "Draft email request was not misclassified as a calendar read."
                if draft_calendar_range is None
                else "Draft email request was unexpectedly parsed as a calendar read."
            ),
            details={
                "request": draft_request,
                "calendar_range_detected": draft_calendar_range is not None,
            },
            issue_type="routing",
        )
    )

    send_started = time.time()
    send_calendar_range = parse_calendar_time_range(send_request)
    steps.append(
        _make_step(
            step="gmail.routing_send_not_calendar_read",
            service="gmail",
            status="pass" if send_calendar_range is None else "fail",
            duration_ms=int((time.time() - send_started) * 1000),
            message=(
                "Send email request was not misclassified as a calendar read."
                if send_calendar_range is None
                else "Send email request was unexpectedly parsed as a calendar read."
            ),
            details={
                "request": send_request,
                "calendar_range_detected": send_calendar_range is not None,
            },
            issue_type="routing",
        )
    )

    draft_policy_started = time.time()
    draft_policy = classify_action_request(draft_request)
    draft_policy_ok = draft_policy.action_class == "draft" and draft_policy.requires_confirmation is False
    steps.append(
        _make_step(
            step="gmail.policy_draft_classification",
            service="gmail",
            status="pass" if draft_policy_ok else "fail",
            duration_ms=int((time.time() - draft_policy_started) * 1000),
            message=(
                "Draft email request classified correctly as a draft."
                if draft_policy_ok
                else "Draft email request did not classify as a draft."
            ),
            details={
                "request": draft_request,
                "action_class": draft_policy.action_class,
                "requires_confirmation": draft_policy.requires_confirmation,
            },
            issue_type="policy",
        )
    )

    send_policy_started = time.time()
    send_policy = classify_action_request(send_request)
    send_policy_ok = send_policy.action_class == "external_side_effect" and send_policy.requires_confirmation is True
    steps.append(
        _make_step(
            step="gmail.policy_send_classification",
            service="gmail",
            status="pass" if send_policy_ok else "fail",
            duration_ms=int((time.time() - send_policy_started) * 1000),
            message=(
                "Send email request classified correctly as an external side effect requiring confirmation."
                if send_policy_ok
                else "Send email request did not classify as an external side effect requiring confirmation."
            ),
            details={
                "request": send_request,
                "action_class": send_policy.action_class,
                "requires_confirmation": send_policy.requires_confirmation,
            },
            issue_type="policy",
        )
    )

    follow_up_policy_started = time.time()
    follow_up_policy = classify_action_request(follow_up_send_request)
    follow_up_policy_ok = follow_up_policy.action_class == "internal_write" and follow_up_policy.requires_confirmation is False
    steps.append(
        _make_step(
            step="gmail.policy_follow_up_send_confirmation",
            service="gmail",
            status="pass" if follow_up_policy_ok else "fail",
            duration_ms=int((time.time() - follow_up_policy_started) * 1000),
            message=(
                "Short follow-up send confirmation classified correctly as contextual approval."
                if follow_up_policy_ok
                else "Short follow-up send confirmation did not classify as contextual approval."
            ),
            details={
                "request": follow_up_send_request,
                "action_class": follow_up_policy.action_class,
                "requires_confirmation": follow_up_policy.requires_confirmation,
            },
            issue_type="policy",
        )
    )

    subject_started = time.time()
    inferred_subject = _normalize_gmail_subject(None, "running 10min late")
    subject_ok = bool(inferred_subject.strip())
    steps.append(
        _make_step(
            step="gmail.contract_subject_fallback",
            service="gmail",
            status="pass" if subject_ok else "fail",
            duration_ms=int((time.time() - subject_started) * 1000),
            message=(
                "Gmail subject fallback produced a non-empty subject for a body-only request."
                if subject_ok
                else "Gmail subject fallback was empty for a body-only request."
            ),
            details={
                "body": "running 10min late",
                "inferred_subject": inferred_subject,
            },
            issue_type="tool_contract",
        )
    )

    pending_send_started = time.time()
    pending_send_payload = _build_pending_gmail_send_payload(
        "Draft a email reminder to my wife bnlores@gmail.com of the upcoming Electric bill",
        draft_response,
    )
    pending_send_ok = (
        pending_send_payload is not None
        and pending_send_payload.get("to") == "bnlores@gmail.com"
        and bool(pending_send_payload.get("subject"))
        and bool(pending_send_payload.get("body"))
    )
    steps.append(
        _make_step(
            step="gmail.contract_pending_send_payload_from_draft_response",
            service="gmail",
            status="pass" if pending_send_ok else "fail",
            duration_ms=int((time.time() - pending_send_started) * 1000),
            message=(
                "Drafted email response produced a concrete pending Gmail send payload."
                if pending_send_ok
                else "Drafted email response did not produce a usable pending Gmail send payload."
            ),
            details={
                "request": "Draft a email reminder to my wife bnlores@gmail.com of the upcoming Electric bill",
                "pending_send_detected": pending_send_payload is not None,
                "recipient": None if pending_send_payload is None else pending_send_payload.get("to"),
                "subject": None if pending_send_payload is None else pending_send_payload.get("subject"),
            },
            issue_type="tool_contract",
        )
    )

    pending_send_missing_recipient_started = time.time()
    pending_send_missing_recipient_payload = _build_pending_gmail_send_payload(
        "draft an email to my wife about the Electric bill is due soon",
        draft_response,
    )
    pending_send_missing_recipient_ok = (
        pending_send_missing_recipient_payload is not None
        and pending_send_missing_recipient_payload.get("to") is None
        and bool(pending_send_missing_recipient_payload.get("subject"))
        and bool(pending_send_missing_recipient_payload.get("body"))
    )
    steps.append(
        _make_step(
            step="gmail.contract_pending_send_payload_without_explicit_recipient",
            service="gmail",
            status="pass" if pending_send_missing_recipient_ok else "fail",
            duration_ms=int((time.time() - pending_send_missing_recipient_started) * 1000),
            message=(
                "Drafted email response preserved a pending send payload even when the original request omitted the recipient email address."
                if pending_send_missing_recipient_ok
                else "Drafted email response did not preserve a pending send payload when the original request omitted the recipient email address."
            ),
            details={
                "request": "draft an email to my wife about the Electric bill is due soon",
                "pending_send_detected": pending_send_missing_recipient_payload is not None,
                "recipient": None if pending_send_missing_recipient_payload is None else pending_send_missing_recipient_payload.get("to"),
                "subject": None if pending_send_missing_recipient_payload is None else pending_send_missing_recipient_payload.get("subject"),
            },
            issue_type="tool_contract",
        )
    )

    return steps


async def _audit_gmail(connected_google_email: str) -> list[dict[str, Any]]:
    steps = _audit_gmail_local_contracts()
    search_step, search_result = await _run_read_step(
        step="gmail.search_inbox",
        service="gmail",
        tool_name="search_gmail_messages",
        arguments={
            "user_google_email": connected_google_email,
            "query": "in:inbox",
            "page_size": 1,
        },
        success_message="Inbox search succeeded.",
        details_builder=lambda result: {"message_ids": _extract_gmail_message_ids(result)},
    )
    steps.append(search_step)
    if search_result is None:
        return steps

    message_ids = _extract_gmail_message_ids(search_result)
    if not message_ids:
        steps.append(
            _make_step(
                step="gmail.read_first_message",
                service="gmail",
                status="skip",
                duration_ms=0,
                tool="get_gmail_messages_content_batch",
                message="Inbox search returned no message IDs; content read skipped.",
            )
        )
        return steps

    content_step, _ = await _run_read_step(
        step="gmail.read_first_message",
        service="gmail",
        tool_name="get_gmail_messages_content_batch",
        arguments={
            "user_google_email": connected_google_email,
            "message_ids": [message_ids[0]],
            "format": "full",
        },
        success_message="First inbox message content fetched.",
        details_builder=lambda result: {
            "contains_subject": "Subject:" in result,
            "contains_from": "From:" in result,
            "message_id": message_ids[0],
        },
    )
    steps.append(content_step)
    return steps


async def _audit_calendar(connected_google_email: str, timezone_name: str) -> list[dict[str, Any]]:
    time_min, time_max = _today_window(timezone_name)
    step, _ = await _run_read_step(
        step="calendar.read_today",
        service="calendar",
        tool_name="get_events",
        arguments={
            "user_google_email": connected_google_email,
            "calendar_id": "primary",
            "time_min": time_min,
            "time_max": time_max,
            "max_results": 10,
            "detailed": True,
        },
        success_message="Calendar read for today succeeded.",
        details_builder=lambda result: {
            "time_min": time_min,
            "time_max": time_max,
            "contains_events_markup": "Starts:" in result or "No events" in result,
        },
    )
    return [step]


async def _audit_tasks(connected_google_email: str) -> list[dict[str, Any]]:
    steps: list[dict[str, Any]] = []
    lists_step, lists_result = await _run_read_step(
        step="tasks.list_task_lists",
        service="tasks",
        tool_name="list_task_lists",
        arguments={"user_google_email": connected_google_email},
        success_message="Task list lookup succeeded.",
        details_builder=lambda result: {"default_list_detected": "@default" in result or "Default" in result},
    )
    steps.append(lists_step)
    if lists_result is None:
        return steps

    tasks_step, _ = await _run_read_step(
        step="tasks.list_default_tasks",
        service="tasks",
        tool_name="list_tasks",
        arguments={
            "user_google_email": connected_google_email,
            "task_list_id": "@default",
            "show_completed": False,
            "max_results": 20,
        },
        success_message="Default task list read succeeded.",
        details_builder=lambda result: {"contains_checkbox_or_task_text": "☐" in result or "✅" in result or "task" in result.lower()},
    )
    steps.append(tasks_step)
    return steps


async def _audit_tasks_canary(connected_google_email: str) -> list[dict[str, Any]]:
    steps: list[dict[str, Any]] = []
    canary_title = _build_tasks_canary_title()
    task_id: str | None = None

    try:
        create_started = time.time()
        try:
            create_result = await _call_workspace_tool_checked(
                "manage_task",
                {
                    "user_google_email": connected_google_email,
                    "action": "create",
                    "task_list_id": "@default",
                    "title": canary_title,
                },
            )
            task_id = _extract_task_id(create_result)
            if task_id is None:
                raise RuntimeError("Could not parse the created Google Task ID from the canary response.")
            steps.append(
                _make_step(
                    step="tasks.canary_create",
                    service="tasks",
                    status="pass",
                    duration_ms=int((time.time() - create_started) * 1000),
                    tool="manage_task",
                    message="Canary task creation succeeded.",
                    details={"title": canary_title, "task_id": task_id},
                    raw_output=create_result,
                    issue_type="write_verification",
                )
            )
        except Exception as exc:
            steps.append(
                _make_step(
                    step="tasks.canary_create",
                    service="tasks",
                    status="fail",
                    duration_ms=int((time.time() - create_started) * 1000),
                    tool="manage_task",
                    message=str(exc),
                    issue_type="write_verification",
                )
            )
            return steps

        verify_created_started = time.time()
        try:
            created_list_result = await _call_workspace_tool_checked(
                "list_tasks",
                {
                    "user_google_email": connected_google_email,
                    "task_list_id": "@default",
                    "show_completed": False,
                    "max_results": 50,
                },
            )
            created_block = _extract_task_block(created_list_result, canary_title)
            if created_block is None:
                raise RuntimeError(f"Canary task '{canary_title}' was not visible after create.")
            steps.append(
                _make_step(
                    step="tasks.canary_verify_created",
                    service="tasks",
                    status="pass",
                    duration_ms=int((time.time() - verify_created_started) * 1000),
                    tool="list_tasks",
                    message="Canary task was visible in the default list after create.",
                    details={"title": canary_title, "task_id": task_id, "visible": True},
                    raw_output=created_list_result,
                    issue_type="write_verification",
                )
            )
        except Exception as exc:
            steps.append(
                _make_step(
                    step="tasks.canary_verify_created",
                    service="tasks",
                    status="fail",
                    duration_ms=int((time.time() - verify_created_started) * 1000),
                    tool="list_tasks",
                    message=str(exc),
                    issue_type="write_verification",
                )
            )
            return steps

        complete_started = time.time()
        try:
            complete_result = await _call_workspace_tool_checked(
                "manage_task",
                {
                    "user_google_email": connected_google_email,
                    "action": "update",
                    "task_list_id": "@default",
                    "task_id": task_id,
                    "status": "completed",
                },
            )
            steps.append(
                _make_step(
                    step="tasks.canary_complete",
                    service="tasks",
                    status="pass",
                    duration_ms=int((time.time() - complete_started) * 1000),
                    tool="manage_task",
                    message="Canary task completion succeeded.",
                    details={"title": canary_title, "task_id": task_id, "status": "completed"},
                    raw_output=complete_result,
                    issue_type="write_verification",
                )
            )
        except Exception as exc:
            steps.append(
                _make_step(
                    step="tasks.canary_complete",
                    service="tasks",
                    status="fail",
                    duration_ms=int((time.time() - complete_started) * 1000),
                    tool="manage_task",
                    message=str(exc),
                    issue_type="write_verification",
                )
            )
            return steps

        verify_completed_started = time.time()
        try:
            completed_list_result = await _call_workspace_tool_checked(
                "list_tasks",
                {
                    "user_google_email": connected_google_email,
                    "task_list_id": "@default",
                    "show_completed": True,
                    "max_results": 50,
                },
            )
            completed_block = _extract_task_block(completed_list_result, canary_title)
            if completed_block is None:
                raise RuntimeError(f"Canary task '{canary_title}' was not visible after completion.")
            if "completed" not in completed_block.lower() and "✅" not in completed_block:
                raise RuntimeError(f"Canary task '{canary_title}' did not appear completed after update.")
            steps.append(
                _make_step(
                    step="tasks.canary_verify_completed",
                    service="tasks",
                    status="pass",
                    duration_ms=int((time.time() - verify_completed_started) * 1000),
                    tool="list_tasks",
                    message="Completed canary task was visible in the default list.",
                    details={"title": canary_title, "task_id": task_id, "status_detected": True},
                    raw_output=completed_list_result,
                    issue_type="write_verification",
                )
            )
        except Exception as exc:
            steps.append(
                _make_step(
                    step="tasks.canary_verify_completed",
                    service="tasks",
                    status="fail",
                    duration_ms=int((time.time() - verify_completed_started) * 1000),
                    tool="list_tasks",
                    message=str(exc),
                    issue_type="write_verification",
                )
            )
            return steps
    finally:
        if task_id is not None:
            cleanup_started = time.time()
            try:
                cleanup_result = await _call_workspace_tool_checked(
                    "manage_task",
                    {
                        "user_google_email": connected_google_email,
                        "action": "delete",
                        "task_list_id": "@default",
                        "task_id": task_id,
                    },
                )
                steps.append(
                    _make_step(
                        step="tasks.canary_cleanup",
                        service="tasks",
                        status="pass",
                        duration_ms=int((time.time() - cleanup_started) * 1000),
                        tool="manage_task",
                        message="Canary task cleanup succeeded.",
                        details={"title": canary_title, "task_id": task_id},
                        raw_output=cleanup_result,
                        issue_type="cleanup",
                    )
                )
            except Exception as exc:
                steps.append(
                    _make_step(
                        step="tasks.canary_cleanup",
                        service="tasks",
                        status="fail",
                        duration_ms=int((time.time() - cleanup_started) * 1000),
                        tool="manage_task",
                        message=str(exc),
                        issue_type="cleanup",
                    )
                )
    return steps


def _drive_placeholder_step() -> dict[str, Any]:
    return _make_step(
        step="drive.direct_audit",
        service="drive",
        status="skip",
        duration_ms=0,
        message="Drive is only exposed via the LLM-routed `manage_drive` tool in this repo; no deterministic direct tool contract is verified yet.",
        issue_type="coverage_gap",
    )


def _summarize_status(steps: list[dict[str, Any]], coverage: list[dict[str, str]]) -> tuple[str, str]:
    failures = [step for step in steps if step["status"] == "fail"]
    warnings = [item for item in coverage if item["repo_status"] != "covered"]
    if failures:
        return "fail", f"{len(failures)} audit step(s) failed."
    if warnings:
        return "warn", "Directly audited Google services passed, but some granted or described services are not yet directly auditable in this repo."
    return "pass", "All audited Google services passed."


async def run_google_audit(
    *,
    user_id: int | None,
    connected_google_email: str | None,
    output_format: str,
    mode: str,
) -> int:
    from src.integrations.workspace_mcp import get_connected_google_email as get_connected_email

    started = time.time()
    resolved_user_id = user_id if user_id is not None else _default_user_id()
    timezone_name = _default_timezone()
    email = connected_google_email or await get_connected_email(resolved_user_id)

    steps: list[dict[str, Any]] = []
    coverage = _build_coverage_items(mode)

    if not email:
        result = {
            "tool": "google-audit",
            "status": "fail",
            "message": "No connected Google email was found for the requested user.",
            "details": {
                "user_id": resolved_user_id,
                "hint": "Run /connect google first, then rerun the audit.",
            },
            "steps": [],
            "issue_summary": {"auth": 1},
            "coverage": coverage,
            "duration_ms": int((time.time() - started) * 1000),
        }
        _emit(result, output_format)
        return 1

    steps.append(
        _make_step(
            step="auth.resolve_connected_email",
            service="auth",
            status="pass",
            duration_ms=0,
            message="Connected Google account resolved.",
            details={"user_id": resolved_user_id, "connected_google_email": email},
            issue_type="auth",
        )
    )

    steps.extend(await _audit_gmail(email))
    steps.extend(await _audit_calendar(email, timezone_name))
    steps.extend(await _audit_tasks(email))
    if mode == "canary":
        steps.extend(await _audit_tasks_canary(email))
    steps.append(_drive_placeholder_step())

    status, message = _summarize_status(steps, coverage)
    note = "This audit verifies repo-wired Google capabilities with safe read checks. Drive is reported separately because it is currently LLM-routed, and broader consent-screen scopes are reported as uncovered if the repo does not implement them."
    if mode == "canary":
        note = "This audit verifies repo-wired Google capabilities with safe read checks and a cleanup-safe Google Tasks canary create/complete/delete cycle. Drive is reported separately because it is currently LLM-routed, and broader consent-screen scopes are reported as uncovered if the repo does not implement them."
    result = {
        "tool": "google-audit",
        "status": status,
        "message": message,
        "details": {
            "user_id": resolved_user_id,
            "connected_google_email": email,
            "timezone": timezone_name,
            "audit_mode": mode,
            "note": note,
        },
        "steps": steps,
        "issue_summary": _summarize_issue_types(steps),
        "coverage": coverage,
        "duration_ms": int((time.time() - started) * 1000),
    }
    _emit(result, output_format)
    return 0 if status != "fail" else 1


def main() -> int:
    parser = argparse.ArgumentParser(prog="python -m src.google_audit")
    parser.add_argument("--user-id", type=int, default=None)
    parser.add_argument("--email", default=None)
    parser.add_argument("--format", choices=("json", "human", "yaml"), default="json")
    parser.add_argument("--mode", choices=("read_only", "canary"), default="read_only")
    args = parser.parse_args()
    return asyncio.run(
        run_google_audit(
            user_id=args.user_id,
            connected_google_email=args.email,
            output_format=args.format,
            mode=args.mode,
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())
