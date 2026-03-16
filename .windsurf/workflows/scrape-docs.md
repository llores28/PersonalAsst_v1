---
description: Fetch and summarize external documentation or API content
---
# Scrape Docs

## 1) Single page fetch
```
python bootstrap/cli/bs_cli.py scrape page <url> --format json
```

## 2) JSON API fetch
```
python bootstrap/cli/bs_cli.py scrape api <url> --format json
```

## 3) Extract links from a page
```
python bootstrap/cli/bs_cli.py scrape links <url> --format json
```

## 4) Crawl a documentation site
```
python bootstrap/cli/bs_cli.py scrape docs <url> --depth 2 --format json
```

## When to use native tools instead
- For a single page: prefer Cascade's `read_url_content` (fewer tokens, user-approved)
- For batch crawling or structured extraction: use this CLI tool

## Security notes
- All URLs validated against private IP ranges (SSRF protection)
- Rate limited: 2 req/s max, 50 pages per crawl
- Cached in `.cache/scrape/`
