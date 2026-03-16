---
description: Pre-release verification checklist for PersonalAsst
---

1. Run all tests:
   ```
   pytest tests/ -v
   ```
2. Lint and format:
   ```
   ruff check src/ tests/
   ruff format --check src/ tests/
   ```
3. Type check:
   ```
   mypy src/ --strict
   ```
4. Docker build:
   ```
   docker compose build --no-cache
   ```
5. Fresh start test:
   ```
   docker compose down -v
   docker compose up -d
   ```
6. Verify migrations:
   ```
   docker compose exec assistant alembic upgrade head
   ```
7. Send `/start` to Telegram bot — verify response.
8. Send `/help` — verify command list.
9. Verify no secrets in code:
   ```
   grep -rn "sk-" src/ --include="*.py"
   ```
10. Verify `.env.example` is up to date.
