# Fast Daily Nexus Bootstrap (Short)

You are Cascade inside Windsurf.

Mission: set up a **Nexus intelligent project operating system** for this repository that is fast, practical, and token-efficient for daily development.

## Output targets (lean set)

Create:

### 1) Rules
- `.windsurf/rules/00-project-overview.md`
- `.windsurf/rules/01-security-and-secrets.md`
- `.windsurf/rules/00-token-efficiency.md` (always_on, quota conservation + model selection guide)
- Optional component rules if relevant:
  - `.windsurf/rules/10-backend.md`
  - `.windsurf/rules/11-frontend-or-admin.md`
  - `.windsurf/rules/12-plugin-or-client.md`
  - `.windsurf/rules/13-infra.md`
- `AGENTS.md` (root)
- scoped `AGENTS.md` in 1-3 key subdirs (only where needed)
- `.windsurf/skills/setup-dev-environment/SKILL.md`
- `.windsurf/skills/setup-test-environment/SKILL.md`
- `.windsurf/skills/run-quality-gates/SKILL.md`
- `.windsurf/workflows/setup-test-env.md`
- `.windsurf/workflows/run-quality.md`
- `.windsurf/workflows/smoketest.md`
- `.windsurf/workflows/debug-investigate.md`
- `bootstrap/cli/` (CLI toolkit — smoketest + debug tools)
- `docs/DEVELOPER_GUIDE.md` (full)
- `docs/TEST_ENVIRONMENT.md` (full)
- `docs/RUNBOOK.md` (practical minimal)
- `docs/HANDOFF.md` (practical minimal)
- `docs/USER_GUIDE.md` (skeleton + TODOs if product UX unclear)
- `docs/WHITEPAPER.md` (skeleton + TODOs if architecture unclear)
- 5-8 high-signal Windsurf memories
- `.windsurf/rules/00-token-efficiency.md` (always_on, quota conservation + model selection guide)
- `bootstrap/model-selection-reference.md` (on-demand model cost database, excluded from indexing)
- `.codeiumignore` (exclude large/generated files from indexing)
- `.github/copilot-instructions.md` (VS Code Copilot cross-IDE support)
- `CLAUDE.md` (Claude Code cross-IDE support)
- `.cursorrules` (Cursor IDE cross-IDE support)

## Hard constraints

1. Never expose secret values.
2. Never invent commands; only use verified scripts/tooling in repo.
3. Prefer minimal, reversible edits.
4. If unsure, write `TODO(verify)` instead of guessing.
5. Keep rule files concise (<12k chars each).
6. No n8n-based orchestration.

## Phase 0 — Quick discovery (read-only)

Read only authoritative files:
- `README*`, `docs/*` key guides
- build manifests (`package.json`, `pyproject.toml`, `go.mod`, etc.)
- CI config (`.github/workflows/*`) and infra manifests
- test config/surfaces
- detect `.env*` and credential-like files (do not print values)

Produce a compact discovery table:
- components, entrypoints, local run commands
- test/lint/format/typecheck commands
- deploy surface
- auth model summary
- risks + unknowns

## Phase 1 — Rules

Create 3 core rules:
- project overview + definition of done
- security + secrets handling
- change safety + test expectations

If components are clearly separated, add optional scoped rules:
- backend / frontend / plugin / infra

Activation guidance (token-efficient defaults):
- `00-project-overview` = Always On (small, essential context)
- `01-security-and-secrets` = Always On (non-negotiable)
- `02-change-safety-and-testing` = Model Decision (loaded only when relevant)
- component rules = Glob (loaded only when matching files touched)
- Always create a `00-token-efficiency.md` rule (always_on) with quota conservation instructions.
If activation metadata cannot be encoded in files, provide manual mapping for `Customizations → Rules`.

## Phase 2 — AGENTS.md + cross-IDE instructions

- Root `AGENTS.md`: navigation, command verification, safe commands, testing/doc expectations.
  - This file is recognized by both Windsurf AND VS Code Copilot.
- Scoped `AGENTS.md`: only subtree-specific conventions (no duplication).
- `.github/copilot-instructions.md`: project context, coding standards, commands (for VS Code Copilot).
- `CLAUDE.md`: project context, constraints, commands (for Claude Code / VS Code with Claude).
- `.cursorrules`: project context, constraints, commands (for Cursor IDE).

## Phase 3 — Skills + workflows

Each skill must include `SKILL.md` with YAML frontmatter:
- `name`
- `description`

Add supporting checklists/templates per skill.
Workflows must be slash-command-ready and use verified commands only.

## Phase 4 — Docs

Prioritize correctness over completeness.
- Developer Guide + Test Environment must be actionable.
- Runbook + Handoff must be immediately useful.
- User Guide + Whitepaper can be structured skeletons if evidence is limited.

## Phase 5 — Memories + final report

Create small, stable memories:
- component map
- canonical commands
- auth/token conventions
- critical env var names (names only)
- recurring risks

Final output:
1. Artifact checklist (exact paths)
2. Rule activation map
3. Verified command source map
4. Open TODO(verify) items
5. Next manual steps

## Phase 6 — CLI Toolkit verification

After all phases complete:
1. Run `/prereqs` to check prerequisites (Docker, Python, Git).
2. Run `/smoketest --level quick` to verify project health.
3. For web apps: use Cascade's `browser_preview` tool for visual verification.
4. Report available toolkit commands: `/smoketest`, `/debug`, `/prereqs`.