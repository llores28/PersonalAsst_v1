---
name: debug-investigate
description: Investigate a failing test, traceback, or runtime issue in PersonalAsst
agent: agent
---

Run a structured debugging session for this repository.

Read first:

- `AGENTS.md`
- `.windsurf/workflows/debug-investigate.md`
- `.windsurf/skills/debug-investigate/SKILL.md`

Execution rules:

- Use the smallest relevant read-only or test command first.
- Prefer verified repo commands and direct code inspection over legacy Windsurf-only helpers.
- If the issue involves services, prefer read-only checks like `docker compose ps` before log tails or restarts.

Suggested flow:

1. Capture the concrete symptom: failing test, traceback, bad behavior, or log line.
2. Identify the narrowest relevant file or test target.
3. Run the smallest relevant check first.
4. If Docker services are involved, use `docker compose ps` or `docker compose logs -f assistant` as needed.
5. Apply the minimum safe fix.
6. Re-run the specific verification first, then the broader relevant suite.

Report:

- Root cause
- Files changed
- Verification performed
- Remaining risks or follow-ups
