"""
Supply Chain Security Scanner — detect compromised npm packages and system IOCs.

Subcommands:
  scan          — scan a project or directory tree for known compromised packages
  ioc           — check the local system for indicators of compromise
  audit         — run full audit: scan + IOC check + recommendations
  advisories    — list known supply chain advisories tracked by Nexus
"""

import json
import os
import platform
import re
import subprocess
from pathlib import Path
from typing import Any, Optional

from bootstrap.cli.utils import (
    OutputFormat, Status, Severity, emit, make_result, find_project_root,
)


# --- Known Compromised Packages Registry ---

KNOWN_COMPROMISED = [
    {
        "id": "AXIOS-2026-03-31",
        "package": "axios",
        "ecosystem": "npm",
        "compromised_versions": ["1.14.1", "0.30.4"],
        "safe_versions": ["1.14.0", "0.30.3", "1.8.4"],
        "malicious_deps": ["plain-crypto-js"],
        "iocs": {
            "windows": [r"%PROGRAMDATA%\wt.exe"],
            "macos": ["/Library/Caches/com.apple.act.mond"],
            "linux": ["/tmp/ld.py"],
        },
        "c2_indicators": ["sfrclak.com", "142.11.206.73"],
        "severity": "critical",
        "date": "2026-03-31",
        "description": "Axios npm maintainer account compromised. Malicious versions inject plain-crypto-js which deploys a cross-platform RAT via postinstall hook.",
        "references": [
            "https://www.helpnetsecurity.com/2026/03/31/axios-npm-backdoored-supply-chain-attack/",
            "https://snyk.io/blog/axios-npm-package-compromised-supply-chain-attack-delivers-cross-platform/",
            "https://security.snyk.io/vuln/SNYK-JS-AXIOS-15850650",
        ],
        "remediation": [
            "Downgrade to a safe version (1.14.0 or 0.30.3) and pin it",
            "Add overrides block in package.json to prevent transitive resolution",
            "Remove plain-crypto-js from node_modules",
            "Use --ignore-scripts in CI/CD to block postinstall hooks",
            "Rotate all credentials if compromised version was installed",
            "Block C2 traffic to 142.11.206.73 and sfrclak.com",
        ],
    },
]


# --- Lockfile Scanners ---

def _scan_lockfile(lockfile_path: Path, registry: list[dict]) -> list[dict]:
    """Scan a single lockfile for known compromised package versions."""
    findings = []
    try:
        content = lockfile_path.read_text(encoding="utf-8", errors="replace")
    except (OSError, PermissionError):
        return findings

    for advisory in registry:
        pkg = advisory["package"]
        for ver in advisory["compromised_versions"]:
            # npm package-lock.json patterns
            patterns = [
                rf'"{pkg}":\s*"[^"]*{re.escape(ver)}',
                rf'"version":\s*"{re.escape(ver)}"',
                rf'{pkg}@{re.escape(ver)}',
                rf'"resolved":\s*"[^"]*{re.escape(pkg)}/-/{re.escape(pkg)}-{re.escape(ver)}\.tgz"',
            ]
            for pattern in patterns:
                if re.search(pattern, content):
                    findings.append({
                        "advisory_id": advisory["id"],
                        "package": pkg,
                        "version": ver,
                        "lockfile": str(lockfile_path),
                        "severity": advisory["severity"],
                        "description": advisory["description"],
                    })
                    break

        # Check for malicious transitive dependencies
        for mal_dep in advisory.get("malicious_deps", []):
            if mal_dep in content:
                findings.append({
                    "advisory_id": advisory["id"],
                    "package": mal_dep,
                    "version": "any",
                    "lockfile": str(lockfile_path),
                    "severity": "critical",
                    "description": f"Malicious dependency '{mal_dep}' found — this is a direct indicator of compromise.",
                })

    return findings


def _find_lockfiles(scan_dir: Path) -> list[Path]:
    """Find all npm/yarn/bun lockfiles under scan_dir."""
    lockfile_names = ["package-lock.json", "yarn.lock", "bun.lock", "bun.lockb"]
    found = []
    for root, dirs, files in os.walk(scan_dir):
        # Skip deep node_modules to avoid noise
        dirs[:] = [d for d in dirs if d != ".git"]
        for f in files:
            if f in lockfile_names:
                found.append(Path(root) / f)
    return found


def _scan_node_modules(scan_dir: Path, registry: list[dict]) -> list[dict]:
    """Check node_modules directories for malicious packages."""
    findings = []
    for advisory in registry:
        for mal_dep in advisory.get("malicious_deps", []):
            for root, dirs, files in os.walk(scan_dir):
                dirs[:] = [d for d in dirs if d != ".git"]
                if root.endswith(f"node_modules/{mal_dep}") or root.endswith(f"node_modules\\{mal_dep}"):
                    findings.append({
                        "advisory_id": advisory["id"],
                        "package": mal_dep,
                        "location": root,
                        "severity": "critical",
                        "description": f"Malicious package '{mal_dep}' found in node_modules — system may be compromised.",
                    })
    return findings


# --- IOC Scanner ---

def _check_iocs(registry: list[dict]) -> list[dict]:
    """Check local system for indicators of compromise."""
    findings = []
    system = platform.system().lower()

    platform_key = {
        "windows": "windows",
        "darwin": "macos",
        "linux": "linux",
    }.get(system)

    if not platform_key:
        return findings

    for advisory in registry:
        iocs = advisory.get("iocs", {}).get(platform_key, [])
        for ioc_path in iocs:
            expanded = os.path.expandvars(ioc_path)
            if os.path.exists(expanded):
                findings.append({
                    "advisory_id": advisory["id"],
                    "ioc_type": "file",
                    "path": expanded,
                    "severity": "critical",
                    "description": f"RAT artifact found at '{expanded}' — SYSTEM IS COMPROMISED. Isolate immediately.",
                    "action": "Isolate system from network. Do not attempt to clean — rebuild from known-good state.",
                })

    return findings


def _check_c2_connectivity(registry: list[dict]) -> list[dict]:
    """Check hosts file or DNS for known C2 domains (passive, no network calls)."""
    findings = []
    hosts_paths = {
        "windows": r"C:\Windows\System32\drivers\etc\hosts",
        "darwin": "/etc/hosts",
        "linux": "/etc/hosts",
    }
    system = platform.system().lower()
    hosts_path = hosts_paths.get(system)

    if not hosts_path or not os.path.exists(hosts_path):
        return findings

    try:
        hosts_content = Path(hosts_path).read_text(encoding="utf-8", errors="replace")
    except (OSError, PermissionError):
        return findings

    for advisory in registry:
        for c2 in advisory.get("c2_indicators", []):
            if c2 in hosts_content:
                findings.append({
                    "advisory_id": advisory["id"],
                    "indicator": c2,
                    "severity": "info",
                    "description": f"C2 indicator '{c2}' found in hosts file — may indicate blocking rule (good) or compromise.",
                })

    return findings


# --- Package.json Hardening Check ---

def _check_hardening(scan_dir: Path) -> list[dict]:
    """Check package.json files for supply chain hardening measures."""
    recommendations = []
    for root, dirs, files in os.walk(scan_dir):
        dirs[:] = [d for d in dirs if d not in (".git", "node_modules")]
        if "package.json" in files:
            pkg_path = Path(root) / "package.json"
            try:
                pkg = json.loads(pkg_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue

            # Check for pinned versions (no ^ or ~)
            deps = {**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})}
            unpinned = [
                f"{name}@{ver}" for name, ver in deps.items()
                if ver.startswith("^") or ver.startswith("~")
            ]
            if unpinned:
                recommendations.append({
                    "file": str(pkg_path),
                    "severity": "medium",
                    "issue": "unpinned_dependencies",
                    "count": len(unpinned),
                    "description": f"{len(unpinned)} dependencies use ^ or ~ ranges — consider pinning exact versions.",
                    "examples": unpinned[:5],
                })

            # Check for overrides/resolutions block
            has_overrides = "overrides" in pkg or "resolutions" in pkg
            if deps and not has_overrides:
                recommendations.append({
                    "file": str(pkg_path),
                    "severity": "low",
                    "issue": "no_overrides_block",
                    "description": "No 'overrides' or 'resolutions' block — add one to block known bad transitive versions.",
                })

            # Check for ignore-scripts in .npmrc
            npmrc_path = Path(root) / ".npmrc"
            if npmrc_path.exists():
                npmrc_content = npmrc_path.read_text(encoding="utf-8", errors="replace")
                if "ignore-scripts=true" not in npmrc_content:
                    recommendations.append({
                        "file": str(npmrc_path),
                        "severity": "medium",
                        "issue": "postinstall_hooks_enabled",
                        "description": "Consider adding 'ignore-scripts=true' to .npmrc for CI/CD environments.",
                    })
            elif deps:
                recommendations.append({
                    "file": str(pkg_path),
                    "severity": "medium",
                    "issue": "no_npmrc",
                    "description": "No .npmrc found — consider creating one with 'ignore-scripts=true' for CI/CD.",
                })

    return recommendations


# --- Main Entry Points ---

def _run_scan(scan_dir: str, output_format: str) -> None:
    """Scan a project directory for compromised packages."""
    scan_path = Path(scan_dir).resolve()
    if not scan_path.exists():
        emit(make_result(
            "supply-chain-scan",
            Status.FAIL,
            message=f"Directory not found: {scan_dir}",
        ), OutputFormat(output_format))
        return

    lockfiles = _find_lockfiles(scan_path)
    lockfile_findings = []
    for lf in lockfiles:
        lockfile_findings.extend(_scan_lockfile(lf, KNOWN_COMPROMISED))

    node_module_findings = _scan_node_modules(scan_path, KNOWN_COMPROMISED)

    all_findings = lockfile_findings + node_module_findings
    critical_count = sum(1 for f in all_findings if f.get("severity") == "critical")

    status = Status.FAIL if critical_count > 0 else Status.PASS
    message = (
        f"CRITICAL: {critical_count} compromised package(s) found!"
        if critical_count > 0
        else f"Clean — scanned {len(lockfiles)} lockfile(s), no compromised packages found."
    )

    emit(make_result(
        "supply-chain-scan",
        status,
        message=message,
        details={
            "scan_directory": str(scan_path),
            "lockfiles_scanned": len(lockfiles),
            "findings": all_findings,
            "finding_count": len(all_findings),
            "critical_count": critical_count,
        },
    ), OutputFormat(output_format))


def _run_ioc(output_format: str) -> None:
    """Check local system for indicators of compromise."""
    file_findings = _check_iocs(KNOWN_COMPROMISED)
    c2_findings = _check_c2_connectivity(KNOWN_COMPROMISED)
    all_findings = file_findings + c2_findings

    critical_count = sum(1 for f in all_findings if f.get("severity") == "critical")
    status = Status.FAIL if critical_count > 0 else Status.PASS
    message = (
        f"CRITICAL: {critical_count} IOC(s) found — system may be compromised!"
        if critical_count > 0
        else "Clean — no indicators of compromise found on this system."
    )

    emit(make_result(
        "supply-chain-ioc",
        status,
        message=message,
        details={
            "platform": platform.system(),
            "findings": all_findings,
            "finding_count": len(all_findings),
            "critical_count": critical_count,
        },
    ), OutputFormat(output_format))


def _run_audit(scan_dir: str, output_format: str) -> None:
    """Full audit: scan + IOC + hardening recommendations."""
    scan_path = Path(scan_dir).resolve()

    # Phase 1: Lockfile scan
    lockfiles = _find_lockfiles(scan_path) if scan_path.exists() else []
    lockfile_findings = []
    for lf in lockfiles:
        lockfile_findings.extend(_scan_lockfile(lf, KNOWN_COMPROMISED))

    # Phase 2: node_modules scan
    node_module_findings = _scan_node_modules(scan_path, KNOWN_COMPROMISED) if scan_path.exists() else []

    # Phase 3: IOC check
    ioc_findings = _check_iocs(KNOWN_COMPROMISED)
    c2_findings = _check_c2_connectivity(KNOWN_COMPROMISED)

    # Phase 4: Hardening check
    hardening = _check_hardening(scan_path) if scan_path.exists() else []

    all_findings = lockfile_findings + node_module_findings + ioc_findings + c2_findings
    critical_count = sum(1 for f in all_findings if f.get("severity") == "critical")

    status = Status.FAIL if critical_count > 0 else (Status.WARN if hardening else Status.PASS)
    if critical_count > 0:
        message = f"CRITICAL: {critical_count} compromise indicator(s) found!"
    elif hardening:
        message = f"Clean, but {len(hardening)} hardening recommendation(s) found."
    else:
        message = "Clean — no compromised packages, no IOCs, hardening looks good."

    emit(make_result(
        "supply-chain-audit",
        status,
        message=message,
        details={
            "scan_directory": str(scan_path),
            "lockfiles_scanned": len(lockfiles),
            "compromised_packages": lockfile_findings + node_module_findings,
            "ioc_findings": ioc_findings + c2_findings,
            "hardening_recommendations": hardening,
            "summary": {
                "critical": critical_count,
                "total_findings": len(all_findings),
                "hardening_items": len(hardening),
            },
        },
    ), OutputFormat(output_format))


def _run_advisories(output_format: str) -> None:
    """List known supply chain advisories tracked by Nexus."""
    advisories = []
    for adv in KNOWN_COMPROMISED:
        advisories.append({
            "id": adv["id"],
            "package": adv["package"],
            "ecosystem": adv["ecosystem"],
            "severity": adv["severity"],
            "date": adv["date"],
            "compromised_versions": adv["compromised_versions"],
            "description": adv["description"],
            "references": adv["references"],
            "remediation": adv["remediation"],
        })

    emit(make_result(
        "supply-chain-advisories",
        Status.INFO,
        message=f"{len(advisories)} advisory/advisories tracked.",
        details={"advisories": advisories},
    ), OutputFormat(output_format))


# --- CLI dispatcher ---

def run_supply_chain(subcommand: str, args: tuple, output_format: str, project_dir: str) -> None:
    """Dispatch supply chain subcommands."""
    if subcommand == "scan":
        scan_dir = args[0] if args else project_dir
        _run_scan(scan_dir=scan_dir, output_format=output_format)
    elif subcommand == "ioc":
        _run_ioc(output_format=output_format)
    elif subcommand == "audit":
        scan_dir = args[0] if args else project_dir
        _run_audit(scan_dir=scan_dir, output_format=output_format)
    elif subcommand == "advisories":
        _run_advisories(output_format=output_format)
    else:
        emit(make_result(
            "supply-chain",
            Status.FAIL,
            message=f"Unknown subcommand: {subcommand}",
        ), OutputFormat(output_format))
