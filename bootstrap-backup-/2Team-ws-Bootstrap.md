# Team Nexus Bootstrap (Balanced)

You are Cascade inside Windsurf.

Mission: set up a **team-grade Nexus intelligent project operating system** for this repository that is practical, safe, and repeatable across contributors.

Create a middle-ground setup between "fast daily" and "strict enterprise":
- stronger process than daily use
- less governance overhead than enterprise compliance mode

---

## Required outputs

Create:

### 1) Rules
- `.windsurf/rules/00-project-overview.md`
- `.windsurf/rules/01-security-and-secrets.md`
- `.windsurf/rules/02-change-safety-and-testing.md`
- `.windsurf/rules/03-release-and-ops.md`
- Optional component rules if relevant:
  - `.windsurf/rules/10-backend.md`
  - `.windsurf/rules/11-frontend-or-admin.md`
  - `.windsurf/rules/12-plugin-or-client.md`
  - `.windsurf/rules/13-infra.md`

### 2) Agents
- `AGENTS.md` (root)
- scoped `AGENTS.md` in key subdirectories (2-5 max, only where useful)

### 3) Skills
Create 5-8 skills under `.windsurf/skills/`, including:
- `setup-dev-environment`
- `setup-test-environment`
- `run-quality-gates`
- `security-sweep`
- `release-checklist`
- `debug-incident` (if services/ops exist)
- `docs-refresh` (optional but recommended)

Additionally, install CLI toolkit skills from `bootstrap/`:
- `prereqs-check` — check prerequisites (Docker, MCP, extensions)
- `smoketest` — tiered smoke tests
- `debug-investigate` — systematic debugging tools
- `research-investigate` — dependency/docs research
- `local-env` — container validation
- `create-cli-tool` — scaffold new CLI tools

Each skill folder must include:
- `SKILL.md` with YAML frontmatter (`name`, `description`)
- at least one supporting resource file (checklist/template/command map)

### 4) Workflows
Create 3-5 workflows under `.windsurf/workflows/`:
- `setup-test-env.md`
- `run-quality.md`
- `release-checklist.md`
- `incident-triage.md` (if applicable)
- `prepare-handoff.md`

Additionally, install CLI toolkit workflows:
- `prereqs-check.md` — `/prereqs`
- `smoketest.md` — `/smoketest`
- `debug-investigate.md` — `/debug`
- `research.md` — `/research`
- `local-env.md` — `/local-env`
- `create-tool.md` — `/create-tool`

### 5) Documentation bundle
Create/update:
- `docs/DEVELOPER_GUIDE.md` (full)
- `docs/TEST_ENVIRONMENT.md` (full)
- `docs/RUNBOOK.md` (full practical runbook)
- `docs/HANDOFF.md` (full practical handoff)
- `docs/USER_GUIDE.md` (task-based; may include TODOs if UX evidence is missing)
- `docs/WHITEPAPER.md` (architecture/tradeoffs; allow TODOs where evidence is missing)

### 6) Memories
Create 6-10 high-signal workspace memories.

### 7) Token efficiency + cross-IDE support
- `.windsurf/rules/00-token-efficiency.md` (always_on, quota conservation + model selection guide)
- `.codeiumignore` (exclude large/generated files from indexing)
- `.github/copilot-instructions.md` (VS Code Copilot cross-IDE support)
- `CLAUDE.md` (Claude Code cross-IDE support)
- `.cursorrules` (Cursor IDE cross-IDE support)

---

## Hard constraints

1. Never expose secret values in output or commits.
2. Never invent commands; verify all commands from repository files.
3. Prefer minimal, reversible changes.
4. If uncertain, use `TODO(verify)` instead of guessing.
5. Keep each rule file under 12,000 chars.
6. No destructive operations by default.
7. No n8n-based orchestration.
8. For high-impact claims, include `evidence:` with source file paths.

---

## Phase 0 — Discovery (read-only first)

Read only authoritative sources with minimal scope:
- `README*`
- `docs/*` (developer guide/runbook/deployment/architecture/handoff if present)
- manifests and lockfiles (`package.json`, `pyproject.toml`, `go.mod`, etc.)
- CI/CD configs (`.github/workflows/*`, pipelines)
- infra manifests (Docker/K8s/Terraform/etc.)
- test configs/surfaces
- detect `.env*` and credential-like files (do not print secret values)

Produce a **Discovery Matrix**:
- components + entrypoints
- verified dev commands
- verified test/lint/format/typecheck commands
- deploy/release surface
- auth model summary
- top risks and unknowns

If key ambiguity remains, ask up to 6 focused questions before writing files.

---

## Phase 1 — Rules

Create concise, operational rules:
- project overview + definition of done
- security + secrets policy
- change safety + testing expectations
- release/ops checks

Add component-specific rules only if component boundaries are clear.

### Activation guidance (token-efficient defaults)
- `00-project-overview` => Always On (small, essential context)
- `01-security-and-secrets` => Always On (non-negotiable)
- `02-change-safety-and-testing` => Model Decision (loaded only when relevant)
- `03-release-and-ops` => Model Decision (loaded only when relevant)
- Component rules (`10+`) => Glob (loaded only when matching files touched)
- Always create a `00-token-efficiency.md` rule (always_on) with quota conservation instructions.
If activation metadata cannot be reliably encoded in files, provide manual mapping for:
`Cascade → Customizations → Rules`.

---

## Phase 2 — AGENTS.md hierarchy + cross-IDE instructions

Root `AGENTS.md` must define:
- repo navigation
- command verification policy
- safe command execution policy
- testing + docs update expectations
- escalation behavior when uncertain
- This file is recognized by both Windsurf AND VS Code Copilot.

Scoped `AGENTS.md` files must contain only subtree-specific guidance and avoid duplication.

Also create:
- `.github/copilot-instructions.md`: project context, coding standards, commands (for VS Code Copilot).
- `CLAUDE.md`: project context, constraints, commands (for Claude Code / VS Code with Claude).
- `.cursorrules`: project context, constraints, commands (for Cursor IDE).

---

## Phase 3 — Skills

Each `SKILL.md` must include YAML frontmatter:
```yaml
---
name: lowercase-hyphen-name
description: When this skill should be used
---