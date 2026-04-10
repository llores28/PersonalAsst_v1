# ADR: Override workspace-mcp OAuth response template

- Status: Accepted
- Date: 2026-03-16

## Context

The Google Workspace MCP container serves its own OAuth callback success page. The upstream page used a static countdown label and a bare `window.close()` call, which left the countdown frozen and the close button non-functional in common browser flows.

## Decision

Keep the upstream container image, but mount a local override for `auth/oauth_responses.py` from this repository. The override preserves the existing page design while adding a live countdown and a browser-safe close fallback message.

## Tradeoffs

This keeps the fix small and reversible, but it means the repository now owns one targeted override of upstream sidecar behavior that should be reviewed when the sidecar image changes.
