---
description: Run Nexus health checks to validate all components work cohesively
---
# Nexus Health Check

Validates that all Nexus components (rules, skills, workflows, cross-IDE files) are properly configured, security posture is healthy, and CLI tools are functioning correctly.

## 1) Run full health check
// turbo
```
python bootstrap/cli/bs_cli.py health check --format human
```

## 2) Review results
Examine the health score and any issues found. A score > 80 indicates healthy configuration.

## 3) If issues found, run detailed report
// turbo
```
python bootstrap/cli/bs_cli.py health report --format human
```

## 4) Fix critical issues first
Follow the recommendations in priority order:
- **Critical**: Leaked secrets → remove immediately
- **High**: Missing components → run `/migrate-toolkit` or create manually
- **Medium**: Configuration gaps → update .gitignore, .codeiumignore, cross-IDE files
- **Low**: Style/consistency → update frontmatter, descriptions

## 5) Re-run health check to verify fixes
// turbo
```
python bootstrap/cli/bs_cli.py health check --format human
```

## 6) Optional: Check security posture separately
// turbo
```
python bootstrap/cli/bs_cli.py health security --format human
```

## 7) Optional: Review CLI usage analytics
// turbo
```
python bootstrap/cli/bs_cli.py health usage --format human
```
