"""
Prerequisites Detection & Setup Assistant.

Detects installed tools (Docker, Docker Desktop extensions, Windsurf Docker Extension,
Python, Git, Node) and provides guided setup instructions for missing components.
"""

import json
import os
import platform
import shutil
import subprocess
from pathlib import Path
from typing import Any, Optional

from nexus.cli.utils import OutputFormat, Status, Severity, emit, make_result


# --- Component detection functions ---

def _run_cmd(cmd: list[str], timeout: int = 10) -> tuple[bool, str]:
    """Run a command, return (success, stdout_or_stderr)."""
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if result.returncode == 0:
            return True, result.stdout.strip()
        return False, result.stderr.strip()
    except FileNotFoundError:
        return False, f"Command not found: {cmd[0]}"
    except subprocess.TimeoutExpired:
        return False, f"Command timed out after {timeout}s"
    except Exception as e:
        return False, str(e)


def check_python() -> dict[str, Any]:
    """Check Python 3.10+ is available."""
    ok, output = _run_cmd(["python", "--version"])
    if not ok:
        ok, output = _run_cmd(["python3", "--version"])

    if ok:
        version_str = output.replace("Python ", "")
        parts = version_str.split(".")
        if len(parts) >= 2 and int(parts[0]) >= 3 and int(parts[1]) >= 10:
            return {"name": "python", "installed": True, "version": version_str, "status": "ok"}
        return {
            "name": "python", "installed": True, "version": version_str,
            "status": "warn", "message": f"Python {version_str} found but 3.10+ recommended",
        }
    return {"name": "python", "installed": False, "status": "missing", "message": output}


def check_git() -> dict[str, Any]:
    """Check Git is available."""
    ok, output = _run_cmd(["git", "--version"])
    if ok:
        version = output.replace("git version ", "").strip()
        return {"name": "git", "installed": True, "version": version, "status": "ok"}
    return {"name": "git", "installed": False, "status": "missing", "message": output}


def check_node() -> dict[str, Any]:
    """Check Node.js is available (optional)."""
    ok, output = _run_cmd(["node", "--version"])
    if ok:
        return {"name": "node", "installed": True, "version": output.strip(), "status": "ok"}
    return {"name": "node", "installed": False, "status": "optional", "message": "Node.js not found (optional)"}


def check_docker() -> dict[str, Any]:
    """Check Docker Desktop / Docker Engine is running."""
    ok, output = _run_cmd(["docker", "info", "--format", "{{.ServerVersion}}"])
    if ok:
        return {"name": "docker", "installed": True, "version": output.strip(), "status": "ok"}

    # Docker might be installed but not running
    which = shutil.which("docker")
    if which:
        return {
            "name": "docker", "installed": True, "version": "unknown",
            "status": "warn", "message": "Docker is installed but not running. Start Docker Desktop.",
        }
    return {"name": "docker", "installed": False, "status": "missing", "message": output}


def check_docker_compose() -> dict[str, Any]:
    """Check Docker Compose (v2, bundled with Docker Desktop)."""
    ok, output = _run_cmd(["docker", "compose", "version", "--short"])
    if ok:
        return {"name": "docker-compose", "installed": True, "version": output.strip(), "status": "ok"}
    return {"name": "docker-compose", "installed": False, "status": "missing", "message": output}


def check_docker_extensions() -> dict[str, Any]:
    """Check which Docker Desktop extensions are installed."""
    ok, output = _run_cmd(["docker", "extension", "ls"])
    if not ok:
        return {
            "name": "docker-extensions", "installed": False,
            "status": "skip", "message": "Cannot list extensions (Docker not running or extensions not supported)",
            "extensions": {},
        }

    extensions = {}
    lines = output.strip().splitlines()
    for line in lines[1:]:  # Skip header
        parts = line.split()
        if parts:
            ext_name = parts[0]
            extensions[ext_name] = {"image": ext_name, "installed": True}

    # Check for specific extensions we care about
    ngrok_found = any("ngrok" in k.lower() for k in extensions)
    release_share_found = any("docker/resource-usage" in k.lower() or "release" in k.lower() for k in extensions)

    return {
        "name": "docker-extensions",
        "installed": True,
        "status": "ok" if ngrok_found else "partial",
        "extensions": extensions,
        "ngrok_installed": ngrok_found,
        "release_share_installed": release_share_found,
    }


def check_docker_extension() -> dict[str, Any]:
    """
    Check if the Docker Extension is installed in Windsurf.
    Windsurf uses Open VSX extensions; the Docker extension ID is 'ms-azuretools.vscode-docker'.
    We check the extensions directory for its presence.
    """
    ext_dirs: list[Path] = []
    if platform.system() == "Windows":
        userprofile = os.environ.get("USERPROFILE", "")
        if userprofile:
            ext_dirs.append(Path(userprofile) / ".windsurf" / "extensions")
            ext_dirs.append(Path(userprofile) / ".codeium" / "windsurf" / "extensions")
    else:
        home = Path.home()
        ext_dirs.append(home / ".windsurf" / "extensions")
        ext_dirs.append(home / ".codeium" / "windsurf" / "extensions")

    for ext_dir in ext_dirs:
        if ext_dir.exists():
            # Look for any directory matching the Docker extension
            for child in ext_dir.iterdir():
                if child.is_dir() and "docker" in child.name.lower():
                    return {
                        "name": "docker-extension", "installed": True, "status": "ok",
                        "extensions_dir": str(ext_dir),
                        "extension_path": str(child),
                        "message": "Docker Extension found in Windsurf",
                    }
            return {
                "name": "docker-extension", "installed": False, "status": "missing",
                "extensions_dir": str(ext_dir),
                "message": "Extensions directory found but Docker Extension not installed",
            }

    return {
        "name": "docker-extension", "installed": False, "status": "missing",
        "message": "Windsurf extensions directory not found",
    }


# --- Setup guides ---

_GUIDES: dict[str, dict[str, Any]] = {
    "python": {
        "name": "Python 3.10+",
        "auto_install": False,
        "steps": [
            "Download from https://www.python.org/downloads/",
            "During install, check 'Add Python to PATH'",
            "Verify: python --version",
        ],
    },
    "git": {
        "name": "Git",
        "auto_install": False,
        "steps": [
            "Download from https://git-scm.com/downloads",
            "Install with default options",
            "Verify: git --version",
        ],
    },
    "node": {
        "name": "Node.js (optional)",
        "auto_install": False,
        "steps": [
            "Download LTS from https://nodejs.org/",
            "Install with default options",
            "Verify: node --version",
        ],
    },
    "docker": {
        "name": "Docker Desktop",
        "auto_install": False,
        "steps": [
            "Download from https://www.docker.com/products/docker-desktop/",
            "Install and start Docker Desktop",
            "Verify: docker info",
        ],
    },
    "ngrok-extension": {
        "name": "ngrok Docker Desktop Extension",
        "auto_install": True,
        "install_cmd": ["docker", "extension", "install", "ngrok/ngrok-docker-extension", "--force"],
        "steps": [
            "Requires Docker Desktop to be running",
            "Auto-install available: docker extension install ngrok/ngrok-docker-extension",
            "After install, activate via Docker Desktop toolbar → ngrok icon",
        ],
    },
    "docker-extension": {
        "name": "Docker Extension for Windsurf",
        "auto_install": False,
        "steps": [
            "1. Open Windsurf",
            "2. Go to Extensions panel (Ctrl+Shift+X / Cmd+Shift+X)",
            "3. Search for 'Docker'",
            "4. Install the Docker extension (ms-azuretools.vscode-docker or equivalent)",
            "5. Reload Windsurf if prompted",
            "6. Verify: Docker icon appears in the sidebar",
        ],
    },
}


def get_guide(component: str) -> dict[str, Any]:
    """Get setup guide for a component."""
    guide = _GUIDES.get(component)
    if not guide:
        return {"error": f"No guide for component: {component}", "available": list(_GUIDES.keys())}

    result = dict(guide)
    return result


# --- Main runner ---

def run_prereqs(
    output_format: str = "json",
    component: Optional[str] = None,
    guide: bool = False,
) -> None:
    """Run prerequisites check or generate setup guide."""
    fmt = OutputFormat(output_format)

    if guide:
        if component:
            guide_data = get_guide(component)
            result = make_result("prereqs", Status.INFO, f"Setup guide for {component}")
            result["guide"] = guide_data
        else:
            result = make_result("prereqs", Status.INFO, "Available setup guides")
            result["guides"] = {k: v["name"] for k, v in _GUIDES.items()}
        emit(result, fmt)
        return

    # Run all checks or a specific one
    checks = {}
    if component:
        check_fn = {
            "python": check_python,
            "git": check_git,
            "node": check_node,
            "docker": check_docker,
            "docker-compose": check_docker_compose,
            "docker-extensions": check_docker_extensions,
            "docker-extension": check_docker_extension,
        }.get(component)
        if check_fn:
            checks[component] = check_fn()
        else:
            result = make_result("prereqs", Status.FAIL, f"Unknown component: {component}")
            emit(result, fmt)
            return
    else:
        checks["python"] = check_python()
        checks["git"] = check_git()
        checks["node"] = check_node()
        checks["docker"] = check_docker()
        checks["docker-compose"] = check_docker_compose()
        checks["docker-extensions"] = check_docker_extensions()
        checks["docker-extension"] = check_docker_extension()

    # Summarize
    missing = [k for k, v in checks.items() if v.get("status") == "missing"]
    warnings = [k for k, v in checks.items() if v.get("status") == "warn"]
    ok_count = sum(1 for v in checks.values() if v.get("status") == "ok")

    if missing:
        overall_status = Status.FAIL
        message = f"{len(missing)} missing: {', '.join(missing)}"
    elif warnings:
        overall_status = Status.WARN
        message = f"All installed but {len(warnings)} warning(s)"
    else:
        overall_status = Status.PASS
        message = f"All {ok_count} components ready"

    result = make_result("prereqs", overall_status, message)
    result["checks"] = checks
    result["missing"] = missing
    result["warnings"] = warnings
    result["auto_installable"] = [
        k for k in missing
        if k in _GUIDES and _GUIDES.get(k, {}).get("auto_install")
    ]

    if missing:
        result["next_steps"] = []
        for m in missing:
            g = _GUIDES.get(m)
            if g:
                result["next_steps"].append({
                    "component": m,
                    "auto_install": g.get("auto_install", False),
                    "guide_cmd": f"python bootstrap/cli/bs_cli.py prereqs --guide --component {m}",
                })

    emit(result, fmt)
