# Claude Code Instructions — PersonalAsst

This file provides project-specific instructions to Claude Code and VS Code with Claude.
For Windsurf, see `.windsurf/rules/` and `AGENTS.md`. For GitHub Copilot, see `.github/copilot-instructions.md`.

## Project

PersonalAsst — Single-user, Dockerized, self-improving multi-agent Personal Assistant.
Primary UX: Telegram. LLM: OpenAI (Responses API). Infra: Docker Compose (PostgreSQL 17, Redis 7, Qdrant).

## Stack
- Python 3.12+, FastAPI, aiogram 3.x, OpenAI Agents SDK
- Entry (bot): `src/main.py`
- CLI toolkit: `nexus` command (installed editable from sibling clone at `../Nexus/`, source at `../Nexus/nexus/cli/bs_cli.py`). Dev-time only — NOT bundled into Atlas's production Docker image.

## Constraints
- No secrets in output/commits/logs
- No invented commands — verify from repo files
- No shell=True, eval(), exec()
- Validate paths via `../Nexus/nexus/cli/security.py:validate_path()`
- Validate URLs via `../Nexus/nexus/cli/security.py:validate_url()`
- Structured output via `../Nexus/nexus/cli/utils.py:emit()`
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

## Workspace routing tests

After ANY change to `_maybe_handle_connected_*`, `_is_simple_connected_*`, the
`SkillRegistry` matcher, or the `WebSearchTool` gate in `src/agents/orchestrator.py`,
run the routing harness to catch regressions like the 2026-04-28 incident
(model called `WebSearchTool` on a Gmail query and cited support.google.com):

```bash
# Mock-only, no network — fast (<2s), runs in CI
python scripts/test_workspace_routing.py
# or via pytest:
python -m pytest tests/test_workspace_routing_harness.py

# Live, sandboxed probe against the real workspace (read-only)
docker compose exec -e LIVE_WORKSPACE_EMAIL=you@gmail.com -w /app assistant \
    python scripts/test_workspace_routing.py --live

# Full live integration suite (creates [ATLAS-TEST]-prefixed fixtures only)
docker compose exec -e LIVE_WORKSPACE_TEST=1 -e LIVE_WORKSPACE_EMAIL=you@gmail.com \
    -w /app assistant python -m pytest tests/integration/test_live_workspace_smoke.py -v
```

When adding a new natural-language phrasing for a Workspace tool: add a case to
`tests/test_workspace_routing_harness.py` AND `scripts/test_workspace_routing.py`
(they share the same case data — keep them in lockstep).

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
- `../Nexus/` — Nexus CLI toolkit (sibling clone of github.com/llores28/Nexus). Installed via `pip install -e ../Nexus` declared in `requirements-dev.txt`. Edit + commit happens in that sibling repo, not in Atlas's tree. Atlas's production Docker build installs only `requirements.txt` so Nexus is absent at runtime.
- `../Nexus/nexus/cli/tools/` — Individual CLI tool implementations
- `Nexus.archive-2026-04-29/` — Working-tree backup of the previous nested Nexus layout. Gitignored. Safe to delete once the sibling-clone setup has been verified in production.
- `.nexus/` — Runtime state (gitignored): `state.json`, `state-summary.md`, `journal/YYYY-MM/DD.md`
- `docs/ADR-YYYY-MM-DD-<slug>.md` — Atlas project ADRs (note: this is the project-local convention; Nexus's own `nexus journal decision add` would create files at `docs/decisions/` — ADRs live at the top of `docs/` here instead)
- `.windsurf/` — Windsurf-specific rules, skills, workflows
- `.github/` — GitHub Copilot instructions
