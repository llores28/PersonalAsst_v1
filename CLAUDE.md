# Claude Code Instructions — PersonalAsst

This file provides project-specific instructions to Claude Code and VS Code with Claude.
For Windsurf, see `.windsurf/rules/` and `AGENTS.md`. For GitHub Copilot, see `.github/copilot-instructions.md`.

## Project

PersonalAsst — Single-user, Dockerized, self-improving multi-agent Personal Assistant.
Primary UX: Telegram. LLM: OpenAI (Responses API). Infra: Docker Compose (PostgreSQL 17, Redis 7, Qdrant).

## Stack
- Python 3.12+, FastAPI, aiogram 3.x, OpenAI Agents SDK
- Entry (bot): `src/main.py`
- Entry (CLI toolkit): `bootstrap/cli/bs_cli.py`

## Constraints
- No secrets in output/commits/logs
- No invented commands — verify from repo files
- No shell=True, eval(), exec()
- Validate paths via `bootstrap/cli/security.py:validate_path()`
- Validate URLs via `bootstrap/cli/security.py:validate_url()`
- Structured output via `bootstrap/cli/utils.py:emit()`
- Mark uncertainty as `TODO(verify)`

## Commands
```bash
python bootstrap/cli/bs_cli.py prereqs           # Check prerequisites
python bootstrap/cli/bs_cli.py smoketest --level quick  # Quick smoke test
python bootstrap/cli/bs_cli.py debug secrets-scan # Scan for leaked secrets
python bootstrap/cli/bs_cli.py research docs <q>  # Search docs
python bootstrap/cli/bs_cli.py scaffold <name>    # Create new CLI tool
python bootstrap/cli/bs_cli.py health check        # Nexus health check
```

## Documentation Update Rule

After any large fix, feature, or improvement to `src/` code, **you MUST update** these living docs:

- `README.md` — Features list, architecture diagram, tech stack, project structure, latest updates
- `README_ORCHESTRATION.md` — Dashboard API endpoints, orchestration architecture, UI features
- `docs/DEVELOPER_GUIDE.md` — Architecture, setup, adding agents/tools
- `docs/USER_GUIDE.md` — Telegram commands, UX flows
- `docs/RUNBOOK.md` — Containers, env vars, health checks, troubleshooting
- `docs/HANDOFF.md` — Current status, completed phases, pending work
- `docs/CHANGELOG.md` — What changed (append at top)
- `docs/PRD.md` — Requirements, acceptance criteria, constraints
- `docs/architecture-report.html` — Regenerate if architecture changed

Trigger: Any change that adds/removes/renames an agent, tool, command, API endpoint, env var, container, or DB model. Skip for minor typos or internal refactors with no behavior change.

## Key Directories
- `src/` — Application source code
- `bootstrap/` — Nexus CLI toolkit and model selection reference
- `bootstrap/cli/tools/` — Individual CLI tool implementations
- `.windsurf/` — Windsurf-specific rules, skills, workflows
- `.github/` — GitHub Copilot instructions
