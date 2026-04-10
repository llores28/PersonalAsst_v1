# ADR-2026-03-19 — Needs-Input Clarification Contract

## Status
Accepted

## Context
PersonalAsst currently mixes three different outcomes in specialist and direct-handler flows:
- successful execution
- user-correctable missing input
- true tool or integration failure

When those outcomes are not separated, missing user input can fall through to generic fallback errors or guardrail-style responses. Gmail draft follow-up handling showed this clearly when a draft existed but the recipient email address was still missing.

## Decision
Introduce a shared `needs_input` clarification contract for user-correctable missing fields.

The contract includes:
- `status`
- `missing_fields`
- `user_prompt`
- `pending_action_type`
- `resume_token`
- `safe_to_retry`
- `context`

Use this contract when:
- a required business field is missing
- the value cannot be safely inferred
- multiple plausible values exist

Do not use this contract when:
- the system can deterministically resolve the field
- the missing value is an internal identifier
- the issue is an auth, MCP, or tool failure

## Pilot
The first pilot is Gmail drafted-send follow-up:
- missing recipient email -> return `needs_input`
- store generic pending clarification state in Redis
- clear clarification state when the recipient is later supplied or the send succeeds

## Consequences
### Positive
- reduces generic fallback errors for user-correctable missing input
- creates a reusable shape for future calendar, task, and MCP clarification flows
- makes audits able to test missing-input behavior directly

### Tradeoffs
- adds a small amount of structured state management
- full adoption still requires each specialist to define required vs inferable fields
