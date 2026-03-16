# Fast Daily Windsurf Bootstrap (Short)

You are Cascade inside Windsurf.

Goal: quickly set up a safe, practical project bootstrap that is fast enough for daily use while still producing usable rules, agents, skills, test-environment setup, and core documentation.

## Output targets (lean set)

Create:

- `.windsurf/rules/00-project-overview.md`
- `.windsurf/rules/01-security-and-secrets.md`
- `.windsurf/rules/02-change-safety-and-testing.md`
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

Activation guidance:
- core rules = Always On
- component rules = Glob
If activation metadata cannot be encoded in files, provide manual mapping for `Customizations → Rules`.

## Phase 2 — AGENTS.md

- Root `AGENTS.md`: navigation, command verification policy, safe command policy, testing/doc expectations.
- Scoped `AGENTS.md`: only subtree-specific conventions (no duplication).

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