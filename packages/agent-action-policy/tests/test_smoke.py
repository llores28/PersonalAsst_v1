"""Smoke tests for agent-action-policy as a standalone package."""

from __future__ import annotations

import pytest


def test_public_api_imports() -> None:
    from agent_action_policy import (
        classify_action_request,
        ActionPolicyDecision,
        is_contextual_follow_up_confirmation,
        build_action_policy_context_block,
        should_append_action_policy_context,
    )
    assert callable(classify_action_request)
    assert callable(is_contextual_follow_up_confirmation)
    assert callable(build_action_policy_context_block)
    assert callable(should_append_action_policy_context)


@pytest.mark.parametrize("user_message,expected_class", [
    # External side-effects (cue substrings — match exactly as in
    # _EXTERNAL_SIDE_EFFECT_CUES). Approval gate fires.
    ("please send email now", "external_side_effect"),
    ("can you forward email to bob", "external_side_effect"),
    ("schedule meeting with the team", "external_side_effect"),
    ("create calendar event for monday", "external_side_effect"),
    # Drafts — approval gate doesn't fire (drafts are reversible).
    ("draft an email to bob about the demo", "draft"),
    # Internal writes — assistant-managed state, optional confirmation.
    ("create a reminder for tomorrow", "internal_write"),
    # Reads — no approval, default fall-through.
    ("what's on my calendar today", "read"),
    ("list my tasks", "read"),
])
def test_classification_buckets(user_message: str, expected_class: str) -> None:
    from agent_action_policy import classify_action_request

    decision = classify_action_request(user_message)
    assert decision.action_class == expected_class, (
        f"{user_message!r} classified as {decision.action_class!r}, "
        f"expected {expected_class!r}. Rationale: {decision.rationale}"
    )


def test_external_side_effect_requires_confirmation() -> None:
    """When a clearly destructive cue fires, the gate flips to required."""
    from agent_action_policy import classify_action_request

    decision = classify_action_request("please send email to alice")
    assert decision.action_class == "external_side_effect"
    assert decision.requires_confirmation is True


def test_read_does_not_require_confirmation() -> None:
    from agent_action_policy import classify_action_request

    decision = classify_action_request("what's on my calendar")
    assert decision.requires_confirmation is False


def test_contextual_follow_up_confirmation_detected() -> None:
    """Whole-message exact-match confirmation cues — these signal "yes
    proceed with the previously-staged action" rather than starting a
    new request. Match the literal cue list in the module."""
    from agent_action_policy import is_contextual_follow_up_confirmation

    for confirm in ("yes", "send it", "yes send it", "go ahead"):
        assert is_contextual_follow_up_confirmation(confirm), (
            f"Should be a follow-up confirmation: {confirm!r}"
        )


def test_arbitrary_chitchat_is_not_a_follow_up() -> None:
    from agent_action_policy import is_contextual_follow_up_confirmation

    assert not is_contextual_follow_up_confirmation("how's the weather?")
    # Long sentences that *contain* "yes" but aren't pure confirmations
    # must not match — the function uses exact whole-message comparison.
    assert not is_contextual_follow_up_confirmation("yes I'd like to also do something else")
