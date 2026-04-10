# GitHub Copilot Instructions — Nexus

This file provides project-specific instructions to GitHub Copilot in VS Code.
For Windsurf (Cascade), see `.windsurf/rules/` and `AGENTS.md`.

## Project Context

Nexus — Intelligent Project Operating System. Generates project-specific AI-powered operating systems (rules, agents, skills, workflows, docs) with automatic model selection and cross-IDE support.
- **Stack**: Python 3.10+, Click, Rich, httpx, beautifulsoup4
- **Entry point**: `nexus/cli/bs_cli.py`
- **Templates**: `nexus/1Fast-ws-Bootstrap.md`, `2Team-ws-Bootstrap.md`, `3Enterprise-ws-Bootstrap.md`

## Coding Standards

- Python: PEP 8, type hints, docstrings for public functions.
- No `shell=True` in subprocess calls.
- No `eval()` or `exec()`.
- All file paths validated via `nexus/cli/security.py:validate_path()`.
- All URLs validated via `nexus/cli/security.py:validate_url()`.
- Structured output via `nexus/cli/utils.py:emit()`.

## Security

- Never hardcode secrets or API keys.
- Never log secret values.
- Validate all user inputs (paths, URLs, package names).
- Run `python nexus/cli/bs_cli.py debug secrets-scan` before commits.

## Testing

- Run quick verification: `python nexus/cli/bs_cli.py smoketest --level quick`
- Check prerequisites: `python nexus/cli/bs_cli.py prereqs`
- CLI emits JSON by default (`--format json`), human output via `--format human`.
- Run health check: `python nexus/cli/bs_cli.py health check --format human`

## File Organization

```
nexus/          — Templates and CLI toolkit
nexus/cli/      — Python CLI tools
.windsurf/rules/    — Windsurf rule files
.windsurf/skills/   — Skill definitions
.windsurf/workflows/— Workflow definitions
.github/            — GitHub Copilot instructions
```
