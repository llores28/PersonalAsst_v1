# Nexus Bootstrap Toolkit

This folder contains the 3-tier bootstrap prompts and intake template for Nexus - the intelligent project operating system.

## Files

- `1Fast-ws-Bootstrap.md` -> Daily/speed-focused bootstrap
- `2Team-ws-Bootstrap.md` -> Balanced/team bootstrap
- `3Enterprise-ws-Bootstrap.md` -> Strict enterprise bootstrap
- `Uni-WindsurfBootstrap.md` -> Universal fallback bootstrap
- `Bootstrap-Project-Intake.md` -> Intake template used by the wizard
- `PRD-Template.md` -> Template used by `/bootstrap-prd` to generate `docs/PRD.md`
- `model-selection-reference.md` -> AI model cost database and selection algorithm

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

## Token Efficiency (Quota Conservation)

Windsurf's quota-based system charges per-token. The bootstrap now includes several optimizations:

| Optimization | Savings | How |
|---|---|---|
| `model_decision` triggers | ~50% fewer always-on rule tokens | Non-critical rules loaded only when relevant |
| Slim wizard workflow | ~5000 tokens/wizard invocation | Decision logic moved to `wizard-reference.md` (read on-demand) |
| `00-token-efficiency.md` rule | Behavioral savings | Instructs Cascade to batch reads, use Fast Context first, suggest Ctrl+I |
| `.codeiumignore` | Reduces indexing overhead | Excludes large reference docs and cache from ambient context |
| SWE-1/SWE-1.5 for routine tasks | Free (0 credits) | Windsurf proprietary models consume no quota |
| Auto model selection | Optimal cost per task | `model-selection-reference.md` maps task complexity → cheapest capable model |

### Auto Model Selection

The `00-token-efficiency.md` rule includes a quick decision guide that recommends the optimal model based on task complexity. For the full model database (all tiers, costs, capabilities, selection algorithm), see `bootstrap/model-selection-reference.md`.

| Task Complexity | Recommended Model | Cost |
|---|---|---|
| Simple (typos, formatting, boilerplate) | SWE-1.5 | Free |
| Moderate (multi-file edits, unit tests) | SWE-1.5 → GPT-5 Low | Free → 0.5x |
| Complex (refactoring, API integration) | GPT-5 Med / Gemini 3.1 Pro | 1x |
| Expert (architecture, security audit) | Claude Sonnet 4.6 / GPT-5 High | 2x |
| Frontier (novel design, threat modeling) | Claude Opus 4.6 (Thinking) | 2x–3x |

**Escalation pattern**: Always start with the cheapest model (SWE-1.5), escalate only if output quality is insufficient. Stick to one model per session to leverage context caching.

### Tips for users
- Use **Ctrl+I** (Command mode) for simple edits — it's free, no quota cost.
- Use **SWE-1.5** for routine tasks (free, near-Claude 4.5 performance).
- Use **Plan mode** before Code mode to reduce wasted tool calls.
- Ask general questions to an external AI (ChatGPT, etc.) instead of Cascade.
- Keep prompts precise and avoid unnecessary context.
- Stick to **one model per session** to maximize context caching savings.

## Cross-IDE Support

The bootstrap generates instruction files for multiple IDEs:

| File | IDE | Purpose |
|---|---|---|
| `AGENTS.md` | Windsurf + VS Code Copilot | Always-on project instructions |
| `.github/copilot-instructions.md` | VS Code (GitHub Copilot) | Project context, coding standards |
| `CLAUDE.md` | Claude Code / VS Code + Claude | Project constraints, commands |
| `.cursorrules` | Cursor IDE | Project constraints, commands |
| `.windsurf/rules/*.md` | Windsurf only | Activation-triggered rules |
| `.windsurf/skills/` | Windsurf only | Reusable skill procedures |
| `.windsurf/workflows/` | Windsurf only | Slash-command workflows |

### Switching from Windsurf to VS Code
1. Open the project in VS Code.
2. Install GitHub Copilot extension (or Continue/Cline).
3. `AGENTS.md` and `.github/copilot-instructions.md` are auto-detected.
4. CLI tools work identically: `python bootstrap/cli/bs_cli.py --help`.
5. Windsurf-specific features (rules, skills, workflows) won't activate but core project context is preserved.

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

## File Reference

| File | Purpose |
|---|---|
| `bootstrap/wizard-reference.md` | Full decision logic for `/bootstrap-wizard` (read on-demand, not indexed) |
| `bootstrap/model-selection-reference.md` | Model cost database + selection algorithm (read on-demand, not indexed) |
| `.codeiumignore` | Excludes large reference docs and cache from Windsurf indexing |
| `.windsurf/rules/00-token-efficiency.md` | Always-on rule for quota conservation + model selection guide |
| `AGENTS.md` | Cross-IDE project instructions (Windsurf + VS Code Copilot) |
| `.github/copilot-instructions.md` | VS Code Copilot instructions |
| `CLAUDE.md` | Claude Code instructions |
| `.cursorrules` | Cursor IDE instructions |
