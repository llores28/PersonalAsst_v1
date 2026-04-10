"""
Security framework for the Bootstrap CLI Toolkit.
Input validation, path sanitization, URL validation, audit logging, secret detection.
"""

import ipaddress
import json
import os
import re
import time
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse


# --- Path Sanitization ---

def validate_path(path_str: str, project_root: Optional[Path] = None) -> Path:
    """
    Validate a file path is safe: no traversal outside project root,
    no absolute paths outside boundary, no symlink escape.
    Returns resolved Path if safe, raises ValueError otherwise.
    """
    if project_root is None:
        from bootstrap.cli.utils import find_project_root
        project_root = find_project_root()

    project_root = project_root.resolve()
    target = (project_root / path_str).resolve()

    if not str(target).startswith(str(project_root)):
        raise ValueError(
            f"Path traversal blocked: '{path_str}' resolves outside project root "
            f"'{project_root}'"
        )

    return target


def validate_path_exists(path_str: str, project_root: Optional[Path] = None) -> Path:
    """Validate path is safe AND exists."""
    target = validate_path(path_str, project_root)
    if not target.exists():
        raise ValueError(f"Path does not exist: '{target}'")
    return target


# --- URL Validation ---

_PRIVATE_NETWORKS = [
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fe80::/10"),
    ipaddress.ip_network("fc00::/7"),
]

_BLOCKED_SCHEMES = {"file", "ftp", "data", "javascript"}


def validate_url(url: str, allow_private: bool = False) -> str:
    """
    Validate a URL is safe for fetching:
    - Must be http or https
    - Must not resolve to private/internal IP ranges (SSRF protection)
    - Must not use blocked schemes
    Returns the URL if safe, raises ValueError otherwise.
    """
    parsed = urlparse(url)

    if not parsed.scheme:
        raise ValueError(f"URL missing scheme: '{url}'. Use http:// or https://")

    if parsed.scheme.lower() in _BLOCKED_SCHEMES:
        raise ValueError(f"Blocked URL scheme: '{parsed.scheme}' in '{url}'")

    if parsed.scheme.lower() not in ("http", "https"):
        raise ValueError(f"Only http/https URLs allowed, got: '{parsed.scheme}'")

    if not parsed.hostname:
        raise ValueError(f"URL missing hostname: '{url}'")

    if not allow_private:
        hostname = parsed.hostname
        try:
            import socket
            resolved = socket.getaddrinfo(hostname, None)
            for _, _, _, _, sockaddr in resolved:
                ip = ipaddress.ip_address(sockaddr[0])
                for network in _PRIVATE_NETWORKS:
                    if ip in network:
                        raise ValueError(
                            f"URL resolves to private/internal IP ({ip}): '{url}'. "
                            f"This is blocked for SSRF protection."
                        )
        except socket.gaierror:
            pass  # DNS resolution failed — will fail at fetch time

    return url


# --- Package Name Validation ---

_PACKAGE_NAME_RE = re.compile(r"^[@a-zA-Z0-9][\w.\-/]{0,213}$")


def validate_package_name(name: str) -> str:
    """Validate a package name is safe (no shell metacharacters)."""
    if not _PACKAGE_NAME_RE.match(name):
        raise ValueError(
            f"Invalid package name: '{name}'. "
            f"Must be alphanumeric with hyphens/dots/slashes, max 214 chars."
        )
    return name


# --- Command Safety ---

def safe_command_args(cmd: list[str]) -> list[str]:
    """
    Validate command arguments for subprocess calls.
    Ensures no shell metacharacters that could enable injection.
    Returns the args list if safe, raises ValueError otherwise.
    """
    shell_metachars = set(";|&$`\\!#(){}[]<>")
    for arg in cmd:
        dangerous = shell_metachars.intersection(arg)
        if dangerous:
            raise ValueError(
                f"Shell metacharacters detected in command arg: '{arg}' "
                f"(chars: {dangerous}). Use explicit args, not shell strings."
            )
    return cmd


# --- Secret Detection ---

_SECRET_PATTERNS = [
    re.compile(r"(?i)(api[_-]?key|apikey)\s*[:=]\s*\S+"),
    re.compile(r"(?i)(secret|password|passwd|pwd)\s*[:=]\s*\S+"),
    re.compile(r"(?i)(access[_-]?token|auth[_-]?token|bearer)\s*[:=]\s*\S+"),
    re.compile(r"AKIA[0-9A-Z]{16}"),                          # AWS access key
    re.compile(r"sk_live_[0-9a-zA-Z]{24,}"),                  # Stripe secret key
    re.compile(r"sk-[0-9a-zA-Z]{20,}"),                       # OpenAI key pattern
    re.compile(r"ghp_[0-9a-zA-Z]{36}"),                       # GitHub personal token
    re.compile(r"gho_[0-9a-zA-Z]{36}"),                       # GitHub OAuth token
    re.compile(r"glpat-[0-9a-zA-Z\-_]{20,}"),                 # GitLab token
    re.compile(r"xox[bpors]-[0-9a-zA-Z\-]{10,}"),             # Slack token
    re.compile(r"-----BEGIN (?:RSA |EC |DSA )?PRIVATE KEY-----"),
    re.compile(r"(?i)mongodb(\+srv)?://[^\s]+:[^\s]+@"),       # MongoDB connection string
    re.compile(r"(?i)postgres(ql)?://[^\s]+:[^\s]+@"),         # Postgres connection string
]


def scan_text_for_secrets(text: str) -> list[dict]:
    """
    Scan text for common secret patterns.
    Returns list of findings with line number and pattern name — never the actual secret.
    """
    findings = []
    for line_num, line in enumerate(text.splitlines(), 1):
        for pattern in _SECRET_PATTERNS:
            if pattern.search(line):
                findings.append({
                    "line": line_num,
                    "pattern": pattern.pattern[:60],
                    "preview": _redact_line(line),
                })
    return findings


def _redact_line(line: str, max_len: int = 80) -> str:
    """Show line structure but redact potential secret values."""
    line = line.strip()
    if len(line) > max_len:
        line = line[:max_len] + "..."
    # Redact anything after = or : that looks like a value
    redacted = re.sub(r"([:=]\s*).+", r"\1[REDACTED]", line)
    return redacted


def sanitize_output(text: str) -> str:
    """Strip secret-like patterns from output before logging."""
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub("[REDACTED]", text)
    return text


# --- Audit Logging ---

_AUDIT_DIR = Path(".cache") / "bs-cli"
_AUDIT_FILE = _AUDIT_DIR / "audit.jsonl"


def audit_log(
    tool: str,
    args: dict,
    exit_code: int = 0,
    duration_ms: int = 0,
    project_root: Optional[Path] = None,
) -> None:
    """
    Append an audit entry to .cache/bs-cli/audit.jsonl.
    Sanitizes args to remove potential secret values.
    """
    root = project_root or Path.cwd()
    audit_path = root / _AUDIT_FILE

    try:
        audit_path.parent.mkdir(parents=True, exist_ok=True)

        entry = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "tool": tool,
            "args": {k: sanitize_output(str(v)) for k, v in args.items()},
            "exit_code": exit_code,
            "duration_ms": duration_ms,
        }

        with open(audit_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
    except OSError:
        pass  # Don't fail the tool if audit logging fails
