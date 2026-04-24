"""
Nexus Health Check Tool — validates Nexus components work cohesively.

Subcommands:
  check      — full health check (all tiers)
  components — Tier 1: component inventory and integrity
  security   — Tier 2: security posture validation
  usage      — Tier 3: CLI audit trail analysis
  report     — full report with recommendations
"""

import json
import re
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

from bootstrap.cli.utils import (
    OutputFormat,
    Status,
    Severity,
    emit,
    make_result,
    truncate_output,
    find_project_root,
)
from bootstrap.cli.security import scan_text_for_secrets, validate_path


# --- Expected Nexus Components ---

EXPECTED_RULES = [
    "00-token-efficiency.md",
]

# Rules that a bootstrapped project SHOULD have (not required for Nexus repo itself)
OPTIONAL_RULES = [
    "00-project-overview.md",
    "01-security-and-secrets.md",
    "02-change-safety-and-testing.md",
    "03-release-and-ops.md",
    "02-secure-coding-and-input-validation.md",
    "03-change-management-and-approvals.md",
    "04-testing-and-quality-gates.md",
]

EXPECTED_SKILLS = [
    "prereqs-check",
    "smoketest",
    "debug-investigate",
    "research-investigate",
    "webscrape",
    "create-cli-tool",
    "local-env",
]

EXPECTED_WORKFLOWS = [
    "bootstrap-wizard.md",
    "bootstrap-prd.md",
    "prereqs-check.md",
    "smoketest.md",
    "debug-investigate.md",
    "research.md",
    "scrape-docs.md",
    "local-env.md",
    "create-tool.md",
    "migrate-toolkit.md",
]

EXPECTED_CROSS_IDE = [
    "AGENTS.md",
    "CLAUDE.md",
    ".cursorrules",
    ".github/copilot-instructions.md",
]

RULE_MAX_SIZE_BYTES = 12_000  # 12KB limit per rule file

GITIGNORE_REQUIRED_PATTERNS = [
    ".env",
    "__pycache__",
    "node_modules",
    ".venv",
    "*.key",
    "*.pem",
]

CODEIUMIGNORE_EXPECTED = [
    "wizard-reference.md",
    "model-selection-reference.md",
]


# --- Tier 1: Component Inventory & Integrity ---

def _check_rules(project_dir: Path) -> dict[str, Any]:
    """Validate rules exist, are well-formed, and within size limits."""
    rules_dir = project_dir / ".windsurf" / "rules"
    issues: list[dict] = []
    found_rules: list[str] = []

    if not rules_dir.is_dir():
        return {
            "status": "fail",
            "found": 0,
            "expected": len(EXPECTED_RULES),
            "issues": [{"severity": "high", "message": ".windsurf/rules/ directory not found"}],
        }

    # Discover all rule files
    all_rules = sorted(rules_dir.glob("*.md"))
    found_rules = [r.name for r in all_rules]

    # Check expected rules
    for expected in EXPECTED_RULES:
        if expected not in found_rules:
            issues.append({
                "severity": "high",
                "message": f"Missing required rule: {expected}",
            })

    # Validate each rule file
    for rule_path in all_rules:
        # Size check
        size = rule_path.stat().st_size
        if size > RULE_MAX_SIZE_BYTES:
            issues.append({
                "severity": "medium",
                "message": f"Rule {rule_path.name} exceeds 12KB limit ({size:,} bytes)",
            })

        # Check for activation trigger in frontmatter
        try:
            content = rule_path.read_text(encoding="utf-8", errors="replace")
            if content.startswith("---"):
                frontmatter_end = content.find("---", 3)
                if frontmatter_end > 0:
                    frontmatter = content[3:frontmatter_end]
                    if "trigger" not in frontmatter.lower() and "activation" not in frontmatter.lower():
                        # Check for common trigger patterns
                        has_trigger = any(
                            pat in frontmatter.lower()
                            for pat in ["always_on", "model_decision", "glob", "manual"]
                        )
                        if not has_trigger:
                            issues.append({
                                "severity": "low",
                                "message": f"Rule {rule_path.name} may be missing activation trigger in frontmatter",
                            })
            elif not content.strip().startswith("#"):
                issues.append({
                    "severity": "low",
                    "message": f"Rule {rule_path.name} has no frontmatter or heading",
                })
        except OSError:
            issues.append({
                "severity": "medium",
                "message": f"Cannot read rule: {rule_path.name}",
            })

    # Determine status
    high_issues = [i for i in issues if i["severity"] == "high"]
    status = "fail" if high_issues else ("warn" if issues else "pass")

    return {
        "status": status,
        "found": len(found_rules),
        "expected": len(EXPECTED_RULES),
        "all_rules": found_rules,
        "issues": issues,
    }


def _check_skills(project_dir: Path) -> dict[str, Any]:
    """Validate skills exist and have valid SKILL.md files."""
    skills_dir = project_dir / ".windsurf" / "skills"
    issues: list[dict] = []
    found_skills: list[str] = []

    if not skills_dir.is_dir():
        return {
            "status": "fail",
            "found": 0,
            "expected": len(EXPECTED_SKILLS),
            "issues": [{"severity": "high", "message": ".windsurf/skills/ directory not found"}],
        }

    # Discover all skill folders
    for skill_dir in sorted(skills_dir.iterdir()):
        if skill_dir.is_dir() and not skill_dir.name.startswith("."):
            found_skills.append(skill_dir.name)

            skill_md = skill_dir / "SKILL.md"
            if not skill_md.exists():
                issues.append({
                    "severity": "high",
                    "message": f"Skill {skill_dir.name}/ missing SKILL.md",
                })
            else:
                # Validate SKILL.md has frontmatter with name and description
                try:
                    content = skill_md.read_text(encoding="utf-8", errors="replace")
                    if not content.startswith("---"):
                        issues.append({
                            "severity": "medium",
                            "message": f"Skill {skill_dir.name}/SKILL.md missing YAML frontmatter",
                        })
                    else:
                        frontmatter_end = content.find("---", 3)
                        if frontmatter_end > 0:
                            fm = content[3:frontmatter_end].lower()
                            if "name" not in fm:
                                issues.append({
                                    "severity": "medium",
                                    "message": f"Skill {skill_dir.name}/SKILL.md missing 'name' in frontmatter",
                                })
                            if "description" not in fm:
                                issues.append({
                                    "severity": "medium",
                                    "message": f"Skill {skill_dir.name}/SKILL.md missing 'description' in frontmatter",
                                })
                except OSError:
                    issues.append({
                        "severity": "medium",
                        "message": f"Cannot read skill: {skill_dir.name}/SKILL.md",
                    })

    # Check expected skills
    for expected in EXPECTED_SKILLS:
        if expected not in found_skills:
            issues.append({
                "severity": "medium",
                "message": f"Missing expected skill: {expected}",
            })

    high_issues = [i for i in issues if i["severity"] == "high"]
    status = "fail" if high_issues else ("warn" if issues else "pass")

    return {
        "status": status,
        "found": len(found_skills),
        "expected": len(EXPECTED_SKILLS),
        "all_skills": found_skills,
        "issues": issues,
    }


def _check_workflows(project_dir: Path) -> dict[str, Any]:
    """Validate workflows exist and have valid frontmatter."""
    wf_dir = project_dir / ".windsurf" / "workflows"
    issues: list[dict] = []
    found_wfs: list[str] = []

    if not wf_dir.is_dir():
        return {
            "status": "fail",
            "found": 0,
            "expected": len(EXPECTED_WORKFLOWS),
            "issues": [{"severity": "high", "message": ".windsurf/workflows/ directory not found"}],
        }

    # Discover all workflow files
    all_wfs = sorted(wf_dir.glob("*.md"))
    found_wfs = [w.name for w in all_wfs]

    for expected in EXPECTED_WORKFLOWS:
        if expected not in found_wfs:
            issues.append({
                "severity": "medium",
                "message": f"Missing expected workflow: {expected}",
            })

    # Validate each workflow
    for wf_path in all_wfs:
        try:
            content = wf_path.read_text(encoding="utf-8", errors="replace")
            if not content.startswith("---"):
                issues.append({
                    "severity": "low",
                    "message": f"Workflow {wf_path.name} missing YAML frontmatter with description",
                })
            else:
                frontmatter_end = content.find("---", 3)
                if frontmatter_end > 0:
                    fm = content[3:frontmatter_end].lower()
                    if "description" not in fm:
                        issues.append({
                            "severity": "low",
                            "message": f"Workflow {wf_path.name} missing 'description' in frontmatter",
                        })
        except OSError:
            issues.append({
                "severity": "medium",
                "message": f"Cannot read workflow: {wf_path.name}",
            })

    high_issues = [i for i in issues if i["severity"] == "high"]
    status = "fail" if high_issues else ("warn" if issues else "pass")

    return {
        "status": status,
        "found": len(found_wfs),
        "expected": len(EXPECTED_WORKFLOWS),
        "all_workflows": found_wfs,
        "issues": issues,
    }


def _check_cross_ide(project_dir: Path) -> dict[str, Any]:
    """Validate cross-IDE files exist and are consistent."""
    issues: list[dict] = []
    found_files: list[str] = []
    project_names: dict[str, str] = {}

    for rel_path in EXPECTED_CROSS_IDE:
        full_path = project_dir / rel_path
        if full_path.exists():
            found_files.append(rel_path)

            # Extract project name/description from first meaningful line
            try:
                content = full_path.read_text(encoding="utf-8", errors="replace")
                first_line = ""
                for line in content.splitlines():
                    stripped = line.strip().lstrip("#").strip()
                    if stripped and len(stripped) > 5:
                        first_line = stripped
                        break
                if first_line:
                    project_names[rel_path] = first_line[:100]
            except OSError:
                pass
        else:
            issues.append({
                "severity": "medium",
                "message": f"Missing cross-IDE file: {rel_path}",
            })

    # Check consistency — all files should reference similar project context
    if len(project_names) >= 2:
        names_list = list(project_names.values())
        # Simple check: do they all contain a common substring?
        common_words = set(names_list[0].lower().split()) if names_list else set()
        for name in names_list[1:]:
            common_words &= set(name.lower().split())
        # Filter out very short/common words
        meaningful_common = {w for w in common_words if len(w) > 3}
        if not meaningful_common:
            issues.append({
                "severity": "low",
                "message": "Cross-IDE files may have inconsistent project descriptions",
                "details": project_names,
            })

    status = "fail" if not found_files else ("warn" if issues else "pass")

    return {
        "status": status,
        "found": len(found_files),
        "expected": len(EXPECTED_CROSS_IDE),
        "files": found_files,
        "issues": issues,
    }


def _check_bootstrap_templates(project_dir: Path) -> dict[str, Any]:
    """Validate nexus templates reference required files."""
    templates_dir = project_dir / "nexus"
    issues: list[dict] = []
    checked = 0

    template_patterns = ["*Bootstrap*.md", "*ws-Bootstrap*.md"]
    # Exclude non-output templates (intake forms, PRD templates, README)
    exclude_names = {"README.md", "Bootstrap-Project-Intake.md", "PRD-Template.md"}
    templates: list[Path] = []
    for pattern in template_patterns:
        templates.extend(templates_dir.glob(pattern))

    for tmpl in templates:
        if tmpl.name in exclude_names:
            continue
        checked += 1
        try:
            content = tmpl.read_text(encoding="utf-8", errors="replace")
            if "model-selection-reference" not in content:
                issues.append({
                    "severity": "medium",
                    "message": f"Template {tmpl.name} missing reference to model-selection-reference.md",
                })
            if "token-efficiency" not in content.lower() and "00-token-efficiency" not in content:
                issues.append({
                    "severity": "low",
                    "message": f"Template {tmpl.name} missing reference to token-efficiency rule",
                })
        except OSError:
            issues.append({
                "severity": "medium",
                "message": f"Cannot read template: {tmpl.name}",
            })

    status = "warn" if issues else "pass"
    return {
        "status": status,
        "templates_checked": checked,
        "issues": issues,
    }


def run_components(project_dir: Path) -> dict[str, Any]:
    """Tier 1: Full component inventory and integrity check."""
    rules = _check_rules(project_dir)
    skills = _check_skills(project_dir)
    workflows = _check_workflows(project_dir)
    cross_ide = _check_cross_ide(project_dir)
    templates = _check_bootstrap_templates(project_dir)

    all_issues = (
        rules["issues"] + skills["issues"] + workflows["issues"]
        + cross_ide["issues"] + templates["issues"]
    )
    high_count = sum(1 for i in all_issues if i["severity"] == "high")
    med_count = sum(1 for i in all_issues if i["severity"] == "medium")

    if high_count > 0:
        status = "fail"
    elif med_count > 0:
        status = "warn"
    else:
        status = "pass"

    return {
        "status": status,
        "rules": rules,
        "skills": skills,
        "workflows": workflows,
        "cross_ide": cross_ide,
        "templates": templates,
        "total_issues": len(all_issues),
    }


# --- Tier 2: Security & Configuration ---

def _check_gitignore(project_dir: Path) -> dict[str, Any]:
    """Validate .gitignore covers sensitive patterns."""
    gitignore = project_dir / ".gitignore"
    issues: list[dict] = []

    if not gitignore.exists():
        return {
            "status": "fail",
            "issues": [{"severity": "high", "message": ".gitignore not found"}],
        }

    try:
        content = gitignore.read_text(encoding="utf-8", errors="replace")
        for pattern in GITIGNORE_REQUIRED_PATTERNS:
            if pattern not in content:
                issues.append({
                    "severity": "medium",
                    "message": f".gitignore missing pattern: {pattern}",
                })
    except OSError:
        return {
            "status": "fail",
            "issues": [{"severity": "high", "message": "Cannot read .gitignore"}],
        }

    status = "fail" if any(i["severity"] == "high" for i in issues) else ("warn" if issues else "pass")
    return {
        "status": status,
        "patterns_checked": len(GITIGNORE_REQUIRED_PATTERNS),
        "issues": issues,
    }


def _check_codeiumignore(project_dir: Path) -> dict[str, Any]:
    """Validate .codeiumignore excludes large reference files."""
    codeiumignore = project_dir / ".codeiumignore"
    issues: list[dict] = []

    if not codeiumignore.exists():
        return {
            "status": "warn",
            "issues": [{"severity": "medium", "message": ".codeiumignore not found — large files may waste tokens"}],
        }

    try:
        content = codeiumignore.read_text(encoding="utf-8", errors="replace")
        for expected in CODEIUMIGNORE_EXPECTED:
            if expected not in content:
                issues.append({
                    "severity": "medium",
                    "message": f".codeiumignore missing exclusion: {expected}",
                })
    except OSError:
        return {
            "status": "fail",
            "issues": [{"severity": "medium", "message": "Cannot read .codeiumignore"}],
        }

    status = "warn" if issues else "pass"
    return {
        "status": status,
        "exclusions": len(CODEIUMIGNORE_EXPECTED),
        "issues": issues,
    }


# Files that are gitignored by convention and allowed to contain secrets
_GITIGNORED_SECRET_FILES = {".env", ".env.local", ".env.production", ".env.staging"}
# Files that must ONLY have placeholder values (fail if real-looking secrets found)
_PLACEHOLDER_FILES = {".env.example", ".env.sample", ".env.template"}


def _looks_like_placeholder(value: str) -> bool:
    """Return True if value appears to be a placeholder, not a real secret."""
    placeholder_patterns = [
        "your_", "<", ">", "placeholder", "changeme", "change_me",
        "xxxxx", "example", "replace_me", "todo", "...", "your-",
        "insert", "random_string", "random_password", "set_this",
        "fill_in", "put_your", "enter_your", "add_your",
    ]
    v = value.lower().strip()
    return any(p in v for p in placeholder_patterns) or len(v) < 3


def _check_secrets(project_dir: Path) -> dict[str, Any]:
    """Quick secrets scan on tracked config files.

    Skips gitignored .env files (they SHOULD contain secrets — that's the point).
    Scans .env.example files only for accidental real values (not placeholders).
    Scans committed config files (docker-compose, JS configs) for hardcoded secrets.
    """
    all_findings: list[dict] = []
    files_scanned = 0

    # Only scan files that are committed to the repo — skip pure secret stores
    committed_patterns = [
        "*.config.js", "*.config.ts",
        "docker-compose*.yml", "docker-compose*.yaml",
    ]
    example_patterns = [".env.example", ".env.sample", ".env.template"]

    scan_targets: list[Path] = []
    for pattern in committed_patterns:
        for fpath in project_dir.glob(pattern):
            if fpath.is_file():
                scan_targets.append(fpath)
    for pattern in example_patterns:
        for fpath in project_dir.glob(pattern):
            if fpath.is_file():
                scan_targets.append(fpath)

    # Detect shell variable substitution anywhere in the value portion of a line
    _shell_var_anywhere_re = re.compile(r"\$\{[^}]+\}|\$[A-Z_][A-Z0-9_]*")

    for fpath in scan_targets[:30]:
        files_scanned += 1
        fname = fpath.name
        is_example = fname in _PLACEHOLDER_FILES
        try:
            raw_lines = fpath.read_text(encoding="utf-8", errors="replace").splitlines()
            content = "\n".join(raw_lines)
            findings = scan_text_for_secrets(content)
            for f in findings:
                line_idx = f.get("line", 1) - 1
                raw_line = raw_lines[line_idx].strip() if line_idx < len(raw_lines) else ""

                # Extract value part (everything after first = or :)
                val_part = raw_line.split("=", 1)[-1] if "=" in raw_line else raw_line.split(":", 1)[-1]

                # Skip lines where the value only references shell variables (no hardcoded secrets)
                # e.g. ${OPENAI_API_KEY}, postgresql+asyncpg://user:${DB_PASSWORD}@host
                val_stripped = _shell_var_anywhere_re.sub("", val_part).strip()
                if not val_stripped or val_stripped in ("postgresql+asyncpg://assistant:@postgres:5432/assistant",):
                    continue
                # If after removing all ${VAR} references the remaining non-variable chars are only
                # URL structure or empty, it's safe (no literal secret)
                remaining = re.sub(r"[/@:+a-zA-Z0-9._\-]", "", val_stripped).strip()
                if not remaining:
                    continue

                # For .env.example: skip findings that look like placeholders
                if is_example:
                    example_val = raw_line.split("=", 1)[-1] if "=" in raw_line else ""
                    # Strip inline comments (e.g. "change_me  # comment")
                    example_val = example_val.split("#")[0].strip()
                    if _looks_like_placeholder(example_val):
                        continue

                f["file"] = str(fpath.relative_to(project_dir))
                all_findings.append(f)
        except OSError:
            continue

    real_findings = all_findings

    status = "fail" if real_findings else "pass"
    return {
        "status": status,
        "files_scanned": files_scanned,
        "secrets_found": len(real_findings),
        "findings": real_findings[:20],
    }


def _check_dependencies(project_dir: Path) -> dict[str, Any]:
    """Check that CLI toolkit dependencies are importable."""
    issues: list[dict] = []
    checked = 0

    required_packages = {
        "click": "click",
        "rich": "rich",
        "yaml": "pyyaml",
        "httpx": "httpx",
        "bs4": "beautifulsoup4",
    }

    for import_name, pkg_name in required_packages.items():
        checked += 1
        try:
            __import__(import_name)
        except ImportError:
            issues.append({
                "severity": "high",
                "message": f"Cannot import '{import_name}' (pip install {pkg_name})",
            })

    status = "fail" if issues else "pass"
    return {
        "status": status,
        "packages_checked": checked,
        "issues": issues,
    }


def run_security(project_dir: Path) -> dict[str, Any]:
    """Tier 2: Security and configuration health."""
    gitignore = _check_gitignore(project_dir)
    codeiumignore = _check_codeiumignore(project_dir)
    secrets = _check_secrets(project_dir)
    deps = _check_dependencies(project_dir)

    all_issues = (
        gitignore["issues"] + codeiumignore["issues"]
        + secrets.get("findings", []) + deps["issues"]
    )
    high_count = sum(1 for i in all_issues if i.get("severity") == "high")

    status = "fail" if high_count > 0 else ("warn" if all_issues else "pass")

    return {
        "status": status,
        "gitignore": gitignore,
        "codeiumignore": codeiumignore,
        "secrets": secrets,
        "dependencies": deps,
    }


# --- Tier 3: Usage Analytics ---

def run_usage(project_dir: Path) -> dict[str, Any]:
    """Tier 3: Analyze CLI audit trail for usage patterns."""
    audit_file = project_dir / ".cache" / "bs-cli" / "audit.jsonl"

    if not audit_file.exists():
        return {
            "status": "info",
            "message": "No audit trail found — CLI tools haven't been used yet",
            "total_invocations": 0,
        }

    entries: list[dict] = []
    try:
        with open(audit_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        entries.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
    except OSError:
        return {
            "status": "fail",
            "message": "Cannot read audit trail",
        }

    if not entries:
        return {
            "status": "info",
            "message": "Audit trail is empty",
            "total_invocations": 0,
        }

    # Analyze usage
    total = len(entries)
    errors = sum(1 for e in entries if e.get("exit_code", 0) != 0)
    error_rate = errors / total if total > 0 else 0.0

    # Tool usage counts
    tool_counts: dict[str, int] = {}
    for e in entries:
        tool = e.get("tool", "unknown")
        tool_counts[tool] = tool_counts.get(tool, 0) + 1

    most_used = max(tool_counts, key=tool_counts.get) if tool_counts else "none"
    least_used = min(tool_counts, key=tool_counts.get) if tool_counts else "none"

    # Duration analysis
    durations = [e.get("duration_ms", 0) for e in entries if e.get("duration_ms")]
    avg_duration = sum(durations) / len(durations) if durations else 0

    # Last activity
    last_entry = entries[-1] if entries else {}
    last_activity = last_entry.get("timestamp", "unknown")

    # Recent errors (last 10 failures)
    recent_errors = [
        {
            "tool": e.get("tool"),
            "timestamp": e.get("timestamp"),
            "exit_code": e.get("exit_code"),
        }
        for e in reversed(entries)
        if e.get("exit_code", 0) != 0
    ][:10]

    status = "fail" if error_rate > 0.25 else ("warn" if error_rate > 0.1 else "pass")

    return {
        "status": status,
        "total_invocations": total,
        "error_count": errors,
        "error_rate": round(error_rate, 3),
        "most_used_tool": most_used,
        "least_used_tool": least_used,
        "tool_counts": dict(sorted(tool_counts.items(), key=lambda x: -x[1])),
        "avg_duration_ms": round(avg_duration),
        "last_activity": last_activity,
        "recent_errors": recent_errors,
    }


# --- Tier 4: Recommendations ---

def _generate_recommendations(
    components: dict, security: dict, usage: dict
) -> list[dict]:
    """Generate actionable recommendations from health check results."""
    recs: list[dict] = []

    # Component recommendations
    if components.get("rules", {}).get("status") == "fail":
        recs.append({
            "severity": "high",
            "category": "components",
            "message": "Missing required rules — run /bootstrap-wizard to set up rules",
            "action": "Run /bootstrap-wizard or manually create .windsurf/rules/ directory",
        })

    for issue in components.get("rules", {}).get("issues", []):
        if "exceeds 12KB" in issue.get("message", ""):
            recs.append({
                "severity": "medium",
                "category": "performance",
                "message": issue["message"],
                "action": "Split large rules into multiple files or move content to model_decision trigger",
            })

    if components.get("skills", {}).get("status") in ("fail", "warn"):
        missing = [
            i["message"] for i in components.get("skills", {}).get("issues", [])
            if "Missing" in i.get("message", "")
        ]
        if missing:
            recs.append({
                "severity": "medium",
                "category": "components",
                "message": f"Missing skills: {', '.join(missing)}",
                "action": "Run /migrate-toolkit to install missing skills",
            })

    if components.get("cross_ide", {}).get("found", 0) < len(EXPECTED_CROSS_IDE):
        recs.append({
            "severity": "medium",
            "category": "compatibility",
            "message": "Missing cross-IDE configuration files",
            "action": "Create missing files from Nexus templates for full IDE compatibility",
        })

    # Security recommendations
    if security.get("gitignore", {}).get("status") != "pass":
        recs.append({
            "severity": "high",
            "category": "security",
            "message": ".gitignore missing critical exclusion patterns",
            "action": "Add missing patterns to .gitignore to prevent accidental secret commits",
        })

    if security.get("secrets", {}).get("secrets_found", 0) > 0:
        recs.append({
            "severity": "critical",
            "category": "security",
            "message": f"Potential secrets found in {security['secrets']['secrets_found']} location(s)",
            "action": "Review and remove leaked credentials immediately",
        })

    if security.get("dependencies", {}).get("status") != "pass":
        recs.append({
            "severity": "high",
            "category": "dependencies",
            "message": "Missing CLI toolkit dependencies",
            "action": "Run: pip install -r nexus/cli/requirements.txt",
        })

    # Usage recommendations
    if usage.get("error_rate", 0) > 0.1:
        recs.append({
            "severity": "medium",
            "category": "reliability",
            "message": f"High CLI error rate: {usage['error_rate']:.1%}",
            "action": "Investigate recent errors with: python bs_cli.py debug logs .cache/bs-cli/",
        })

    if usage.get("total_invocations", 0) == 0:
        recs.append({
            "severity": "low",
            "category": "adoption",
            "message": "No CLI tool usage detected",
            "action": "Try: python bs_cli.py smoketest --format human",
        })

    # Sort by severity
    severity_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    recs.sort(key=lambda r: severity_order.get(r["severity"], 99))

    return recs


# --- Health Score Calculator ---

def _calculate_score(components: dict, security: dict, usage: dict) -> int:
    """Calculate weighted health score (0-100)."""
    score = 100
    weights = {"high": 10, "medium": 5, "low": 2, "critical": 20}

    # Component issues
    for section in ["rules", "skills", "workflows", "cross_ide", "templates"]:
        section_data = components.get(section, {})
        for issue in section_data.get("issues", []):
            penalty = weights.get(issue.get("severity", "low"), 2)
            score -= penalty

    # Security issues
    for section in ["gitignore", "codeiumignore", "dependencies"]:
        section_data = security.get(section, {})
        for issue in section_data.get("issues", []):
            penalty = weights.get(issue.get("severity", "low"), 2)
            score -= penalty

    # Secrets are critical — but penalise per-finding (not 20pt each which tanks score for env files)
    secrets_found = security.get("secrets", {}).get("secrets_found", 0)
    if secrets_found > 0:
        score -= min(40, secrets_found * 10)  # cap at -40 total

    # Usage error rate penalty
    error_rate = usage.get("error_rate", 0)
    if error_rate > 0.25:
        score -= 15
    elif error_rate > 0.1:
        score -= 8

    return max(0, min(100, score))


# --- Main Runners ---

def run_health_check(project_dir: Path) -> dict[str, Any]:
    """Full health check across all tiers."""
    start = time.time()

    components = run_components(project_dir)
    security = run_security(project_dir)
    usage = run_usage(project_dir)
    recommendations = _generate_recommendations(components, security, usage)
    score = _calculate_score(components, security, usage)

    duration_ms = int((time.time() - start) * 1000)

    # Count totals
    total_checks = 0
    passed = 0
    warnings = 0
    failed = 0
    for section in [components, security]:
        for key, val in section.items():
            if isinstance(val, dict) and "status" in val:
                total_checks += 1
                if val["status"] == "pass":
                    passed += 1
                elif val["status"] == "warn":
                    warnings += 1
                elif val["status"] == "fail":
                    failed += 1

    # Overall status
    if failed > 0:
        overall = "fail"
    elif warnings > 0:
        overall = "warn"
    else:
        overall = "pass"

    return {
        "status": overall,
        "score": score,
        "summary": {
            "total_checks": total_checks,
            "passed": passed,
            "warnings": warnings,
            "failed": failed,
            "score": score,
        },
        "components": components,
        "security": security,
        "usage": usage,
        "recommendations": recommendations,
        "duration_ms": duration_ms,
    }


def run_health_report(project_dir: Path) -> dict[str, Any]:
    """Full report with all details and recommendations."""
    result = run_health_check(project_dir)
    result["report_type"] = "full"
    return result


# --- CLI Entry Point ---

def run_health(
    subcommand: str,
    output_format: str = "json",
    project_dir: str = ".",
) -> None:
    """Route to the appropriate health subcommand."""
    fmt = OutputFormat(output_format)
    # Auto-detect project root by walking up to find .windsurf/ or .git/
    raw_path = Path(project_dir).resolve()
    proj_path = find_project_root(raw_path)

    if subcommand == "check":
        data = run_health_check(proj_path)
        status = Status(data["status"])
        msg = f"Health score: {data['score']}/100 — {data['summary']['passed']} passed, {data['summary']['warnings']} warnings, {data['summary']['failed']} failed"
        result = make_result("health.check", status, msg, duration_ms=data.get("duration_ms"))
        result["health"] = data

    elif subcommand == "components":
        data = run_components(proj_path)
        status = Status(data["status"])
        msg = f"Components: {data['total_issues']} issue(s) found"
        result = make_result("health.components", status, msg)
        result["components"] = data

    elif subcommand == "security":
        data = run_security(proj_path)
        status = Status(data["status"])
        result = make_result("health.security", status)
        result["security"] = data

    elif subcommand == "usage":
        data = run_usage(proj_path)
        status = Status(data.get("status", "info"))
        msg = f"{data.get('total_invocations', 0)} total invocations, {data.get('error_rate', 0):.1%} error rate"
        result = make_result("health.usage", status, msg)
        result["usage"] = data

    elif subcommand == "report":
        data = run_health_report(proj_path)
        status = Status(data["status"])
        msg = f"Nexus Health Report — Score: {data['score']}/100 — {len(data['recommendations'])} recommendation(s)"
        result = make_result("health.report", status, msg, duration_ms=data.get("duration_ms"))
        result["report"] = data

    else:
        result = make_result("health", Status.FAIL, f"Unknown subcommand: {subcommand}")

    emit(result, fmt)
