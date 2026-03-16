---
description: Set up the test environment for PersonalAsst
---

1. Ensure Python 3.12+ is installed: `python --version`
2. Create and activate virtual environment:
   ```
   python -m venv .venv
   .venv\Scripts\activate
   ```
3. Install dependencies:
   ```
   pip install -r requirements.txt
   pip install pytest pytest-asyncio pytest-cov ruff mypy
   ```
4. Start Docker Compose stack (for integration tests):
   ```
   docker compose up -d postgres redis qdrant
   ```
5. Run test suite to verify:
   ```
   pytest tests/ -v --tb=short
   ```
