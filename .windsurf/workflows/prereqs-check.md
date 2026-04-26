---
description: Check prerequisites and guide setup for missing components (Docker, extensions, Windsurf Docker Extension)
---
# Prerequisites Check

## 1) Run full prerequisites check
// turbo
```
nexus prereqs --format human
```

## 2) Review results

If all components show "ok", proceed to the next workflow.

If components are missing:
- For **auto-installable** items (Docker Desktop extensions): offer to install with user approval
- For **manual** items (Docker Desktop, Windsurf Docker Extension, Python): show the setup guide

## 3) Show guide for missing component (if needed)
```
nexus prereqs --guide --component <component-name>
```

## 4) For Docker Desktop extension auto-install (with user approval)
```
docker extension install ngrok/ngrok-docker-extension --force
```

## 5) Verify after setup
// turbo
```
nexus prereqs --format human
```
