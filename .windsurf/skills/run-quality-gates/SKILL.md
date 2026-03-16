---
name: run-quality-gates
description: Run all quality gates (lint, type check, tests) before committing
---

# Run Quality Gates

## Full Quality Check

```bash
# 1. Lint
ruff check src/ tests/

# 2. Format check
ruff format --check src/ tests/

# 3. Type check
mypy src/ --strict

# 4. Tests
pytest tests/ -v --tb=short

# 5. Docker build
docker compose build --no-cache
```

## Quick Check (pre-commit)

```bash
ruff check src/ tests/ && mypy src/ && pytest tests/ -v --tb=short
```

## Fix Common Issues

```bash
# Auto-fix lint issues
ruff check --fix src/ tests/

# Auto-format
ruff format src/ tests/
```
