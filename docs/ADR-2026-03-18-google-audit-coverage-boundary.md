# ADR: Audit only repo-wired Google capabilities and report coverage gaps explicitly

- Status: Accepted
- Date: 2026-03-18

## Context

PersonalAsst's Google OAuth consent screen currently grants broader access than the repository directly exercises in deterministic code paths.

This creates a risk that an audit could claim end-to-end coverage for capabilities that are:

1. granted by Google but not implemented in this repo
2. only exposed through model-routed behavior rather than verified direct tool contracts
3. difficult to verify or clean up safely without inventing API assumptions

## Decision

Add a read-only Google audit harness that:

1. runs inside the assistant container against the live `workspace-mcp` network and environment
2. directly verifies only repo-wired Google capabilities with known tool contracts
3. reports broader consent-screen capabilities as `partial` or `uncovered` instead of treating them as audited
4. emits structured results with step-level status, raw output excerpts, and explicit coverage boundaries

The first version directly audits Gmail, Calendar, and Tasks with safe read checks. Drive is reported as partial because this repo currently exposes it through the model-routed `manage_drive` path without a deterministic direct contract in local code. Chat, Contacts, Docs, Sheets, Slides, Forms, Apps Script, and related scopes are reported as uncovered because they are not currently wired in this repository.

## Tradeoffs

This keeps the audit honest, reproducible, and low risk. The tradeoff is that a `warn` result can still be the expected healthy state when all implemented checks pass but repo coverage is intentionally incomplete. Future work can tighten that boundary by adding deterministic direct contracts and cleanup-safe write verification for additional Google services.
