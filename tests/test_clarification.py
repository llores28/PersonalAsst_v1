from src.clarification import build_needs_input_result


def test_build_needs_input_result_returns_structured_payload() -> None:
    result = build_needs_input_result(
        missing_fields=("recipient_email",),
        user_prompt="What email address should I use?",
        pending_action_type="gmail_send_draft",
        context={"subject": "Electric Bill Due Soon"},
    )

    payload = result.to_payload()

    assert payload["status"] == "needs_input"
    assert payload["missing_fields"] == ["recipient_email"]
    assert payload["user_prompt"] == "What email address should I use?"
    assert payload["pending_action_type"] == "gmail_send_draft"
    assert payload["safe_to_retry"] is True
    assert payload["context"] == {"subject": "Electric Bill Due Soon"}
    assert isinstance(payload["resume_token"], str)
    assert payload["resume_token"]
