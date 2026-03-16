---
name: webscrape
description: Fetch and extract content from external URLs for docs, APIs, and comparisons
---
# Webscrape

## Trigger
- Need to read external documentation
- Need to fetch API reference pages
- Need to crawl a documentation site for local reference
- User runs `/scrape`

## Commands
```
python bootstrap/cli/bs_cli.py scrape page <url>
python bootstrap/cli/bs_cli.py scrape api <url>
python bootstrap/cli/bs_cli.py scrape links <url>
python bootstrap/cli/bs_cli.py scrape docs <url> --depth 2
```

## When to use CLI vs native tools
- **Single page read**: prefer Cascade's `read_url_content` (fewer tokens, user-approved)
- **Batch crawl / docs site**: use CLI `scrape docs`
- **Extract structured links**: use CLI `scrape links`
- **JSON API fetch**: use CLI `scrape api`

## Security
- URLs validated: no private IPs, no file:// schemes, redirect chain checked
- Rate limited: max 2 req/s, max 50 pages per crawl
- Response size capped at 5MB
- Respects robots.txt

## Caching
Pages cached 1h, doc crawls cached 24h in `.cache/scrape/`.
