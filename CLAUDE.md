# Claude Code Instructions — PersonalAsst

This file provides project-specific instructions to Claude Code and VS Code with Claude.
For Windsurf, see `.windsurf/rules/` and `AGENTS.md`. For GitHub Copilot, see `.github/copilot-instructions.md`.

## Project

PersonalAsst — Single-user, Dockerized, self-improving multi-agent Personal Assistant.
Primary UX: Telegram. LLM: OpenAI (Responses API). Infra: Docker Compose (PostgreSQL 17, Redis 7, Qdrant).

## Stack
- Python 3.12+, FastAPI, aiogram 3.x, OpenAI Agents SDK
- Entry (bot): `src/main.py`
- CLI toolkit: `nexus` command (installed editable from `Nexus/`, source at `Nexus/nexus/cli/bs_cli.py`)

## Constraints
- No secrets in output/commits/logs
- No invented commands — verify from repo files
- No shell=True, eval(), exec()
- Validate paths via `Nexus/nexus/cli/security.py:validate_path()`
- Validate URLs via `Nexus/nexus/cli/security.py:validate_url()`
- Structured output via `Nexus/nexus/cli/utils.py:emit()`
- Mark uncertainty as `TODO(verify)`

## Commands
```bash
nexus prereqs                       # Check prerequisites
nexus smoketest --level quick       # Quick smoke test
nexus debug secrets-scan            # Scan for leaked secrets
nexus research docs <q>             # Search docs
nexus scaffold <name>               # Create new CLI tool
nexus health check                  # Nexus health check (target: 100/100)
nexus journal status                # Project state dashboard
nexus journal health                # Drift / staleness diagnosis
nexus journal health refresh        # Auto-fix drift (backfill missing commits)
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
- `Nexus/` — Nexus CLI toolkit (clone of github.com/llores28/Nexus, installed editable; `git pull` in this dir live-updates the `nexus` CLI)
- `Nexus/nexus/cli/tools/` — Individual CLI tool implementations
- `bootstrap/` — DEPRECATED forked copy; do not edit
- `.nexus/` — Runtime state (gitignored): `state.json`, `state-summary.md`, `journal/YYYY-MM/DD.md`
- `docs/ADR-YYYY-MM-DD-<slug>.md` — Atlas project ADRs (note: this is the project-local convention; Nexus's own `nexus journal decision add` would create files at `docs/decisions/` — ADRs live at the top of `docs/` here instead)
- `.windsurf/` — Windsurf-specific rules, skills, workflows
- `.github/` — GitHub Copilot instructions
