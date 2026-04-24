---
trigger: glob
globs: ["**/package.json", "**/package-lock.json", "**/yarn.lock", "**/bun.lock"]
---

# Supply Chain Security Rule

## When This Rule Activates
This rule activates when Cascade reads or edits npm dependency files.

## Required Behavior

### On Any npm Dependency Change
When adding, updating, or reviewing npm dependencies:

1. **Check for known compromised versions** before recommending any package version.
2. **Never recommend** these known-compromised versions:
   - `axios@1.14.1` — backdoored with RAT via `plain-crypto-js`
   - `axios@0.30.4` — backdoored with RAT via `plain-crypto-js`
3. **Flag** if `plain-crypto-js` appears anywhere in dependencies — this is always malicious.
4. **Recommend pinning** exact versions instead of using `^` or `~` ranges for critical dependencies.

### Hardening Recommendations
When creating or editing `package.json`:
- Suggest adding an `overrides` block to pin known-safe versions of critical packages.
- Suggest creating `.npmrc` with `ignore-scripts=true` for CI/CD environments.
- Recommend committing lockfiles (`package-lock.json` / `yarn.lock`) to version control.

### If Compromise Is Suspected
Suggest running:
```
python bootstrap/cli/bs_cli.py supply-chain audit . --format human
python bootstrap/cli/bs_cli.py supply-chain ioc --format human
```

### Known Malicious Packages (Block List)
- `plain-crypto-js` — any version
- `@qqbrowser/openclaw-qbot@0.0.130`
- `@shadanai/openclaw@2026.3.31-1`
- `@shadanai/openclaw@2026.3.31-2`

### Known Malicious Infrastructure
- C2 domain: `sfrclak.com`
- C2 IP: `142.11.206.73`
