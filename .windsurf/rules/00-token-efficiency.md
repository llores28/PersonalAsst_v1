---
trigger: always_on
---
# Token Efficiency (Quota Conservation)

## Context & Tool Discipline
- Use `code_search` (Fast Context) for initial exploration before reading files.
- Read files in large chunks (500+ lines) to avoid multiple small reads.
- Do not re-read files already in the conversation context.
- Batch independent tool calls in parallel.
- When running commands, prefer short read-only commands first.
- Do not run tests automatically unless asked — suggest the command for the user to run.

## Response Discipline
- Keep responses concise — avoid restating what the user already knows.
- For simple edits, suggest the user use Ctrl+I (Command mode, no quota cost).
- Prefer `model_decision` or `glob` trigger for non-critical rules over `always_on`.

## Model Selection — INTERACTIVE (Use ask_user_question)

**IMPORTANT**: When you detect a task that would benefit from a more capable model, you MUST use the `ask_user_question` tool to present model options to the user. Do NOT just mention it in text — present an interactive choice.

### Authority Model
Use a hybrid authority model:
- Use `nexus/model-selection-reference.md` for strategy, complexity mapping, and cost/quality heuristics.
- Use the Windsurf model selector as the source of truth for exact currently available model names and pricing.
- If the two differ, keep the strategy from `nexus/model-selection-reference.md` but present the current Windsurf model names to the user.

### Complexity Ladder
Use this progression so recommendations scale with task difficulty and cost:

| Complexity | Typical Work | Default Recommendation |
|-----------|--------------|------------------------|
| **Low** | Typos, small edits, explanations, simple boilerplate | Stay on **SWE-1.5** or another current low-cost/latest coding model |
| **Moderate** | Multi-file edits, routine refactors, standard debugging, test updates | Prefer **SWE-1.5** first; optionally suggest a newer low-cost/latest model only if the task looks likely to exceed SWE-1.5 comfort level |
| **Complex** | Architecture changes, cross-module refactors, security hardening | Suggest a stronger current model such as **GPT-5 Medium Thinking** or a comparable latest mid-cost model |
| **Expert** | Security audits, deep debugging, system design, high-stakes reasoning | Suggest a top-tier current model such as **Claude Sonnet 4.6 Thinking** or **GPT-5 High** |
| **Frontier** | Novel architecture, advanced threat modeling, deep research | Suggest the strongest available current model such as **Claude Opus 4.6 Thinking** |

### When to Trigger Model Selection
Assess the user's request. If it matches these patterns AND the current model is likely underpowered for the task, present options.

| Task Type | Indicators | Recommended Model |
|-----------|------------|-------------------|
| **Low** | Small edits, quick questions, minor cleanup | Stay on **SWE-1.5** or current low-cost/latest model |
| **Moderate** | Routine multi-file work, standard debug, normal tests | Usually stay on **SWE-1.5**; optionally suggest a low-cost latest upgrade if needed |
| **Complex** | Architecture, refactoring across modules, security hardening | **GPT-5 Medium Thinking** or comparable latest mid-cost model |
| **Expert** | Security audit, deep debugging, system design | **Claude Sonnet 4.6 Thinking** or **GPT-5 High** |
| **Frontier** | Novel architecture, threat modeling, research | **Claude Opus 4.6 Thinking** |

### How to Present Options
Use the `ask_user_question` tool with options like:

```
Question: "This looks like a [moderate/complex/expert/frontier] task. Which model would you prefer? (Note: You'll need to manually switch in the model menu)"

Options:
1. "[Recommended Model] — Best fit for this task"
2. "Stay on current model — lower cost, possibly lower quality"
3. "Let me clarify the task first"
```

Prefer recommendations that are:
- the **latest available** model appropriate for the task tier
- the **lowest-cost** model that is still likely to handle the task well
- aligned with the Windsurf model menu as the source of truth for exact names and current pricing

After user selects, respond with:
- If they chose a different model: "Great choice! Please switch to [Model] in the model menu for optimal results."
- If they stayed: "Understood. I'll proceed with the current model."

### Reminder Logic
- If the user selects a model different from the current one, explicitly remind them that the selection does **not** switch models automatically.
- Use direct wording like: "You selected [Model]. Please switch to it in the Windsurf model menu before I continue if you want me to use it."
- If the user keeps chatting without switching, give one brief reminder before proceeding.
- Do not repeatedly nag the user after the first reminder.

### When NOT to Trigger
- Simple tasks (edits, typos, explanations) — stay on SWE-1.5 or the current low-cost/latest model
- Moderate tasks that SWE-1.5 can reasonably handle without quality risk
- User explicitly said to use current model
- Already on a capable model for the task type
- Quick questions or clarifications

### Escalation Pattern
Start with SWE-1.5 or another low-cost/latest model.
Escalate only when task complexity, risk, or expected reasoning depth warrants it.
Stick to one model per session to leverage context caching.

Reference: `nexus/model-selection-reference.md` for the project's model database, but prefer the Windsurf model selector for the most current names and pricing.
