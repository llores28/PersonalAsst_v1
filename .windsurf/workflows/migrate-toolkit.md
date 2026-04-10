---
description: One-shot migration to add the CLI toolkit to a project that already ran the old bootstrap
---
# Migrate Toolkit

Adds the Bootstrap CLI Toolkit (skills, workflows, CLI tools) to an existing project
without touching any files created by the original bootstrap.

## 1) Verify existing bootstrap artifacts
Confirm the project already has `.windsurf/rules/` and `AGENTS.md`.
If not, run `/bootstrap-wizard` first — this migration is for projects that already bootstrapped.

## 2) Check for conflicts (safety)
Verify none of the new files already exist:
- `bootstrap/cli/`
- `.windsurf/skills/prereqs-check/`
- `.windsurf/skills/smoketest/`
- `.windsurf/skills/debug-investigate/`
- `.windsurf/skills/research-investigate/`
- `.windsurf/skills/webscrape/`
- `.windsurf/skills/create-cli-tool/`
- `.windsurf/skills/local-env/`

If any exist, skip those — they were already migrated.

## 3) Copy CLI toolkit
Copy the `bootstrap/cli/` directory from ws-bootstrap-master into the project.

## 4) Install Python dependencies
```
pip install -r bootstrap/cli/requirements.txt
```

## 5) Copy new skills
Copy all 7 skill folders into `.windsurf/skills/` alongside existing skills.

## 6) Copy new workflows
Copy all 8 workflow files into `.windsurf/workflows/` alongside existing workflows.

## 6b) Add token efficiency + cross-IDE support
Create these files if they don't already exist:
- `.windsurf/rules/00-token-efficiency.md` (always_on, quota conservation rule)
- `.codeiumignore` (exclude large/generated files from indexing)
- `.github/copilot-instructions.md` (VS Code Copilot cross-IDE support)
- `CLAUDE.md` (Claude Code cross-IDE support)
- `.cursorrules` (Cursor IDE cross-IDE support)

If `AGENTS.md` already exists, verify it contains project context usable by VS Code Copilot.

Review existing `.windsurf/rules/` and suggest changing non-critical rules from `always_on` to `model_decision` trigger for quota savings.

## 7) Run prerequisites check
// turbo
```
python bootstrap/cli/bs_cli.py prereqs --format human
```

## 8) Run quick smoketest
// turbo
```
python bootstrap/cli/bs_cli.py smoketest --level quick --format human
```

## 9) Report
List what was added and what slash commands are now available:
- `/prereqs` — check prerequisites
- `/smoketest` — run smoke tests
- `/debug` — debug investigation
- `/research` — dependency/docs research
- `/scrape` — webscraping
- `/create-tool` — scaffold new CLI tools
- `/local-env` — container validation
- `/migrate-toolkit` — this migration (one-shot, already done)
