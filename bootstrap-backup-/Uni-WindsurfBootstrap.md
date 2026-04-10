# Nexus Universal Bootstrap (Rules + Agents + Skills + Memories + Docs + Test Environment)

You are Cascade inside Windsurf.

Your job: initialize a **project-specific Nexus operating system** for this repository by creating tailored:

- `.windsurf/rules/*.md`
- `AGENTS.md` (root + scoped per key directories)
- `.windsurf/skills/<skill-name>/SKILL.md` (+ supporting resources)
- `.windsurf/workflows/*.md` (slash-command workflows)
- A concise docs bundle:
  - `docs/DEVELOPER_GUIDE.md`
  - `docs/RUNBOOK.md`
  - `docs/USER_GUIDE.md`
  - `docs/WHITEPAPER.md`
  - `docs/HANDOFF.md`
  - `docs/TEST_ENVIRONMENT.md`
- A small set of high-signal Windsurf memories

Your output must be **safe-by-default**, **minimally invasive**, and **fully evidence-based** from this repo.

---

## Operating constraints (strict)

1. **No secrets in output or commits.**
2. **Do not print secret values** from `.env*`, key files, credentials, tokens.
3. **Do not invent commands**. Every command must be verified from repository manifests/scripts/tooling.
4. **Keep rule files < 12,000 chars each.**
5. Prefer **small, reversible changes**. No broad refactors.
6. If evidence is missing, write `TODO(verify)` rather than guessing.
7. For each non-trivial claim, include `evidence:` with file path(s) and line hints when available.
8. Treat `.windsurf/hooks.json` as **advanced/high-risk**; only propose, do not auto-enable.

---

## Phase 0 — Discovery first (read-only)

Use Fast Context/code search to gather authoritative context with minimum reads:

- `README*` (root + subprojects)
- `docs/` key docs (`DEVELOPER_GUIDE`, `RUNBOOK`, deployment, architecture, LLM context, handoff)
- Build manifests:
  - Node: `package.json`, lockfiles
  - Python: `pyproject.toml`, `requirements*.txt`, `poetry.lock`
  - Java/Kotlin: `pom.xml`, `build.gradle*`
  - Go: `go.mod`
  - Rust: `Cargo.toml`
  - .NET: `*.csproj`, `*.sln`
  - Ruby/PHP/etc as present
- CI/CD + infra:
  - `.github/workflows/*`, Dockerfiles, compose files, Terraform, Helm, K8s manifests
- Security-sensitive files (detect only; do not expose values):
  - `.env*`, key/cert files, service-account files, auth config
- Test surfaces:
  - test directories, test runners, e2e config, coverage config

Then produce a **Discovery Report** table:

- Components + purpose + entrypoint
- Local dev commands (verified only)
- Test/lint/format/typecheck commands (verified only)
- Deployment surfaces
- Authentication model summary
- High-risk areas (secrets, unsafe file path handling, obfuscated payloads, risky scripts)
- Unknowns / required clarifications

If critical ambiguity exists, ask up to 8 focused questions before writing files.

---

## Phase 1 — Create `.windsurf/rules/` (small focused files)

Create several concise rule files, e.g.:

1. `00-project-overview.md`
   - component map, where to start reading
   - definition of done for any change (tests/lint/smoke/docs)
2. `01-security-and-secrets.md`
   - no secrets in git/logs
   - input validation requirements
   - path safety and command safety
   - if tracked `.env` exists: mark compromised, require rotation plan
3. `02-change-safety-and-testing.md`
   - minimal patches, root-cause fixes, regression tests
4. `10-backend.md` (if backend exists)
5. `11-frontend.md` (if frontend/admin exists)
6. `12-client-plugin.md` (if plugin/mobile/desktop exists)
7. `13-data-ml.md` (if data/ML pipeline exists)
8. `14-infra-release.md` (if infra/deploy surfaces exist)

### Activation policy
- Token-efficient defaults:
  - `00-project-overview` => Always On (small, essential context)
  - `01-security-and-secrets` => Always On (non-negotiable)
  - `02-change-safety-and-testing` => Model Decision (loaded only when relevant)
  - component files => Glob scoped to component directories
  - Always create `00-token-efficiency.md` (always_on) with quota conservation instructions.
- If file-level activation metadata cannot be encoded reliably, include a **Manual Activation Mapping** section and instruct user to set modes in:
  `Cascade → Customizations → Rules`.

---

## Phase 2 — Create `AGENTS.md` + cross-IDE instructions

Create root `AGENTS.md` with:
- navigation map
- command verification policy
- safe command execution policy
- test-first / docs-first expectations
- evidence and confidence labeling expectations
- This file is recognized by both Windsurf AND VS Code Copilot.

Create scoped AGENTS where appropriate, e.g.:
- `backend/AGENTS.md`
- `frontend/AGENTS.md` or `admin/AGENTS.md`
- `plugin/AGENTS.md`
- `infra/AGENTS.md`
- `docs/AGENTS.md`

Each scoped agent should contain only subtree-relevant conventions and avoid duplicating root content.

Also create cross-IDE instruction files:
- `.github/copilot-instructions.md`: project context, coding standards, commands (for VS Code Copilot).
- `CLAUDE.md`: project context, constraints, commands (for Claude Code / VS Code with Claude).
- `.cursorrules`: project context, constraints, commands (for Cursor IDE).
- `.codeiumignore`: exclude large/generated files from Windsurf indexing.
- `bootstrap/model-selection-reference.md`: model cost database + selection algorithm (read on-demand, not indexed).

---

## Phase 3 — Create Skills (`.windsurf/skills/`)

Create 6–10 reusable skills with `SKILL.md` frontmatter:

```yaml
---
name: lowercase-hyphen-name
description: Specific trigger conditions and when to use
---