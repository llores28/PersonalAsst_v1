---
description: Structured research session for dependencies, docs, and architecture decisions
---
# Research

## 1) Search project docs first
// turbo
```
nexus research docs "<query>" --format json
```

## 2) Research a specific package
```
nexus research deps <package-name> --format json
```

## 3) Check recent changes / changelog
```
nexus research changelog <package-name> --format json
```

## 4) Compare two packages
```
nexus research compare <pkg-a> <pkg-b> --format json
```

## 5) Broader web search (if CLI results insufficient)
Use Cascade's native `search_web` tool for broader context.

## 6) Summarize findings
Provide: recommendation, key tradeoffs, risks, and evidence sources.
