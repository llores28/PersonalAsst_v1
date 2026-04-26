# ADR-2026-04-26 — Workspace-MCP Token Persistence Pinning

**Status:** Accepted
**Date:** April 26, 2026
**Deciders:** Owner

## Context

The OAuth heartbeat shipped same-day (see [ADR-2026-04-26-oauth-heartbeat-and-reauth-nudge.md](ADR-2026-04-26-oauth-heartbeat-and-reauth-nudge.md)) classifies any Workspace tool returning `[AUTH ERROR]` as `auth_failed` and sends a Telegram nudge. That's the right behavior — *unless we caused the auth failure ourselves by losing the persisted tokens.*

A read of the upstream `taylorwilsdon/google_workspace_mcp` source (`core/server.py`, `auth/credential_store.py`, `.env.oauth21` example) revealed three default settings that conspire to silently drop every persisted OAuth token on `docker compose up --build`:

1. **`WORKSPACE_MCP_OAUTH_PROXY_STORAGE_BACKEND` defaults to `memory` on Linux.** Proxy state — including the OAuth-2.1 PKCE handshake artifacts — is wiped on every container restart, not just rebuild.
2. **`WORKSPACE_MCP_CREDENTIALS_DIR` defaults to `~/.google_workspace_mcp/credentials`.** Inside the container, that's on the writable layer, *not* the named volume. Our pre-existing `workspace_tokens:/data/tokens` mount was effectively unused: tokens were being written to a path the volume didn't cover.
3. **The Fernet token-encryption key derives from `GOOGLE_OAUTH_CLIENT_SECRET`** if `FASTMCP_SERVER_AUTH_GOOGLE_JWT_SIGNING_KEY` is unset. Stable until the OAuth client secret is rotated (perfectly normal operational practice, e.g. after a leak), at which point every previously-encrypted token becomes unreadable — silently.

Combined with the Mon 09:00 UTC heartbeat, any of these three would trigger a *mass* false-positive nudge storm — looking exactly like a Google revocation event.

## Decision

### Pin all three defaults explicitly in [docker-compose.yml](../docker-compose.yml)
| Variable | Value | Why |
|---|---|---|
| `WORKSPACE_MCP_OAUTH_PROXY_STORAGE_BACKEND` | `disk` | Proxy state survives restart. |
| `WORKSPACE_MCP_OAUTH_PROXY_DISK_DIRECTORY` | `/data/oauth-proxy` | Lives in the named volume. |
| `WORKSPACE_MCP_CREDENTIALS_DIR` | `/data/credentials` | Lives in the named volume. |
| `FASTMCP_SERVER_AUTH_GOOGLE_JWT_SIGNING_KEY` | `${WORKSPACE_MCP_SIGNING_KEY}` | Independent of OAuth client secret rotation. |

### Move the volume mount from `/data/tokens` → `/data`
A single mount at `/data` covers *both* `/data/credentials` and `/data/oauth-proxy` without needing two volumes. The previous `/data/tokens` path was a dead mount; nothing wrote to it.

### Inline the rationale as a multi-paragraph comment on the service
Future cleanup passes (or contributors who read the compose file top-to-bottom) will see *why* each env var is pinned, not just that it is. The defaults look reasonable in isolation; the trap only emerges when you understand the heartbeat depends on persistence.

### `scripts/ensure_workspace_mcp_key.py` for one-time bootstrap
Idempotent. Reads `.env`. If `WORKSPACE_MCP_SIGNING_KEY` is missing or empty, generates a 64-char hex key via `secrets.token_hex(32)` and writes it. If present and non-empty, prints a one-line summary and exits 0 — never overwrites. Tested against missing/empty/present cases.

We chose hex over Fernet's native 44-char base64 because:
- The image's `derive_jwt_key()` accepts any sufficiently long string (≥12 chars warned).
- Hex is easier to manually inspect and copy without losing characters to terminal selection quirks.
- 64 hex chars = 256 bits of entropy, comparable to a Fernet key.

### Operator documentation in [RUNBOOK.md](RUNBOOK.md)
- Why each setting is pinned (linked to this ADR).
- First-time setup: `cp .env.example .env` → `python scripts/ensure_workspace_mcp_key.py` → `docker compose up -d`.
- Recovery procedures for: lost `.env`, deleted volume (`docker compose down -v`), and a verification command exercising the round-trip via `weekly_oauth_heartbeat()`.

## Consequences

### Positive
- A `docker compose up --build` no longer wipes tokens.
- A `GOOGLE_OAUTH_CLIENT_SECRET` rotation no longer invalidates persisted tokens.
- The heartbeat's auth-failed signal is now trustworthy — a nudge means Google revoked the token, not "we forgot it."
- The setup script is idempotent and safe to re-run, so it can be added to onboarding scripts and CI.

### Trade-offs / Limitations
- **One-time migration cost for existing deployments.** Tokens that lived on the writable layer at `~/.google_workspace_mcp/credentials` are gone. Each connected user has to run `/connect google` once. Documented in CHANGELOG and RUNBOOK.
- **`WORKSPACE_MCP_SIGNING_KEY` becomes a high-blast-radius secret.** Lose it (e.g. accidental commit to git, lost machine, deleted `.env`) and every persisted token is permanently unreadable — same effect as the deleted-volume case. RUNBOOK calls this out explicitly.
- **No automatic key rotation.** Rotating the signing key invalidates every persisted token; we treat this as an expected once-per-incident operation, not routine. Contrast with the OAuth client secret, which is now decoupled from token decryption and can be rotated freely.
- **Volume mount path change is breaking** for anyone who manually inspected `/data/tokens` on the running container. The volume itself is unchanged (same named volume, same data); the in-container mount path differs.

## Alternatives Considered

- **Leave the defaults; trust them to work.** Rejected after reading the source — the Linux default for the proxy backend (`memory`) is documented in `.env.oauth21` as something the operator MUST override. We were one rebuild away from a heartbeat-induced false-positive incident.
- **Two separate named volumes** (one for credentials, one for oauth-proxy). Rejected as needless complexity; both directories are tightly coupled (both belong to one MCP service) and have the same backup/restore lifetime.
- **Generate the signing key inside `docker compose up`** via a startup script. Rejected — `.env` interpolation happens before the container starts, and we want the key to live in `.env` so the operator can back it up alongside other secrets, copy it across machines, etc.
- **Use Fernet's native base64 format for the key.** Rejected per the entropy/UX argument above; the image's `derive_jwt_key()` handles either.
- **Switch to GCS-backed credential storage** (`WORKSPACE_MCP_CREDENTIAL_STORE_BACKEND=gcs`). Rejected for now — single-user system with self-hosted infra (HC-1); adding cloud dependency increases blast radius for marginal benefit. Reconsider if Atlas grows to multi-user.
