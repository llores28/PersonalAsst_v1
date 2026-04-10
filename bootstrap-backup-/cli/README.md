# Bootstrap CLI Toolkit

Focused "sniper agent" CLI tools for Cascade — smoketesting, debugging, research, webscraping, container validation, and tool scaffolding.

## Quick Start

```bash
pip install -r bootstrap/cli/requirements.txt
python bootstrap/cli/bs_cli.py --help
```

## Design Principles

- **Structured JSON output** by default (`--format json`) — model-friendly, minimal tokens
- **`--format human`** for rich terminal output via `rich`
- **One tool = one purpose** (sniper agent pattern, prevents context rot)
- **Security by default** — input validation, path sanitization, SSRF protection, audit trail
- **Lazy imports** — heavy deps only loaded when the subcommand needs them

## Commands

| Command | Purpose |
|---|---|
| `bs_cli.py prereqs` | Check prerequisites (Docker, MCP, extensions, Python, Git) |
| `bs_cli.py smoketest` | Run tiered smoke tests (quick or full) |
| `bs_cli.py debug <sub>` | Debug tools: logs, trace, deps, env, ports, secrets-scan |
| `bs_cli.py research <sub>` | Research: docs search, package info, changelog, compare |
| `bs_cli.py scrape <sub>` | Webscrape: page, api, links, docs crawl |
| `bs_cli.py scaffold <name>` | Generate a new CLI tool from template |
| `bs_cli.py local-env <sub>` | Docker container validation: init, build, up, down, validate |

## Exit Codes

- `0` — success
- `1` — failure
- `2` — partial (details in output)

## Preview Strategy (3-tier)

1. **Dev preview**: Use Cascade's `browser_preview` tool (zero config, 1 tool call)
2. **Container validation**: `local-env build` → `local-env up` → `browser_preview`
3. **Shareable**: Docker Desktop ngrok/Release Share extensions (GUI, user-driven)

## Security

- All paths validated against project root (no traversal)
- All URLs checked for private IPs (SSRF protection)
- All subprocess calls use args arrays (no `shell=True`)
- Audit log: `.cache/bs-cli/audit.jsonl`
- Secret detection: `debug secrets-scan`
- Scaffold template enforces security constraints

## Caching

- Research results: `.cache/research/` (24h TTL)
- Scrape results: `.cache/scrape/` (1h pages, 24h doc crawls)
- Add `.cache/` to `.gitignore`

## For Existing Projects

Use `/migrate-toolkit` workflow to add this toolkit to a project that already ran the old bootstrap. Zero file conflicts — the toolkit is purely additive.
