---
description: Generate a cohesive project PRD from bootstrap wizard outputs without conflicting with rules, AGENTS, skills, or workflows
---
# Bootstrap PRD Generator

Goal: create or update `docs/PRD.md` from bootstrap wizard outputs while keeping all Windsurf settings cohesive.

## 1) Required inputs

Use these inputs as source of truth:
1. Latest `/bootstrap-wizard` output:
   - recommended tier
   - selected bootstrap file path
   - reasoning table
   - scenario branches and answers
   - architecture summary
   - risk note
   - confidence
2. `bootstrap/Bootstrap-Project-Intake.md`
3. Existing project context (`README*`, `docs/*`) when available

If key inputs are missing, ask focused follow-up questions before writing `docs/PRD.md`.

## 2) Non-conflict operating model (mandatory)

Use strict responsibility boundaries:
- **PRD** defines product intent: `what`, `why`, success metrics, scope, non-goals, NFR targets.
- **Rules + AGENTS** define constraints and policy: security/compliance/process guardrails.
- **Skills + Workflows** define execution procedure: repeatable operational steps.

Conflict policy:
1. Never weaken security/compliance constraints from rules or AGENTS.
2. If PRD intent conflicts with rules/AGENTS/workflows, do not silently override. Add it to `Conflict Register`.
3. Prefer stricter controls when ambiguous.
4. Every conflict must include owner, decision deadline, and resolution path.

## 3) Drafting steps

1. Create/update `docs/PRD.md` using `bootstrap/PRD-Template.md`.
2. Fill all sections with evidence from wizard output and intake.
3. Add measurable acceptance criteria for each requirement.
4. Add a `Cohesion Matrix` mapping each PRD requirement to:
   - supporting rule(s)
   - AGENTS scope
   - related skill/workflow
   - verification method (test/check/smoke)
5. Add `Conflict Register` (write `None at drafting time` if empty).
6. Add `Decisions & ADR Triggers` for unresolved tradeoffs.

## 4) Validation checklist

Before final output, verify:
- No secret values are included.
- Each goal has at least one measurable success metric.
- Each high-risk item has mitigation and owner.
- `Cohesion Matrix` references at least one rule/agent/workflow for each critical requirement.
- PRD does not contradict the selected bootstrap tier constraints.

## 5) Final output

Return:
1. `docs/PRD.md` created/updated
2. Summary of major decisions
3. Open conflicts and owners
4. Top 5 implementation priorities
5. Suggested next command: run selected bootstrap (if not yet run)
