# Security & Secrets Policy

## Secrets Management

- **Never** hardcode API keys, tokens, or passwords in source code.
- **Never** log secrets (mask in log output).
- All secrets via environment variables loaded through `src/settings.py` (Pydantic Settings).
- Docker Compose uses `.env` file (gitignored). `.env.example` is committed with placeholder values.
- Google OAuth tokens stored in Docker volume `workspace_tokens` — not in code or DB.

## Files That Must Be Gitignored

```
.env
*.pyc
__pycache__/
tools/*/output/
*.db
```

## PII Handling

- User data from Google Workspace (emails, contacts, calendar) is PII.
- Output guardrail checks all responses for PII patterns (SSN, CC numbers).
- Mem0 memories may contain PII — stored only in self-hosted PostgreSQL/Qdrant.
- Audit logs may reference PII — stored in PostgreSQL, never exposed via API.
- `/forget` command allows user to delete specific memories.

## Authentication

- Telegram user ID allowlist stored in `allowed_users` PostgreSQL table.
- Owner seeded from `OWNER_TELEGRAM_ID` env var on first boot.
- Owner can add/remove users via `/allow` and `/revoke` Telegram commands.
- Unauthorized messages are silently ignored (no response to prevent enumeration).

## API Key Scoping

- `OPENAI_API_KEY` — used by agent framework only, never passed to generated tools.
- `TELEGRAM_BOT_TOKEN` — used by aiogram only.
- `GOOGLE_OAUTH_*` — used by workspace-mcp container only.
- Generated CLI tools run in subprocess with **empty env** (no inherited secrets).

## Cost Control

- Daily and monthly cost caps configured via `DAILY_COST_CAP_USD` / `MONTHLY_COST_CAP_USD`.
- Every OpenAI API call is tracked in `daily_costs` table.
- At 80% of daily cap → user warned. At 100% → requests blocked until midnight.
- `/stats` command shows current usage.

## Dependency Policy

- Pin all dependencies in `requirements.txt` with exact versions.
- No `pip install` at runtime — all deps baked into Docker image.
- Review new dependencies for known CVEs before adding.
