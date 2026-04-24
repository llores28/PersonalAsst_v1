# Bootstrap Wizard — Decision Reference

This file contains the full decision logic for the bootstrap wizard.
It is read on-demand by the `/bootstrap-wizard` workflow, NOT loaded into every prompt.

---

## Solution Architect Discovery Interview

Ask the user to fill `bootstrap/Bootstrap-Project-Intake.md`.
If they already provided intake in chat, reuse it. Ask only missing fields.

Interview behavior:
- One question at a time, adapt based on answers.
- Prioritize architecture drivers over implementation details.
- Stop when minimum fields are complete. Max 12 questions.

Architecture question bank (prioritized):
1. Business outcome in 6-12 months?
2. Impact of downtime/data loss?
3. Primary users/tenants?
4. Systems/services that must integrate?
5. Uptime/SLA expectations?
6. Expected growth?
7. Approval/audit/traceability requirements?
8. Data classes handled?
9. On-call/incident response expectations?
10. Tradeoff: delivery speed vs governance control?

Required fields:
`project_stage`, `customer_impact`, `tenant_profile`, `oncall_required`,
`compliance_requirements`, `data_sensitivity`, `security_governance`,
`needs_formal_approvals`, `needs_audit_trail`, `speed_vs_control`,
`timeline_pressure`, `business_criticality`, `architecture_complexity`,
`integration_count`, `availability_target`

---

## Scenario Triggers

After required fields, detect triggers and ask branch-specific follow-ups.

Triggers:
- `scenario_multi_tenant`: tenant_profile is `external-multi-tenant` or `mixed`
- `scenario_regulated_data`: compliance non-empty OR data sensitivity is `pii`/`phi`/`pci`
- `scenario_high_sla`: availability target is `99.9`/`99.95+` OR on-call required

Ask branches in order: Regulated-data → High-SLA → Multi-tenant. Complete one before next.

**Branch A — Multi-tenant**: tenancy_model, tenant_isolation_strategy, tenant_authz_boundary, tenant_customization_level
**Branch B — Regulated data**: regulated_data_scope, audit_log_granularity, retention_and_deletion_obligations, data_residency_requirement
**Branch C — High SLA**: rto_target, rpo_target, dr_strategy, deployment_safety_level

If not triggered, mark fields `not-applicable`.

---

## Normalize Values

Lowercase, map synonyms: `production`/`prod` → production, `external` → external-customers,
`soc 2` → soc2, `none`/`[]` → no compliance, `mission critical` → mission-critical,
`99.9+` → 99.9, `many` integrations → 5+, `multi tenant` → multi-tenant. Treat `unknown` as missing.

---

## Compute Decision Flags

- `regulated` = compliance list not empty
- `sensitive_data` = data_sensitivity in `pii`/`phi`/`pci`
- `high_governance` = security_governance is `high`
- `process_heavy` = needs_formal_approvals OR needs_audit_trail
- `prod_risk` = production stage OR external-customers OR oncall_required
- `high_business_criticality` = business_criticality is `high`/`mission-critical`
- `high_arch_complexity` = architecture_complexity is `high`
- `integration_heavy` = integration_count is `5+`
- `high_availability` = availability_target is `99.9`/`99.95+`
- `multi_tenant_candidate` = tenant_profile is `external-multi-tenant`/`mixed`
- `scenario_multi_tenant` = multi_tenant_candidate OR tenancy_model is `multi-tenant`/`hybrid`
- `scenario_regulated_data` = regulated OR sensitive_data
- `scenario_high_sla` = high_availability OR oncall_required
- `multi_tenant_risk` = scenario_multi_tenant AND shared-app isolation
- `regulated_operational_risk` = scenario_regulated_data AND (fine-grained audit OR retention OR residency)
- `sla_operational_risk` = scenario_high_sla AND (rto <4h OR rpo <1h OR active-active DR)
- `architect_risk_high` = 2+ of (high_business_criticality, high_arch_complexity, integration_heavy, high_availability)

---

## Selection Logic (Deterministic)

**Enterprise** if ANY: regulated, sensitive_data AND prod_risk, process_heavy AND prod_risk,
high_governance, speed_vs_control is strict, architect_risk_high AND prod_risk,
high_business_criticality AND high_availability, multi_tenant_risk AND external-customers,
regulated_operational_risk AND prod_risk, sla_operational_risk AND prod_risk.

**Fast** if ALL: NOT regulated, NOT sensitive data, prototype/pre-production stage,
NOT external-customers, no oncall, low/medium governance, low complexity,
0-4 integrations, no multi-tenant, no high-SLA, speed/urgent.

**Team** otherwise. Tie-breaker: uncertain Fast/Team → Team; uncertain Team/Enterprise → Enterprise.

---

## File Mapping

- Fast → `bootstrap/1Fast-ws-Bootstrap.md`
- Team → `bootstrap/2Team-ws-Bootstrap.md`
- Enterprise → `bootstrap/3Enterprise-ws-Bootstrap.md`
- Fallback → `bootstrap/Uni-WindsurfBootstrap.md`

---

## Output Format

Return: recommended tier, selected file path, reasoning table, scenario branches triggered,
architecture summary, risk note, confidence (High/Medium/Low), PRD recommendation.

Then ask: `Apply this selection? (yes/no)`

If yes: read selected file, paste content, offer: generate PRD via /bootstrap-prd? Run bootstrap now?
If no: ask which tier to force, read and paste that file.
