---
description: Guided debugging session using systematic investigation tools
---
# Debug Investigation

## 1) Identify the error
Ask user for the error message, log output, or failing test.

## 2) Check environment basics
// turbo
```
python bootstrap/cli/bs_cli.py debug env --format json
```
// turbo
```
python bootstrap/cli/bs_cli.py debug ports --format json
```

## 3) Check dependencies
// turbo
```
python bootstrap/cli/bs_cli.py debug deps --format json
```

## 4) Trace error origin
```
python bootstrap/cli/bs_cli.py debug trace "<error message from step 1>"
```

## 5) Scan logs (if applicable)
```
python bootstrap/cli/bs_cli.py debug logs <log-path-or-directory>
```

## 6) Secrets scan (always run before any commit suggestion)
// turbo
```
python bootstrap/cli/bs_cli.py debug secrets-scan --format json
```

## 7) Narrow and fix
Based on findings:
1. Identify root cause file + line
2. Apply minimal fix (prefer upstream fix over downstream workaround)
3. Re-run the failing test or `/smoketest` to verify

## 8) Report
Summarize: root cause, fix applied, verification result.
