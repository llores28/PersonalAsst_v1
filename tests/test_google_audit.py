import pytest
from unittest.mock import AsyncMock, patch

from src.google_audit import (
    _audit_gmail_local_contracts,
    _audit_tasks_canary,
    _build_coverage_items,
    _extract_gmail_message_ids,
    _extract_task_block,
    _extract_task_id,
    _summarize_issue_types,
    _summarize_status,
    _tool_result_is_error,
)


def test_extract_gmail_message_ids_preserves_order_and_uniqueness() -> None:
    search_results = (
        "Found 3 messages matching 'in:inbox'\n"
        "📧 MESSAGES:\n"
        "  1. Message ID: abc123\n"
        "  2. Message ID: def456\n"
        "  3. Message ID: abc123\n"
    )

    assert _extract_gmail_message_ids(search_results) == ["abc123", "def456"]


@pytest.mark.parametrize(
    ("result_text", "expected"),
    [
        ("created", False),
        ("Found 1 message matching 'in:inbox'", False),
        ("Error calling tool 'manage_task': invalid payload", True),
        ("UserInputError: title is required", True),
        ("Input error in list_tasks: missing task_list_id", True),
    ],
)
def test_tool_result_is_error_detects_sidecar_failures(result_text: str, expected: bool) -> None:
    assert _tool_result_is_error(result_text) is expected


def test_build_coverage_items_marks_direct_partial_and_uncovered_services() -> None:
    items = _build_coverage_items()
    by_service = {item["service"]: item for item in items}

    assert by_service["gmail"]["repo_status"] == "covered"
    assert by_service["calendar"]["repo_status"] == "covered"
    assert by_service["tasks"]["repo_status"] == "covered"
    assert by_service["drive"]["repo_status"] == "partial"
    assert by_service["chat"]["repo_status"] == "uncovered"
    assert by_service["docs"]["repo_status"] == "uncovered"


def test_build_coverage_items_marks_tasks_canary_mode_when_enabled() -> None:
    items = _build_coverage_items("canary")
    by_service = {item["service"]: item for item in items}

    assert by_service["tasks"]["audit_mode"] == "direct_read_plus_canary"
    assert "cleanup-safe canary write audit" in by_service["tasks"]["note"]
    assert by_service["drive"]["audit_mode"] == "llm_routed_only"


def test_extract_task_id_reads_manage_task_response() -> None:
    result_text = "Task created successfully\n- ID: task123\n- Title: Example"

    assert _extract_task_id(result_text) == "task123"


def test_extract_task_block_returns_matching_task_section() -> None:
    results = (
        "Tasks in list @default for user@example.com:\n"
        "- First task (ID: task111)\n"
        "  Status: needsAction\n"
        "- [AUDIT] PersonalAsst Google Tasks Canary 20260318153000 (ID: task123)\n"
        "  Status: completed\n"
        "  Updated: 2026-03-18T20:30:00Z\n"
    )

    block = _extract_task_block(results, "[AUDIT] PersonalAsst Google Tasks Canary 20260318153000")

    assert block is not None
    assert "task123" in block
    assert "Status: completed" in block


def test_summarize_issue_types_counts_non_pass_issue_buckets() -> None:
    counts = _summarize_issue_types(
        [
            {"step": "gmail.search_inbox", "status": "pass", "issue_type": "read_verification"},
            {"step": "tasks.canary_create", "status": "fail", "issue_type": "tool_contract"},
            {"step": "drive.direct_audit", "status": "skip", "issue_type": "coverage_gap"},
            {"step": "tasks.canary_cleanup", "status": "fail", "issue_type": "cleanup"},
        ]
    )

    assert counts == {
        "tool_contract": 1,
        "coverage_gap": 1,
        "cleanup": 1,
    }


def test_audit_gmail_local_contracts_checks_routing_policy_and_subject_fallback() -> None:
    steps = _audit_gmail_local_contracts()
    by_step = {step["step"]: step for step in steps}

    assert [step["step"] for step in steps] == [
        "gmail.routing_draft_not_calendar_read",
        "gmail.routing_send_not_calendar_read",
        "gmail.policy_draft_classification",
        "gmail.policy_send_classification",
        "gmail.policy_follow_up_send_confirmation",
        "gmail.contract_subject_fallback",
        "gmail.contract_pending_send_payload_from_draft_response",
        "gmail.contract_pending_send_payload_without_explicit_recipient",
    ]
    assert all(step["status"] == "pass" for step in steps)
    assert by_step["gmail.routing_draft_not_calendar_read"]["issue_type"] == "routing"
    assert by_step["gmail.policy_send_classification"]["issue_type"] == "policy"
    assert by_step["gmail.contract_subject_fallback"]["details"]["inferred_subject"] == "Running 10min late"
    assert by_step["gmail.policy_follow_up_send_confirmation"]["details"]["action_class"] == "internal_write"
    assert by_step["gmail.contract_pending_send_payload_from_draft_response"]["details"]["recipient"] == "bnlores@gmail.com"
    assert by_step["gmail.contract_pending_send_payload_without_explicit_recipient"]["details"]["recipient"] is None


def test_summarize_status_fails_on_failed_steps() -> None:
    status, message = _summarize_status(
        [
            {"step": "gmail.search_inbox", "status": "pass"},
            {"step": "tasks.list_default_tasks", "status": "fail"},
        ],
        _build_coverage_items(),
    )

    assert status == "fail"
    assert "1 audit step(s) failed" in message


def test_summarize_status_warns_when_coverage_is_partial_but_steps_pass() -> None:
    status, message = _summarize_status(
        [
            {"step": "gmail.search_inbox", "status": "pass"},
            {"step": "calendar.read_today", "status": "pass"},
            {"step": "tasks.list_default_tasks", "status": "pass"},
            {"step": "drive.direct_audit", "status": "skip"},
        ],
        _build_coverage_items(),
    )

    assert status == "warn"
    assert "not yet directly auditable" in message


@pytest.mark.asyncio
async def test_audit_tasks_canary_runs_create_verify_complete_and_cleanup_cycle() -> None:
    canary_title = "[AUDIT] PersonalAsst Google Tasks Canary TEST"
    create_result = "Task created successfully\n- ID: task123\n- Title: [AUDIT] PersonalAsst Google Tasks Canary TEST"
    visible_after_create = (
        "Tasks in list @default for user@example.com:\n"
        "- [AUDIT] PersonalAsst Google Tasks Canary TEST (ID: task123)\n"
        "  Status: needsAction\n"
    )
    visible_after_complete = (
        "Tasks in list @default for user@example.com:\n"
        "- [AUDIT] PersonalAsst Google Tasks Canary TEST (ID: task123)\n"
        "  Status: completed\n"
    )

    with (
        patch("src.google_audit._build_tasks_canary_title", return_value=canary_title),
        patch(
            "src.google_audit._call_workspace_tool_checked",
            new=AsyncMock(
                side_effect=[
                    create_result,
                    visible_after_create,
                    "updated",
                    visible_after_complete,
                    "deleted",
                ]
            ),
        ) as mock_call_workspace_tool_checked,
    ):
        steps = await _audit_tasks_canary("user@example.com")

    assert [step["step"] for step in steps] == [
        "tasks.canary_create",
        "tasks.canary_verify_created",
        "tasks.canary_complete",
        "tasks.canary_verify_completed",
        "tasks.canary_cleanup",
    ]
    assert all(step["status"] == "pass" for step in steps)
    assert steps[-1]["issue_type"] == "cleanup"
    assert steps[0]["details"]["task_id"] == "task123"

    assert mock_call_workspace_tool_checked.await_args_list[0].args == (
        "manage_task",
        {
            "user_google_email": "user@example.com",
            "action": "create",
            "task_list_id": "@default",
            "title": canary_title,
        },
    )
    assert mock_call_workspace_tool_checked.await_args_list[2].args == (
        "manage_task",
        {
            "user_google_email": "user@example.com",
            "action": "update",
            "task_list_id": "@default",
            "task_id": "task123",
            "status": "completed",
        },
    )
    assert mock_call_workspace_tool_checked.await_args_list[4].args == (
        "manage_task",
        {
            "user_google_email": "user@example.com",
            "action": "delete",
            "task_list_id": "@default",
            "task_id": "task123",
        },
    )
