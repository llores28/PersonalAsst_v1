"""Persona Interview Agent — structured 3-session conversational interview.

Based on Stanford's "Generative Agent Simulations of 1,000 People" (2024)
and Cambridge/DeepMind's psychometric framework for LLMs (2025).

Conducts a structured interview across 3 sessions to build a deep
personality profile (OCEAN scores, communication style, work context, values).
"""

import logging
from datetime import datetime
from typing import Optional

from sqlalchemy import select, update

from src.settings import settings

logger = logging.getLogger(__name__)

# ── Interview Session Definitions ─────────────────────────────────────

INTERVIEW_SESSIONS: dict[int, dict] = {
    1: {
        "title": "Identity & Context",
        "description": "Getting to know who you are, what you do, and how you communicate.",
        "questions": [
            {
                "id": "name",
                "text": "Let's start simple — what should I call you? And what do you do for work or day-to-day?",
                "follow_up": "That's interesting. What does a typical workday look like for you?",
            },
            {
                "id": "communication",
                "text": "How do you prefer people communicate with you? For example, do you like detailed explanations or just the bottom line? Do you prefer formal or casual?",
                "follow_up": "Got it. And when someone sends you information, what format helps you absorb it fastest — bullet points, paragraphs, summaries?",
            },
            {
                "id": "productivity",
                "text": "What are you trying to get better at, or what takes up too much of your time?",
                "follow_up": "If I could take one thing off your plate entirely, what would it be?",
            },
            {
                "id": "misunderstood",
                "text": "What's something people often get wrong about you, or assume incorrectly?",
                "follow_up": "How does that affect how you want me to interact with you?",
            },
            {
                "id": "expectations",
                "text": "What would make a personal assistant truly useful to you? What's your dream scenario?",
                "follow_up": None,
            },
        ],
    },
    2: {
        "title": "Work Style & Values",
        "description": "Understanding how you work, make decisions, and handle your day.",
        "questions": [
            {
                "id": "daily_routine",
                "text": "Walk me through a typical busy day — from when you wake up to when you wind down. What are the key moments?",
                "follow_up": "When during the day are you at your sharpest? And when do you usually hit a wall?",
            },
            {
                "id": "overwhelm",
                "text": "When you're overwhelmed with tasks or requests, what's your instinct? Do you prioritize ruthlessly, delegate, or push through everything?",
                "follow_up": "How would you want me to help when you're in that mode?",
            },
            {
                "id": "reminders",
                "text": "What kind of reminders and nudges actually help you versus annoy you? Be honest — some people hate being reminded.",
                "follow_up": "How about morning briefings or daily summaries — would those be useful or noise?",
            },
            {
                "id": "autonomy",
                "text": "If I could handle things on your behalf — like drafting emails, scheduling meetings, organizing files — would you want me to just do it and report back, or always ask first?",
                "follow_up": "Are there specific things you'd never want me to do without asking?",
            },
            {
                "id": "decisions",
                "text": "Think of a decision you made recently that you felt good about. What was it and what made it the right call?",
                "follow_up": "How do you usually make decisions — gut feeling, data, advice from others, or a mix?",
            },
        ],
    },
    3: {
        "title": "Communication & Personality",
        "description": "How you express yourself, your humor, and your boundaries.",
        "questions": [
            {
                "id": "email_voice",
                "text": "If I had to write an email as you — to a colleague, a boss, or a friend — what would it sound like? Can you describe the vibe or give me an example?",
                "follow_up": "Do you sign off emails a certain way? Any phrases you always use or always avoid?",
            },
            {
                "id": "humor",
                "text": "Do you use humor in your day-to-day communication? If so, what kind — sarcastic, dry, goofy, punny? Or do you keep things straight?",
                "follow_up": "How about emojis — do you use them, and if so, which ones?",
            },
            {
                "id": "audience_adapt",
                "text": "How does the way you communicate change depending on who you're talking to — colleagues, friends, family, strangers?",
                "follow_up": "Are there people you communicate with regularly that I should know about?",
            },
            {
                "id": "boundaries",
                "text": "Are there topics or areas that are off-limits or sensitive for you? Things I should never bring up or be extra careful about?",
                "follow_up": "And on the flip side — are there topics you love talking about or always want to hear about?",
            },
            {
                "id": "mood_boosters",
                "text": "Last one — what's something that always makes your day better? A type of music, a routine, a person, an activity?",
                "follow_up": "Perfect. If you could describe your ideal relationship with a personal assistant in one sentence, what would it be?",
            },
        ],
    },
}

SESSION_INTRO_MESSAGES: dict[int, str] = {
    1: (
        "👋 Welcome to the Persona Interview!\n\n"
        "I'm going to ask you some questions to really understand who you are — "
        "your communication style, your work habits, and what matters to you. "
        "This helps me become a much better assistant, tailored specifically to you.\n\n"
        "**Session 1 of 3: Identity & Context** (about 5–10 minutes)\n\n"
        "There are no wrong answers — just be yourself. Ready? Let's go."
    ),
    2: (
        "Welcome back! 🎯\n\n"
        "**Session 2 of 3: Work Style & Values** (about 5–10 minutes)\n\n"
        "This time I want to understand how you work, make decisions, and handle your day. "
        "Let's dive in."
    ),
    3: (
        "Final session! 🏁\n\n"
        "**Session 3 of 3: Communication & Personality** (about 5–10 minutes)\n\n"
        "This one is about how you express yourself — your voice, humor, and boundaries. "
        "After this, I'll have a deep understanding of who you are."
    ),
}

SESSION_COMPLETE_MESSAGES: dict[int, str] = {
    1: (
        "✅ **Session 1 complete!** Great start — I'm already learning a lot about you.\n\n"
        "When you're ready for Session 2 (Work Style & Values), just say "
        "`/persona interview` again. No rush — pick it up whenever."
    ),
    2: (
        "✅ **Session 2 complete!** Your work style and values are becoming clear.\n\n"
        "One more session to go. When you're ready for Session 3 "
        "(Communication & Personality), say `/persona interview`."
    ),
    3: (
        "✅ **All 3 sessions complete!** 🎉\n\n"
        "I now have a deep understanding of who you are. I'm synthesizing everything "
        "into a personality profile that will shape how I communicate with you, "
        "make decisions, and prioritize your requests.\n\n"
        "You can update your profile anytime with `/persona interview`."
    ),
}

# ── LLM Synthesis Prompt ──────────────────────────────────────────────

SYNTHESIS_SYSTEM_PROMPT = """\
You are an expert personality analyst combining perspectives from multiple disciplines.
Given an interview transcript between a user and their personal assistant,
produce a structured personality profile.

Analyze the transcript from these expert perspectives:
1. **Communication psychologist**: How do they prefer to give and receive information?
2. **Productivity coach**: What are their work patterns, energy cycles, and pain points?
3. **Personality assessor**: Where do they fall on the Big Five (OCEAN) traits?
4. **Executive assistant**: What would a great assistant do differently for this person?

Output ONLY valid JSON matching this exact schema:
{
  "ocean": {
    "openness": <float 0.0-1.0>,
    "conscientiousness": <float 0.0-1.0>,
    "extraversion": <float 0.0-1.0>,
    "agreeableness": <float 0.0-1.0>,
    "neuroticism": <float 0.0-1.0>
  },
  "communication": {
    "formality": "<casual|friendly|professional|formal>",
    "humor": "<description of humor style or 'none'>",
    "emoji_use": "<none|minimal|moderate|frequent>",
    "verbosity_preference": "<very_concise|concise|moderate|detailed>",
    "email_tone": "<description>",
    "pet_peeves": ["<list of communication pet peeves>"]
  },
  "work_context": {
    "role": "<job title or description>",
    "typical_day": "<brief summary>",
    "peak_hours": "<when they're most productive>",
    "priorities": ["<list of top priorities>"],
    "pain_points": ["<list of pain points>"]
  },
  "values": {
    "decision_style": "<description>",
    "autonomy_preference": "<low|medium|high> — <brief explanation>",
    "sensitive_topics": ["<list>"],
    "motivators": ["<list of things that energize them>"]
  },
  "synthesis": "<2-3 sentence summary of this person's personality and how an assistant should interact with them>"
}

Be specific and grounded in what the user actually said. Do not infer or fabricate
details that are not supported by the transcript. If information is missing for a
field, use reasonable defaults and note the uncertainty.
"""


# ── Interview State Management ────────────────────────────────────────

async def get_interview_state(user_id: int) -> dict:
    """Get the current interview state for a user.

    Returns:
        {
            "has_started": bool,
            "current_session": int (1-3),
            "current_question_index": int,
            "sessions_completed": list[int],
            "interview_id": int | None,
        }
    """
    from src.db.session import async_session
    from src.db.models import PersonaInterview

    db_user_id = await _get_db_user_id(user_id)
    if db_user_id is None:
        return {
            "has_started": False,
            "current_session": 1,
            "current_question_index": 0,
            "sessions_completed": [],
            "interview_id": None,
        }

    async with async_session() as session:
        result = await session.execute(
            select(PersonaInterview)
            .where(PersonaInterview.user_id == db_user_id)
            .order_by(PersonaInterview.session_number)
        )
        interviews = result.scalars().all()

    if not interviews:
        return {
            "has_started": False,
            "current_session": 1,
            "current_question_index": 0,
            "sessions_completed": [],
            "interview_id": None,
        }

    completed = [i.session_number for i in interviews if i.status == "completed"]
    in_progress = [i for i in interviews if i.status == "in_progress"]

    if in_progress:
        current = in_progress[0]
        transcript = current.transcript or []
        # Count user answers (every other message after the first question)
        user_answers = sum(1 for t in transcript if t.get("role") == "user")
        session_questions = INTERVIEW_SESSIONS[current.session_number]["questions"]
        # Each question can have a follow-up, so question_index = user_answers // 2
        # (main question + follow-up = 2 user answers per question)
        question_index = min(user_answers // 2, len(session_questions) - 1)

        return {
            "has_started": True,
            "current_session": current.session_number,
            "current_question_index": question_index,
            "sessions_completed": completed,
            "interview_id": current.id,
            "awaiting_follow_up": (user_answers % 2 == 1),
        }

    # All interviews completed or we need the next session
    next_session = max(completed) + 1 if completed else 1
    if next_session > 3:
        next_session = 3  # All done

    return {
        "has_started": len(completed) > 0,
        "current_session": next_session,
        "current_question_index": 0,
        "sessions_completed": completed,
        "interview_id": None,
        "all_complete": len(completed) >= 3,
    }


async def start_interview_session(user_id: int, session_number: int) -> int:
    """Create a new interview session row. Returns the interview ID."""
    from src.db.session import async_session
    from src.db.models import PersonaInterview

    db_user_id = await _get_db_user_id(user_id)
    if db_user_id is None:
        raise ValueError(f"No DB user found for telegram_id={user_id}")

    async with async_session() as session:
        interview = PersonaInterview(
            user_id=db_user_id,
            session_number=session_number,
            status="in_progress",
            transcript=[],
        )
        session.add(interview)
        await session.commit()
        await session.refresh(interview)
        logger.info(
            "Interview session %d started for user %d (id=%d)",
            session_number, user_id, interview.id,
        )
        return interview.id


async def append_to_transcript(
    interview_id: int, role: str, content: str
) -> None:
    """Append a message to an interview transcript."""
    from src.db.session import async_session
    from src.db.models import PersonaInterview

    async with async_session() as session:
        result = await session.execute(
            select(PersonaInterview).where(PersonaInterview.id == interview_id)
        )
        interview = result.scalar_one_or_none()
        if interview is None:
            logger.error("Interview %d not found", interview_id)
            return

        transcript = list(interview.transcript or [])
        transcript.append({"role": role, "content": content})
        interview.transcript = transcript
        await session.commit()


async def complete_interview_session(interview_id: int) -> None:
    """Mark an interview session as completed."""
    from src.db.session import async_session
    from src.db.models import PersonaInterview

    async with async_session() as session:
        await session.execute(
            update(PersonaInterview)
            .where(PersonaInterview.id == interview_id)
            .values(status="completed", completed_at=datetime.utcnow())
        )
        await session.commit()
    logger.info("Interview session %d completed", interview_id)


async def save_session_synthesis(interview_id: int, synthesis: dict) -> None:
    """Store the LLM synthesis result for an interview session."""
    from src.db.session import async_session
    from src.db.models import PersonaInterview

    async with async_session() as session:
        await session.execute(
            update(PersonaInterview)
            .where(PersonaInterview.id == interview_id)
            .values(synthesis=synthesis)
        )
        await session.commit()
    logger.info("Synthesis saved for interview %d", interview_id)


async def get_all_transcripts(user_id: int) -> list[dict]:
    """Get all completed interview transcripts for a user."""
    from src.db.session import async_session
    from src.db.models import PersonaInterview

    db_user_id = await _get_db_user_id(user_id)
    if db_user_id is None:
        return []

    async with async_session() as session:
        result = await session.execute(
            select(PersonaInterview)
            .where(
                PersonaInterview.user_id == db_user_id,
                PersonaInterview.status == "completed",
            )
            .order_by(PersonaInterview.session_number)
        )
        interviews = result.scalars().all()

    return [
        {
            "session_number": i.session_number,
            "transcript": i.transcript,
            "synthesis": i.synthesis,
        }
        for i in interviews
    ]


# ── Interview Flow Controller ─────────────────────────────────────────

async def handle_interview_message(
    user_id: int, user_message: str
) -> str:
    """Process a user message during an active interview session.

    Returns the next question or a session-complete message.
    """
    state = await get_interview_state(user_id)

    # All sessions complete — offer restart
    if state.get("all_complete"):
        return (
            "You've already completed all 3 interview sessions! 🎉\n\n"
            "Your personality profile is active and shaping how I interact with you.\n"
            "Would you like to redo the interview to update your profile? "
            "Say **yes** to start fresh."
        )

    session_num = state["current_session"]
    interview_id = state.get("interview_id")
    session_def = INTERVIEW_SESSIONS[session_num]
    questions = session_def["questions"]

    # Start new session if needed
    if interview_id is None:
        interview_id = await start_interview_session(user_id, session_num)
        intro = SESSION_INTRO_MESSAGES[session_num]
        first_q = questions[0]["text"]

        await append_to_transcript(interview_id, "assistant", intro)
        await append_to_transcript(interview_id, "assistant", first_q)

        return f"{intro}\n\n{first_q}"

    # Record user's answer
    await append_to_transcript(interview_id, "user", user_message)

    # Determine where we are in the question flow
    awaiting_follow_up = state.get("awaiting_follow_up", False)
    q_index = state["current_question_index"]

    if awaiting_follow_up:
        # User answered a follow-up — move to next question
        next_q_index = q_index + 1
        if next_q_index >= len(questions):
            # Session complete
            await complete_interview_session(interview_id)

            # Run synthesis
            synthesis = await _synthesize_session(user_id, interview_id, session_num)
            if synthesis:
                await save_session_synthesis(interview_id, synthesis)
                await _apply_synthesis_to_persona(user_id, synthesis, session_num)

            return SESSION_COMPLETE_MESSAGES[session_num]
        else:
            next_q = questions[next_q_index]["text"]
            await append_to_transcript(interview_id, "assistant", next_q)
            return next_q
    else:
        # User answered main question — ask follow-up if exists
        follow_up = questions[q_index].get("follow_up")
        if follow_up:
            await append_to_transcript(interview_id, "assistant", follow_up)
            return follow_up
        else:
            # No follow-up, move to next question
            next_q_index = q_index + 1
            if next_q_index >= len(questions):
                await complete_interview_session(interview_id)

                synthesis = await _synthesize_session(
                    user_id, interview_id, session_num
                )
                if synthesis:
                    await save_session_synthesis(interview_id, synthesis)
                    await _apply_synthesis_to_persona(user_id, synthesis, session_num)

                return SESSION_COMPLETE_MESSAGES[session_num]
            else:
                next_q = questions[next_q_index]["text"]
                await append_to_transcript(interview_id, "assistant", next_q)
                return next_q


# ── LLM Synthesis ─────────────────────────────────────────────────────

async def _synthesize_session(
    user_id: int, interview_id: int, session_number: int
) -> Optional[dict]:
    """Run LLM synthesis on a completed interview session transcript."""
    import json

    try:
        from agents import Agent, Runner

        # Load transcript
        from src.db.session import async_session
        from src.db.models import PersonaInterview

        async with async_session() as session:
            result = await session.execute(
                select(PersonaInterview).where(PersonaInterview.id == interview_id)
            )
            interview = result.scalar_one_or_none()

        if not interview or not interview.transcript:
            logger.warning("No transcript found for interview %d", interview_id)
            return None

        # Format transcript for the synthesis prompt
        transcript_text = _format_transcript(interview.transcript)

        # Also include any previous session syntheses for context
        all_transcripts = await get_all_transcripts(user_id)
        previous_context = ""
        for t in all_transcripts:
            if t["session_number"] < session_number and t.get("synthesis"):
                previous_context += (
                    f"\n\n--- Previous Session {t['session_number']} Synthesis ---\n"
                    f"{json.dumps(t['synthesis'], indent=2)}"
                )

        user_prompt = f"""Analyze this interview transcript from Session {session_number} of 3.

--- Interview Transcript (Session {session_number}: {INTERVIEW_SESSIONS[session_number]['title']}) ---
{transcript_text}
{previous_context}

Produce the personality profile JSON. If this is session 2 or 3, merge insights
with previous sessions to produce a more complete and refined profile."""

        synthesis_agent = Agent(
            name="PersonaSynthesizer",
            instructions=SYNTHESIS_SYSTEM_PROMPT,
            model=settings.default_model,
        )

        result = await Runner.run(synthesis_agent, user_prompt)
        raw_output = result.final_output.strip()

        # Parse JSON from the response (strip markdown code fences if present)
        if raw_output.startswith("```"):
            raw_output = raw_output.split("\n", 1)[1]
            if raw_output.endswith("```"):
                raw_output = raw_output[:-3]
            raw_output = raw_output.strip()

        synthesis = json.loads(raw_output)
        logger.info(
            "Synthesis complete for session %d, user %d", session_number, user_id
        )
        return synthesis

    except Exception as e:
        logger.error("Synthesis failed for interview %d: %s", interview_id, e)
        return None


async def _apply_synthesis_to_persona(
    user_id: int, synthesis: dict, session_number: int
) -> None:
    """Apply the synthesis results to the user's active persona."""
    from src.memory.persona import get_active_persona, create_persona_version

    db_user_id = await _get_db_user_id(user_id)
    if db_user_id is None:
        return

    current = await get_active_persona(db_user_id)
    if current is None:
        personality = {
            "traits": ["helpful", "proactive", "concise"],
            "style": settings.default_persona_style,
        }
        name = settings.default_assistant_name
    else:
        personality = dict(current["personality"])
        name = current["assistant_name"]

    # Merge synthesis into personality
    if "ocean" in synthesis:
        personality["ocean"] = synthesis["ocean"]
    if "communication" in synthesis:
        personality["communication"] = synthesis["communication"]
        # Also update top-level style from communication formality
        formality = synthesis["communication"].get("formality", "friendly")
        personality["style"] = formality
    if "work_context" in synthesis:
        personality["work_context"] = synthesis["work_context"]
    if "values" in synthesis:
        personality["values"] = synthesis["values"]
    if "synthesis" in synthesis:
        personality["synthesis"] = synthesis["synthesis"]

    personality["interview_sessions_completed"] = session_number
    personality["last_synthesis_date"] = datetime.utcnow().strftime("%Y-%m-%d")

    await create_persona_version(
        db_user_id,
        name,
        personality,
        f"Interview session {session_number} synthesis applied",
    )
    logger.info(
        "Persona updated from interview session %d for user %d",
        session_number, user_id,
    )


# ── Utilities ─────────────────────────────────────────────────────────

def _format_transcript(transcript: list[dict]) -> str:
    """Format a transcript list into readable text."""
    lines = []
    for entry in transcript:
        role = entry.get("role", "unknown")
        content = entry.get("content", "")
        prefix = "🤖 Assistant" if role == "assistant" else "👤 User"
        lines.append(f"{prefix}: {content}")
    return "\n\n".join(lines)


async def _get_db_user_id(telegram_id: int) -> Optional[int]:
    """Resolve internal users.id from Telegram ID."""
    from src.db.session import async_session
    from src.db.models import User

    async with async_session() as session:
        result = await session.execute(
            select(User.id).where(User.telegram_id == telegram_id)
        )
        return result.scalar_one_or_none()


async def reset_interview(user_id: int) -> str:
    """Reset all interview sessions for a user (for redo)."""
    from src.db.session import async_session
    from src.db.models import PersonaInterview

    db_user_id = await _get_db_user_id(user_id)
    if db_user_id is None:
        return "No interview data found."

    async with async_session() as session:
        await session.execute(
            update(PersonaInterview)
            .where(PersonaInterview.user_id == db_user_id)
            .values(status="archived")
        )
        await session.commit()

    return "Interview history archived. You can start fresh with `/persona interview`."
