"""Skill Factory Agent — creates skills via AI-guided interview (no code generation).

Unlike Tool Factory which generates executable code, Skill Factory creates
SKILL.md files with YAML frontmatter and Markdown instructions.

Key insight: Skills are declarative expertise packages, not executable code.
This makes them safe to create via AI without sandboxing concerns.
"""

import json
import logging
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Optional

from agents import Agent, Runner, function_tool

from src.memory.conversation import get_session_field, set_session_field
from src.models.router import ModelRole, select_model
from src.skills.loader import SkillLoader

logger = logging.getLogger(__name__)

USER_SKILLS_DIR = Path("src/user_skills")

SKILL_FACTORY_INSTRUCTIONS = """\
You are a Skill Factory specialist. You help users create custom skills for their AI assistant.

## What is a Skill?
A skill is a package of expertise that guides how the AI responds to specific requests.
Unlike tools (which execute code), skills provide instructions and context.

Examples:
- "Write weekly status reports in my preferred format"
- "Generate devotionals with specific theological perspectives"
- "Answer emails with my tone and style guidelines"
- "Help me brainstorm using my creative process"

## Skill Structure
Each skill has:
1. **Metadata** (YAML frontmatter): name, description, tags, routing hints
2. **Instructions** (Markdown): Detailed guidance for the AI
3. **Optional Resources**: Templates, examples, reference docs

## Interview Process
1. Understand what the user wants the skill to do
2. Ask 3-5 clarifying questions to capture:
   - Format/style preferences
   - Key components/sections
   - Tone and voice
   - Specific examples of desired output
3. Generate appropriate YAML frontmatter
4. Write comprehensive instructions
5. Suggest routing hints (phrases that trigger this skill)

## Routing Hints
These are natural language phrases that should activate the skill:
- Good: "when writing my weekly report", "for status updates", "when generating devotionals"
- Bad: "use this skill", "activate skill", "run this" (too meta)

## YAML Frontmatter Template
```yaml
---
name: [Human-readable name]
description: [One-line description]
version: 1.0.0
author: user
tags: [relevant, tags]
routing_hints:
  - "when [specific context]"
  - "for [use case]"
requires_skills: []  # Dependencies (optional)
extends_skill: null  # Parent skill (optional)
tools: []  # Knowledge-only for now
requires_connection: false
read_only: true
---
```

## Instruction Writing Guidelines
- Be specific and concrete
- Include examples of desired output
- Define structure/format clearly
- Address edge cases
- Keep instructions actionable

## Safety
- Skills cannot execute code (safe by design)
- Skills only provide instructions to guide AI responses
- No sandboxing needed unlike tool creation
"""


class SkillCreationSession:
    """Tracks a skill creation session for a user."""

    def __init__(self, user_id: int):
        self.user_id = user_id
        self.step = "start"  # start, interviewing, generating, review, completed
        self.skill_name: Optional[str] = None
        self.skill_description: Optional[str] = None
        self.questions_asked: list[str] = []
        self.answers: dict[str, str] = {}
        self.generated_skill: Optional[dict] = None
        self.skill_id: Optional[str] = None


async def get_creation_session(user_id: int) -> Optional[SkillCreationSession]:
    """Get or create a skill creation session."""
    session_data = await get_session_field(user_id, "skill_creation_session")
    if not session_data:
        return None

    try:
        data = json.loads(session_data)
        session = SkillCreationSession(user_id)
        session.__dict__.update(data)
        return session
    except Exception as e:
        logger.error("Failed to parse skill creation session: %s", e)
        return None


async def save_creation_session(session: SkillCreationSession) -> None:
    """Save the skill creation session to Redis."""
    try:
        data = json.dumps(session.__dict__, default=str)
        await set_session_field(session.user_id, "skill_creation_session", data)
    except Exception as e:
        logger.error("Failed to save skill creation session: %s", e)


async def clear_creation_session(user_id: int) -> None:
    """Clear the skill creation session."""
    await set_session_field(user_id, "skill_creation_session", "")
    await set_session_field(user_id, "skill_creation_active", "false")
    await set_session_field(user_id, "skill_creation_state", "completed")


@function_tool
def generate_skill_from_interview(
    name: str,
    description: str,
    routing_hints: list[str],
    instructions: str,
    tags: list[str],
    suggested_questions: list[str],
) -> str:
    """Generate a skill from interview answers (placeholder for structured output)."""
    return json.dumps({
        "name": name,
        "description": description,
        "routing_hints": routing_hints,
        "instructions": instructions,
        "tags": tags,
        "questions": suggested_questions,
    })


async def handle_skill_creation_message(user_id: int, message: str) -> str:
    """Handle a message from the user during skill creation mode."""
    session = await get_creation_session(user_id)

    if not session:
        # Start new session
        session = SkillCreationSession(user_id)
        session.step = "interviewing"
        await save_creation_session(session)

        return (
            "🎨 **Skill Creation Wizard**\n\n"
            "I'll help you create a custom skill. To get started, tell me:\n\n"
            "**What should this skill do?**\n\n"
            "Examples:\n"
            "• 'Help me write weekly status reports for my team'\n"
            "• 'Generate morning devotionals in a specific style'\n"
            "• 'Format my emails with a professional but friendly tone'\n\n"
            "Describe what you need:"
        )

    if session.step == "interviewing":
        # Store the initial description if this is the first answer
        if not session.skill_description:
            session.skill_description = message
            await save_creation_session(session)

        # Use LLM to conduct the interview and generate the skill
        model_selection = select_model(ModelRole.FAST)
        agent = Agent(
            name="Skill Factory",
            instructions=SKILL_FACTORY_INSTRUCTIONS,
            model=model_selection.model_id if hasattr(model_selection, 'model_id') else str(model_selection),
            tools=[generate_skill_from_interview],
        )

        # Build context from session
        context = f"""\
User wants to create a skill: {session.skill_description}

Previous Q&A:
"""
        for q, a in zip(session.questions_asked, session.answers.values()):
            context += f"Q: {q}\nA: {a}\n\n"

        current_input = f"User's latest message: {message}\n\n"

        if not session.questions_asked:
            # First interaction - start asking questions
            current_input += (
                "This is the initial description. Ask 3-5 clarifying questions "
                "to understand the skill requirements. Focus on:\n"
                "1. Format/style preferences\n"
                "2. Key components/structure\n"
                "3. Tone and voice\n"
                "4. Specific examples\n\n"
                "Return questions as a numbered list."
            )
        else:
            # Continue interview or generate if we have enough info
            current_input += (
                "Continue the interview if you need more information (2+ questions), "
                "or generate the complete skill if you have enough context.\n\n"
                "If generating, use the generate_skill_from_interview tool with:\n"
                "- name: Human-readable skill name\n"
                "- description: One-line description\n"
                "- routing_hints: 3-5 natural language triggers\n"
                "- instructions: Full markdown instructions (be comprehensive)\n"
                "- tags: 3-5 relevant keywords\n"
            )

        result = await Runner.run(agent, context + current_input)

        # Check if skill was generated (SDK internal attributes)
        tool_calls = [m for m in result.new_messages if hasattr(m, 'tool_calls')]  # type: ignore[attr-defined]
        if tool_calls:
            # Skill was generated
            for msg in tool_calls:
                for tc in msg.tool_calls:
                    if tc.tool_name == "generate_skill_from_interview":
                        try:
                            skill_data = json.loads(tc.tool_input)
                            session.generated_skill = skill_data
                            session.step = "review"
                            session.skill_name = skill_data.get("name", "Unnamed Skill")
                            session.skill_id = _generate_skill_id(session.skill_name)
                            await save_creation_session(session)

                            return _format_skill_preview(skill_data, session.skill_id)
                        except Exception as e:
                            logger.error("Failed to parse generated skill: %s", e)

        # Otherwise, extract questions from response
        response_text = result.final_output

        # Try to extract numbered questions
        questions = _extract_questions(response_text)
        if questions:
            session.questions_asked.extend(questions)
            await save_creation_session(session)

        return response_text

    if session.step == "review":
        # User is reviewing the generated skill
        msg_lower = message.lower()

        if any(word in msg_lower for word in ["yes", "create", "save", "ok", "good", "perfect"]):
            # Save the skill
            if session.generated_skill and session.skill_id:
                success = await _save_skill_file(session)
                if success:
                    await clear_creation_session(user_id)
                    return (
                        f"✅ **Skill Created!**\n\n"
                        f"Your skill `{session.skill_id}` has been saved.\n\n"
                        f"**Test it now:** Try asking me something related to:\n"
                        f"'{session.generated_skill.get('routing_hints', ['this topic'])[0]}'\n\n"
                        f"**Manage your skills:**\n"
                        f"• `/skills` - List all skills\n"
                        f"• `/skills info {session.skill_id}` - View details\n"
                        f"• `/skills reload` - Refresh if you edit the file"
                    )
                else:
                    return "❌ Failed to save skill. Please try again."
            else:
                return "Error: No skill generated to save."

        elif any(word in msg_lower for word in ["no", "edit", "change", "modify", "fix"]):
            # Go back to interviewing with feedback
            session.step = "interviewing"
            session.questions_asked.append(f"User feedback: {message}`")
            await save_creation_session(session)

            return (
                "Got it. What would you like to change?\n\n"
                "Tell me what to adjust and I'll regenerate the skill."
            )

        elif "cancel" in msg_lower:
            await clear_creation_session(user_id)
            return "Skill creation cancelled. Your existing skills are unchanged."

        else:
            skill_name = session.generated_skill.get('name', 'Unnamed') if session.generated_skill else 'Unnamed'
            return (
                f"**Review your skill:**\n\n"
                f"{skill_name}\n\n"
                f"Does this look good?\n"
                f"• **Yes** - Save the skill\n"
                f"• **Edit** - Tell me what to change\n"
                f"• **Cancel** - Discard and exit"
            )

    return "Skill creation wizard. How can I help?"


def _format_skill_preview(skill_data: dict, skill_id: str) -> str:
    """Format a skill preview for user review."""
    lines = [
        f"📝 **Skill Preview: {skill_data.get('name', 'Unnamed')}**\n",
        f"**ID:** `{skill_id}`",
        f"**Description:** {skill_data.get('description', 'No description')}",
        f"",
        f"**Tags:** {', '.join(skill_data.get('tags', []))}",
        f"",
        f"**Routing Hints:**",
    ]

    for hint in skill_data.get('routing_hints', []):
        lines.append(f"  • {hint}")

    lines.append(f"")
    lines.append(f"**Instructions Preview:**")
    instructions = skill_data.get('instructions', '')
    # Show first 300 chars of instructions
    preview = instructions[:300] + "..." if len(instructions) > 300 else instructions
    lines.append(f"```")
    lines.append(preview)
    lines.append(f"```")

    lines.append(f"")
    lines.append(f"**Save this skill?**")
    lines.append(f"• **Yes** - Create the skill")
    lines.append(f"• **Edit** - Tell me what to change")
    lines.append(f"• **Cancel** - Discard")

    return "\n".join(lines)


def _generate_skill_id(name: str) -> str:
    """Generate a URL-friendly skill ID from name."""
    # Convert to lowercase, replace spaces with hyphens, remove non-alphanumeric
    skill_id = name.lower()
    skill_id = re.sub(r'[^\w\s-]', '', skill_id)
    skill_id = re.sub(r'\s+', '-', skill_id)
    skill_id = skill_id[:50]  # Limit length
    return skill_id


def _extract_questions(text: str) -> list[str]:
    """Extract numbered questions from text."""
    questions = []
    lines = text.split('\n')

    for line in lines:
        # Match numbered questions (1., 1), etc.)
        match = re.match(r'^\s*(?:\d+[\.\)])+\s*(.+)$', line)
        if match:
            questions.append(match.group(1).strip())

    return questions


async def _save_skill_file(session: SkillCreationSession) -> bool:
    """Save the generated skill to a SKILL.md file."""
    if not session.skill_id or not session.generated_skill:
        return False

    try:
        skill_dir = USER_SKILLS_DIR / session.skill_id
        skill_dir.mkdir(parents=True, exist_ok=True)

        skill_file = skill_dir / "SKILL.md"

        # Build YAML frontmatter
        yaml_content = f"""---
name: {session.generated_skill.get('name', 'Unnamed Skill')}
description: {session.generated_skill.get('description', '')}
version: 1.0.0
author: user
tags:
{''.join(f'  - {tag}' for tag in session.generated_skill.get('tags', []))}
routing_hints:
{''.join(f'  - "{hint}"' for hint in session.generated_skill.get('routing_hints', []))}
requires_skills: []
extends_skill: null
tools: []
requires_connection: false
read_only: true
---

{session.generated_skill.get('instructions', '')}
"""

        skill_file.write_text(yaml_content, encoding="utf-8")
        logger.info("Saved skill file: %s", skill_file)
        return True

    except Exception as e:
        logger.error("Failed to save skill file: %s", e)
        return False
