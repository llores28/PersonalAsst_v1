# Nexus - Intelligent Project Operating System

Nexus creates a complete, AI-powered project operating system that automatically optimizes development workflows, agent behaviors, and model selection based on task complexity.

## Project Overview

This is `Nexus`, a reusable bootstrap toolkit that generates project-specific AI-powered operating systems including rules, agents, skills, workflows, and documentation.

### Key directories
- `nexus/` — Bootstrap prompt templates (Fast/Team/Enterprise) and model selection reference
- `nexus/cli/` — Python CLI tools (smoketest, debug, research, scrape, local-env, scaffold)
- `.windsurf/rules/` — Nexus rule files with activation triggers
- `.windsurf/skills/` — Reusable skill definitions (SKILL.md + resources)
- `.windsurf/workflows/` — Slash-command workflow definitions

### Stack
- Python 3.10+ (CLI tools)
- Click + Rich (CLI framework)
- httpx + beautifulsoup4 (web scraping)
- Markdown (all config/templates)

## Operating Constraints

1. **No secrets** in output, commits, or logs.
2. **No invented commands** — verify from repo files before suggesting.
3. **Minimal changes** — prefer small, reversible edits.
4. **Security defaults** — validate paths, validate URLs, no shell=True, no eval/exec.
5. **Evidence-based** — cite file paths for non-trivial claims.
6. Mark uncertainty as `TODO(verify)`.

## Token/Quota Efficiency

- Use code search / Fast Context before reading full files.
- Read files in large chunks to avoid repeated small reads.
- Batch independent tool calls in parallel.
- Keep responses concise — no restating known context.
- For simple edits, suggest Ctrl+I (Command mode, free, no quota cost).
- For routine tasks, use SWE-1.5 (free model) or SWE-1.
- **Auto model selection**: Nexus automatically selects the optimal model based on task complexity via `nexus/model-selection-reference.md`.
- Suggest user run tests manually rather than auto-executing.

## Testing

- Run `python nexus/cli/bs_cli.py smoketest --level quick` for quick verification.
- Run `python nexus/cli/bs_cli.py prereqs` to check prerequisites.
- CLI tools emit structured JSON by default (`--format json`), human output via `--format human`.

## CLI Toolkit Commands

```
python nexus/cli/bs_cli.py prereqs          # Check prerequisites
python nexus/cli/bs_cli.py smoketest         # Run smoke tests
python nexus/cli/bs_cli.py debug logs <path> # Inspect logs
python nexus/cli/bs_cli.py debug secrets-scan # Scan for leaked secrets
python nexus/cli/bs_cli.py research docs <q> # Search docs
python nexus/cli/bs_cli.py scrape page <url> # Scrape a page
python nexus/cli/bs_cli.py local-env up      # Start containers
python nexus/cli/bs_cli.py scaffold <name>   # Create new CLI tool
```

## Model Selection

Nexus includes intelligent model selection that automatically chooses the optimal AI model based on task complexity:

- **Simple tasks** (typos, formatting): SWE-1.5 (Free)
- **Moderate tasks** (multi-file edits): GPT-5 Low (0.5x)
- **Complex tasks** (refactoring): GPT-5 Med / Gemini 3.1 Pro (1x)
- **Expert tasks** (architecture): Claude Sonnet 4.6 / GPT-5 High (2x)
- **Frontier tasks** (threat modeling): Claude Opus 4.6 (2-3x)

See `nexus/model-selection-reference.md` for the complete model database and selection algorithm.
