"""Runtime action classification for approval-sensitive requests."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

ActionClass = Literal["read", "draft", "internal_write", "external_side_effect"]

_EXTERNAL_SIDE_EFFECT_CUES = (
    "send email",
    "send this email",
    "reply and send",
    "forward email",
    "share file",
    "upload file",
    "delete file",
    "remove file",
    "move file",
    "add to my calendar",
    "add to my schedule",
    "add to my schedual",
    "create event",
    "create calendar event",
    "schedule meeting",
    "book meeting",
    "delete event",
    "cancel event",
    "reschedule event",
)

_DRAFT_CUES = (
    "draft",
    "compose",
    "write an email",
    "write email",
    "prepare an email",
    "draft response",
)

_INTERNAL_WRITE_CUES = (
    "remember that",
    "remember this",
    "forget ",
    "forget all",
    "clear memories",
    "remind me",
    "set a reminder",
    "create reminder",
    "schedule a reminder",
    "add to my task",
    "add to my todo",
    "add to my to do",
    "create a task",
    "create task",
    "create a todo",
    "create todo",
    "pause schedule",
    "cancel schedule",
    "morning brief",
)

_READ_HINTS = (
    "what",
    "show",
    "check",
    "list",
    "find",
    "search",
    "read",
    "look up",
    "do i have",
    "am i free",
)

_CONTEXTUAL_CONFIRMATION_CUES = (
    "yes",
    "yes please",
    "yeah",
    "yep",
    "ok",
    "okay",
    "retry",
    "try again",
    "please retry",
    "go ahead",
    "do it",
    "send",
    "send it",
    "send now",
    "send the email",
    "send the draft",
    "send the draft email",
    "yes send it",
    "ready to send",
)


@dataclass(frozen=True)
class ActionPolicyDecision:
    action_class: ActionClass
    requires_confirmation: bool
    rationale: str


def is_contextual_follow_up_confirmation(user_message: str) -> bool:
    normalized = " ".join(user_message.strip().lower().split())
    return normalized in _CONTEXTUAL_CONFIRMATION_CUES


def classify_action_request(user_message: str) -> ActionPolicyDecision:
    lowered = user_message.strip().lower()

    if is_contextual_follow_up_confirmation(lowered):
        return ActionPolicyDecision(
            action_class="internal_write",
            requires_confirmation=False,
            rationale="This looks like approval to continue the immediately preceding pending action from recent conversation context.",
        )

    if any(cue in lowered for cue in _EXTERNAL_SIDE_EFFECT_CUES):
        return ActionPolicyDecision(
            action_class="external_side_effect",
            requires_confirmation=True,
            rationale="This request would change an external system like Gmail, Calendar, or Drive.",
        )

    if any(verb in lowered for verb in ("add ", "create ", "schedule ", "book ", "put ")) and any(
        target in lowered for target in ("calendar", "schedule", "schedual", "event", "meeting")
    ):
        return ActionPolicyDecision(
            action_class="external_side_effect",
            requires_confirmation=True,
            rationale="This request would change an external system like Gmail, Calendar, or Drive.",
        )

    if any(cue in lowered for cue in _DRAFT_CUES):
        return ActionPolicyDecision(
            action_class="draft",
            requires_confirmation=False,
            rationale="This request prepares content for review before anything is sent or changed.",
        )

    if any(verb in lowered for verb in ("complete", "mark ")) and any(
        target in lowered for target in ("task", "todo", "to-do", "it", "this", "that")
    ):
        return ActionPolicyDecision(
            action_class="internal_write",
            requires_confirmation=False,
            rationale="This follow-up updates task state using the most recent task context.",
        )

    if any(verb in lowered for verb in ("add ", "create ", "set ", "schedule ")) and any(
        target in lowered for target in ("task", "todo", "to do")
    ):
        return ActionPolicyDecision(
            action_class="internal_write",
            requires_confirmation=True,
            rationale="This request changes assistant-managed state like reminders, tasks, or schedules.",
        )

    if any(cue in lowered for cue in _INTERNAL_WRITE_CUES):
        requires_confirmation = any(keyword in lowered for keyword in ("forget", "clear", "pause", "cancel", "remind me", "set a reminder", "create reminder", "schedule a reminder", "add to my task", "add to my todo", "add to my to do", "create a task", "create task", "create a todo", "create todo", "morning brief"))
        rationale = "This request changes assistant-managed state like memory or schedules."
        return ActionPolicyDecision(
            action_class="internal_write",
            requires_confirmation=requires_confirmation,
            rationale=rationale,
        )

    if any(hint in lowered for hint in _READ_HINTS):
        return ActionPolicyDecision(
            action_class="read",
            requires_confirmation=False,
            rationale="This request reads or summarizes information without changing state.",
        )

    return ActionPolicyDecision(
        action_class="read",
        requires_confirmation=False,
        rationale="No strong write signal was detected, so this is treated as a read by default.",
    )


def build_action_policy_context_block(user_message: str) -> str:
    decision = classify_action_request(user_message)
    lines = [
        "## Action Policy Context",
        f"Action Class: {decision.action_class}",
        f"Confirmation Required: {'yes' if decision.requires_confirmation else 'no'}",
        f"Rationale: {decision.rationale}",
    ]
    if decision.action_class == "external_side_effect":
        lines.append("Policy: confirm exact details before executing the action. Draft first when possible.")
    elif decision.action_class == "internal_write":
        lines.append("Policy: confirm destructive or schedule-changing writes before execution.")
    elif decision.action_class == "draft":
        lines.append("Policy: drafting is allowed without sending; present the draft for approval.")
    else:
        lines.append("Policy: proceed directly if the request is clear.")
    return "\n".join(lines)


def should_append_action_policy_context(user_message: str) -> bool:
    return classify_action_request(user_message).action_class != "read"


def append_action_policy_context(user_message: str) -> str:
    if not should_append_action_policy_context(user_message):
        return user_message
    return f"{user_message}\n\n{build_action_policy_context_block(user_message)}"


def build_task_local_context(user_message: str) -> str:
    decision = classify_action_request(user_message)
    lines = [
        f"User request: {user_message}",
        f"Action class: {decision.action_class}",
        f"Confirmation required: {'yes' if decision.requires_confirmation else 'no'}",
        f"Policy note: {decision.rationale}",
    ]
    if is_contextual_follow_up_confirmation(user_message):
        lines.extend(
            [
                "Follow-up type: contextual confirmation.",
                "Instruction: Use the pending action details from the Recent Context section.",
                "Instruction: If the immediately preceding assistant turn asked for confirmation and the details are already clear, execute that pending action now instead of treating this as a new standalone request.",
            ]
        )
    return "\n".join(lines)
