# agent-action-policy

Framework-agnostic **4-tier action classifier** for LLM agents — distinguishes
*"summarize my inbox"* (read) from *"send my email to alice@x.com"*
(external_side_effect) so your agent's approval gate fires at the right
moment, not on every turn or never.

## The four tiers

| Tier | Examples | Approval gate |
|---|---|---|
| `read` | "what's on my calendar today", "summarize my inbox" | none — always allowed |
| `draft` | "draft an email to bob", "compose a tweet" | none — drafts are reversible |
| `internal_write` | "save this to my notes", "create a reminder" | optional — internal data only |
| `external_side_effect` | "send the email", "post the tweet", "delete that calendar event" | **required** — explicit confirmation |

## Why a separate library

Most agent frameworks bundle approval gating into their tool framework — you
either approve everything (annoying) or approve nothing (unsafe). This
package gives you the classifier as a standalone deterministic Python
function. Bring your own approval UI: Telegram inline button, web modal,
CLI prompt, or auto-confirm-by-policy.

Used by [Atlas (PersonalAsst)](https://github.com/llores28/PersonalAsst)
in production since 2026-04 to gate Gmail send + Calendar write tools.

## Install

```bash
pip install agent-action-policy
```

## Usage

```python
from agent_action_policy import classify_action_request

decision = classify_action_request("send the email to alice@example.com")
print(decision.action_class)  # "external_side_effect"
print(decision.reason)        # "phrasing matches send-email cue"
print(decision.requires_confirmation)  # True

decision = classify_action_request("what's on my calendar today")
print(decision.action_class)  # "read"
print(decision.requires_confirmation)  # False
```

Drop-in for system prompt injection:

```python
from agent_action_policy import (
    should_append_action_policy_context,
    append_action_policy_context,
)

if should_append_action_policy_context(user_message):
    user_message = append_action_policy_context(user_message)
agent.run(user_message)
```

## Compatibility

- Python ≥ 3.10
- Works with any agent framework: LangGraph, OpenAI Agents SDK, Claude
  Agent SDK, OpenClaw, Hermes, custom ReAct
- Pairs with [`agent-poison-filter`](https://pypi.org/project/agent-poison-filter/)
  and [`mem0-park-scoring`](https://pypi.org/project/mem0-park-scoring/)
  for a complete agent-safety baseline

## License

MIT — see `LICENSE`.
