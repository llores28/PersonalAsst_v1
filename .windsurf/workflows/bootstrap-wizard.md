---
description: Intake-driven wizard that selects the right bootstrap prompt (Fast, Team, or Enterprise)
auto_execution_mode: 3
---
# Bootstrap Wizard

Goal: choose the correct bootstrap prompt from `bootstrap/` using structured project intake.

## 1) Solution Architect discovery interview

1. Ask the user to fill `bootstrap/Bootstrap-Project-Intake.md`.
2. If they already provided intake in chat, reuse it and do not ask duplicate questions.
3. Ask only missing fields, then run a short architecture interview for unknown/high-impact areas.

Interview behavior (required):
- Ask one question at a time and adapt based on previous answers.
- Prioritize architecture drivers over implementation details.
- Stop when the minimum decision fields are complete and uncertainty is acceptable.
- Ask at most 12 discovery questions in one pass.

Architecture question bank (prioritized):
1. What business outcome must this project achieve in 6-12 months?
2. What is the impact of downtime/data loss (financial, customer, compliance)?
3. Who are the primary users/tenants (internal, external, multi-tenant)?
4. What systems/services must integrate with this project?
5. What uptime/SLA expectations exist (best effort vs strict uptime)?
6. What is expected growth (traffic, data volume, contributors)?
7. What approval, audit, or traceability requirements are mandatory?
8. What data classes are handled (public/internal/PII/PHI/PCI)?
9. What on-call and incident response expectations exist?
10. What tradeoff matters most now: delivery speed or governance control?

Required fields:
- `project_stage`
- `customer_impact`
- `tenant_profile`
- `oncall_required`
- `compliance_requirements`
- `data_sensitivity`
- `security_governance`
- `needs_formal_approvals`
- `needs_audit_trail`
- `speed_vs_control`
- `timeline_pressure`
- `business_criticality`
- `architecture_complexity`
- `integration_count`
- `availability_target`

### Scenario triggers and branching follow-ups

After minimum required fields are captured, detect scenario triggers and ask branch-specific questions.

Trigger conditions:
- `scenario_multi_tenant` if `tenant_profile` is `external-multi-tenant` or `mixed`.
- `scenario_regulated_data` if compliance requirements are non-empty OR data sensitivity is `pii`/`phi`/`pci`.
- `scenario_high_sla` if availability target is `99.9`/`99.95+` OR on-call is required.

### Question sequencing policy (short)

If multiple scenarios are triggered, ask branch follow-ups in this strict order:
1. Regulated-data
2. High-SLA
3. Multi-tenant

Complete one branch before moving to the next.

Branch A — Multi-tenant (ask all if triggered):
- `tenancy_model`: single-tenant | multi-tenant | hybrid
- `tenant_isolation_strategy`: shared-app | schema-per-tenant | db-per-tenant
- `tenant_authz_boundary`: weak | strong
- `tenant_customization_level`: low | medium | high

Branch B — Regulated data (ask all if triggered):
- `regulated_data_scope`: limited | broad
- `audit_log_granularity`: baseline | fine-grained
- `retention_and_deletion_obligations`: no | yes
- `data_residency_requirement`: no | yes

Branch C — High SLA (ask all if triggered):
- `rto_target`: >24h | 4-24h | <4h
- `rpo_target`: >24h | 1-24h | <1h
- `dr_strategy`: none | backup-restore | active-passive | active-active
- `deployment_safety_level`: basic | canary-bluegreen | progressive-with-auto-rollback

If a branch is not triggered, mark its fields `not-applicable`.

If key fields remain unknown after one follow-up, continue with conservative defaults and explicitly mark assumptions.

## 2) Normalize values

Normalize to lowercase and map synonyms:
- `production` -> production
- `prod` -> production
- `external` -> external-customers
- `soc 2` -> soc2
- `none`, `[]`, empty list -> no compliance requirements
- `mission critical` -> mission-critical
- `99.9+` -> 99.9
- `many` integrations -> 5+
- `multi tenant` -> multi-tenant
- `external multi tenant` -> external-multi-tenant
- `mixed tenancy` -> mixed

Treat `unknown` as missing.

## 3) Compute decision flags

Set booleans:
- `regulated` = compliance requirements list is not empty
- `sensitive_data` = data_sensitivity is one of `pii`, `phi`, `pci`
- `high_governance` = security_governance is `high`
- `process_heavy` = needs_formal_approvals is `yes` OR needs_audit_trail is `yes`
- `prod_risk` = project_stage is `production` OR customer_impact is `external-customers` OR oncall_required is `yes`
- `high_business_criticality` = business_criticality is `high` OR `mission-critical`
- `high_arch_complexity` = architecture_complexity is `high`
- `integration_heavy` = integration_count is `5+`
- `high_availability` = availability_target is `99.9` OR `99.95+`
- `multi_tenant_candidate` = tenant_profile is `external-multi-tenant` OR `mixed`
- `scenario_multi_tenant` = multi_tenant_candidate OR tenancy_model is `multi-tenant` OR `hybrid`
- `scenario_regulated_data` = regulated OR sensitive_data
- `scenario_high_sla` = high_availability OR oncall_required is `yes`
- `multi_tenant_risk` = scenario_multi_tenant AND tenant_isolation_strategy is `shared-app`
- `regulated_operational_risk` = scenario_regulated_data AND (`audit_log_granularity` is `fine-grained` OR `retention_and_deletion_obligations` is `yes` OR `data_residency_requirement` is `yes`)
- `sla_operational_risk` = scenario_high_sla AND (`rto_target` is `<4h` OR `rpo_target` is `<1h` OR `dr_strategy` is `active-active`)
- `architect_risk_high` = two or more of (`high_business_criticality`, `high_arch_complexity`, `integration_heavy`, `high_availability`) are true

## 4) Selection logic (deterministic)

Apply in this exact order:

1. **Enterprise** if ANY of:
   - `regulated`
   - `sensitive_data` AND `prod_risk`
   - `process_heavy` AND `prod_risk`
   - `high_governance`
   - `speed_vs_control` is `strict`
   - `architect_risk_high` AND `prod_risk`
   - `high_business_criticality` AND `high_availability`
   - `multi_tenant_risk` AND `customer_impact` is `external-customers`
   - `regulated_operational_risk` AND `prod_risk`
   - `sla_operational_risk` AND `prod_risk`

2. **Fast (Daily)** if ALL of:
   - NOT `regulated`
   - `data_sensitivity` is NOT `pii`/`phi`/`pci`
   - project_stage is `prototype` OR `pre-production`
   - customer_impact is NOT `external-customers`
   - oncall_required is `no`
   - security_governance is `low` OR `medium`
   - architecture_complexity is `low`
   - integration_count is `0-1` OR `2-4`
   - scenario_multi_tenant is false
   - scenario_high_sla is false
   - speed_vs_control is `speed` OR timeline_pressure is `urgent`

3. **Team (Balanced)** otherwise.

Tie-breaker rule:
- If uncertain between Fast and Team, choose **Team**.
- If uncertain between Team and Enterprise, choose **Enterprise**.

## 5) File mapping

Map the selected tier to these files:
- Fast -> `bootstrap/1Fast-ws-Bootstrap.md`
- Team -> `bootstrap/2Team-ws-Bootstrap.md`
- Enterprise -> `bootstrap/3Enterprise-ws-Bootstrap.md`

If mapped file is missing, fallback to `bootstrap/Uni-WindsurfBootstrap.md` and state why.

## 6) Output format

Return:

1. **Recommended tier**: Fast / Team / Enterprise
2. **Selected file**: exact path
3. **Reasoning table**: intake field -> value -> effect on decision
4. **Scenario branches triggered**: multi-tenant / regulated-data / high-SLA + key branch answers
5. **Architecture summary**: business criticality, complexity, integrations, availability, governance
6. **Risk note**: key risks that justified the tier
7. **Confidence**: High / Medium / Low
8. **PRD recommendation**: `Run /bootstrap-prd` (recommended before implementation for Team/Enterprise or Medium/Low confidence)

Then ask:
- `Apply this selection? (yes/no)`

If yes:
1. Read the selected bootstrap file.
2. Paste its full prompt content in the chat.
3. Offer next actions in this order:
   - `Generate a cohesive PRD now via /bootstrap-prd?`
   - `Run this bootstrap now against the current repository?`

If no:
1. Ask which tier to force (`Fast`, `Team`, `Enterprise`).
2. Read and paste that file.

## 7) Safety rules for this wizard

- Never include secret values.
- Never invent repository commands.
- If required inputs are missing, ask focused follow-up questions first.
- Prioritize architecture and risk clarity before deciding tier.
- Prefer Team tier when uncertainty remains.
- Keep recommendations reversible and explicit.
