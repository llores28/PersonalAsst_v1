"""Tests for the Persona Interview Agent (Phase 7).

Tests cover:
- Interview session definitions and structure
- Interview state management
- Transcript formatting
- Deep profile formatting in persona_mode.py
- LLM synthesis prompt structure
"""



# ── Interview Session Definitions ─────────────────────────────────────


class TestInterviewSessionDefinitions:
    """Verify interview sessions are well-structured."""

    def test_all_three_sessions_defined(self):
        from src.agents.persona_interview_agent import INTERVIEW_SESSIONS

        assert set(INTERVIEW_SESSIONS.keys()) == {1, 2, 3}

    def test_each_session_has_required_fields(self):
        from src.agents.persona_interview_agent import INTERVIEW_SESSIONS

        for num, session in INTERVIEW_SESSIONS.items():
            assert "title" in session, f"Session {num} missing title"
            assert "description" in session, f"Session {num} missing description"
            assert "questions" in session, f"Session {num} missing questions"
            assert len(session["questions"]) >= 4, (
                f"Session {num} has too few questions: {len(session['questions'])}"
            )

    def test_each_question_has_id_and_text(self):
        from src.agents.persona_interview_agent import INTERVIEW_SESSIONS

        for num, session in INTERVIEW_SESSIONS.items():
            for i, q in enumerate(session["questions"]):
                assert "id" in q, f"Session {num}, question {i} missing id"
                assert "text" in q, f"Session {num}, question {i} missing text"
                assert len(q["text"]) > 20, (
                    f"Session {num}, question {i} text too short"
                )

    def test_session_titles(self):
        from src.agents.persona_interview_agent import INTERVIEW_SESSIONS

        assert INTERVIEW_SESSIONS[1]["title"] == "Identity & Context"
        assert INTERVIEW_SESSIONS[2]["title"] == "Work Style & Values"
        assert INTERVIEW_SESSIONS[3]["title"] == "Communication & Personality"

    def test_intro_messages_exist(self):
        from src.agents.persona_interview_agent import SESSION_INTRO_MESSAGES

        assert set(SESSION_INTRO_MESSAGES.keys()) == {1, 2, 3}
        for num, msg in SESSION_INTRO_MESSAGES.items():
            assert len(msg) > 50, f"Session {num} intro too short"

    def test_complete_messages_exist(self):
        from src.agents.persona_interview_agent import SESSION_COMPLETE_MESSAGES

        assert set(SESSION_COMPLETE_MESSAGES.keys()) == {1, 2, 3}
        for num, msg in SESSION_COMPLETE_MESSAGES.items():
            assert "complete" in msg.lower() or "✅" in msg, (
                f"Session {num} complete message missing completion indicator"
            )


# ── Transcript Formatting ─────────────────────────────────────────────


class TestTranscriptFormatting:
    """Test transcript formatting utility."""

    def test_format_empty_transcript(self):
        from src.agents.persona_interview_agent import _format_transcript

        result = _format_transcript([])
        assert result == ""

    def test_format_basic_transcript(self):
        from src.agents.persona_interview_agent import _format_transcript

        transcript = [
            {"role": "assistant", "content": "What's your name?"},
            {"role": "user", "content": "I'm Alex."},
        ]
        result = _format_transcript(transcript)
        assert "🤖 Assistant: What's your name?" in result
        assert "👤 User: I'm Alex." in result

    def test_format_preserves_order(self):
        from src.agents.persona_interview_agent import _format_transcript

        transcript = [
            {"role": "assistant", "content": "First"},
            {"role": "user", "content": "Second"},
            {"role": "assistant", "content": "Third"},
        ]
        result = _format_transcript(transcript)
        lines = result.split("\n\n")
        assert len(lines) == 3
        assert "First" in lines[0]
        assert "Second" in lines[1]
        assert "Third" in lines[2]


# ── Deep Profile Formatting ───────────────────────────────────────────


class TestDeepProfileFormatting:
    """Test _format_deep_profile in persona_mode.py."""

    def test_empty_personality_returns_empty(self):
        from src.agents.persona_mode import _format_deep_profile

        result = _format_deep_profile({})
        assert result == ""

    def test_basic_personality_returns_empty(self):
        from src.agents.persona_mode import _format_deep_profile

        result = _format_deep_profile({"traits": ["helpful"], "style": "friendly"})
        assert result == ""

    def test_ocean_scores_formatted(self):
        from src.agents.persona_mode import _format_deep_profile

        personality = {
            "ocean": {
                "openness": 0.8,
                "conscientiousness": 0.7,
                "extraversion": 0.5,
                "agreeableness": 0.6,
                "neuroticism": 0.3,
            }
        }
        result = _format_deep_profile(personality)
        assert "Big Five" in result or "OCEAN" in result
        assert "Openness" in result
        assert "Conscientiousness" in result
        assert "Extraversion" in result
        assert "Agreeableness" in result
        assert "Neuroticism" in result
        assert "0.8" in result

    def test_communication_profile_formatted(self):
        from src.agents.persona_mode import _format_deep_profile

        personality = {
            "communication": {
                "formality": "casual",
                "humor": "dry, occasional",
                "emoji_use": "minimal",
                "verbosity_preference": "concise",
                "email_tone": "professional but warm",
                "pet_peeves": ["unnecessary meetings", "vague requests"],
            }
        }
        result = _format_deep_profile(personality)
        assert "Communication Profile" in result
        assert "casual" in result
        assert "dry, occasional" in result
        assert "unnecessary meetings" in result

    def test_work_context_formatted(self):
        from src.agents.persona_mode import _format_deep_profile

        personality = {
            "work_context": {
                "role": "software engineer",
                "typical_day": "code review, meetings, deep work",
                "priorities": ["deep work", "code quality"],
                "pain_points": ["email overload"],
            }
        }
        result = _format_deep_profile(personality)
        assert "Work Context" in result
        assert "software engineer" in result
        assert "deep work" in result

    def test_values_formatted(self):
        from src.agents.persona_mode import _format_deep_profile

        personality = {
            "values": {
                "decision_style": "data-driven",
                "autonomy_preference": "high",
                "sensitive_topics": ["health"],
                "motivators": ["learning new things"],
            }
        }
        result = _format_deep_profile(personality)
        assert "Values" in result or "Decision" in result
        assert "data-driven" in result
        assert "health" in result

    def test_synthesis_formatted(self):
        from src.agents.persona_mode import _format_deep_profile

        personality = {
            "synthesis": "A concise, action-oriented professional."
        }
        result = _format_deep_profile(personality)
        assert "Personality Synthesis" in result
        assert "action-oriented" in result
        assert "Embody" in result

    def test_full_profile_all_sections(self):
        from src.agents.persona_mode import _format_deep_profile

        personality = {
            "ocean": {"openness": 0.7, "conscientiousness": 0.8,
                      "extraversion": 0.5, "agreeableness": 0.7,
                      "neuroticism": 0.3},
            "communication": {"formality": "casual", "humor": "dry"},
            "work_context": {"role": "engineer"},
            "values": {"decision_style": "gut + data"},
            "synthesis": "Concise and decisive.",
        }
        result = _format_deep_profile(personality)
        assert "OCEAN" in result
        assert "Communication" in result
        assert "Work Context" in result
        assert "Values" in result or "Decision" in result
        assert "Synthesis" in result


# ── assemble_persona_prompt with deep profile ─────────────────────────


class TestAssemblePersonaPromptDeepProfile:
    """Test that assemble_persona_prompt correctly includes deep profile."""

    def test_without_personality_data(self):
        from src.agents.persona_mode import assemble_persona_prompt

        result = assemble_persona_prompt(
            name="Atlas",
            user_name="Test User",
            personality_traits="helpful, proactive",
            communication_style="friendly",
            user_preferences="None",
            procedural_memories="None",
            recent_context="None",
            task_context="None",
        )
        assert "Atlas" in result
        assert "Test User" in result
        # Should not have OCEAN sections
        assert "Big Five" not in result

    def test_with_personality_data(self):
        from src.agents.persona_mode import assemble_persona_prompt

        personality_data = {
            "ocean": {"openness": 0.8, "conscientiousness": 0.7,
                      "extraversion": 0.5, "agreeableness": 0.6,
                      "neuroticism": 0.3},
            "synthesis": "A creative, organized professional.",
        }
        result = assemble_persona_prompt(
            name="Atlas",
            user_name="Test User",
            personality_traits="helpful",
            communication_style="friendly",
            user_preferences="None",
            procedural_memories="None",
            recent_context="None",
            task_context="None",
            personality_data=personality_data,
        )
        assert "Big Five" in result or "OCEAN" in result
        assert "0.8" in result
        assert "creative, organized" in result


# ── Synthesis Prompt ──────────────────────────────────────────────────


class TestSynthesisPrompt:
    """Test that the synthesis prompt is well-structured."""

    def test_synthesis_prompt_contains_ocean_fields(self):
        from src.agents.persona_interview_agent import SYNTHESIS_SYSTEM_PROMPT

        assert "openness" in SYNTHESIS_SYSTEM_PROMPT
        assert "conscientiousness" in SYNTHESIS_SYSTEM_PROMPT
        assert "extraversion" in SYNTHESIS_SYSTEM_PROMPT
        assert "agreeableness" in SYNTHESIS_SYSTEM_PROMPT
        assert "neuroticism" in SYNTHESIS_SYSTEM_PROMPT

    def test_synthesis_prompt_requests_json(self):
        from src.agents.persona_interview_agent import SYNTHESIS_SYSTEM_PROMPT

        assert "JSON" in SYNTHESIS_SYSTEM_PROMPT

    def test_synthesis_prompt_has_expert_perspectives(self):
        from src.agents.persona_interview_agent import SYNTHESIS_SYSTEM_PROMPT

        assert "psychologist" in SYNTHESIS_SYSTEM_PROMPT.lower()
        assert "productivity" in SYNTHESIS_SYSTEM_PROMPT.lower()
        assert "assistant" in SYNTHESIS_SYSTEM_PROMPT.lower()


# ── PersonaInterview DB Model ─────────────────────────────────────────


class TestPersonaInterviewModel:
    """Test that the PersonaInterview model is correctly defined."""

    def test_model_exists(self):
        from src.db.models import PersonaInterview

        assert PersonaInterview.__tablename__ == "persona_interviews"

    def test_model_columns(self):
        from src.db.models import PersonaInterview

        columns = {c.name for c in PersonaInterview.__table__.columns}
        expected = {
            "id", "user_id", "session_number", "status",
            "transcript", "synthesis", "started_at", "completed_at",
        }
        assert expected.issubset(columns), f"Missing columns: {expected - columns}"

    def test_user_relationship(self):
        from src.db.models import User

        rel_names = {r.key for r in User.__mapper__.relationships}
        assert "persona_interviews" in rel_names


# ── Migration ─────────────────────────────────────────────────────────


class TestMigration:
    """Test that the migration file is valid."""

    def test_migration_file_exists(self):
        import os

        path = os.path.join(
            os.path.dirname(__file__), "..",
            "src", "db", "migrations", "versions",
            "003_add_persona_interviews.py",
        )
        assert os.path.exists(path), f"Migration file not found: {path}"

    def test_migration_has_upgrade_and_downgrade(self):
        import sys
        import os

        # Add the migration to the path
        migration_dir = os.path.join(
            os.path.dirname(__file__), "..",
            "src", "db", "migrations", "versions",
        )
        sys.path.insert(0, migration_dir)
        try:
            import importlib
            mod = importlib.import_module("003_add_persona_interviews")
            assert hasattr(mod, "upgrade"), "Migration missing upgrade()"
            assert hasattr(mod, "downgrade"), "Migration missing downgrade()"
            assert mod.revision == "003"
            assert mod.down_revision == "002"
        finally:
            sys.path.pop(0)
