---
description: Local container validation and shareable preview via Docker Desktop
---
# Local Environment

## 1) Prerequisites check
// turbo
```
nexus prereqs --component docker --format json
```

## 2) Initialize Docker files (if needed)
```
nexus local-env init --format json
```

## 3) Build the image
```
nexus local-env build --format json
```

## 4) Start containers
```
nexus local-env up --format json
```

## 5) Visual verification
If health check passes, use Cascade's `browser_preview` tool on the reported localhost URL.

## 6) Pre-production readiness check
// turbo
```
nexus local-env validate --format json
```

## 7) Share with others (if requested)
Instruct user to use Docker Desktop:
1. Open Docker Desktop
2. Find the running container
3. Click the ngrok extension or Release Share button
4. Share the generated URL

Cascade cannot drive Docker Desktop GUI extensions directly.

## 8) Teardown
```
nexus local-env down --format json
```
