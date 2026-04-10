"""Skill Validation — on-demand testing for skills (no scheduling).

This module provides:
1. On-demand skill testing at creation time
2. Validation when skills are modified
3. Routing confidence analysis
4. Dashboard-based retesting

Usage:
    # Test at creation time
    results = await validate_skill(skill_id, test_cases)

    # Quick routing check
    confidence = calculate_routing_confidence(user_input, routing_hints)
"""

import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from src.skills.loader import SkillLoader

logger = logging.getLogger(__name__)


@dataclass
class SkillTestCase:
    """A single test case for a skill."""
    name: str
    input: str
    expected_keywords: list[str] = field(default_factory=list)
    min_confidence: float = 0.5


@dataclass
class SkillTestResult:
    """Result of running a skill test."""
    test_case: str
    passed: bool
    skill_matched: bool
    routing_confidence: float
    output: str
    execution_time_ms: int
    error: Optional[str] = None


async def validate_skill(
    skill_id: str,
    test_cases: Optional[list[dict]] = None,
) -> list[SkillTestResult]:
    """Validate a skill by running test cases.

    This is called ON-DEMAND:
    - At skill creation time (before saving)
    - When user clicks "Test" in dashboard
    - After skill modifications

    Args:
        skill_id: The skill to validate
        test_cases: List of test case dicts. If None, uses routing hints as tests.

    Returns:
        List of test results
    """
    skill_path = Path(f"user_skills/{skill_id}")
    if not skill_path.exists():
        raise ValueError(f"Skill not found: {skill_id}")

    loader = SkillLoader()
    skill = loader.load_from_path(skill_path)

    # If no test cases provided, create some from routing hints
    if not test_cases and skill.routing_hints:
        test_cases = [
            {"name": f"Routing test {i}", "input": hint, "expected_keywords": []}
            for i, hint in enumerate(skill.routing_hints[:3])  # Test first 3 hints
        ]

    if not test_cases:
        return [SkillTestResult(
            test_case="default",
            passed=False,
            skill_matched=False,
            routing_confidence=0.0,
            output="",
            execution_time_ms=0,
            error="No test cases or routing hints available"
        )]

    results = []
    for tc_data in test_cases:
        tc = SkillTestCase(
            name=tc_data.get("name", "Unnamed test"),
            input=tc_data["input"],
            expected_keywords=tc_data.get("expected_keywords", []),
            min_confidence=tc_data.get("min_confidence", 0.5),
        )

        result = await _run_test_case(skill_id, tc)
        results.append(result)

    return results


async def _run_test_case(skill_id: str, test_case: SkillTestCase) -> SkillTestResult:
    """Run a single test case against a skill."""
    start_time = time.time()

    try:
        skill_path = Path(f"user_skills/{skill_id}")
        loader = SkillLoader()
        skill = loader.load_from_path(skill_path)

        # Calculate routing confidence
        routing_confidence = calculate_routing_confidence(test_case.input, skill.routing_hints)
        skill_matched = routing_confidence >= test_case.min_confidence

        # Check expected keywords in instructions
        instructions = skill.get_full_instructions().lower()
        missing_keywords = [
            kw for kw in test_case.expected_keywords
            if kw.lower() not in instructions
        ]

        passed = skill_matched and not missing_keywords

        execution_time = int((time.time() - start_time) * 1000)

        output = f"Routing confidence: {routing_confidence:.2f}"
        if missing_keywords:
            output += f"\nMissing expected keywords: {', '.join(missing_keywords)}"

        return SkillTestResult(
            test_case=test_case.name,
            passed=passed,
            skill_matched=skill_matched,
            routing_confidence=routing_confidence,
            output=output,
            execution_time_ms=execution_time,
        )

    except Exception as e:
        execution_time = int((time.time() - start_time) * 1000)
        return SkillTestResult(
            test_case=test_case.name,
            passed=False,
            skill_matched=False,
            routing_confidence=0.0,
            output="",
            execution_time_ms=execution_time,
            error=str(e),
        )


def calculate_routing_confidence(user_input: str, routing_hints: list[str]) -> float:
    """Calculate how likely the input matches the skill's routing hints.

    Args:
        user_input: The user's message/input
        routing_hints: List of phrases that should trigger this skill

    Returns:
        Confidence score 0.0-1.0
    """
    if not routing_hints:
        return 0.0

    input_lower = user_input.lower()
    input_words = set(re.findall(r'\b\w+\b', input_lower))

    if not input_words:
        return 0.0

    max_confidence = 0.0

    for hint in routing_hints:
        hint_lower = hint.lower()
        hint_words = set(re.findall(r'\b\w+\b', hint_lower))

        if not hint_words:
            continue

        # Calculate Jaccard similarity (intersection / union)
        intersection = len(input_words & hint_words)
        union = len(input_words | hint_words)

        if union > 0:
            confidence = intersection / len(hint_words)  # Match ratio against hint
            max_confidence = max(max_confidence, confidence)

    return min(max_confidence, 1.0)


async def quick_test_skill(skill_id: str, test_input: str) -> dict:
    """Quick test a skill with a single input (for dashboard preview).

    Returns:
        Dict with routing_confidence, would_trigger, and suggestions
    """
    try:
        skill_path = Path(f"user_skills/{skill_id}")
        if not skill_path.exists():
            return {"error": f"Skill {skill_id} not found"}

        loader = SkillLoader()
        skill = loader.load_from_path(skill_path)

        confidence = calculate_routing_confidence(test_input, skill.routing_hints)

        return {
            "skill_id": skill_id,
            "skill_name": skill.name,
            "test_input": test_input,
            "routing_confidence": round(confidence, 2),
            "would_trigger": confidence > 0.5,
            "routing_hints_matched": [
                hint for hint in skill.routing_hints
                if calculate_routing_confidence(test_input, [hint]) > 0.3
            ],
        }

    except Exception as e:
        return {"error": str(e)}
