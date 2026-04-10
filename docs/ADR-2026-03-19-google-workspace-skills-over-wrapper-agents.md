# ADR-2026-03-19 — Google Workspace Skills Over Wrapper Agents

## Status
Accepted

## Context
PersonalAsst previously exposed Google Workspace through four wrapper agents:
- `EmailAgent`
- `CalendarAgent`
- `TasksAgent`
- `DriveAgent`

For connected Google accounts, the first three agents were thin wrappers around deterministic `function_tool` calls that already bound `user_google_email` and called `call_workspace_tool()` directly.

This created an unnecessary architecture pattern:
- orchestrator LLM decides to call a specialist agent
- specialist agent LLM decides to call one deterministic Google tool
- Google tool performs the actual MCP-backed operation

That pattern added extra latency and token cost without adding meaningful reasoning value for Gmail, Calendar, or Google Tasks. It also made cross-tool flows harder because a request like "draft an email with my flight info from my calendar" required multiple nested reasoning hops instead of one orchestrator turn calling tools sequentially.

Earlier MCP lifecycle issues also showed that sub-agent wrapping increased integration complexity for connected Google tools.

## Decision
Flatten connected Google Workspace capabilities onto the orchestrator as direct skills/tools for:
- Gmail
- Google Calendar
- Google Tasks

Keep the existing connected wrapper builders and attach their tools directly to the orchestrator when `connected_google_email` is available:
- `_build_connected_gmail_tools()`
- `_build_connected_calendar_tools()`
- `_build_connected_tasks_tools()`

Update orchestrator instructions and persona action-policy guidance to reference direct skill tools instead of wrapper tool names such as `manage_email` and `manage_calendar`.

Keep `DriveAgent` temporarily as an agent-backed tool:
- Drive still depends on raw MCP exposure instead of connected `function_tool` wrappers
- the repo does not yet define a deterministic direct Drive skill contract comparable to Gmail, Calendar, and Tasks

Retain the old specialist-agent fallback path only for the non-connected Google state.

## Consequences
### Positive
- removes one LLM hop for connected Gmail, Calendar, and Tasks operations
- makes cross-tool orchestration simpler because the orchestrator can call multiple Google tools in one reasoning loop
- reduces token cost and latency for connected Google flows
- eliminates connected sub-agent MCP lifecycle complexity for Gmail, Calendar, and Tasks
- keeps the existing direct-handler fast paths unchanged

### Tradeoffs
- increases the orchestrator's direct tool surface area
- requires prompt guidance to stay aligned with the direct tool names
- leaves Drive temporarily inconsistent until direct Drive skill wrappers are added
- means some legacy tests that assumed wrapper-agent creation must be updated

## Boundary
This ADR does not remove all specialist agents.

Keep separate agents when they need a distinct reasoning mode or isolation boundary, such as:
- `SchedulerAgent`
- `RepairAgent`
- `ToolFactoryAgent`
- `ReflectorAgent`

These agents are not simple deterministic Google API wrappers and still benefit from separate prompts and routing boundaries.
