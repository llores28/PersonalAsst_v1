"""
Local Environment & Container Validation Tool.

NOT production deployment — validates that the app works in a
production-like container and reports readiness.

Subcommands:
  init      — generate Dockerfile + docker-compose.yml from project detection
  build     — build Docker image
  up        — start containers, wait for health
  down      — stop and clean up
  logs      — tail container logs
  status    — show running containers, ports, health
  validate  — pre-production readiness check
"""

import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any, Optional

from nexus.cli.utils import (
    OutputFormat, Status, Severity, emit, make_result, truncate_output,
)
from nexus.cli.security import validate_path, scan_text_for_secrets


# --- Project detection for Dockerfile generation ---

def _detect_stack(project_dir: Path) -> dict[str, Any]:
    """Detect project stack for Dockerfile template selection."""
    if (project_dir / "package.json").exists():
        pkg = {}
        try:
            pkg = json.loads((project_dir / "package.json").read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass

        deps = {**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})}

        if "next" in deps:
            return {"stack": "nextjs", "port": 3000}
        if "nuxt" in deps or "nuxt3" in deps:
            return {"stack": "nuxt", "port": 3000}
        if "@sveltejs/kit" in deps:
            return {"stack": "sveltekit", "port": 3000}
        if "vite" in deps:
            return {"stack": "vite", "port": 5173}
        if "react-scripts" in deps:
            return {"stack": "cra", "port": 3000}
        if "express" in deps:
            return {"stack": "express", "port": 3000}
        return {"stack": "node", "port": 3000}

    if (project_dir / "pyproject.toml").exists() or (project_dir / "requirements.txt").exists():
        pyproject_content = ""
        try:
            if (project_dir / "pyproject.toml").exists():
                pyproject_content = (project_dir / "pyproject.toml").read_text(encoding="utf-8")
        except OSError:
            pass

        if "fastapi" in pyproject_content:
            return {"stack": "fastapi", "port": 8000}
        if "django" in pyproject_content:
            return {"stack": "django", "port": 8000}
        if "flask" in pyproject_content:
            return {"stack": "flask", "port": 5000}
        return {"stack": "python", "port": 8000}

    if (project_dir / "go.mod").exists():
        return {"stack": "go", "port": 8080}

    return {"stack": "unknown", "port": 8080}


# --- Dockerfile templates ---

_DOCKERFILE_NODE = """\
FROM node:20-slim AS build
WORKDIR /app
COPY package*.json ./
RUN npm ci --only=production
COPY . .
RUN npm run build || true

FROM node:20-slim
WORKDIR /app
RUN addgroup --system appgroup && adduser --system --ingroup appgroup appuser
COPY --from=build /app .
USER appuser
EXPOSE {port}
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \\
  CMD node -e "require('http').get('http://localhost:{port}/', (r) => process.exit(r.statusCode === 200 ? 0 : 1))" || exit 1
CMD ["node", "index.js"]
"""

_DOCKERFILE_PYTHON = """\
FROM python:3.12-slim AS build
WORKDIR /app
COPY requirements*.txt pyproject.toml* ./
RUN pip install --no-cache-dir -r requirements.txt 2>/dev/null \\
    || pip install --no-cache-dir -e . 2>/dev/null || true
COPY . .

FROM python:3.12-slim
WORKDIR /app
RUN addgroup --system appgroup && adduser --system --ingroup appgroup appuser
COPY --from=build /app .
COPY --from=build /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
USER appuser
EXPOSE {port}
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \\
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:{port}/')" || exit 1
CMD ["python", "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "{port}"]
"""

_DOCKERFILE_GO = """\
FROM golang:1.22 AS build
WORKDIR /app
COPY go.mod go.sum* ./
RUN go mod download
COPY . .
RUN CGO_ENABLED=0 go build -o /app/server .

FROM gcr.io/distroless/static-debian12
WORKDIR /app
COPY --from=build /app/server .
USER nonroot:nonroot
EXPOSE {port}
CMD ["/app/server"]
"""

_DOCKERFILES = {
    "node": _DOCKERFILE_NODE,
    "python": _DOCKERFILE_PYTHON,
    "go": _DOCKERFILE_GO,
}

_COMPOSE_TEMPLATE = """\
services:
  app:
    build:
      context: .
      dockerfile: Dockerfile
    ports:
      - "{port}:{port}"
    environment:
      - NODE_ENV=production
      - PORT={port}
    restart: unless-stopped
    read_only: true
    tmpfs:
      - /tmp
"""

_DOCKERIGNORE_TEMPLATE = """\
node_modules/
.git/
.env
.env.*
*.log
__pycache__/
.venv/
venv/
dist/
build/
.next/
.nuxt/
target/
.cache/
coverage/
.DS_Store
"""


# --- Docker CLI helpers ---

def _docker_cmd(args: list[str], cwd: str, timeout: int = 300) -> dict[str, Any]:
    """Run a docker command and return structured result."""
    cmd = ["docker"] + args
    start = time.time()
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, cwd=cwd,
        )
        duration_ms = int((time.time() - start) * 1000)
        return {
            "command": " ".join(cmd),
            "exit_code": result.returncode,
            "stdout": truncate_output(result.stdout, 1000) if result.stdout else "",
            "stderr": truncate_output(result.stderr, 500) if result.stderr else "",
            "duration_ms": duration_ms,
            "success": result.returncode == 0,
        }
    except subprocess.TimeoutExpired:
        return {
            "command": " ".join(cmd), "exit_code": -1,
            "stderr": f"Timed out after {timeout}s",
            "duration_ms": int((time.time() - start) * 1000), "success": False,
        }
    except FileNotFoundError:
        return {
            "command": " ".join(cmd), "exit_code": -1,
            "stderr": "Docker not found. Is Docker Desktop running?",
            "duration_ms": 0, "success": False,
        }


# --- Subcommand implementations ---

def _init(project_dir: Path) -> dict[str, Any]:
    """Generate Dockerfile + docker-compose.yml."""
    stack_info = _detect_stack(project_dir)
    stack = stack_info["stack"]
    port = stack_info["port"]
    files_created: list[str] = []

    template_key = "node"
    if stack in ("python", "fastapi", "django", "flask"):
        template_key = "python"
    elif stack == "go":
        template_key = "go"

    dockerfile_path = project_dir / "Dockerfile"
    if not dockerfile_path.exists():
        template = _DOCKERFILES.get(template_key, _DOCKERFILES["node"])
        dockerfile_path.write_text(template.format(port=port), encoding="utf-8")
        files_created.append("Dockerfile")

    compose_path = project_dir / "docker-compose.yml"
    if not compose_path.exists():
        compose_path.write_text(_COMPOSE_TEMPLATE.format(port=port), encoding="utf-8")
        files_created.append("docker-compose.yml")

    dockerignore_path = project_dir / ".dockerignore"
    if not dockerignore_path.exists():
        dockerignore_path.write_text(_DOCKERIGNORE_TEMPLATE, encoding="utf-8")
        files_created.append(".dockerignore")

    return {
        "stack": stack_info,
        "files_created": files_created,
        "files_skipped": [
            f for f in ["Dockerfile", "docker-compose.yml", ".dockerignore"]
            if f not in files_created
        ],
    }


def _build(project_dir: Path) -> dict[str, Any]:
    return _docker_cmd(["compose", "build"], cwd=str(project_dir), timeout=600)


def _up(project_dir: Path) -> dict[str, Any]:
    result = _docker_cmd(["compose", "up", "-d"], cwd=str(project_dir))
    if not result["success"]:
        return result

    stack_info = _detect_stack(project_dir)
    port = stack_info["port"]
    healthy = False

    import socket
    for _ in range(30):
        time.sleep(1)
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1):
                healthy = True
                break
        except (ConnectionRefusedError, OSError, socket.timeout):
            continue

    result["health_check"] = {
        "port": port, "healthy": healthy,
        "message": f"Container healthy on port {port}" if healthy
                   else f"Container not responding on port {port} after 30s",
    }
    if healthy:
        result["browser_preview"] = {
            "url": f"http://localhost:{port}",
            "message": "Use Cascade browser_preview tool for visual verification",
        }
    return result


def _down(project_dir: Path) -> dict[str, Any]:
    return _docker_cmd(["compose", "down", "--remove-orphans"], cwd=str(project_dir))


def _logs(project_dir: Path) -> dict[str, Any]:
    return _docker_cmd(["compose", "logs", "--tail=100", "--no-color"], cwd=str(project_dir), timeout=15)


def _status(project_dir: Path) -> dict[str, Any]:
    ps_result = _docker_cmd(
        ["compose", "ps", "--format", "json"], cwd=str(project_dir), timeout=10,
    )
    containers: list[dict] = []
    if ps_result["success"] and ps_result["stdout"]:
        try:
            for line in ps_result["stdout"].strip().splitlines():
                line = line.strip()
                if line and line.startswith("{"):
                    containers.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    return {"containers": containers, "container_count": len(containers), "raw": ps_result}


def _validate(project_dir: Path) -> dict[str, Any]:
    """Pre-production readiness check."""
    checks: list[dict] = []

    dockerfile = project_dir / "Dockerfile"
    if dockerfile.exists():
        content = dockerfile.read_text(encoding="utf-8")
        lines = content.splitlines()

        has_user = any("USER" in l and not l.strip().startswith("#") for l in lines)
        checks.append({
            "check": "non-root-user", "status": "pass" if has_user else "fail",
            "severity": "high",
            "message": "Dockerfile uses non-root USER" if has_user
                       else "Dockerfile missing USER directive — container runs as root",
        })

        has_healthcheck = any("HEALTHCHECK" in l for l in lines)
        checks.append({
            "check": "healthcheck", "status": "pass" if has_healthcheck else "warn",
            "severity": "medium",
            "message": "HEALTHCHECK defined" if has_healthcheck else "No HEALTHCHECK in Dockerfile",
        })

        has_add_url = any(
            l.strip().startswith("ADD") and ("http://" in l or "https://" in l)
            for l in lines
        )
        checks.append({
            "check": "no-add-urls", "status": "fail" if has_add_url else "pass",
            "severity": "medium",
            "message": "ADD used for remote URLs — use COPY + RUN curl instead" if has_add_url
                       else "No ADD with remote URLs",
        })

        from_count = sum(1 for l in lines if l.strip().upper().startswith("FROM"))
        checks.append({
            "check": "multi-stage", "status": "pass" if from_count > 1 else "info",
            "severity": "low",
            "message": f"Multi-stage build ({from_count} stages)" if from_count > 1
                       else "Single-stage build (consider multi-stage for smaller images)",
        })
    else:
        checks.append({
            "check": "dockerfile-exists", "status": "fail", "severity": "high",
            "message": "No Dockerfile found — run: bs_cli.py local-env init",
        })

    dockerignore = project_dir / ".dockerignore"
    checks.append({
        "check": "dockerignore",
        "status": "pass" if dockerignore.exists() else "warn",
        "severity": "medium",
        "message": ".dockerignore exists" if dockerignore.exists()
                   else ".dockerignore missing — secrets and node_modules may leak into image",
    })

    for fname in ["Dockerfile", "docker-compose.yml", "docker-compose.yaml", "compose.yml"]:
        fpath = project_dir / fname
        if fpath.exists():
            findings = scan_text_for_secrets(fpath.read_text(encoding="utf-8"))
            checks.append({
                "check": f"no-secrets-in-{fname}",
                "status": "fail" if findings else "pass",
                "severity": "critical" if findings else "info",
                "message": f"Potential secrets in {fname}: {len(findings)} finding(s)" if findings
                           else f"No secrets detected in {fname}",
            })

    failed = [c for c in checks if c["status"] == "fail"]
    warned = [c for c in checks if c["status"] == "warn"]
    passed = [c for c in checks if c["status"] == "pass"]

    return {
        "checks": checks,
        "summary": {"passed": len(passed), "warned": len(warned), "failed": len(failed), "total": len(checks)},
        "ready": len(failed) == 0,
    }


# --- Main runner ---

def run_local_env(
    subcommand: str,
    output_format: str = "json",
    project_dir: str = ".",
) -> None:
    """Route to the appropriate local-env subcommand."""
    fmt = OutputFormat(output_format)
    proj_path = Path(project_dir).resolve()

    if subcommand == "init":
        data = _init(proj_path)
        status = Status.PASS if data["files_created"] else Status.INFO
        msg = f"Created: {', '.join(data['files_created'])}" if data["files_created"] else "All files already exist"
        result = make_result("local-env.init", status, msg)
        result.update(data)

    elif subcommand == "build":
        data = _build(proj_path)
        status = Status.PASS if data["success"] else Status.FAIL
        result = make_result("local-env.build", status, "Build succeeded" if data["success"] else "Build failed")
        result.update(data)

    elif subcommand == "up":
        data = _up(proj_path)
        health = data.get("health_check", {})
        status = Status.PASS if health.get("healthy") else Status.FAIL
        result = make_result("local-env.up", status, health.get("message", ""))
        result.update(data)

    elif subcommand == "down":
        data = _down(proj_path)
        status = Status.PASS if data["success"] else Status.FAIL
        result = make_result("local-env.down", status, "Containers stopped" if data["success"] else "Failed to stop")
        result.update(data)

    elif subcommand == "logs":
        data = _logs(proj_path)
        status = Status.INFO
        result = make_result("local-env.logs", status, "Container logs")
        result.update(data)

    elif subcommand == "status":
        data = _status(proj_path)
        status = Status.INFO
        result = make_result("local-env.status", status, f"{data['container_count']} container(s)")
        result.update(data)

    elif subcommand == "validate":
        data = _validate(proj_path)
        status = Status.PASS if data["ready"] else Status.FAIL
        msg = "Ready for deployment" if data["ready"] else f"{data['summary']['failed']} check(s) failed"
        result = make_result("local-env.validate", status, msg)
        result.update(data)

    else:
        result = make_result("local-env", Status.FAIL, f"Unknown subcommand: {subcommand}")

    emit(result, fmt)
