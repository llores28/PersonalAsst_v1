---
name: create-cli-tool
description: Scaffold a new project-specific CLI tool that inherits the security framework
---
# Create CLI Tool

## Trigger
- Model identifies a repetitive task that would benefit from automation
- User requests a custom CLI tool for the project
- User runs `/create-tool`

## Command
```
python bootstrap/cli/bs_cli.py scaffold <name> --description "What this tool does"
```

## What gets created
- `bootstrap/cli/tools/<name>.py` — tool implementation with security imports
- Registration snippet for `bs_cli.py` (must be added manually)

## Guardrails (enforced by template)
- No `shell=True` in subprocess calls
- No `eval()` or `exec()`
- All path inputs validated via `security.validate_path()`
- All URL inputs validated via `security.validate_url()`
- Must emit structured output via `utils.emit()`

## After scaffolding
1. Edit the generated file to implement tool logic
2. Add the registration snippet to `bs_cli.py`
3. Test with `--format human` first, then `--format json`
