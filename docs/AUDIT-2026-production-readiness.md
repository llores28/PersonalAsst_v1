# Atlas Personal Assistant — Production Readiness Audit

**Date:** 2026-04-12  
**Scope:** Full system (src/, tests/, deployment, docs)  
**Environment:** Local Docker Compose (from repo state, not live telemetry)  
**Scale Expectations:** Single-user, moderate tool-heavy workloads  
**Auditor:** Cascade (AI pair-programmer)  
**Confidence Level:** HIGH — based on comprehensive code review of all critical paths

---

## ✅ Production Readiness Score: 62 / 100

| Dimension | Weight | Score | Weighted |
|---|---|---|---|
| Functional Correctness | 20% | 75 | 15.0 |
| Security (OWASP baseline) | 20% | 52 | 10.4 |
| Test Coverage / Reliability | 15% | 65 | 9.75 |
| Operational Resilience | 15% | 48 | 7.2 |
| Performance / Scalability | 10% | 70 | 7.0 |
| Deployment / DevOps | 10% | 68 | 6.8 |
| Observability / Monitoring | 5% | 55 | 2.75 |
| Documentation / Maintainability | 5% | 60 | 3.0 |
| **Total** | **100%** | — | **61.9 ≈ 62** |

---

## 🚨 Critical Issues (Must Fix Before Production)

### C1. Dashboard API Authentication Is Trivially Spoofable
**Severity:** CRITICAL  
**Files:** `src/orchestration/api.py`  
**Evidence:** All mutating endpoints (persona update, budget update, org CRUD, agent CRUD, task management) authenticate via `X-Telegram-Id` HTTP header with no signature, HMAC, or token validation. Any HTTP client can impersonate the owner by setting this header.

```python
x_telegram_id: Optional[int] = Header(default=None, alias="X-Telegram-Id")
```

**Impact:** Full unauthorized access to persona modification, budget manipulation, org management, and repair ticket approval.  
**Fix:** Add JWT or API key authentication. Minimum viable: validate requests against a shared secret or issue session tokens via Telegram OAuth.

### C2. Owner Telegram ID Exposed in Public Endpoint
**Severity:** CRITICAL  
**File:** `src/orchestration/api.py:530-534`  
**Evidence:** The `/api/config` endpoint returns `owner_telegram_id` with no authentication required. Combined with C1, an attacker can discover the owner's ID and use it to authenticate all API calls.

```python
@app.get("/api/config")
async def get_config():
    """Return public dashboard configuration (no auth required)."""
    return {"owner_telegram_id": int(os.getenv("OWNER_TELEGRAM_ID", "0"))}
```

**Fix:** Remove this endpoint or require authentication.

### C3. No Rate Limiting on Any Endpoint
**Severity:** HIGH  
**Files:** `src/orchestration/api.py`, `src/bot/handlers.py`  
**Evidence:** Grep for `rate.?limit|throttl|RateLimiter` returns zero results across the entire codebase. Both the Dashboard API and Telegram bot handlers have no rate limiting.  
**Impact:** Abuse via API results in unbounded OpenAI API costs (the cost cap only applies per-user per-day, and the API doesn't consistently enforce it). DoS risk on the single-process async server.  
**Fix:** Add `slowapi` or similar rate limiter to FastAPI. Add per-user message throttling in the Telegram handler queue.

### C4. Safety Guardrail Fails Open
**Severity:** HIGH  
**File:** `src/agents/safety_agent.py:343-345`  
**Evidence:** When the LLM safety check call fails (network error, API timeout, etc.), the guardrail returns `tripwire_triggered=False`:

```python
except Exception as e:
    logger.error("Safety check LLM call failed: %s", e)
    # Fail open — don't block the user if the safety check itself fails
```

**Impact:** Any OpenAI API outage or rate limit disables all safety checks. Prompt injection patterns not caught by the fast pattern matcher will pass through.  
**Fix:** Add a fallback decision. If the LLM check fails, block messages that match partial injection heuristics OR require a second attempt before allowing through.

---

## ⚠️ Important Improvements

### I1. `orchestrator.py` Is a 2533-Line God Module
**File:** `src/agents/orchestrator.py` (2533 lines)  
**Impact:** Maintenance risk, merge conflicts, cognitive load. Contains: email parsing (~200 lines), calendar formatting (~200 lines), Google Tasks flow (~260 lines), Gmail send flow (~200 lines), repair routing, cost tracking, trace recording, background job detection, parallel fan-out, complexity classification, registry caching, persona prompt building.  
**Recommendation:** Split into focused modules:
- `src/agents/gmail_handler.py` — direct Gmail check/send flows
- `src/agents/calendar_handler.py` — direct calendar check/formatting
- `src/agents/tasks_handler.py` — Google Tasks direct flows
- `src/agents/orchestrator_core.py` — agent construction + `run_orchestrator()`
- Keep routing constants and helpers co-located with their consumers.

### I2. Duplicate Function Definition
**File:** `src/agents/orchestrator.py`  
**Evidence:** `_format_connected_gmail_write_error()` is defined identically at **line 823** and again at **line 1595**. The second definition shadows the first.  
**Fix:** Remove the duplicate at line 1595.

### I3. MCP Connection-Per-Call Overhead
**File:** `src/integrations/workspace_mcp.py:96-163`  
**Evidence:** Every `call_workspace_tool()` invocation creates a new `MCPServerStreamableHttp`, calls `connect()`, calls `call_tool()`, then calls `cleanup()`. For an email check (search + batch fetch), this means 2 full TCP connect/disconnect cycles.  
**Impact:** Latency and resource waste. For parallel multi-domain fan-out, this multiplies.  
**Recommendation:** Implement a connection pool or reuse connections within a single `run_orchestrator()` invocation.

### I4. `ast.literal_eval()` on User-Influenced Data
**File:** `src/orchestration/api.py` (lines 3949, 4020, 4171), `src/skills/loader.py:253`  
**Evidence:** `ast.literal_eval()` is used to parse list-like strings from query parameters and skill config files. While safer than `eval()`, it still parses arbitrary Python literals and can cause `ValueError` or `MemoryError` on crafted inputs.  
**Fix:** Use `json.loads()` instead, or validate the input format with a regex first.

### I5. No Retry Logic for External Service Calls
**Files:** `src/integrations/workspace_mcp.py`, `src/agents/orchestrator.py`  
**Evidence:** All MCP tool calls and OpenAI API calls have no retry-with-backoff logic. A single transient failure (network hiccup, 429 rate limit, 503 service unavailable) causes immediate failure.  
**Fix:** Add `tenacity` or a simple retry decorator with exponential backoff for MCP and OpenAI calls.

### I6. No Timeout on MCP Tool Calls
**File:** `src/integrations/workspace_mcp.py:120`  
**Evidence:** `server.call_tool(tool_name, clean_args)` has no `asyncio.wait_for()` wrapper. A hung MCP sidecar will block the entire user message flow indefinitely.  
**Impact:** User queue worker blocks forever; all subsequent messages for that user are queued but never processed.  
**Fix:** Wrap MCP calls in `asyncio.wait_for(server.call_tool(...), timeout=30)`.

---

## 🧹 Refactoring Opportunities

### R1. Inline Imports (Low Priority)
**Scope:** ~40+ `from ... import` statements inside function bodies across `orchestrator.py`, `handlers.py`, and `handler_utils.py`.  
**Reason:** Used to break circular imports. Acceptable pattern, but makes dependency graphs opaque.  
**Suggestion:** Long-term, consider a dependency injection container or restructure module boundaries.

### R2. Magic Strings for Repair Ticket Statuses
**Files:** `src/repair/engine.py`, `src/orchestration/api.py`, `src/db/models.py`  
**Evidence:** Ticket statuses are bare strings: `"open"`, `"plan_ready"`, `"debug_analysis_ready"`, `"ready_for_deploy"`, `"deployed"`, `"verification_failed"`. No enum or central definition.  
**Fix:** Create a `TicketStatus` enum in `src/repair/models.py` and use it everywhere.

### R3. Hardcoded Model Pricing in orchestrator.py
**File:** `src/agents/orchestrator.py:2340-2349`  
**Evidence:** `_MODEL_PRICING` dict is hardcoded inline:
```python
_MODEL_PRICING: dict[str, tuple[float, float]] = {
    "gpt-5.4-pro":  (3.75, 15.00),
    "gpt-5.4":      (3.00, 12.00),
    ...
}
```
**Fix:** Move to `settings.py` or a config file. Pricing changes with every model release.

### R4. Fire-and-Forget `asyncio.create_task()` Without Error Handling
**Files:** `src/agents/orchestrator.py` (lines 2174, 2187, 2289, 2488), `src/memory/conversation.py:103`  
**Evidence:** Multiple fire-and-forget tasks (reflector, session compaction) with no result collection or error callback. While each has internal try/except, if the task is garbage-collected before completion, its exception is silently lost.  
**Fix:** Store task references and add a `task.add_done_callback()` that logs exceptions.

### R5. Per-User Queue Memory Growth
**File:** `src/bot/handlers.py:26-57`  
**Evidence:** Worker cleanup happens after 30 minutes idle. In the single-user scenario this is fine, but the pattern creates unbounded dict growth if the system is ever extended.  
**Impact:** Low for current single-user deployment.

---

## 📈 Performance & Scaling Risks

### P1. Single-Process Async Runtime
**Impact:** LOW for single-user. All request handling, scheduler, and background jobs share one event loop. A CPU-intensive operation (e.g., large email parsing regex) would block everything.  
**Mitigation:** Already uses async I/O throughout. The single-user constraint means this is acceptable.

### P2. Database Connection Pool Size
**File:** `src/db/session.py:10`  
```python
engine = create_async_engine(settings.database_url, echo=False, pool_size=5, max_overflow=10)
```
**Assessment:** Adequate for single-user. Pool of 5 with overflow to 15 handles concurrent scheduler + bot + API traffic.

### P3. Registry Cache TTL of 30 Seconds
**File:** `src/agents/orchestrator.py:58`  
**Assessment:** Good optimization. Prevents repeated DB/Mem0/plugin discovery on rapid follow-ups. 30s is a reasonable balance between freshness and performance.

### P4. Selective Skill Injection
**Assessment:** Excellent optimization. Reduces tool count from 50+ to ~10-20 per request based on keyword matching. Reduces prompt size and LLM confusion.

### P5. Scheduler DB Sync Every 30 Seconds
**File:** `src/scheduler/engine.py:48-56`  
**Assessment:** Polls `ScheduledTask` table every 30s. Acceptable for single-user. Would not scale to multi-tenant.

---

## 🔐 Security Risks

### S1. Dashboard API — No Real Authentication (see C1, C2)
**OWASP A01: Broken Access Control**  
The entire Dashboard API relies on a spoofable `X-Telegram-Id` header. Combined with the public `/api/config` endpoint that exposes the owner's Telegram ID, any network-adjacent attacker has full API access.

### S2. CORS Configuration Allows All Methods and Headers
**File:** `src/orchestration/api.py:136-141`
```python
app.add_middleware(
    CORSMiddleware,
    allow_origins=_parse_allowed_origins(...),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
```
**Impact:** While origins are restricted, `allow_methods=["*"]` and `allow_headers=["*"]` with `allow_credentials=True` weakens CORS protections. A compromised allowed origin can make any request.  
**Fix:** Restrict to `["GET", "POST", "PUT", "DELETE"]` and specific headers.

### S3. Playwright in Production Image
**File:** `Dockerfile:9-11`
```dockerfile
ENV PLAYWRIGHT_BROWSERS_PATH=/opt/playwright
RUN playwright install --with-deps chromium
```
**Impact:** Chromium in the production image is a large attack surface (~400MB). If an attacker gains container access, they have a full browser for further exploitation.  
**Recommendation:** Run browser tools in a separate sidecar container with tighter network restrictions.

### S4. Repair Engine Command Execution
**File:** `src/repair/engine.py:203-226`  
**Assessment:** WELL DEFENDED. Uses a strict allowlist (`_ALLOWED_COMMAND_PREFIXES`), disallows shell tokens (`&&`, `||`, `;`, `|`, etc.), uses `shlex.split()`, and runs via `create_subprocess_exec()` (not `shell=True`). Path traversal is blocked by `_resolve_repo_path()`. This is a **strength**.

### S5. Security Challenge Gate
**File:** `src/security/challenge.py`  
**Assessment:** WELL IMPLEMENTED. PIN/security questions stored hashed (SHA-256). TTL-based expiry in Redis. Required for all destructive repair operations. This is a **strength**.

### S6. PII Guardrails
**File:** `src/agents/safety_agent.py`  
**Assessment:** Good three-layer check (context-aware → output-marker → owner email). However, regex-based PII detection has known false negatives (non-US formats, partial matches). Acceptable for single-user personal assistant.

### S7. Prompt Injection Protection
**File:** `src/agents/safety_agent.py:22-32`  
**Assessment:** Two-layer approach (pattern matching + LLM check) is solid. However, the pattern list is static and can be bypassed with encoding tricks (base64, Unicode homoglyphs). The fail-open behavior (C4) weakens this.

---

## 🔍 Observability Gaps

### O1. No Structured Logging
**Evidence:** All modules use Python `logging` with plain text format. No JSON structured logging, no correlation IDs, no request tracing.  
**Impact:** Log analysis in Docker is manual `grep`. Cannot correlate a user request across bot → orchestrator → MCP → scheduler.  
**Fix:** Add `python-json-logger` or `structlog`. Include `request_id` and `user_id` in all log records.

### O2. No Health Endpoint for Main Assistant Container
**File:** `docker-compose.yml`  
**Evidence:** The `assistant` service has no health check defined. Only `postgres` and `redis` have health checks. The `orchestration-api` has a health check (`/api/health`).  
**Impact:** Docker cannot detect if the bot is stuck or crashed without checking container exit status.  
**Fix:** Add a lightweight HTTP health endpoint (e.g., `aiohttp` on an internal port) or use a file-based liveness probe.

### O3. No Metrics or Alerting
**Evidence:** No Prometheus, StatsD, or any metrics collection. Quality scoring is tracked in Redis but not exposed as metrics.  
**Impact:** No visibility into: request latency, error rates, OpenAI API latency, MCP call success rates, queue depth.  
**Recommendation:** For single-user, this is low priority. Consider adding basic metrics if the system grows.

### O4. Agent Trace Recording Is Best-Effort
**File:** `src/agents/orchestrator.py:2377-2436`  
**Evidence:** Trace recording is wrapped in try/except with `logger.debug`. If it fails, no trace is recorded and no alert is raised. The trace schema depends on internal SDK types (`FunctionCallItem`, `ToolCallOutputItem`) which may change between SDK versions.

---

## 🚀 Deployment Risks

### D1. No Image Tagging or Versioning
**File:** `docker-compose.yml`  
**Evidence:** Images are built from local context with no tag. Every `docker compose build` creates a new unversioned image.  
**Fix:** Add version tagging: `docker compose build --tag assistant:$(git rev-parse --short HEAD)`.

### D2. Alembic Migrations Run at Startup (Conditional)
**File:** `src/main.py`  
**Evidence:** Migrations run if `STARTUP_MIGRATIONS_ENABLED=true`. This is a **safety gate** — good. However, there's no rollback automation if a migration fails mid-apply.  
**Assessment:** Acceptable for single-user. 8 migration files exist with a clean linear chain.

### D3. No CI/CD Pipeline
**Assessment:** Acknowledged as intentional for single-user project. Pre-commit checklist exists in docs. Manual `docker compose build && docker compose up -d` is the deployment method.

### D4. Volume Mounts for Plugin Hot-Reload
**File:** `docker-compose.yml`  
```yaml
volumes:
  - ./src/tools/plugins:/app/src/tools/plugins
```
**Impact:** Plugins are mounted from the host, allowing hot-reload. This is convenient but means a host-level compromise directly affects the running container's code.  
**Recommendation:** In production, bake plugins into the image instead of volume mounting.

---

## 🛡️ Operational Resilience

### OR1. No Circuit Breakers
**Evidence:** No circuit breaker pattern for OpenAI API or MCP calls. If OpenAI is down, every user message will attempt an API call, wait for timeout (or hang indefinitely for MCP), and fail.  
**Fix:** Add circuit breaker (e.g., `pybreaker`) that opens after N consecutive failures and returns a cached/fallback response.

### OR2. Graceful Degradation — Partial
**Strengths:**
- RedisSession failure → runs without session memory (graceful)
- Dynamic persona failure → falls back to static persona (graceful)
- Reflector failure → silently skipped (acceptable)
- Cost tracking failure → silently skipped (acceptable)

**Gaps:**
- MCP failure → error message but no retry or fallback
- OpenAI API failure → exception propagates to user as generic error
- Scheduler failure → `start_in_background()` raises, potentially blocking bot startup

### OR3. Scheduler Lifecycle
**File:** `src/scheduler/engine.py:41-64`  
**Evidence:** Scheduler uses `__aenter__`/`__aexit__` directly (not `async with`). If `start_scheduler()` fails after `__aenter__`, the cleanup path via `stop_scheduler()` may not run.  
**Fix:** Use `async with` context manager or ensure cleanup in a `finally` block.

### OR4. Data Backup Strategy
**Assessment:** Documented in `RUNBOOK.md` with `pg_dump` commands. Automated backup via `src/scheduler/backup.py`. Qdrant data in Docker volume. Redis is ephemeral (acceptable — sessions rebuild).

---

## 📋 Architecture Inventory Summary

| Component | Files | Lines (approx) | Tests |
|---|---|---|---|
| Orchestrator | 1 | 2533 | test_orchestrator.py |
| Safety Agent | 1 | 376 | test_safety_agent.py |
| Repair Engine | 1 | 1170 | test_repair_engine.py, test_repair_flow.py |
| Scheduler | 2 | ~500 | test_scheduler.py |
| Bot Handlers | 2 | ~2120 | test_bot_handlers_resilience.py |
| Dashboard API | 1 | ~4200 | test_orchestration_api_*.py |
| Workspace MCP | 1 | 252 | test_workspace.py |
| Memory/Persona | 3 | ~700 | test_memory.py, test_persona_interview.py |
| DB Models | 1 | 382 | (covered by integration tests) |
| Agent Files | 28 total | — | 43 test files total |
| Alembic Migrations | 8 | — | — |
| Docker Services | 7 | — | — |

---

## 🏁 Final Verdict

**CONDITIONAL GO — with blockers**

Atlas is a well-architected single-user personal assistant with impressive feature breadth: 28 agents, 50+ tools, Google Workspace integration, self-healing repair pipeline, dynamic persona profiling, and autonomous background jobs. The codebase demonstrates strong security instincts (command allowlists, path traversal protection, PII guardrails, security challenges).

However, **4 issues must be resolved before production deployment:**

1. **Dashboard API authentication** (C1+C2) — the entire management API is unprotected
2. **Rate limiting** (C3) — unbounded cost exposure via API abuse
3. **Safety guardrail fail-open** (C4) — OpenAI outage disables all safety checks
4. **MCP call timeout** (I6) — hung sidecar blocks all user messages permanently

The orchestrator's 2533-line size (I1) is a maintenance risk but not a blocker. The single-user constraint makes many scalability concerns acceptable.

---

## 🎯 Top 5 Prioritized Next Actions

| # | Action | Impact | Effort | Category |
|---|---|---|---|---|
| 1 | **Add real auth to Dashboard API** — JWT/API key, remove `/api/config` owner ID exposure | Eliminates C1+C2 | Medium (1-2 days) | Security |
| 2 | **Add rate limiting** — `slowapi` on API, per-user throttle on Telegram handlers | Eliminates C3, cost protection | Low (half day) | Security |
| 3 | **Fix safety guardrail fail-open** — fail closed or require retry on LLM check failure | Eliminates C4 | Low (1 hour) | Security |
| 4 | **Add MCP call timeout** — `asyncio.wait_for(call_tool(...), timeout=30)` | Eliminates I6, prevents permanent hangs | Low (1 hour) | Resilience |
| 5 | **Split orchestrator.py** — extract Gmail/Calendar/Tasks handlers into separate modules | Reduces I1 maintenance risk | Medium (1 day) | Maintainability |

---

*This audit was performed via static code analysis of the full repository. No live system testing was conducted. Findings should be verified against the running Docker Compose stack.*
