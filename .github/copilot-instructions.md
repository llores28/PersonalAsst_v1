# GitHub Copilot Instructions — PersonalAsst

This file provides project-specific instructions to GitHub Copilot in VS Code.
For Windsurf (Cascade), see `.windsurf/rules/` and `AGENTS.md`.

## Project Context

PersonalAsst — Single-user, Dockerized, self-improving multi-agent Personal Assistant.
Primary UX: Telegram. LLM: OpenAI (Responses API). Infra: Docker Compose (PostgreSQL 17, Redis 7, Qdrant).
- **Stack**: Python 3.12+, FastAPI, aiogram 3.x, OpenAI Agents SDK
- **Entry point (bot)**: `src/main.py`
- **Entry point (CLI)**: `bootstrap/cli/bs_cli.py`

## Coding Standards

- Python: PEP 8, type hints, docstrings for public functions.
- No `shell=True` in subprocess calls.
- No `eval()` or `exec()`.
- All file paths validated via `bootstrap/cli/security.py:validate_path()`.
- All URLs validated via `bootstrap/cli/security.py:validate_url()`.
- Structured output via `bootstrap/cli/utils.py:emit()`.

## Security

- Never hardcode secrets or API keys.
- Never log secret values.
- Validate all user inputs (paths, URLs, package names).
- Run `python bootstrap/cli/bs_cli.py debug secrets-scan` before commits.

## Testing

- Run quick verification: `python bootstrap/cli/bs_cli.py smoketest --level quick`
- Check prerequisites: `python bootstrap/cli/bs_cli.py prereqs`
- CLI emits JSON by default (`--format json`), human output via `--format human`.
- Run health check: `python bootstrap/cli/bs_cli.py health check --format human`

## File Organization

```
src/                 — Application source code
bootstrap/           — Nexus CLI toolkit and model selection reference
bootstrap/cli/       — Python CLI tools entry point
.windsurf/rules/     — Windsurf rule files
.windsurf/skills/    — Skill definitions
.windsurf/workflows/ — Workflow definitions
.github/             — GitHub Copilot instructions
```
