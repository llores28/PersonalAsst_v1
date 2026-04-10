---
name: setup-test-environment
description: Prepare and validate the PersonalAsst test environment in VS Code
agent: agent
---

Help prepare the test environment for this repo.

Read first:

- `AGENTS.md`
- `.windsurf/workflows/setup-test-env.md`
- `.windsurf/skills/setup-test-environment/SKILL.md`

Execution rules:

- Treat the Windsurf files as reference, but follow current repo command policy.
- If dependencies or local services are missing, explain the gap clearly and ask before running approval-required install steps.
- Use `python -m pytest tests/ --collect-only` as the first validation step whenever possible.

Suggested flow:

1. Confirm the repo context from `AGENTS.md`.
2. Validate test discovery with `python -m pytest tests/ --collect-only`.
3. If integration services are needed, use `docker compose up -d` only when appropriate.
4. Run the smallest relevant test target.
5. When the environment is healthy, run `python -m pytest tests/ -v`.

Report:

- What was already working
- What was missing
- What was validated
- Any approval-needed next step
