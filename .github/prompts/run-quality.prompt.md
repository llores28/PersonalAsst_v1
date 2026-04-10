---
name: run-quality
description: Run PersonalAsst quality gates with repo-approved commands
agent: agent
---

Run the repo quality workflow for PersonalAsst.

Read first:

- `AGENTS.md`
- `.windsurf/workflows/run-quality.md`
- `.windsurf/skills/run-quality-gates/SKILL.md`

Execution rules:

- `AGENTS.md` takes precedence over the legacy Windsurf workflow if they differ.
- Use only verified commands from `AGENTS.md` or `docs/DEVELOPER_GUIDE.md`.
- Do not install dependencies or run destructive commands without user approval.

Suggested sequence:

1. `ruff check src/ tests/`
2. `ruff format --check src/ tests/`
3. `mypy src/ --strict`
4. While debugging, run the smallest relevant pytest target first.
5. Before finishing, run `python -m pytest tests/ -v`

Report:

- Passed, failed, and skipped steps
- Root causes for failures
- Recommended next fixes
