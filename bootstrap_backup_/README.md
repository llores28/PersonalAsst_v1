# Bootstrap Prompt Pack

This folder contains the 3-tier bootstrap prompts and intake template.

## Files

- `1Fast-ws-Bootstrap.md` -> Daily/speed-focused bootstrap
- `2Team-ws-Bootstrap.md` -> Balanced/team bootstrap
- `3Enterprise-ws-Bootstrap.md` -> Strict enterprise bootstrap
- `Uni-WindsurfBootstrap.md` -> Universal fallback bootstrap
- `Bootstrap-Project-Intake.md` -> Intake template used by the wizard
- `PRD-Template.md` -> Template used by `/bootstrap-prd` to generate `docs/PRD.md`

## Wizard usage in Cascade

1. Run `/bootstrap-wizard`.
2. Fill in `bootstrap/Bootstrap-Project-Intake.md` (or answer prompts in chat).
3. The wizard recommends Fast, Team, or Enterprise and maps to the matching file.
4. Confirm selection.
5. Run `/bootstrap-prd` to create/update `docs/PRD.md` with cohesion + conflict checks.
6. Run the selected bootstrap prompt.

## Cohesive usage model

- PRD (`/bootstrap-prd`) defines product direction (`what`/`why`/success metrics).
- Rules + AGENTS define constraints and guardrails.
- Skills + workflows define execution and operational procedure.
- If there is a conflict, use PRD Conflict Register and resolve explicitly (never silently override constraints).

## Selection defaults

- If uncertainty exists between Fast and Team, default to **Team**.
- If mapped tier file is missing, fallback to `Uni-WindsurfBootstrap.md`.

## CLI Toolkit

The `cli/` subdirectory contains the Bootstrap CLI Toolkit — focused "sniper agent" tools for Cascade.

See `cli/README.md` for full documentation.

### Quick start
```bash
pip install -r bootstrap/cli/requirements.txt
python bootstrap/cli/bs_cli.py --help
```

### Available slash commands
| Command | Purpose |
|---|---|
| `/prereqs` | Check prerequisites (Docker, MCP, extensions) |
| `/smoketest` | Run tiered smoke tests |
| `/debug` | Systematic debugging investigation |
| `/research` | Dependency/docs research |
| `/scrape` | External docs/API webscraping |
| `/create-tool` | Scaffold a new CLI tool |
| `/local-env` | Docker container validation |
| `/migrate-toolkit` | Add toolkit to existing project (one-shot) |

### Preview strategy (3-tier)
1. **Dev preview**: Cascade's `browser_preview` (default, zero config, 1 tool call)
2. **Container validation**: Docker CLI → `browser_preview` on localhost
3. **Shareable**: Docker Desktop ngrok/Release Share extensions (user-driven GUI)

### For existing projects
Run `/migrate-toolkit` to add the toolkit without touching any existing bootstrap artifacts.

### Security
- Input validation, path sanitization, SSRF protection
- Audit trail at `.cache/bs-cli/audit.jsonl`
- Secret detection via `debug secrets-scan`
- Add `.cache/` to `.gitignore`
