---
name: local-env
description: Local container validation and Docker Desktop sharing — NOT production deployment
---
# Local Environment

## Trigger
- User wants to test in a production-like container
- User wants to validate Dockerfile correctness
- User wants to share a preview with others
- User runs `/local-env`

## Prechecks
- Docker Desktop running (check via `prereqs`)
- For sharing: Docker Desktop ngrok/Release Share extension installed

## Commands
```
python bootstrap/cli/bs_cli.py local-env init
python bootstrap/cli/bs_cli.py local-env build
python bootstrap/cli/bs_cli.py local-env up
python bootstrap/cli/bs_cli.py local-env down
python bootstrap/cli/bs_cli.py local-env logs
python bootstrap/cli/bs_cli.py local-env status
python bootstrap/cli/bs_cli.py local-env validate
```

## Workflow (typical sequence)
1. `local-env init` → generates Dockerfile/compose from detected stack
2. `local-env build` → builds image
3. `local-env up` → starts container, waits for health
4. Use Cascade `browser_preview` on the localhost port → visual verification
5. If user wants to share: instruct them to use Docker Desktop ngrok/Release Share extension
6. `local-env validate` → pre-production readiness report
7. `local-env down` → cleanup

## Docker Extension integration
- If Windsurf Docker Extension is installed: use it for container management, Dockerfile intellisense, and compose visualization
- CLI tools handle build/up/down/health/validate regardless of extension presence

## Scope boundary
This tool validates containers locally. It does NOT deploy to production.
Production deployment goes through CI/CD pipelines.
