---
name: nexus-health
description: Validate Nexus components work cohesively — rules, skills, workflows, cross-IDE files, security posture, and CLI usage patterns
---

# Nexus Health Check

Run comprehensive health checks to validate that all Nexus components are properly configured and working together.

## When to Use
- After bootstrapping a new project
- After running `/migrate-toolkit`
- When Nexus behavior seems inconsistent
- Before committing major changes
- Periodic health monitoring

## Prerequisites
- Python 3.10+ with CLI toolkit dependencies installed
- Project must have `.windsurf/` directory

## Commands

### Full Health Check
```bash
python bootstrap/cli/bs_cli.py health check --format human
```

### Component Inventory Only
```bash
python bootstrap/cli/bs_cli.py health components --format human
```

### Security Posture
```bash
python bootstrap/cli/bs_cli.py health security --format human
```

### CLI Usage Analytics
```bash
python bootstrap/cli/bs_cli.py health usage --format human
```

### Full Report with Recommendations
```bash
python bootstrap/cli/bs_cli.py health report --format human
```

## What It Checks

### Tier 1: Components
- Rules exist and are well-formed (frontmatter, size limits, activation triggers)
- Skills exist with valid SKILL.md files
- Workflows exist with valid frontmatter
- Cross-IDE files present and consistent (AGENTS.md, CLAUDE.md, .cursorrules, copilot-instructions.md)
- Bootstrap templates reference required files

### Tier 2: Security
- .gitignore covers sensitive patterns
- .codeiumignore excludes large reference files
- No leaked secrets in config files
- CLI dependencies are importable

### Tier 3: Usage
- CLI audit trail analysis (tool counts, error rates, duration trends)
- Recent error identification
- Tool adoption patterns

### Tier 4: Recommendations
- Actionable fixes sorted by severity
- Links to relevant commands and workflows

## Health Score
Score is 0–100, calculated from weighted issue counts:
- Critical issues: -20 points each
- High issues: -10 points each
- Medium issues: -5 points each
- Low issues: -2 points each

**Target**: Score > 80 indicates healthy Nexus configuration.
