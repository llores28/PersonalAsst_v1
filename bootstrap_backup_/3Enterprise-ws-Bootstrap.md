# Universal Windsurf Bootstrap — Strict Enterprise Variant

You are Cascade inside Windsurf.

Mission: create a compliance-grade, security-first project operating system for this repository with traceable evidence, governance controls, and production-ready documentation.

## Mandatory deliverables

### A) Rules
Create a multi-file ruleset under `.windsurf/rules/`:

1. `00-project-overview.md`
2. `01-security-and-secrets.md`
3. `02-secure-coding-and-input-validation.md`
4. `03-change-management-and-approvals.md`
5. `04-testing-and-quality-gates.md`
6. `05-incidents-and-oncall.md`
7. `06-data-governance-and-privacy.md`
8. `07-dependency-and-supply-chain.md`
9. component-specific rules (`10-backend.md`, `11-frontend.md`, `12-client-plugin.md`, `13-data-ml.md`, `14-infra-release.md`) as applicable

### B) AGENTS
- root `AGENTS.md`
- scoped AGENTS in each critical subtree:
  - app/backend/frontend/plugin/infra/docs/security (adapt to repo structure)

### C) Skills
Create 8-12 skills under `.windsurf/skills/`, including:
- `setup-dev-environment`
- `setup-test-environment`
- `run-quality-gates`
- `security-sweep`
- `dependency-risk-review`
- `debug-production-incident`
- `release-readiness`
- `docs-refresh`
- `handoff-package`

Additionally, install full CLI toolkit skills from `bootstrap/`:
- `prereqs-check` — check prerequisites (Docker, MCP, extensions)
- `smoketest` — tiered smoke tests
- `debug-investigate` — systematic debugging tools with secrets-scan
- `research-investigate` — dependency/docs research with CVE checking
- `webscrape` — external docs/API fetching with SSRF protection
- `local-env` — container validation with readiness checks
- `create-cli-tool` — scaffold new CLI tools (security-enforced template)

Each skill folder includes:
- `SKILL.md` with YAML frontmatter (`name`, `description`)
- supporting checklists/templates

### D) Workflows
Create enterprise workflows under `.windsurf/workflows/`:
- `bootstrap-verify.md`
- `setup-test-env.md`
- `run-quality.md`
- `incident-triage.md`
- `release-gate.md`
- `prepare-handoff.md`

Additionally, install full CLI toolkit workflows:
- `prereqs-check.md` — `/prereqs`
- `smoketest.md` — `/smoketest`
- `debug-investigate.md` — `/debug`
- `research.md` — `/research`
- `scrape-docs.md` — `/scrape`
- `local-env.md` — `/local-env`
- `create-tool.md` — `/create-tool`

Enterprise-specific toolkit gates:
- `local-env validate` is mandatory before any merge to main
- `debug secrets-scan` is mandatory before any commit suggestion
- Docker Desktop extension sharing requires explicit user approval (documented in workflow)

### E) Documentation set (full, not skeletons unless explicitly impossible)
- `docs/DEVELOPER_GUIDE.md`
- `docs/RUNBOOK.md`
- `docs/USER_GUIDE.md`
- `docs/WHITEPAPER.md`
- `docs/HANDOFF.md`
- `docs/TEST_ENVIRONMENT.md`
- `docs/SECURITY_MODEL.md`
- `docs/THREAT_MODEL.md`
- `docs/CHANGELOG_PROCESS.md` (or equivalent process doc)
- `docs/COMPLIANCE_TRACEABILITY.md` (map policies ↔ controls ↔ docs)

### F) Memories
Create 8-15 high-signal memories with stable operational context.

---

## Non-negotiable constraints

1. Never expose or store secret values in output.
2. Never invent commands; verify from repository manifests/scripts/tooling.
3. No destructive operations by default.
4. No network installs, external calls, or privileged operations unless explicitly approved.
5. No n8n-based orchestration.
6. Mark uncertainty as `TODO(verify)` with owner recommendation.
7. Keep each rule file <12,000 chars.
8. Every non-trivial claim must include `evidence:` path references.

---

## Evidence protocol (required)

For each section produced, include:
- `evidence:` list of source files used
- confidence level: `High | Medium | Low`
- unresolved assumptions

If confidence is Low for critical behavior (auth/deploy/data handling), ask targeted questions before finalizing.

---

## Phase 0 — Governance precheck

Before writing files, extract and report:
- repository type(s): monorepo vs single service
- data sensitivity signals (PII/PCI/PHI hints, if detectable)
- runtime/deploy environment surface
- ownership signals (oncall/team docs)
- security red flags (tracked `.env`, key material, obfuscated code, risky scripts)

Ask up to 10 clarifying questions if ambiguity blocks safe setup.

---

## Phase 1 — Deep discovery (read-only)

Read authoritative files only:
- README/docs
- manifests and lockfiles
- test configs
- CI/CD pipelines
- infra/deployment manifests
- auth/config code paths
- env templates (`.env.example`, etc.)

Build a structured discovery matrix:
- components, boundaries, dependencies
- local dev and test commands
- lint/format/typecheck/coverage commands
- release/deploy process
- authn/authz model
- data flows and high-risk operations

---

## Phase 2 — Rules with activation model

Activation policy:
- Always On: security, change safety, testing, incident discipline
- Glob: component-specific rules by directory
- Manual/Model Decision: specialized procedures only

If activation cannot be encoded in-file, produce explicit manual mapping for `Cascade → Customizations → Rules`.

---

## Phase 3 — AGENTS hierarchy

Root AGENTS must define:
- repo navigation
- safe command policy
- evidence-first response style
- test-first expectations
- documentation update expectations
- escalation when uncertain

Scoped AGENTS must define:
- local conventions
- local test strategy
- local risk zones
- boundaries and interfaces

---

## Phase 4 — Skills (procedural reliability)

Skills must include:
- objective
- trigger conditions
- prechecks
- exact verified command map
- stop/fail conditions
- expected artifacts
- rollback/recovery guidance (where applicable)

---

## Phase 5 — Workflows (operational enforcement)

Each workflow must include:
- entry conditions
- ordered steps
- failure gates (stop on failed checks)
- output verification checklist
- escalation notes

---

## Phase 6 — Documentation standards

Apply docs-as-code discipline and clear doc taxonomy:
- Developer Guide: build/run/test/contribute
- Runbook: incident triage, mitigation, rollback, escalation, postmortem
- User Guide: task-based user actions and troubleshooting
- Whitepaper: problem, architecture, rationale, tradeoffs, security/scalability posture
- Handoff: ownership, access transfer checklist, known debt/risk, first-week plan
- Test Environment: local/CI/staging matrix, env var names, test data policy, smoke tests

If information is missing, annotate with `TODO(verify)` and specific evidence gap.

---

## Phase 7 — Final enterprise report (required)

Output:

1. Artifact inventory (exact paths)
2. Rule activation map
3. Command verification ledger (command → source file)
4. Security findings summary (no secret values)
5. Test-environment readiness score
6. Documentation completeness score (all required docs)
7. Open risks with severity (`Critical/High/Medium/Low`)
8. Go/No-Go recommendation with rationale
9. Manual follow-ups:
   - configure rule activation modes
   - configure `.codeiumignore`
   - configure MCP server access safely (no tokens in repo)
   - optional `.windsurf/hooks.json` review by security owner