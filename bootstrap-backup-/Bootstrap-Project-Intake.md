# Bootstrap Project Intake (Wizard Input)

Copy/paste this into Cascade when running `/bootstrap-wizard` and fill values.

```yaml
project_name:
repo_type: single-service | monorepo | polyrepo
primary_stack:
team_size:
active_contributors:

project_stage: prototype | pre-production | production
customer_impact: none | internal-only | external-customers
tenant_profile: internal-only | external-single-tenant | external-multi-tenant | mixed
oncall_required: yes | no
business_criticality: low | medium | high | mission-critical

compliance_requirements: [] # e.g. [soc2], [hipaa], [pci], [fedramp], [iso27001]
data_sensitivity: public | internal | confidential | pii | phi | pci
security_governance: low | medium | high
availability_target: best-effort | 99.0 | 99.9 | 99.95+

release_frequency: ad-hoc | weekly | daily
needs_formal_approvals: yes | no
needs_audit_trail: yes | no
architecture_complexity: low | medium | high
integration_count: 0-1 | 2-4 | 5+

documentation_depth: low | medium | high
risk_tolerance: high | medium | low
speed_vs_control: speed | balanced | strict

timeline_pressure: urgent | normal | flexible
notes:

# Conditional scenario fields (wizard asks only if scenario is triggered)

# Multi-tenant branch
tenancy_model: not-applicable | single-tenant | multi-tenant | hybrid
tenant_isolation_strategy: not-applicable | shared-app | schema-per-tenant | db-per-tenant
tenant_authz_boundary: not-applicable | weak | strong
tenant_customization_level: not-applicable | low | medium | high

# Regulated-data branch
regulated_data_scope: not-applicable | limited | broad
audit_log_granularity: not-applicable | baseline | fine-grained
retention_and_deletion_obligations: not-applicable | no | yes
data_residency_requirement: not-applicable | no | yes

# High-SLA branch
rto_target: not-applicable | >24h | 4-24h | <4h
rpo_target: not-applicable | >24h | 1-24h | <1h
dr_strategy: not-applicable | none | backup-restore | active-passive | active-active
deployment_safety_level: not-applicable | basic | canary-bluegreen | progressive-with-auto-rollback
```

## Quick guidance

- If any fields are unknown, write `unknown`.
- If a scenario does not apply, keep its fields as `not-applicable`.
- Do not include secret values.
- Keep answers factual and brief.
