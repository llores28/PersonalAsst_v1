# ADR: Expand Google audit with issue taxonomy, cleanup-safe Tasks canary checks, and routing guard regressions

- Status: Accepted
- Date: 2026-03-18

## Context

The original Google audit harness only reported step status as pass, fail, or skip and focused on read-only checks for Gmail, Calendar, and Tasks.

That left three practical gaps:

1. failures were not classified by cause, which made it harder to distinguish auth problems, tool-contract mismatches, coverage gaps, cleanup failures, and ordinary read verification issues
2. Google Tasks write-path regressions could slip through even when read-path checks were healthy
3. deterministic routing and policy boundaries for Gmail, Calendar, Tasks, and Drive were not protected by a focused regression matrix

## Decision

Expand the audit and regression coverage in three layers:

1. add step-level `issue_type` classification and an aggregate `issue_summary` to the Google audit output
2. keep `read_only` as the default audit mode, but add an explicit `canary` mode that performs a cleanup-safe Google Tasks create, read-back, complete, read-back, and delete cycle
3. add focused regression tests that keep deterministic Gmail, Calendar, and Google Tasks fast paths narrow while ensuring Drive remains outside those direct handlers and policy guardrails still classify each workspace path correctly

The Tasks canary must always attempt cleanup after creation so the audit does not silently leave test artifacts behind.

## Tradeoffs

This improves diagnostic value and write-path confidence without pretending that every granted Google scope is fully audited.

The tradeoff is that `canary` mode intentionally performs a small external write in Google Tasks, so it remains opt-in rather than the default. The routing matrix also protects the current deterministic boundaries, which keeps the tests stable and honest but does not replace future end-to-end Drive or broader Google write audits if deterministic contracts are added later.
