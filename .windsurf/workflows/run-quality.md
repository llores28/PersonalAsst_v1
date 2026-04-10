---
description: Run all quality gates before committing changes
auto_execution_mode: 3
---

0. Ensure dev test tooling is installed:
   ```
   python -m pip install -r requirements-dev.txt
   ```
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
4. During debugging, run the smallest relevant test file first:
   ```
   python -m pytest tests/test_orchestrator.py -v
   ```
5. Run the full test suite:
   ```
   python -m pytest tests/ -v --tb=short
   ```
6. If any step fails, fix issues before committing.
