---
description: Run a supply chain security audit to detect compromised npm packages, system IOCs, and hardening gaps
---

# Supply Chain Security Audit

## Quick Scan (current project)
// turbo
1. Run the supply chain scanner on the current project:
```
python nexus/cli/bs_cli.py supply-chain audit . --format human
```

## System-Wide Scan
2. Run IOC check on the local system:
```
python nexus/cli/bs_cli.py supply-chain ioc --format human
```

3. Scan all projects under a parent directory:
```
python nexus/cli/bs_cli.py supply-chain scan D:\PyProjects --format human
```

## View Known Advisories
// turbo
4. List all tracked supply chain advisories:
```
python nexus/cli/bs_cli.py supply-chain advisories --format human
```

## If Compromised Packages Are Found

5. **Isolate** — Disconnect the affected system from the network immediately.

6. **Do NOT clean in place** — Rebuild from a known-good state.

7. **Rotate credentials**:
   - npm tokens
   - AWS access keys
   - SSH private keys
   - Cloud credentials (GCP, Azure)
   - CI/CD secrets
   - Any values in `.env` files accessible at install time

8. **Pin safe versions** in `package.json`:
   ```json
   {
     "overrides": {
       "axios": "1.14.0"
     }
   }
   ```

9. **Block C2 traffic** at the network level:
   - `142.11.206.73`
   - `sfrclak.com`

10. **Add `.npmrc` hardening** for CI/CD:
    ```
    ignore-scripts=true
    ```

11. **Re-audit** after remediation:
    ```
    python nexus/cli/bs_cli.py supply-chain audit . --format human
    ```
