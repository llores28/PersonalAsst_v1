"""
Debug Investigation Tool — systematic debugging utilities.

Subcommands:
  logs          — tail/search logs with structured highlights
  trace         — search codebase for likely error origin
  deps          — check for version conflicts, missing deps
  env           — validate env vars vs .env.example
  ports         — check if expected ports are in use/free
  secrets-scan  — scan files for leaked secret patterns
"""

import json
import os
import re
import socket
import subprocess
from pathlib import Path
from typing import Any, Optional

from nexus.cli.utils import (
    OutputFormat, Status, Severity, emit, make_result, truncate_output, find_project_root,
)
from nexus.cli.security import (
    validate_path, scan_text_for_secrets, sanitize_output,
)


# --- logs subcommand ---

_ERROR_PATTERNS = [
    (re.compile(r"(?i)\b(error|exception|fatal|panic|crash)\b"), Severity.HIGH),
    (re.compile(r"(?i)\b(warn|warning|deprecated)\b"), Severity.MEDIUM),
    (re.compile(r"(?i)(traceback|stack trace|at \S+:\d+)"), Severity.HIGH),
    (re.compile(r"(?i)(ECONNREFUSED|EADDRINUSE|ENOENT|EPERM)"), Severity.HIGH),
    (re.compile(r"(?i)(segfault|segmentation fault|core dump)"), Severity.CRITICAL),
    (re.compile(r"(?i)(out of memory|oom|heap)"), Severity.CRITICAL),
]


def _scan_logs(path: Path, pattern: Optional[str] = None, max_lines: int = 500) -> dict[str, Any]:
    """Scan a log file or directory for errors/warnings."""
    findings: list[dict] = []
    files_scanned = 0
    lines_scanned = 0

    targets = []
    if path.is_file():
        targets = [path]
    elif path.is_dir():
        targets = sorted(path.glob("**/*.log"))[:20]  # Cap at 20 log files
        targets += sorted(path.glob("**/*.txt"))[:10]

    search_re = re.compile(pattern, re.IGNORECASE) if pattern else None

    for fpath in targets:
        files_scanned += 1
        try:
            lines = fpath.read_text(encoding="utf-8", errors="replace").splitlines()
            # Take last max_lines if file is large
            if len(lines) > max_lines:
                lines = lines[-max_lines:]
                start_line = len(lines) - max_lines
            else:
                start_line = 0

            for i, line in enumerate(lines):
                line_num = start_line + i + 1
                lines_scanned += 1

                if search_re and not search_re.search(line):
                    continue

                for err_pattern, severity in _ERROR_PATTERNS:
                    if err_pattern.search(line):
                        findings.append({
                            "file": str(fpath),
                            "line": line_num,
                            "severity": severity.value,
                            "text": truncate_output(line.strip(), 200),
                        })
                        break  # One finding per line
        except OSError:
            continue

    return {
        "files_scanned": files_scanned,
        "lines_scanned": lines_scanned,
        "findings": findings[:100],  # Cap findings
        "truncated": len(findings) > 100,
    }


# --- trace subcommand ---

def _trace_error(error_msg: str, project_dir: Path) -> dict[str, Any]:
    """Search codebase for the origin of an error message."""
    results: list[dict] = []

    # Extract key parts of the error
    # Remove common prefixes and stack trace noise
    clean_msg = re.sub(r"^\s*(Error|TypeError|ValueError|RuntimeError|Exception):\s*", "", error_msg)
    clean_msg = clean_msg.strip()

    # Search for the error string in source files
    source_exts = {".py", ".js", ".ts", ".jsx", ".tsx", ".go", ".rs", ".java", ".kt", ".rb", ".php"}
    ignore_dirs = {
        "node_modules", ".git", "__pycache__", ".venv", "venv",
        "dist", "build", ".next", ".nuxt", "target",
    }

    for fpath in project_dir.rglob("*"):
        if fpath.is_dir():
            continue
        if any(d in fpath.parts for d in ignore_dirs):
            continue
        if fpath.suffix not in source_exts:
            continue

        try:
            content = fpath.read_text(encoding="utf-8", errors="replace")
            for i, line in enumerate(content.splitlines(), 1):
                # Look for the error message in string literals or raise statements
                if clean_msg[:40] in line:
                    results.append({
                        "file": str(fpath.relative_to(project_dir)),
                        "line": i,
                        "context": truncate_output(line.strip(), 150),
                        "match_type": "exact",
                    })
                elif len(clean_msg) > 10 and any(
                    word in line.lower()
                    for word in clean_msg.lower().split()[:3]
                    if len(word) > 4
                ):
                    results.append({
                        "file": str(fpath.relative_to(project_dir)),
                        "line": i,
                        "context": truncate_output(line.strip(), 150),
                        "match_type": "partial",
                    })
        except OSError:
            continue

        if len(results) >= 50:
            break

    # Sort: exact matches first, then partial
    results.sort(key=lambda r: (0 if r["match_type"] == "exact" else 1))

    return {
        "query": truncate_output(error_msg, 200),
        "results": results[:20],
        "total_matches": len(results),
    }


# --- deps subcommand ---

def _check_deps(project_dir: Path) -> dict[str, Any]:
    """Check for dependency issues."""
    issues: list[dict] = []

    # Node.js
    pkg_json = project_dir / "package.json"
    if pkg_json.exists():
        node_modules = project_dir / "node_modules"
        if not node_modules.exists():
            issues.append({
                "type": "missing",
                "severity": "high",
                "message": "node_modules/ not found — run npm install",
            })

        pkg_lock = project_dir / "package-lock.json"
        yarn_lock = project_dir / "yarn.lock"
        pnpm_lock = project_dir / "pnpm-lock.yaml"
        if not any(f.exists() for f in [pkg_lock, yarn_lock, pnpm_lock]):
            issues.append({
                "type": "no-lockfile",
                "severity": "medium",
                "message": "No lockfile found — dependency versions are not pinned",
            })

    # Python
    pyproject = project_dir / "pyproject.toml"
    requirements = project_dir / "requirements.txt"
    if pyproject.exists() or requirements.exists():
        venv_dirs = [project_dir / ".venv", project_dir / "venv"]
        if not any(d.exists() for d in venv_dirs):
            issues.append({
                "type": "no-venv",
                "severity": "medium",
                "message": "No virtual environment found (.venv/ or venv/)",
            })

    # Go
    go_mod = project_dir / "go.mod"
    if go_mod.exists():
        go_sum = project_dir / "go.sum"
        if not go_sum.exists():
            issues.append({
                "type": "no-lockfile",
                "severity": "medium",
                "message": "go.sum not found — run go mod tidy",
            })

    # Check for duplicate/conflicting deps in package.json
    if pkg_json.exists():
        try:
            with open(pkg_json, "r", encoding="utf-8") as f:
                pkg = json.load(f)
            deps = set(pkg.get("dependencies", {}).keys())
            dev_deps = set(pkg.get("devDependencies", {}).keys())
            overlap = deps & dev_deps
            if overlap:
                issues.append({
                    "type": "duplicate",
                    "severity": "low",
                    "message": f"Packages in both deps and devDeps: {', '.join(sorted(overlap))}",
                })
        except (json.JSONDecodeError, OSError):
            pass

    return {
        "issues": issues,
        "issue_count": len(issues),
    }


# --- env subcommand ---

def _check_env(project_dir: Path) -> dict[str, Any]:
    """Validate env vars exist (names only) vs .env.example."""
    example_files = [
        project_dir / ".env.example",
        project_dir / ".env.sample",
        project_dir / ".env.template",
    ]

    example_path = None
    for f in example_files:
        if f.exists():
            example_path = f
            break

    if not example_path:
        return {
            "status": "skip",
            "message": "No .env.example/.env.sample/.env.template found",
        }

    # Parse expected var names from example
    expected_vars: set[str] = set()
    try:
        for line in example_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                key = line.split("=")[0].strip()
                if key:
                    expected_vars.add(key)
    except OSError:
        return {"status": "fail", "message": f"Cannot read {example_path}"}

    # Check .env
    env_file = project_dir / ".env"
    actual_vars: set[str] = set()
    if env_file.exists():
        try:
            for line in env_file.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line and not line.startswith("#"):
                    key = line.split("=")[0].strip()
                    if key:
                        actual_vars.add(key)
        except OSError:
            pass

    # Also check environment
    env_vars = set(os.environ.keys())

    missing_from_file = expected_vars - actual_vars
    missing_from_both = missing_from_file - env_vars

    return {
        "expected_count": len(expected_vars),
        "env_file_exists": env_file.exists(),
        "defined_in_file": len(actual_vars),
        "missing_from_file": sorted(missing_from_file),  # Names only, never values
        "missing_entirely": sorted(missing_from_both),
        "status": "fail" if missing_from_both else ("warn" if missing_from_file else "pass"),
    }


# --- ports subcommand ---

_COMMON_PORTS = {
    3000: "Node.js / React dev server",
    3001: "Next.js / alternate dev server",
    4000: "GraphQL / Phoenix",
    5000: "Flask / Python",
    5173: "Vite dev server",
    5432: "PostgreSQL",
    6379: "Redis",
    8000: "Django / FastAPI / uvicorn",
    8080: "HTTP alt / Tomcat / Go",
    8443: "HTTPS alt",
    8888: "Jupyter",
    9090: "Prometheus",
    27017: "MongoDB",
}


def _check_ports(project_dir: Path) -> dict[str, Any]:
    """Check if common development ports are in use."""
    results: list[dict] = []

    for port, description in sorted(_COMMON_PORTS.items()):
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                results.append({
                    "port": port,
                    "description": description,
                    "status": "in_use",
                })
        except (ConnectionRefusedError, OSError, socket.timeout):
            results.append({
                "port": port,
                "description": description,
                "status": "free",
            })

    in_use = [r for r in results if r["status"] == "in_use"]

    return {
        "ports_checked": len(results),
        "in_use": len(in_use),
        "results": results,
    }


# --- secrets-scan subcommand ---

def _secrets_scan(project_dir: Path, args: tuple) -> dict[str, Any]:
    """Scan for leaked secrets in git diff, staged files, or specified paths."""
    all_findings: list[dict] = []

    # Default: scan staged + recent changes
    scan_targets: list[Path] = []

    if args:
        # Scan specific paths
        for a in args:
            p = validate_path(a, project_dir)
            if p.exists():
                scan_targets.append(p)
    else:
        # Try git diff (staged)
        try:
            result = subprocess.run(
                ["git", "diff", "--cached", "--name-only"],
                capture_output=True, text=True, cwd=str(project_dir), timeout=10,
            )
            if result.returncode == 0:
                for fname in result.stdout.strip().splitlines():
                    fpath = project_dir / fname
                    if fpath.exists() and fpath.is_file():
                        scan_targets.append(fpath)
        except (subprocess.SubprocessError, OSError):
            pass

        # Also scan common config files
        config_patterns = [
            ".env", ".env.*", "*.config.js", "*.config.ts",
            "docker-compose*.yml", "docker-compose*.yaml",
        ]
        for pattern in config_patterns:
            for fpath in project_dir.glob(pattern):
                if fpath.is_file() and fpath not in scan_targets:
                    scan_targets.append(fpath)

    # Scan each target
    for fpath in scan_targets[:50]:  # Cap at 50 files
        try:
            content = fpath.read_text(encoding="utf-8", errors="replace")
            findings = scan_text_for_secrets(content)
            for f in findings:
                f["file"] = str(fpath.relative_to(project_dir))
                all_findings.append(f)
        except OSError:
            continue

    return {
        "files_scanned": len(scan_targets),
        "secrets_found": len(all_findings),
        "findings": all_findings[:50],  # Never more than 50
        "status": "fail" if all_findings else "pass",
    }


# --- Main runner ---

def run_debug(
    subcommand: str,
    args: tuple = (),
    output_format: str = "json",
    project_dir: str = ".",
) -> None:
    """Route to the appropriate debug subcommand."""
    fmt = OutputFormat(output_format)
    proj_path = Path(project_dir).resolve()

    if subcommand == "logs":
        target = proj_path
        pattern = None
        if args:
            target = validate_path(args[0], proj_path)
            if len(args) > 1:
                pattern = args[1]
        data = _scan_logs(target, pattern)
        status = Status.WARN if data["findings"] else Status.PASS
        result = make_result("debug.logs", status, f"{len(data['findings'])} finding(s)")
        result.update(data)

    elif subcommand == "trace":
        if not args:
            result = make_result("debug.trace", Status.FAIL, "Usage: debug trace <error-message>")
            emit(result, fmt)
            return
        error_msg = " ".join(args)
        data = _trace_error(error_msg, proj_path)
        status = Status.INFO if data["results"] else Status.WARN
        result = make_result("debug.trace", status, f"{len(data['results'])} potential source(s)")
        result.update(data)

    elif subcommand == "deps":
        data = _check_deps(proj_path)
        status = Status.FAIL if data["issues"] else Status.PASS
        result = make_result("debug.deps", status, f"{data['issue_count']} issue(s)")
        result.update(data)

    elif subcommand == "env":
        data = _check_env(proj_path)
        status = Status(data.get("status", "info"))
        result = make_result("debug.env", status)
        result.update(data)

    elif subcommand == "ports":
        data = _check_ports(proj_path)
        status = Status.INFO
        result = make_result("debug.ports", status, f"{data['in_use']} port(s) in use")
        result.update(data)

    elif subcommand == "secrets-scan":
        data = _secrets_scan(proj_path, args)
        status = Status.FAIL if data["secrets_found"] > 0 else Status.PASS
        msg = f"{data['secrets_found']} potential secret(s) found" if data["secrets_found"] else "No secrets detected"
        result = make_result("debug.secrets-scan", status, msg)
        result.update(data)

    else:
        result = make_result("debug", Status.FAIL, f"Unknown subcommand: {subcommand}")

    emit(result, fmt)
