---
description: Set up the test environment for PersonalAsst
auto_execution_mode: 2
---

1. Ensure Python 3.12+ is installed: `python --version`
2. Create and activate virtual environment:
   ```
   python -m venv .venv
   .venv\Scripts\activate
   ```
3. Install dependencies:
   ```
   python -m pip install -r requirements-dev.txt
   ```
4. Start Docker Compose stack (for integration tests):
   ```
   docker compose up -d postgres redis qdrant
   ```
5. Verify test discovery first:
   ```
   python -m pytest tests/ --collect-only
   ```
6. Run the smallest relevant test file while debugging:
   ```
   python -m pytest tests/test_orchestrator.py -v
   ```
7. Run the full suite before finishing:
   ```
   python -m pytest tests/ -v --tb=short
   ```
