# Windsurf Model Selection Reference

This file is a decision database for selecting the optimal Cascade model based on task complexity.
It is read on-demand by the `00-token-efficiency.md` rule, NOT loaded into every prompt.

> **Source**: https://docs.windsurf.com/windsurf/models | Windsurf Changelog
> **Last updated**: March 2026
> **Excludes**: BYOK models (user brings own API key)
> **Note**: Promotional pricing may change. Check the model selector in Windsurf IDE for current costs.

---

## Model Database

### Tier 0 — Free (0 quota cost)

| Model | Performance Level | Speed | Best For |
|---|---|---|---|
| **SWE-1.5** | Near Claude 4.5 | 13x faster than Claude | Default for all tasks. Best free model available. |
| **SWE-1.5 Free** | Same as SWE-1.5 | Standard throughput | Same intelligence, slower speed. Free-tier default. |
| **SWE-1** | Claude 3.5-level | Fast | Legacy free model. Use SWE-1.5 instead. |
| **Grok Code Fast** | Good | Very fast | Quick code generation (free promotional, may change). |

**Internal models** (not user-selectable, always free):
- `SWE-1-mini` — Powers Windsurf Tab autocomplete (always free, unlimited).
- `swe-grep` — Powers Fast Context / code search (always free).

### Tier 1 — Low Cost (0.25x–0.5x credits)

| Model | Credit Cost | Performance | Best For |
|---|---|---|---|
| **Minimax M2.5** | 0.25x | Good | Budget external model for simple tasks. |
| **GPT-5 Low Thinking** | 0.5x | Strong | Cost-effective GPT-5 for moderate complexity. |
| **Gemini 3.1 Pro Low Thinking** | 0.5x | Strong | Cost-effective Gemini for moderate complexity. |
| **Kimi K2** | 0.5x | Good | Budget external model, good for routine work. |

### Tier 2 — Standard Cost (0.75x–1x credits)

| Model | Credit Cost | Performance | Best For |
|---|---|---|---|
| **GLM-5** | 0.75x | Good | Mid-range tasks needing external model quality. |
| **GPT-5 Med Thinking** | 1x | Very Strong | Complex multi-file edits, refactoring. |
| **Gemini 3.1 Pro High Thinking** | 1x | Very Strong | Large codebase reasoning, long context. |

### Tier 3 — Premium (2x–3x credits)

| Model | Credit Cost | Performance | Best For |
|---|---|---|---|
| **Claude Sonnet 4.6** | 2x | Frontier | Complex architecture, nuanced reasoning. |
| **Claude Opus 4.6** | 2x | Frontier+ | Most capable. Complex design, security review. |
| **GPT-5 High Thinking** | 2x | Frontier | Deep reasoning, complex debugging. |
| **Claude Sonnet 4.6 (Thinking)** | 3x | Frontier | Extended reasoning with chain-of-thought. |
| **Claude Opus 4.6 (Thinking)** | 3x | Frontier+ | Maximum capability with extended reasoning. |

### Tier 4 — Ultra Premium (10x+ credits)

| Model | Credit Cost | Performance | Best For |
|---|---|---|---|
| **Claude Opus 4.6 Fast (no thinking)** | 10x | Frontier+ | Opus intelligence at 2.5x output speed. |
| **Claude Opus 4.6 Fast (thinking)** | 12x | Frontier+ | Maximum speed + intelligence. Rarely justified. |

---

## Task Complexity Classification

### Level 1 — Simple (use Tier 0: SWE-1.5)
- Rename variables, fix typos, add comments
- Simple code formatting or linting fixes
- Add/remove imports
- Small boilerplate generation (single file)
- Read/explain code snippets
- Simple file creation from clear specification
- Git operations (commit messages, branch naming)
- Ask general coding questions

### Level 2 — Moderate (use Tier 0: SWE-1.5, or Tier 1 if SWE-1.5 struggles)
- Multi-file edits with clear patterns
- Standard CRUD endpoint implementation
- Write unit tests for existing functions
- Refactor function with clear requirements
- Debug with clear error message/stack trace
- Add validation logic
- Update dependencies and fix breaking changes
- Database migration scripts

### Level 3 — Complex (use Tier 1–2: GPT-5 Low/Med or Gemini 3.1 Pro)
- Multi-file refactoring across modules
- Design pattern implementation
- Complex debugging (race conditions, memory leaks)
- API integration with authentication flows
- Performance optimization requiring analysis
- Complex regex or algorithm implementation
- CI/CD pipeline creation from scratch
- Schema design and data modeling

### Level 4 — Expert (use Tier 2–3: GPT-5 Med/High or Claude Sonnet 4.6)
- Architecture design decisions
- Security audit and vulnerability analysis
- Complex distributed system debugging
- Large-scale codebase migration
- Novel algorithm implementation
- Compliance-sensitive code review
- System design with tradeoff analysis

### Level 5 — Frontier (use Tier 3: Claude Opus 4.6 or Claude Sonnet 4.6 Thinking)
- Novel architecture for unprecedented requirements
- Deep security/threat modeling
- Complex multi-system integration design
- Research-level algorithmic challenges
- Critical production incident with cascading failures

---

## Selection Algorithm

```
START
│
├─ Is this a Tab completion / autocomplete?
│  └─ YES → SWE-1-mini (always free, automatic)
│
├─ Is this a code search / Fast Context lookup?
│  └─ YES → swe-grep (always free, automatic)
│
├─ Is this a simple edit, explanation, or routine task? (Level 1-2)
│  └─ YES → SWE-1.5 (free, near-frontier performance)
│       └─ If SWE-1.5 output is unsatisfactory → GPT-5 Low Thinking (0.5x)
│
├─ Is this a complex multi-file task? (Level 3)
│  └─ YES → Start with SWE-1.5
│       └─ If insufficient → GPT-5 Low Thinking (0.5x)
│       └─ If still insufficient → GPT-5 Med Thinking (1x)
│
├─ Is this an expert-level task? (Level 4)
│  └─ YES → GPT-5 Med Thinking (1x) or Gemini 3.1 Pro High (1x)
│       └─ If insufficient → Claude Sonnet 4.6 (2x) or GPT-5 High (2x)
│
├─ Is this a frontier-level task? (Level 5)
│  └─ YES → Claude Sonnet 4.6 (2x) or Claude Opus 4.6 (2x)
│       └─ If extended reasoning needed → Claude Opus 4.6 Thinking (3x)
│
└─ NEVER use Tier 4 (10x+) unless explicitly requested by user
```

---

## Cost Optimization Strategies

### Strategy 1: Escalation Pattern (Default)
Always start with the cheapest model that could handle the task, then escalate only if output quality is insufficient.
1. Try **SWE-1.5** first (free)
2. If unsatisfied, switch to **GPT-5 Low** (0.5x)
3. If still unsatisfied, switch to **GPT-5 Med** or **Gemini 3.1 Pro** (1x)
4. Only use premium (2x+) for genuinely complex tasks

### Strategy 2: Same-Model Caching
Windsurf caches context per-model. Switching models mid-session wastes cached tokens.
- Pick one model per session and stick with it.
- If you need a premium model, start the session with it rather than switching mid-conversation.

### Strategy 3: Session Splitting
- Use **short sessions** for simple tasks (SWE-1.5, 2-3 messages).
- Use **dedicated sessions** for complex tasks (premium model, focused scope).
- Avoid long conversations that accumulate context and increase token cost.

### Strategy 4: Mode Selection
- **Tab completions**: Always free (SWE-1-mini). Use for inline edits.
- **Command mode (Ctrl+I)**: Free / minimal cost. Use for simple inline edits.
- **Plan mode**: Use before Code mode to reduce wasted tool calls.
- **Chat mode**: Lower cost than Code mode (no file writes). Use for Q&A.
- **Code mode**: Full agentic capability. Use when file edits are needed.

### Strategy 5: Context Minimization
- Smaller context = fewer tokens = less quota consumed.
- Close unnecessary files before starting Cascade.
- Use `.codeiumignore` to exclude large files from indexing.
- Be precise in prompts — avoid "look at the whole codebase" requests.

---

## Quick Reference Card

| Task Type | Recommended Model | Cost | Why |
|---|---|---|---|
| Quick edit / typo fix | Ctrl+I or SWE-1.5 | Free | Overkill to use premium |
| Code explanation / Q&A | SWE-1.5 (Chat mode) | Free | Near-frontier intelligence |
| Write unit tests | SWE-1.5 | Free | Pattern-based, SWE-1.5 excels |
| Multi-file refactor | SWE-1.5 → GPT-5 Low | Free→0.5x | Escalate only if needed |
| Complex debugging | GPT-5 Low/Med | 0.5x–1x | Reasoning depth matters |
| API integration | GPT-5 Med / Gemini 3.1 | 1x | Multi-step planning needed |
| Architecture design | Claude Sonnet 4.6 | 2x | Nuanced tradeoff analysis |
| Security audit | Claude Opus 4.6 | 2x | Maximum capability needed |
| Deep research task | Claude Opus 4.6 Thinking | 3x | Extended chain-of-thought |

---

## Notes

- **Promotional pricing** may change without notice. Always verify in the IDE model selector.
- **Enterprise plans** use per-request credit costs (different from self-serve token-based pricing).
- **SWE-1.5** replaced SWE-1 as the default model in Windsurf (Wave 13, Dec 2025).
- **Arena Mode** lets you compare two models side-by-side on the same task — useful for evaluating if a cheaper model is sufficient.
- **Auto-Continue** counts as a new prompt credit per continue. Keep prompts focused to avoid hitting the 20 tool-call limit.
