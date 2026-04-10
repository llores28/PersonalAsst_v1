---
name: smoketest
description: Run a practical smoke test for PersonalAsst using current repo policies
agent: agent
---

Perform a smoke test for this repository.

Read first:

- `AGENTS.md`
- `.windsurf/workflows/smoketest.md`
- `.windsurf/skills/smoketest/SKILL.md`

Execution rules:

- Prefer repo-approved commands over legacy Windsurf helpers.
- Start with the least expensive verification that can prove or disprove health.
- Do not assume missing dependencies should be installed automatically.

Suggested flow:

1. Run `python -m pytest tests/ --collect-only`.
2. Run the smallest relevant test or, if no narrower target exists, `python -m pytest tests/ -v`.
3. If runtime health matters, use `docker compose up -d` and then inspect service status or logs.
4. Summarize what passed, failed, or could not be verified.

Include:

- Smoke checks performed
- Failures or blockers
- Whether Docker-backed services were needed
- Best next action
