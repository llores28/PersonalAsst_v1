---
name: research-investigate
description: Research dependencies, docs, and APIs for informed architecture decisions
---
# Research Investigation

## Trigger
- Evaluating new dependencies
- Investigating unfamiliar APIs or patterns
- Architecture decisions requiring comparison
- User runs `/research`

## Commands
```
python bootstrap/cli/bs_cli.py research docs "<query>"
python bootstrap/cli/bs_cli.py research deps <package>
python bootstrap/cli/bs_cli.py research changelog <package>
python bootstrap/cli/bs_cli.py research compare <pkg-a> <pkg-b>
```

## When to use CLI vs native tools
- **Single web search**: use Cascade's `search_web` (fewer tokens)
- **Package info/CVEs/comparison**: use CLI `research deps` / `research compare`
- **Project docs search**: use CLI `research docs`
- **Changelog/release notes**: use CLI `research changelog`

## Caching
Results cached in `.cache/research/` with 24h TTL. Cached results noted in output.

## Stop conditions
- Rate limited to 10 requests/minute
- If registry API is unreachable, report and suggest manual check
