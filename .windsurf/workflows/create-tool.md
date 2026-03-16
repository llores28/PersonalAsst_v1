---
description: Scaffold a new project-specific CLI tool from template
---
# Create Tool

## 1) Identify the need
Ask user what repetitive task they want to automate.

## 2) Scaffold the tool
```
python bootstrap/cli/bs_cli.py scaffold <tool-name> --description "What it does" --format json
```

## 3) Implement the tool logic
Edit the generated file at `bootstrap/cli/tools/<tool-name>.py`.
The template includes security imports and structured output boilerplate.

## 4) Register the tool
Add the registration snippet (from scaffold output) to `bootstrap/cli/bs_cli.py`.

## 5) Test
```
python bootstrap/cli/bs_cli.py <tool-name> --format human
```

## Guardrails
- Generated tools inherit `security.py` validators
- Template blocks `shell=True`, `eval()`, `exec()`
- All output must use `utils.emit()` for structured JSON
