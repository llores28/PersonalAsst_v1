# ADR: Persona Interview Onboarding — Digital Clone via Structured Interview

**Date:** March 21, 2026  
**Status:** Accepted  
**Decision:** AD-7

## Context

The current persona system stores minimal data: assistant name, 3 personality traits (e.g., "helpful, proactive, concise"), and a communication style keyword ("friendly"). This gives the LLM almost nothing to work with for true personality replication — the user's values, communication patterns, decision-making style, life context, humor, and priorities are not captured.

The goal is to evolve PersonalAsst from a generic assistant into a **digital clone** — an agent that communicates, decides, and prioritizes like its owner.

## Research Basis

### 1. Stanford + Google DeepMind: "Generative Agent Simulations of 1,000 People" (Nov 2024)
- **Paper:** [arxiv.org/abs/2411.10109](https://arxiv.org/abs/2411.10109)
- A 2-hour semi-structured AI-conducted interview creates an agent that replicates a person's responses on the General Social Survey **85% as accurately** as the person replicates their own answers two weeks later.
- Interview transcripts are synthesized from multiple expert perspectives (psychologist, economist, sociologist) to create a higher-level personality profile.
- Interview-based agents are **more accurate and less biased** than agents given only demographic descriptions.
- **80% correlation on Big Five personality traits**, 66% on economic decision games.

### 2. Cambridge + Google DeepMind: Psychometric Framework for LLMs (Dec 2025)
- **Paper:** [Nature Machine Intelligence](https://doi.org/10.1038/s42256-025-01115-6)
- First scientifically validated personality test for LLMs using the **Big Five (OCEAN)** framework.
- LLM personality can be reliably measured and precisely shaped through prompts.
- Personality prompts carry through to real-world tasks (writing style, decision-making).

### 3. IgniteTech MyPersona (CES 2026)
- Commercial "digital twin" product. Key insight: limiting the knowledge domain reduces hallucination.

### 4. Tavus Digital Twins
- CEO quote: "How about you just talk to an AI interviewer for 30 minutes today, 30 minutes tomorrow? And then we use that to construct this digital twin of you."
- Incremental interviews over days work better than one marathon session.

## Decision

Implement a **3-session structured conversational interview** conducted by a dedicated `PersonaInterviewAgent` via Telegram. Each session is 5–10 minutes. After each session, an LLM synthesis step generates a progressively richer personality profile stored in the existing `PersonaVersion` JSONB column.

### Interview Structure

| Session | Focus | Duration | Questions |
|---------|-------|----------|-----------|
| 1 — Identity & Context | Who you are, what you do, how you prefer to communicate | 5–10 min | Name, role, daily work, communication preferences, common misunderstandings |
| 2 — Work Style & Values | How you work, decide, prioritize | 5–10 min | Typical day, overwhelm response, reminder preferences, autonomy level, recent decisions |
| 3 — Communication & Personality | How you express yourself, humor, boundaries | 5–10 min | Email voice, humor/emoji use, audience adaptation, sensitive topics, mood boosters |

### Persona Profile Schema (expanded)

```json
{
  "traits": ["helpful", "proactive", "concise"],
  "style": "friendly",
  "ocean": {
    "openness": 0.7,
    "conscientiousness": 0.8,
    "extraversion": 0.5,
    "agreeableness": 0.7,
    "neuroticism": 0.3
  },
  "communication": {
    "formality": "casual",
    "humor": "dry, occasional",
    "emoji_use": "minimal",
    "verbosity_preference": "concise",
    "email_tone": "professional but warm",
    "pet_peeves": ["unnecessary meetings", "vague requests"]
  },
  "work_context": {
    "role": "software engineer",
    "typical_day": "...",
    "priorities": ["deep work mornings", "meetings after 2pm"],
    "pain_points": ["email overload", "scheduling conflicts"]
  },
  "values": {
    "decision_style": "data-driven, decisive",
    "autonomy_preference": "high — handle it, report back",
    "sensitive_topics": ["health", "finances"]
  },
  "synthesis": "A concise, action-oriented professional who values...",
  "interview_sessions_completed": 2,
  "last_synthesis_date": "2026-03-21"
}
```

### Implementation Phases

1. **Schema expansion** — Add new fields to `PersonaVersion.personality` JSONB + new `persona_interviews` table for transcript storage
2. **PersonaInterviewAgent** — Dedicated agent with structured question flow, follow-up generation, and session management
3. **LLM synthesis** — Multi-perspective personality analysis (Stanford approach) producing OCEAN scores and structured profile
4. **Prompt integration** — `persona_mode.py` injects the richer profile into system prompts
5. **Telegram commands** — `/persona interview` to start/resume, onboarding trigger on first `/start`
6. **Curator integration** — Weekly re-synthesis from accumulated Mem0 memories

## Alternatives Considered

| Alternative | Why rejected |
|-------------|-------------|
| Survey/questionnaire (multiple choice) | Less information density than conversation. Stanford research shows interviews capture idiosyncrasies surveys miss. |
| Self-written bio | Stanford showed paragraph-length self-descriptions are less accurate than interviews. |
| Passive observation only | Takes weeks to accumulate enough data. Interview bootstraps the profile immediately. |
| Fine-tuning on user data | PRD non-goal. Also requires far more data than a 2-hour interview. |

## Tradeoffs

- **Pro:** Research-backed approach with 85% accuracy on personality replication.
- **Pro:** Works incrementally — each session improves the profile, not all-or-nothing.
- **Pro:** Reuses existing infrastructure (Mem0, PersonaVersion, Curator).
- **Con:** Requires ~15–30 min of user time across 3 sessions.
- **Con:** LLM synthesis adds API cost (one-time per session, ~$0.05–0.10).
- **Con:** OCEAN scores are approximate — LLM-derived, not psychometrically validated for this user.

## References

- Park, J.S. et al. "Generative Agent Simulations of 1,000 People." arXiv:2411.10109 (2024).
- Serapio-García, G. et al. "A psychometric framework for evaluating and shaping personality traits in large language models." Nature Machine Intelligence (2025).
- Stanford HAI: "AI Agents Simulate 1,052 Individuals' Personalities with Impressive Accuracy."
- MIT Technology Review: "AI can now create a replica of your personality." (Nov 2024).
