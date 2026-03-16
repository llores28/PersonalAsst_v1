---
description: Run all quality gates before committing changes
---

1. Lint check:
   ```
   ruff check src/ tests/
   ```
2. Format check:
   ```
   ruff format --check src/ tests/
   ```
3. Type check:
   ```
   mypy src/ --strict
   ```
4. Run tests:
   ```
   pytest tests/ -v --tb=short
   ```
5. If any step fails, fix issues before committing.
