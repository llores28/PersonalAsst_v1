# Audit-First Specialist Template

Use this template before building or changing any tool, CLI, MCP integration, workflow, or specialist agent.

## 1) Request Summary
- User outcome:
- Primary job to be done:
- Why the current system is insufficient:
- Proposed capability name:

## 2) Capability Classification
- Delivery shape: `cli` | `function_tool` | `mcp` | `specialist_agent` | `hybrid`
- Why this shape is preferred:
- Why the rejected shapes were rejected:
- Owner agent or orchestrator entry point:
- External systems touched:
- Action class: `read` | `draft` | `internal_write` | `external_side_effect`
- Confirmation policy:

## 3) User Scenario Matrix
List the ways a user may ask for the same job to be done.

For each row capture:
- Scenario ID
- Natural-language phrasing
- Normalized intent
- Required context
- Expected route
- Expected tool / MCP / specialist
- Confirmation needed?
- Expected success response
- Expected failure response
- Audit coverage type (`direct_contract`, `routed_regression`, `canary_write`, `coverage_gap`)

Minimum scenarios to cover:
- Happy-path explicit request
- Short / incomplete phrasing
- Follow-up confirmation
- Follow-up correction
- Retry after failure
- Ambiguous request that overlaps with another specialist
- Missing parameter / missing context
- Unauthorized / disconnected integration
- Tool contract failure
- Fallback behavior when the direct route is unavailable

## 4) Routing Boundary Matrix
Document what should happen for nearby intents so the orchestrator and specialists do not overlap.

For each neighboring intent capture:
- Phrase example
- Should route here? yes/no
- If no, where should it route?
- Why

Required comparisons:
- Tool vs specialist
- Specialist vs another specialist
- MCP direct contract vs LLM-routed behavior
- Read vs draft vs write
- Fresh request vs contextual follow-up

## 5) Runtime Wireframe
Describe the runtime path end to end.

1. User message enters:
2. Pre-routing normalization:
3. Action policy classification:
4. Safety guardrail behavior:
5. Direct handler checks:
6. Specialist / tool / MCP execution path:
7. Pending state storage, if any:
8. Success formatting:
9. Failure formatting:
10. Retry / reconnect guidance:

## 6) Contract Definition
### Inputs
- Required parameters:
- Optional parameters:
- Derived parameters:
- Validation rules:

### Outputs
- Success shape:
- Error shape:
- User-facing message rules:
- Logging expectations:

### State
- Pending state keys:
- Conversation dependencies:
- Idempotency expectations:
- Cleanup rules:

## 7) Specialist Instruction Scaffold
Use these sections in the specialist prompt.

### Role
- What this specialist owns

### Scope
- What it does
- What it must not do
- Which neighboring specialists own adjacent work

### Routing Rules
- Phrases that should route here
- Phrases that should never route here
- Follow-up phrases that should reuse pending state

### Execution Rules
- Draft-first or direct-execute policy
- Required confirmations
- Parameter resolution rules
- Reconnect / retry behavior

### Error Handling
- Exact targeted error style
- When to ask clarifying questions
- When to stop and escalate

## 8) Audit Plan
Every new capability must define both deterministic and routed coverage.

### A. Local Contract Checks
Add fast deterministic checks for:
- Intent classification
- Routing boundaries
- Parameter extraction
- Pending-state extraction
- Success / failure parser behavior

### B. Direct Contract Audit
If the capability has a direct tool or MCP path, add checks for:
- Tool reachable
- Minimal safe read path
- Required parameter validation
- Deterministic parse of the tool response

### C. Routed Regression Audit
Add prompted scenarios for:
- Explicit request
- Ambiguous request
- Follow-up confirmation
- Retry after tool failure
- Missing context clarification

### D. Canary Write Audit
If the capability mutates external state, specify whether a cleanup-safe canary exists.
- If yes, define create / verify / cleanup steps.
- If no, explain why and add local contract coverage plus non-destructive routed tests.

## 9) Test Plan
Required tests:
- Unit test for classification / parsing
- Unit test for direct handler or tool wrapper
- Regression test for overlapping routing
- Regression test for failure messaging
- Smoke path that proves one end-to-end success path

## 10) Shipping Checklist
- Contracts updated first
- Producers and consumers updated
- Tests added
- Audit coverage added or explicitly deferred
- Targeted user-facing error messages added
- Retry / reconnect guidance added
- Handoff note updated

## 11) Output Format For Specialists
Before implementation, the specialist should present:
- Capability classification
- Scenario matrix summary
- Routing boundary summary
- Runtime wireframe summary
- Audit plan summary
- Implementation plan

## 12) Special Guidance For Tool Factory
When the Tool Factory specialist is asked to build something:
- Start with this template before generating code.
- If the capability is not a good CLI fit, say so explicitly and propose `function_tool`, `mcp`, or `specialist_agent` instead.
- Do not jump straight to code if the routing, follow-up, or audit story is unclear.
- Every proposal must name the audit entry points and the regression tests that will prove the capability works.
