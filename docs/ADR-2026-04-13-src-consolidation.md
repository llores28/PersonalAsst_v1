# ADR-2026-04-13: src/ Directory Consolidation

## Status
Accepted

## Context

The project root contained a mix of deployment-relevant directories (`orchestration-ui/`, `user_skills/`, `config/`, `alembic/`, `alembic.ini`) alongside bootstrap/project-level files (`docker-compose.yml`, `Dockerfile*`, `.env`, `README.md`).

This created several problems:
- Dockerfiles needed multiple separate `COPY` instructions for each directory scattered at root.
- Volume mount paths in `docker-compose.yml` referenced root-level `user_skills/` while Python code ran inside `src/`.
- The `orchestration-ui/` React app had its own implicit build context separate from the `src/` source tree.
- New contributors had no clear rule for where production files live versus project-level files.

## Decision

Move all directories that ship in Docker images under `src/`:

| Before | After |
|---|---|
| `orchestration-ui/` | `src/orchestration-ui/` |
| `user_skills/` | `src/user_skills/` |
| `config/` | `src/config/` |
| `alembic/` | `src/alembic/` |
| `alembic.ini` | `src/alembic.ini` |

**Rule:** If a file or directory is `COPY`'d into a Docker image, it belongs under `src/`. If it is a project-level config (Docker Compose, CI, env template, README), it stays at the root.

`.env` and `.env.example` remain at root — Docker Compose reads them from its own working directory and they must never enter a Docker image.

## Consequences

### Positive
- A single `COPY src/ ./src/` in each Dockerfile covers everything. No more brittle per-directory `COPY` lines.
- `docker-compose.yml` volume mounts for `user_skills` and `orchestration-ui` build context are consistent with the `src/` tree.
- Clear mental model: root = project bootstrap; `src/` = everything that runs.
- `.gitignore` and `.dockerignore` entries for `node_modules/` and `build/` are co-located with the source they describe.

### Negative / Trade-offs
- Required updating 14 Python path references (`user_skills/` → `src/user_skills/`, `config/` → `src/config/`, `alembic.ini` → `src/alembic.ini`).
- `alembic.ini`'s `script_location = src/db/migrations` was already correct (relative to WORKDIR `/app`); no change needed there.
- `src/orchestration-ui/` is an unusual location for a React app — most React projects live at root. Developers must remember the build context for that service.

## Files Changed

- `src/agents/skill_factory_agent.py` — `USER_SKILLS_DIR`
- `src/skills/loader.py` — default `user_skills_dir`
- `src/skills/validation.py` — 3 path refs
- `src/agents/orchestrator.py` — `config/persona_default.yaml`
- `src/orchestration/api.py` — 8 path refs
- `src/bot/handlers.py` — 3 path refs
- `src/main.py` — `alembic.ini` path
- `Dockerfile` — removed separate COPY lines
- `Dockerfile.orchestration` — removed separate COPY lines
- `docker-compose.yml` — build context + volume mounts
- `.gitignore` — `orchestration-ui/` → `src/orchestration-ui/`
- `.dockerignore` — broad exclusion → targeted node_modules/build only
