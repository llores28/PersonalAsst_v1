---
description: Prepare a handoff document when transitioning work to another contributor
---

1. Update `AGENTS.md` with current repo navigation and verified commands.
2. Ensure `docs/DEVELOPER_GUIDE.md` is current.
3. Run full quality gates:
   ```
   ruff check src/ tests/
   mypy src/ --strict
   pytest tests/ -v
   ```
4. Verify Docker Compose starts cleanly:
   ```
   docker compose down -v
   docker compose up -d
   docker compose exec assistant alembic upgrade head
   ```
5. List any known issues or TODOs:
   ```
   grep -rn "TODO" src/ tests/ --include="*.py"
   ```
6. Update `docs/HANDOFF.md` with:
   - Current phase status (which PRD phase is complete)
   - Known issues and workarounds
   - Next steps for the incoming contributor
   - Any environment-specific notes
7. Commit all changes with message: `chore: prepare handoff`
