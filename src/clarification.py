"""Shared clarification contracts for missing user input."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal
from uuid import uuid4

ClarificationStatus = Literal["needs_input"]


@dataclass(frozen=True)
class NeedsInputResult:
    missing_fields: tuple[str, ...]
    user_prompt: str
    pending_action_type: str
    safe_to_retry: bool = True
    context: dict[str, Any] = field(default_factory=dict)
    resume_token: str = field(default_factory=lambda: uuid4().hex)
    status: ClarificationStatus = "needs_input"

    def to_payload(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["missing_fields"] = list(self.missing_fields)
        return payload


def build_needs_input_result(
    *,
    missing_fields: list[str] | tuple[str, ...],
    user_prompt: str,
    pending_action_type: str,
    safe_to_retry: bool = True,
    context: dict[str, Any] | None = None,
    resume_token: str | None = None,
) -> NeedsInputResult:
    return NeedsInputResult(
        missing_fields=tuple(missing_fields),
        user_prompt=user_prompt,
        pending_action_type=pending_action_type,
        safe_to_retry=safe_to_retry,
        context=context or {},
        resume_token=resume_token or uuid4().hex,
    )
