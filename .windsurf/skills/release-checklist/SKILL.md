---
name: release-checklist
description: Pre-release verification checklist for PersonalAsst
---

# Release Checklist

## Before Release

1. [ ] All tests pass: `pytest tests/ -v`
2. [ ] Lint clean: `ruff check src/ tests/`
3. [ ] Type check clean: `mypy src/ --strict`
4. [ ] Docker builds: `docker compose build --no-cache`
5. [ ] Fresh start works: `docker compose down -v && docker compose up -d`
6. [ ] Migrations apply: `docker compose exec assistant alembic upgrade head`
7. [ ] Bot responds to `/start` in Telegram
8. [ ] `/help` returns correct command list
9. [ ] Allowlist blocks unauthorized users
10. [ ] Cost tracking functional: `/stats` returns data
11. [ ] No secrets in committed code
12. [ ] `.env.example` up to date with all required vars
13. [ ] `AGENTS.md` navigation table current
