"""
Smoketest Tool — tiered project health checks.

Auto-detects project type and runs:
  quick: (1) deps verify, (2) lint/typecheck, (3) unit tests
  full:  + (4) build, (5) server start + health check + stop
"""

import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any, Optional

from nexus.cli.utils import (
    OutputFormat, Status, emit, make_result, truncate_output, find_project_root,
)
from nexus.cli.security import validate_path, audit_log


# --- Project detection ---

def _detect_project(project_dir: Path) -> dict[str, Any]:
    """Detect project type, package manager, and available commands."""
    info: dict[str, Any] = {
        "type": "unknown",
        "package_manager": None,
        "commands": {},
        "has_dockerfile": False,
    }

    # Node.js
    pkg_json = project_dir / "package.json"
    if pkg_json.exists():
        info["type"] = "node"
        try:
            with open(pkg_json, "r", encoding="utf-8") as f:
                pkg = json.load(f)
            scripts = pkg.get("scripts", {})
            info["commands"] = {
                "install": _detect_node_pm(project_dir) + " install",
                "lint": _find_script(scripts, ["lint", "eslint"]),
                "typecheck": _find_script(scripts, ["typecheck", "type-check", "tsc"]),
                "test": _find_script(scripts, ["test", "jest", "vitest"]),
                "build": _find_script(scripts, ["build"]),
                "start": _find_script(scripts, ["start", "dev", "serve"]),
            }
            info["package_manager"] = _detect_node_pm(project_dir)
        except (json.JSONDecodeError, OSError):
            pass

    # Python
    pyproject = project_dir / "pyproject.toml"
    requirements = project_dir / "requirements.txt"
    if pyproject.exists() or requirements.exists():
        if info["type"] == "unknown":
            info["type"] = "python"
        elif info["type"] == "node":
            info["type"] = "fullstack"

        py_cmds = {}
        if pyproject.exists():
            py_cmds["install"] = "pip install -e ."
            # Check for common tools in pyproject
            try:
                content = pyproject.read_text(encoding="utf-8")
                if "pytest" in content:
                    py_cmds["test"] = "pytest"
                if "ruff" in content:
                    py_cmds["lint"] = "ruff check ."
                elif "flake8" in content:
                    py_cmds["lint"] = "flake8 ."
                if "mypy" in content:
                    py_cmds["typecheck"] = "mypy ."
            except OSError:
                pass
        elif requirements.exists():
            py_cmds["install"] = "pip install -r requirements.txt"

        # Merge with any existing commands
        for k, v in py_cmds.items():
            if not info["commands"].get(k):
                info["commands"][k] = v

    # Go
    go_mod = project_dir / "go.mod"
    if go_mod.exists():
        info["type"] = "go"
        info["commands"] = {
            "install": "go mod download",
            "build": "go build ./...",
            "test": "go test ./...",
            "lint": "golangci-lint run" if _cmd_exists("golangci-lint") else None,
        }

    # Docker
    info["has_dockerfile"] = (project_dir / "Dockerfile").exists()
    info["has_compose"] = (
        (project_dir / "docker-compose.yml").exists()
        or (project_dir / "docker-compose.yaml").exists()
        or (project_dir / "compose.yml").exists()
        or (project_dir / "compose.yaml").exists()
    )

    return info


def _detect_node_pm(project_dir: Path) -> str:
    """Detect Node package manager."""
    if (project_dir / "pnpm-lock.yaml").exists():
        return "pnpm"
    if (project_dir / "yarn.lock").exists():
        return "yarn"
    if (project_dir / "bun.lockb").exists():
        return "bun"
    return "npm"


def _find_script(scripts: dict, names: list[str]) -> Optional[str]:
    """Find first matching npm script."""
    for name in names:
        if name in scripts:
            pm = "npm"  # Will be replaced at run time
            return f"{pm} run {name}"
    return None


def _cmd_exists(cmd: str) -> bool:
    """Check if a command exists on PATH."""
    import shutil
    return shutil.which(cmd) is not None


# --- Step runners ---

def _run_step(
    name: str,
    cmd_str: Optional[str],
    cwd: Path,
    timeout: int = 120,
) -> dict[str, Any]:
    """Run a single smoketest step. Returns structured result."""
    if not cmd_str:
        return {
            "step": name,
            "status": "skip",
            "message": "No command configured for this step",
            "duration_ms": 0,
        }

    start = time.time()
    try:
        # Split command string into args safely
        import shlex
        args = shlex.split(cmd_str)

        result = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(cwd),
            env={**os.environ, "CI": "true", "NODE_ENV": "test"},
        )

        duration_ms = int((time.time() - start) * 1000)

        if result.returncode == 0:
            return {
                "step": name,
                "status": "pass",
                "command": cmd_str,
                "duration_ms": duration_ms,
                "stdout": truncate_output(result.stdout) if result.stdout else "",
            }
        else:
            return {
                "step": name,
                "status": "fail",
                "command": cmd_str,
                "exit_code": result.returncode,
                "duration_ms": duration_ms,
                "stdout": truncate_output(result.stdout) if result.stdout else "",
                "stderr": truncate_output(result.stderr) if result.stderr else "",
            }

    except subprocess.TimeoutExpired:
        duration_ms = int((time.time() - start) * 1000)
        return {
            "step": name,
            "status": "fail",
            "command": cmd_str,
            "duration_ms": duration_ms,
            "message": f"Timed out after {timeout}s",
        }
    except FileNotFoundError as e:
        return {
            "step": name,
            "status": "fail",
            "command": cmd_str,
            "duration_ms": int((time.time() - start) * 1000),
            "message": f"Command not found: {e}",
        }
    except Exception as e:
        return {
            "step": name,
            "status": "fail",
            "command": cmd_str,
            "duration_ms": int((time.time() - start) * 1000),
            "message": str(e),
        }


# --- Server health check ---

def _check_server_health(
    cmd_str: str,
    cwd: Path,
    port: int = 3000,
    wait_secs: int = 15,
) -> dict[str, Any]:
    """Start a dev server, wait for health, then stop it."""
    import shlex

    start = time.time()
    args = shlex.split(cmd_str)

    try:
        proc = subprocess.Popen(
            args,
            cwd=str(cwd),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env={**os.environ, "PORT": str(port)},
        )

        # Wait for server to come up
        import socket
        healthy = False
        for _ in range(wait_secs * 2):
            time.sleep(0.5)
            if proc.poll() is not None:
                # Process exited
                break
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=1):
                    healthy = True
                    break
            except (ConnectionRefusedError, OSError, socket.timeout):
                continue

        duration_ms = int((time.time() - start) * 1000)

        # Kill the server
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except Exception:
            proc.kill()

        if healthy:
            return {
                "step": "server-health",
                "status": "pass",
                "command": cmd_str,
                "port": port,
                "duration_ms": duration_ms,
                "message": f"Server healthy on port {port}",
            }
        else:
            stdout = proc.stdout.read() if proc.stdout else ""
            stderr = proc.stderr.read() if proc.stderr else ""
            return {
                "step": "server-health",
                "status": "fail",
                "command": cmd_str,
                "port": port,
                "duration_ms": duration_ms,
                "message": f"Server did not respond on port {port} within {wait_secs}s",
                "stderr": truncate_output(stderr),
            }

    except Exception as e:
        return {
            "step": "server-health",
            "status": "fail",
            "command": cmd_str,
            "duration_ms": int((time.time() - start) * 1000),
            "message": str(e),
        }


# --- Main runner ---

def run_smoketest(
    output_format: str = "json",
    level: str = "quick",
    project_dir: str = ".",
) -> None:
    """Run smoketest pipeline."""
    fmt = OutputFormat(output_format)
    proj_path = Path(project_dir).resolve()

    # Detect project
    detection = _detect_project(proj_path)
    cmds = detection["commands"]

    steps: list[dict[str, Any]] = []
    total_start = time.time()

    # Step 1: Dependency install verify
    steps.append(_run_step("deps-verify", cmds.get("install"), proj_path, timeout=180))

    # Step 2: Lint / typecheck
    lint_result = _run_step("lint", cmds.get("lint"), proj_path, timeout=60)
    steps.append(lint_result)
    tc_result = _run_step("typecheck", cmds.get("typecheck"), proj_path, timeout=60)
    steps.append(tc_result)

    # Step 3: Unit tests
    steps.append(_run_step("test", cmds.get("test"), proj_path, timeout=180))

    if level == "full":
        # Step 4: Build
        steps.append(_run_step("build", cmds.get("build"), proj_path, timeout=300))

        # Step 5: Server start + health check
        start_cmd = cmds.get("start")
        if start_cmd:
            steps.append(_check_server_health(start_cmd, proj_path))
        else:
            steps.append({
                "step": "server-health",
                "status": "skip",
                "message": "No start command configured",
                "duration_ms": 0,
            })

    total_duration = int((time.time() - total_start) * 1000)

    # Summarize
    passed = sum(1 for s in steps if s["status"] == "pass")
    failed = sum(1 for s in steps if s["status"] == "fail")
    skipped = sum(1 for s in steps if s["status"] == "skip")

    if failed > 0:
        overall = Status.FAIL
        msg = f"{failed} step(s) failed, {passed} passed, {skipped} skipped"
    elif skipped == len(steps):
        overall = Status.SKIP
        msg = "All steps skipped — no commands detected"
    else:
        overall = Status.PASS
        msg = f"All {passed} step(s) passed ({skipped} skipped)"

    result = make_result("smoketest", overall, msg, duration_ms=total_duration)
    result["level"] = level
    result["project"] = {
        "type": detection["type"],
        "package_manager": detection.get("package_manager"),
        "has_dockerfile": detection["has_dockerfile"],
    }
    result["steps"] = steps

    emit(result, fmt)
