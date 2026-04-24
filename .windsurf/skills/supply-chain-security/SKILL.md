---
name: supply-chain-security
description: Run a supply chain security audit to detect compromised npm packages, system IOCs, and hardening gaps
---
# Supply Chain Security Audit

## When to Use
- After adding or updating npm/Python dependencies
- Periodic security review
- When `supply-chain-security` rule fires (on package.json edits)
- User runs `/supply-chain-audit`

## Commands

### Full package scan
```
python bootstrap/cli/bs_cli.py supply-chain scan . --format human
```

### IOC check (known malicious domains/IPs)
```
python bootstrap/cli/bs_cli.py supply-chain ioc --format human
```

### Detailed audit report
```
python bootstrap/cli/bs_cli.py supply-chain audit . --format human
```

### Check advisories for a specific package
```
python bootstrap/cli/bs_cli.py supply-chain advisories <package> --format human
```

## What It Checks
- **Known compromised packages**: axios backdoor versions, shadanai/openclaw malware
- **IOC detection**: C2 domains/IPs in lockfiles, package metadata, scripts
- **Hardening gaps**: missing `ignore-scripts`, unpinned ranges, missing lockfile
- **`plain-crypto-js` presence**: always malicious, any version

## Known Block List
- `plain-crypto-js` — any version (always malicious)
- `axios@1.14.1` and `axios@0.30.4` — RAT backdoor
- `@shadanai/openclaw@2026.3.31-1` and `@2026.3.31-2`
- `@qqbrowser/openclaw-qbot@0.0.130`

## Stop Conditions
- If any blocked package is found: **stop, alert user, do not proceed with install**
- If C2 infrastructure is detected: escalate to security sweep immediately

## After Scan
1. Review all HIGH/CRITICAL findings
2. Remove or pin flagged packages
3. Add `overrides` block in `package.json` for critical deps
4. Re-run scan to confirm clean
