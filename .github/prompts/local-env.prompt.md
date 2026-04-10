---
name: local-env
description: Work with the PersonalAsst local Docker environment from VS Code
agent: agent
---

Operate on the local development environment for this repository.

Read first:

- `AGENTS.md`
- `.windsurf/workflows/local-env.md`
- `.windsurf/skills/local-env/SKILL.md`

Execution rules:

- Follow the verified Docker commands in `AGENTS.md`.
- Treat Docker Desktop extension guidance in the legacy Windsurf files as optional reference, not a required path.
- Use read-only inspection before restarts when possible.

Suggested commands:

1. `docker compose up -d`
2. `docker compose logs -f assistant`
3. `docker compose down`
4. `docker compose build`

Report:

- Current service state
- What command was run
- Any health or startup issues
- Recommended next step
