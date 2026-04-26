# GitHub Copilot Instructions — PersonalAsst

This file provides project-specific instructions to GitHub Copilot in VS Code.
For Windsurf (Cascade), see `.windsurf/rules/` and `AGENTS.md`.

## Project Context

PersonalAsst — Single-user, Dockerized, self-improving multi-agent Personal Assistant.
Primary UX: Telegram. LLM: OpenAI (Responses API). Infra: Docker Compose (PostgreSQL 17, Redis 7, Qdrant).
- **Stack**: Python 3.12+, FastAPI, aiogram 3.x, OpenAI Agents SDK
- **Entry point (bot)**: `src/main.py`
- **CLI toolkit**: `nexus` command (installed editable from `Nexus/`, source at `Nexus/nexus/cli/bs_cli.py`)

## Coding Standards

- Python: PEP 8, type hints, docstrings for public functions.
- No `shell=True` in subprocess calls.
- No `eval()` or `exec()`.
- All file paths validated via `Nexus/nexus/cli/security.py:validate_path()`.
- All URLs validated via `Nexus/nexus/cli/security.py:validate_url()`.
- Structured output via `Nexus/nexus/cli/utils.py:emit()`.

## Security

- Never hardcode secrets or API keys.
- Never log secret values.
- Validate all user inputs (paths, URLs, package names).
- Run `nexus debug secrets-scan` before commits.

## Testing

- Run quick verification: `nexus smoketest --level quick`
- Check prerequisites: `nexus prereqs`
- CLI emits JSON by default (`--format json`), human output via `--format human`.
- Run health check: `nexus health check --format human`

## File Organization

```
src/                  — Application source code
Nexus/                — Nexus CLI clone, pip-installed editable (`nexus` command)
Nexus/nexus/cli/      — Python CLI tools entry point (`bs_cli.py`)
bootstrap/            — DEPRECATED forked copy; do not edit
.nexus/               — Runtime state: state.json, state-summary.md, journal/, decisions/
.windsurf/rules/      — Windsurf rule files
.windsurf/skills/     — Skill definitions
.windsurf/workflows/  — Workflow definitions
.github/              — GitHub Copilot instructions
```
