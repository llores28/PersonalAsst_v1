---
name: security-sweep
description: Run a security review checklist for PersonalAsst
---

# Security Sweep

## Checklist

1. **Secrets in code**: `grep -r "sk-\|Bearer \|password=" src/ tests/ config/`
2. **Secrets in logs**: Search for any logging of env vars or tokens
3. **.env.example** has no real values (only placeholders)
4. **Generated tools** cannot access env vars (sandbox runs with empty env)
5. **Allowlist** is enforced — test with unauthorized Telegram ID
6. **Cost cap** blocks requests when exceeded
7. **PII guardrail** catches SSN/CC patterns in output
8. **Safety policies** in `config/safety_policies.yaml` are loaded and enforced
9. **Docker** runs app as non-root user
10. **OAuth tokens** stored in Docker volume, not in code or DB

## Commands

```bash
# Check for hardcoded secrets
grep -rn "sk-\|OPENAI_API_KEY\|BOT_TOKEN" src/ --include="*.py" | grep -v "os.environ\|settings\.\|\.env"

# Check gitignore covers sensitive files
cat .gitignore | grep -E "\.env|__pycache__|\.db"

# Verify non-root in Dockerfile
grep "USER" Dockerfile
```
