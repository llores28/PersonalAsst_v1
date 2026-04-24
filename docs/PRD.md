# Product Requirements Document (PRD)

## 0) Metadata

- PRD version: 1.1
- Date: March 21, 2026 (updated)
- Owner: Repository owner (single-user project)
- Selected bootstrap tier: Team (Balanced)
- Wizard confidence: High
- Source artifacts:
  - `/bootstrap-wizard` output: Team tier selected, PII triggers regulated-data scenario
  - `bootstrap/Bootstrap-Project-Intake.md`: prototype stage, internal-only, high complexity
  - Selected bootstrap file: `bootstrap/2Team-ws-Bootstrap.md`
  - Detailed PRD: `PRD_PersonalAssistant.md` (18 sections, all gaps resolved)
  - Research: `RESEARCH_PersonalAssistant.md` (21 sections with gaps analysis)

## 1) Problem Statement

- **Current problem:** Managing personal productivity across email, calendar, files, and tasks requires switching between multiple apps and manual effort. No single assistant can operate across all these tools while learning user preferences and creating new capabilities on demand.
- **Why now:** OpenAI Agents SDK (2025) provides production-ready multi-agent orchestration. MCP protocol (Linux Foundation, 2025) standardizes tool integration. Mem0 open-source (2025) solves persistent memory. The stack is finally mature enough.
- **Business impact if unresolved:** Continued productivity loss from context-switching, missed follow-ups, manual scheduling, and inability to automate repetitive workflows.

## 2) Goals and Success Metrics

### Goals

1. Build a Telegram-based personal assistant that manages Google Workspace and remembers user preferences.
2. Enable the assistant to create its own tools (CLI-first) to handle new tasks without developer intervention.
3. Deliver a system safe and simple enough for a non-technical user, with all infrastructure self-hosted in Docker.

### Success metrics (measurable)

| Metric | Target | Phase |
|--------|--------|-------|
| Persona-consistent responses | 95% in-character | Phase 1 |
| Google Workspace queries return correct data | 100% match to API data | Phase 2 |
| Cross-session memory recall accuracy | >90% on preference queries | Phase 3 |
| Scheduled tasks survive container restart | 100% persistence | Phase 4 |
| Tool creation request → usable tool | <2 minutes | Phase 5 |
| Daily API spend never exceeds cap | Zero overspend incidents | Phase 1+ |
| Boot-to-ready time | <60 seconds | Phase 1 |

## 3) Non-goals

- Multi-user / multi-tenant support
- WhatsApp or Discord as primary UX (optional future adapters only)
- Running on cloud infrastructure (local Docker only)
- Fine-tuning LLM models
- Voice-first interaction (voice is transcribed to text, not real-time)
- Mobile app or web UI
- Enterprise compliance (SOC2, HIPAA, etc.)

## 4) Users and Stakeholders

- **Primary user:** Single non-technical user communicating via Telegram
- **Secondary users:** None (single-user system)
- **Internal stakeholders:** Repository owner / developer
- **External dependencies:** OpenAI API, OpenRouter API, Google Workspace APIs, Telegram Bot API

## 5) Scope

### In scope (MVP — Phases 1-3)

- Telegram bot with persona-consistent responses
- Google Workspace: Gmail, Calendar, Drive (read/write)
- Persistent memory (episodic, semantic, procedural) via Mem0
- Input/output guardrails (safety, PII)
- User authentication (Telegram ID allowlist)
- Cost tracking and daily caps
- Audit logging
- Web search (OpenAI WebSearchTool)

### Out of scope (later phases)

- Tool Factory (Phase 5) — dynamic CLI tool creation (COMPLETED)
- Scheduling automation (Phase 4) — APScheduler integration (COMPLETED)
- WhatsApp / Discord adapters (Phase 6)
- Voice message transcription (Phase 6)
- Self-improvement loop / ACE pattern (Phase 3 partial, Phase 6 full)

### Phase 7 — Persona Interview Onboarding (COMPLETED)

- Structured conversational interview via Telegram (3 sessions)
- LLM-powered personality synthesis (Big Five / OCEAN scoring)
- Deep persona profile schema (communication, work context, values)
- Interview progress tracking with resume capability
- Curator-driven periodic persona re-synthesis from accumulated memories
- Research basis: Stanford "Generative Agent Simulations" (2024), Cambridge/DeepMind Psychometric Framework (2025)

### Phase 8 — Organization Project Setup (COMPLETED)

- Goal-based project setup via `setup_org_project` tool
- Automated agent creation with skills and allowed tools
- Task generation and assignment workflow
- Dynamic CLI tool creation for organization projects
- System-binary tool support (FFmpeg, ImageMagick, etc.)
- Enhanced routing for organization and project requests
- Real-time validation feedback for tools and skills

### Phase 9 — Dashboard Enhancements (COMPLETED)

- AI-guided Tool Wizard dialog in Dashboard (interview → generate → review → save)
- Unified cost tracking via shared `record_llm_cost()` helper with single pricing table
- Duplicate detection (≥ 85% fuzzy-match) on agents, tools, and skills during org project setup
- Selective org deletion with preview dialog and holding org for retained entities
- Manual repair ticket creation from Dashboard (AI Agent or Admin pipeline)
- Interactions drill-down with audit-log drawer and direction filters
- Tasks vs Jobs clarity with tooltips and documentation
- Draggable/resizable Overview grid (react-grid-layout) with per-user Redis layout persistence

## 6) Functional Requirements

| ID | Requirement | Priority | Rationale | Acceptance Criteria |
|----|-------------|----------|-----------|-------------------|
| FR-001 | Telegram bot responds to user messages with persona-consistent replies | P0 | Core UX | Send 10 diverse messages; all get in-character replies |
| FR-002 | User authentication via Telegram ID allowlist | P0 | Security | Unauthorized users silently rejected; owner can `/allow` and `/revoke` |
| FR-003 | Input guardrail blocks prompt injection | P0 | Safety | Known injection patterns blocked; tripwire raised |
| FR-004 | Output guardrail detects PII in responses | P0 | Privacy | SSN/CC patterns redacted before sending to user |
| FR-005 | Cost tracking with daily cap enforcement | P0 | Budget control | At 100% cap → requests blocked; at 80% → user warned |
| FR-006 | Audit logging of every interaction | P0 | Observability | Every message logged with agent, tools, cost, duration |
| FR-007 | Gmail read/search/send/reply via agent | P1 | Productivity | "Read my latest emails" returns correct summaries |
| FR-008 | Calendar view/create/update events via agent | P1 | Productivity | "What's on my calendar today?" returns correct events |
| FR-009 | Drive search/upload/download files via agent | P1 | Productivity | "Find the budget spreadsheet" returns results |
| FR-010 | Persistent memory across sessions (Mem0) | P1 | Intelligence | "Remember I prefer mornings" → recalled in next session |
| FR-011 | Persona system with `/persona` command | P1 | Customization | User can change name, style, proactivity |
| FR-012 | Scheduled tasks with natural language input | P2 | Automation | "Remind me every Monday at 9am" creates persistent job |
| FR-013 | Tool Factory creates CLI tools on demand | P2 | Extensibility | User requests tool → generated, tested, registered in <2 min |
| FR-014 | Web search via OpenAI WebSearchTool | P1 | Information | "Search for X" returns relevant web results |
| FR-015 | `/help` command with capability list | P0 | UX | Returns formatted list of all available commands |
| FR-016 | Persona Interview Onboarding — structured conversational interview to build deep user personality profile | P1 | Intelligence | `/persona interview` launches 3-session interview; transcript synthesized into OCEAN + communication profile |
| FR-017 | OCEAN personality scoring — Big Five trait extraction from interview transcripts | P1 | Intelligence | LLM synthesis produces scores for Openness, Conscientiousness, Extraversion, Agreeableness, Neuroticism |
| FR-018 | Deep persona profile — expanded personality schema with communication style, work context, values | P1 | Customization | PersonaVersion.personality JSONB stores OCEAN, communication, work_context, values, synthesis fields |
| FR-019 | Persona interview progress tracking — multi-session interview state persisted across conversations | P1 | UX | Interview state stored in DB; user can resume interrupted sessions |
| FR-020 | Curator-driven persona re-synthesis — periodic update of persona profile from accumulated Mem0 memories | P2 | Intelligence | Curator weekly cycle includes persona profile refresh from recent memories |
| FR-021 | Organization Project Setup — goal-based project creation with automated agent/task/tool generation | P1 | Productivity | "Setup an FFmpeg Video Composer project" creates org, agents, tasks, and CLI tools in <2 minutes |
| FR-022 | System-Binary Tool Support — safe sandbox execution for FFmpeg, ImageMagick, sox, yt-dlp | P1 | Extensibility | Generated tools using system binaries pass sandbox tests and register successfully |
| FR-023 | Enhanced Routing — organization and project requests routed to MEDIUM complexity for proper tool access | P1 | UX | "Create a project to X" correctly invokes setup_org_project instead of falling through to GENERAL |
| FR-024 | Real-time Validation — immediate feedback on tool creation failures and skill registration issues | P1 | UX | Failed tool creations show specific error messages; validation stored in agent config |
| FR-025 | Tool Wizard — AI-guided tool creation via Dashboard (interview → generate → review → save) | P1 | UX | "AI Wizard" button in Tools tab launches dialog; tool registered on save |
| FR-026 | Unified Cost Tracking — shared `record_llm_cost()` helper with single pricing table | P1 | Maintainability | All agent calls tracked via one function; no duplicate pricing logic |
| FR-027 | Duplicate Detection — fuzzy-match ≥ 85% on agents/tools/skills during org project setup | P1 | Intelligence | "Setup X project" reuses existing items instead of duplicating; reports what was reused |
| FR-028 | Selective Org Deletion — preview dialog + holding org for retained entities | P1 | UX | User can check items to keep; retained entities moved to `__retained__` org |
| FR-029 | Manual Ticket Creation — open repair tickets from Dashboard with pipeline choice | P2 | UX | "New Ticket" button creates ticket; choose AI Agent or Admin pipeline |
| FR-030 | Interactions Drill-Down — clickable tile opens drawer with audit-log rows and filters | P1 | Observability | Click Interactions tile → drawer with all/inbound/outbound/errors filters |
| FR-031 | Draggable Dashboard Grid — customizable Overview layout persisted per user | P1 | UX | 6 tiles draggable/resizable; layout saved in Redis; "Reset Layout" restores defaults |

## 7) Non-Functional Requirements (NFR)

| ID | Category | Requirement | Target |
|----|----------|-------------|--------|
| NFR-001 | Availability | System runs reliably via Docker Compose | Best-effort; auto-restart on crash |
| NFR-002 | Performance | Response latency for simple queries | <5 seconds |
| NFR-003 | Security | No secrets in code, logs, or generated tools | Zero exposure incidents |
| NFR-004 | Security | All databases self-hosted in Docker only | Zero SaaS DB/memory API calls |
| NFR-005 | Security | Generated CLI tools cannot access API keys | Empty env in subprocess |
| NFR-006 | Operability | Single-command deployment | `docker compose up -d` starts everything |
| NFR-007 | Operability | Database migrations via Alembic | Schema changes are versioned and reversible |
| NFR-008 | Data | PII handling for Google Workspace data | Output guardrail + memory isolation |
| NFR-009 | Cost | API spend tracking and enforcement | Daily and monthly caps with alerts |
| NFR-010 | Maintainability | CLI-first tool creation over MCP | MCP only when agent discovery required |

## 8) Architecture and Constraints

- **System context:** Single Docker Compose stack: app + PostgreSQL + Qdrant + Redis + workspace-mcp + watchtower
- **Single async process:** Bot, agents, scheduler all in one Python process (AD-1 in PRD)
- **Integrations:** OpenAI Responses API, Google Workspace (12+ services via MCP), Telegram Bot API
- **Data classification:** PII (user emails, contacts, calendar via Google). Stored only in self-hosted PostgreSQL/Qdrant.
- **Tool creation hierarchy:** CLI (subprocess + argparse) → function_tool → MCP (fallback only)
- **Multi-tenant:** Not applicable (HC-5: single-user system)
- **SLA/RTO/RPO:** Not applicable (best-effort personal use)

See `PRD_PersonalAssistant.md` §3 for all 6 architectural decisions (AD-1 through AD-6).

## 9) Risks, Assumptions, Dependencies

### Risks

| Risk | Severity | Mitigation | Owner |
|------|----------|-----------|-------|
| OpenAI API outage | Medium | Graceful degradation message; cache recent context | Dev |
| API cost overrun | Medium | Daily/monthly caps + 80% alerts + audit tracking | Dev |
| Prompt injection | High | Input guardrail with dedicated fast model (gpt-4.1-nano) | Dev |
| Memory corruption | Low | Versioned memories; user `/forget` command; pruning | Dev |
| Tool Factory generates unsafe code | Medium | Sandbox + static analysis + LLM review + user approval | Dev |
| Google token expiry | Medium | Auto-refresh; alert user for re-auth via `/connect google` | Dev |
| Docker volume data loss | Low | Automated pg_dump backups; local volume persistence | Dev |

### Assumptions

- A1: User has Docker Desktop installed and running.
- A2: User has OpenAI API key with GPT-5.x model access.
- A3: User has Telegram account and can create a bot via @BotFather.
- A4: Google OAuth credentials are obtainable by the user (Google Cloud Console).
- A5: User's machine has at least 4GB RAM and 10GB disk for Docker stack.

### Dependencies

- D1: OpenAI Agents SDK (Python, open-source, `pip install openai-agents`)
- D2: aiogram 3.x (Telegram bot framework)
- D3: Mem0 open-source (`pip install mem0ai`)
- D4: Google Workspace MCP Server (`taylorwilsdon/google_workspace_mcp`)
- D5: APScheduler 4.x
- D6: SQLAlchemy 2.x + Alembic (database ORM + migrations)
- D7: Pydantic 2.x (settings and schema validation)

## 10) Release and Rollout Strategy

- **Rollout approach:** Phased implementation (6 phases, 12 weeks). Each phase has gate tests.
- **Feature flag strategy:** Not needed (single-user, features added incrementally).
- **Validation gates:** Per-phase acceptance criteria defined in `PRD_PersonalAssistant.md` §17.
- **Rollback triggers:** If tests fail after a phase, revert to last working commit. DB rollback via `alembic downgrade -1`.

## 11) Test and Verification Strategy

- **Unit tests:** Every new agent, tool, job callable, guardrail. Mock OpenAI API.
- **Integration tests:** Telegram handler → orchestrator → specialist agent (mocked LLM).
- **Smoke path:** `docker compose up -d` → bot responds to `/start` within 60 seconds.
- **Observability:** Audit log table + `/stats` command + OpenAI built-in tracing.
- **Definition of done:** Code + test + no secrets + works in Docker + Telegram UX tested.

## 12) Cohesion Matrix (anti-conflict)

| Requirement ID | Rule reference | AGENTS scope | Skill/Workflow reference | Verification method |
|---------------|---------------|-------------|------------------------|-------------------|
| FR-001 (persona replies) | `00-project-overview` | `src/agents/orchestrator.py` | setup-dev-environment | Send 10 messages test |
| FR-002 (allowlist) | `01-security-and-secrets` | `src/bot/handlers.py` | security-sweep | Unauthorized user test |
| FR-003 (input guardrail) | `01-security-and-secrets` | `src/agents/safety_agent.py` | security-sweep | Injection pattern test |
| FR-004 (PII guardrail) | `01-security-and-secrets` | `src/agents/safety_agent.py` | security-sweep | PII pattern test |
| FR-005 (cost cap) | `01-security-and-secrets` | `src/db/models.py` | incident-triage | Exceed cap test |
| FR-006 (audit log) | `02-change-safety-and-testing` | `src/db/models.py` | run-quality | Query audit_log test |
| NFR-004 (self-hosted DB) | `00-project-overview` HC-1 | `docker-compose.yml` | setup-dev-environment | No SaaS calls in code |
| NFR-010 (CLI-first tools) | `00-project-overview` HC-2 | `src/tools/` | create-cli-tool | Tool type decision test |

## 13) Conflict Register

| Conflict | Detected in | Proposed resolution | Owner | Due date | Status |
|----------|------------|-------------------|-------|----------|--------|
| X1: Tool Factory blocks `subprocess` but CLI wrappers need it | Research §15 vs §6 | Block applies to generated tool code only, not wrapper code (clarified in PRD §8) | Dev | Resolved | Closed |
| None other at drafting time | — | — | — | — | — |

## 14) Decisions and ADR Triggers

| Decision | Detail | Status |
|----------|--------|--------|
| AD-1: Single async process | Bot + agents + scheduler in one process | Decided |
| AD-2: Redis for active conversations | 30-min TTL, archival to PostgreSQL | Decided |
| AD-3: Handoff only for Tool Factory | All others use `as_tool()` | Decided |
| AD-4: Filesystem watcher for tool hot-reload | watchdog on `tools/` directory | Decided |
| AD-5: Tell user on error, no silent retry | User stays in control | Decided |
| AD-6: Sequential per-user message queue | asyncio.Queue per user_id | Decided |
| AD-7: Interview-based persona onboarding | Structured 3-session conversational interview for deep personality profiling (Stanford approach) | Decided |

**Potential future ADR triggers:**
- If response latency exceeds 10s consistently → consider multi-process separation
- If tool count exceeds 50 → consider MCP-based discovery over filesystem scan
- If user requests multi-user → requires full tenancy redesign
- If persona interview transcripts exceed context window → consider chunked synthesis or RAG approach

## 15) Open Questions

1. ~~All 23 gaps from research resolved in PRD~~ — None remaining.
2. **Persona Interview Onboarding** — How many interview sessions are optimal? Stanford research suggests 2 hours total. Current design uses 3 sessions of 5–10 minutes each. Adjust based on user feedback.
