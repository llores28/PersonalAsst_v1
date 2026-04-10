"""Tests for runtime action classification and approval context."""

import pytest

from src.action_policy import (
    append_action_policy_context,
    build_task_local_context,
    classify_action_request,
)


class TestActionPolicyClassification:
    """Action class detection for approval-sensitive requests."""

    def test_classify_task_creation_as_internal_write(self) -> None:
        decision = classify_action_request(
            "please add to my task for tomorrow to place grocery order at 9am on the app h-e-b"
        )
        assert decision.action_class == "internal_write"
        assert decision.requires_confirmation is True

    def test_classify_draft_request(self) -> None:
        decision = classify_action_request("draft an email to Sam about the meeting")
        assert decision.action_class == "draft"
        assert decision.requires_confirmation is False

    def test_classify_internal_write_request(self) -> None:
        decision = classify_action_request("remember that I prefer morning meetings")
        assert decision.action_class == "internal_write"

    def test_classify_task_completion_follow_up_as_internal_write(self) -> None:
        decision = classify_action_request("Mark as completed on this task")
        assert decision.action_class == "internal_write"
        assert decision.requires_confirmation is False

    @pytest.mark.parametrize(
        ("message", "action_class", "requires_confirmation"),
        [
            ("check my email", "read", False),
            ("show my drive files", "read", False),
            ("add lunch with Sam to my calendar next Sunday at 1pm", "external_side_effect", True),
            ("share file from my drive with Sam", "external_side_effect", True),
            ("please add to my task for tomorrow to place grocery order at 9am", "internal_write", True),
            ("complete this task", "internal_write", False),
            ("send it", "internal_write", False),
            ("send the draft email", "internal_write", False),
            ("yes send it", "internal_write", False),
            ("send the email", "internal_write", False),
            ("do it", "internal_write", False),
        ],
    )
    def test_workspace_policy_matrix(
        self,
        message: str,
        action_class: str,
        requires_confirmation: bool,
    ) -> None:
        decision = classify_action_request(message)

        assert decision.action_class == action_class
        assert decision.requires_confirmation is requires_confirmation


class TestActionPolicyContextFormatting:
    """Formatting helpers for runtime policy context."""

    def test_append_action_policy_context_for_external_request(self) -> None:
        prepared = append_action_policy_context("send email to Sam confirming the plan")
        assert "## Action Policy Context" in prepared
        assert "Action Class: external_side_effect" in prepared
        assert "Confirmation Required: yes" in prepared

    def test_append_action_policy_context_leaves_read_request_unchanged(self) -> None:
        message = "tell me a joke"
        assert append_action_policy_context(message) == message

    def test_build_task_local_context_includes_action_class(self) -> None:
        context = build_task_local_context("remember that I prefer morning meetings")
        assert "User request: remember that I prefer morning meetings" in context
        assert "Action class: internal_write" in context
