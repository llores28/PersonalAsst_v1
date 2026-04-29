# agent-poison-filter

Block **self-reinforcing failure loops** in LLM agent reflection + memory
recall paths. Framework-agnostic. ~250 LOC. Zero runtime dependencies.

## The problem

When an LLM agent fails to call a tool (transient bug, missing creds, MCP
hiccup, etc.) it usually produces a refusal text — *"I can't access your
Gmail right now"*. Reflective agent designs (Hermes, OpenClaw, Atlas, custom
ReAct loops) often summarize that turn as a "learned workflow" and store it
in long-term memory: *"When the user asks to check email, propose a Gmail
search query and ask them to paste the top result."*

Next turn, that learned workflow biases the model to refuse again — even
though the tool is now working. The failure compounds. This is the **OWASP
2026 "procedural drift"** failure mode and the [MINJA](https://arxiv.org/abs/2601.05504)
self-reinforcing injection class.

`agent-poison-filter` blocks the loop at both ends:

- **Write-time** (`is_poisoned_learning`): consult before storing any new
  reflector preference / workflow. Drops it if it's a learned refusal.
- **Read-time** (`filter_stale_memories`): consult before injecting recalled
  memories into a persona prompt. Drops poisoned entries when the relevant
  tool is currently operational.

## Install

```bash
pip install agent-poison-filter
```

## Usage

```python
from agent_poison_filter import is_poisoned_learning, filter_stale_memories

# WRITE TIME — call before storing reflector output
workflow = "Assistant cannot access the user's email in-chat; ask user to paste."
if not is_poisoned_learning(workflow):
    memory_store.add(workflow)

# READ TIME — call before injecting recalled memories into the prompt
memories = memory_store.search(user_message, top_k=10)
clean = filter_stale_memories(memories, workspace_connected=True)
prompt = build_persona_prompt(memories=clean)
```

## What gets blocked

A layered detection: literal substrings (catches phrasings already in your
store) + regex (catches the broader semantic family).

| Pattern | Example |
|---|---|
| Refusal verb + workspace noun | "I can't access your Gmail right now" |
| Workaround request | "ask the user to paste the email content" |
| Search-instead-of-tool | "propose a Gmail search query for the user" |
| In-chat / in-session limitation framing | "in this session the assistant was unable" |

## What survives the filter

Legitimate user preferences and factual workflow notes stay:

- *"User prefers concise email summaries with subject and one-line body"* ✓
- *"User wants morning calendar reviews on weekdays"* ✓
- *"Email delivery configuration has not been fully validated"* ✓

The connection-aware read-time filter (`filter_stale_memories`) keeps
*"can't access Gmail"* memories when the workspace is genuinely
disconnected — those memories are TRUE in that state.

## Compatibility

- Python ≥ 3.10
- Works with any agent framework: LangGraph, CrewAI, AutoGen, OpenAI Agents
  SDK, Claude Agent SDK, OpenClaw, Hermes Agent, custom ReAct loops
- Memory backend agnostic: Mem0, plain Redis lists, SQLite, in-memory dicts

## License

MIT — see `LICENSE`.

## Provenance

Extracted from [Atlas (PersonalAsst)](https://github.com/llores28/PersonalAsst)
where it shipped on 2026-04-28 to recover from a memory-poisoning incident.
The 2026-04-28 reflector wrote *"Assistant cannot access the user's email
in-chat; proposes Gmail search queries and asks user to paste the top
result for summarization"* to Mem0, which then biased every subsequent turn
to refuse. The filter has been hardened against that exact phrase + a
~15-pattern family of variants. See test suite for the regression cases.
