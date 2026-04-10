# ADR-2026-03-19 — Audit-First Capability Spec for Tool and Specialist Design

## Status
Accepted

## Context
PersonalAsst has deterministic direct audits for some Google Workspace surfaces, but new capabilities can still fail when user phrasing, routing overlap, follow-up confirmations, pending state, or fallback messaging are not designed up front. The Tool Factory specialist was also biased toward CLI generation without first proving the capability shape, routing boundaries, or audit story.

## Decision
Adopt an audit-first capability spec for new tools, MCP integrations, workflows, and specialist agents.

The spec requires:
- scenario matrix
- routing boundary matrix
- runtime wireframe
- audit plan
- implementation plan

The Tool Factory specialist must reference `docs/AUDIT_FIRST_SPECIALIST_TEMPLATE.md` before generating code or proposing delivery shape.

## Consequences
### Positive
- Reduces misrouting and generic fallback errors
- Forces explicit handling of follow-up confirmations and retries
- Makes specialist boundaries easier to reason about
- Improves auditability before shipping

### Tradeoffs
- Adds planning overhead before implementation
- Requires disciplined updates to tests and audit coverage

## Notes
This does not replace direct contract audits like `src.google_audit`. It standardizes how new capabilities are designed so those audits and regressions can be defined before implementation.
