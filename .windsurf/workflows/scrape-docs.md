---
description: Fetch and summarize external documentation or API content
---
# Scrape Docs

## 1) Single page fetch
```
nexus scrape page <url> --format json
```

## 2) JSON API fetch
```
nexus scrape api <url> --format json
```

## 3) Extract links from a page
```
nexus scrape links <url> --format json
```

## 4) Crawl a documentation site
```
nexus scrape docs <url> --depth 2 --format json
```

## When to use native tools instead
- For a single page: prefer Cascade's `read_url_content` (fewer tokens, user-approved)
- For batch crawling or structured extraction: use this CLI tool

## Security notes
- All URLs validated against private IP ranges (SSRF protection)
- Rate limited: 2 req/s max, 50 pages per crawl
- Cached in `.cache/scrape/`
