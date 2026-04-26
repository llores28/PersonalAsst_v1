"""Regression tests for audit fixes (atlas-src-audit-5120d6).

Covers:
- SEC-2/BUG-1: get_session() is a proper asynccontextmanager
- SEC-5: sandbox static_analysis uses AST (not substring) for import blocking
- BUG-8: add_interval_job seconds=0 not silently dropped
- MOD-5/router: gpt-5.3-codex removed from routing matrix
- BUG-4: cost pricing uses model-keyed lookup (no stale GPT-4 rates)
- MOD-7: safety_agent imports is_contextual_follow_up_confirmation from action_policy
- QA-4: MarketplaceSkill unique constraint on (name, version) not (id, version)
- FEAT-4: SkillLoader default dir uses settings.user_skills_dir
"""

import ast
import contextlib
import inspect
import re
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ── SEC-2/BUG-1 ────────────────────────────────────────────────────────

def test_get_session_is_asynccontextmanager():
    """get_session() must be an async context manager, not a bare coroutine."""
    from src.db.session import get_session

    # contextlib.asynccontextmanager wraps the function; calling it returns an
    # _AsyncGeneratorContextManager which supports async with.
    cm = get_session()
    assert hasattr(cm, "__aenter__"), (
        "get_session() must return an async context manager (use @asynccontextmanager)"
    )
    assert hasattr(cm, "__aexit__"), (
        "get_session() must return an async context manager (use @asynccontextmanager)"
    )


# ── SEC-5 ───────────────────────────────────────────────────────────────

def test_static_analysis_blocks_subprocess_import():
    """Real `import subprocess` must be blocked by AST analysis."""
    from src.tools.sandbox import static_analysis

    code = "import subprocess\nsubprocess.run(['ls'])"
    violations = static_analysis(code)
    assert any("subprocess" in v for v in violations), (
        f"Expected 'subprocess' violation, got: {violations}"
    )


def test_static_analysis_does_not_block_comment():
    """Comment mentioning subprocess must NOT be flagged (old substring bug)."""
    from src.tools.sandbox import static_analysis

    code = "# This tool does not use subprocess\nprint('hello')"
    violations = static_analysis(code)
    assert not violations, f"False positive on comment: {violations}"


def test_static_analysis_blocks_eval():
    """eval() call must be blocked."""
    from src.tools.sandbox import static_analysis

    code = "result = eval('1+1')"
    violations = static_analysis(code)
    assert any("eval" in v for v in violations), f"Expected eval violation, got: {violations}"


def test_static_analysis_blocks_os_system():
    """os.system() call must be blocked even without `import os`."""
    from src.tools.sandbox import static_analysis

    code = "import os\nos.system('rm -rf /')"
    violations = static_analysis(code)
    assert any("os" in v for v in violations), f"Expected os violation, got: {violations}"


def test_static_analysis_rejects_syntax_error():
    """Unparseable code must return a syntax-error violation."""
    from src.tools.sandbox import static_analysis

    violations = static_analysis("def broken(:\n    pass")
    assert any("Syntax error" in v for v in violations), (
        f"Expected SyntaxError violation, got: {violations}"
    )


def test_static_analysis_allows_clean_code():
    """Simple valid tool code must pass."""
    from src.tools.sandbox import static_analysis

    code = "import json\nprint(json.dumps({'status': 'ok'}))"
    violations = static_analysis(code)
    assert not violations, f"Unexpected violations: {violations}"


# ── BUG-8 ───────────────────────────────────────────────────────────────

def test_add_interval_job_seconds_zero_not_dropped():
    """seconds=0 should not be silently ignored in add_interval_job trigger_kwargs."""
    # Inspect the source — trigger_kwargs construction must use `is not None`
    import src.scheduler.engine as engine_mod
    source = inspect.getsource(engine_mod.add_interval_job)
    assert "is not None" in source, (
        "add_interval_job must check `seconds is not None`, not truthiness of seconds"
    )


# ── MOD-5 / Router ──────────────────────────────────────────────────────

def test_routing_matrix_has_no_nonexistent_models():
    """gpt-5.3-codex must not appear in the routing matrix."""
    from src.models.router import _ROUTING_MATRIX

    all_models = {
        model
        for role_map in _ROUTING_MATRIX.values()
        for model in role_map.values()
    }
    assert "gpt-5.3-codex" not in all_models, (
        "gpt-5.3-codex is not a real OpenAI model — remove it from _ROUTING_MATRIX"
    )


def test_routing_matrix_models_are_known_gpt5():
    """All routing matrix models must be in the known gpt-5.4 family."""
    from src.models.router import _ROUTING_MATRIX, _REASONING_MODELS

    all_models = {
        model
        for role_map in _ROUTING_MATRIX.values()
        for model in role_map.values()
    }
    for model in all_models:
        assert model in _REASONING_MODELS, (
            f"Model '{model}' in routing matrix is not in _REASONING_MODELS whitelist"
        )


# ── BUG-4 / Cost pricing ────────────────────────────────────────────────

def test_cost_pricing_uses_model_keyed_lookup():
    """Cost tracking must use a model-keyed dict, not substring if/elif.

    The pricing block was extracted out of orchestrator.py into the dedicated
    `src/models/cost_tracker.py` module — this test follows the canonical
    location and asserts the same invariant (dict-keyed lookup, key entries
    for the active mini/nano models).
    """
    cost_tracker_path = Path(__file__).resolve().parents[1] / "src" / "models" / "cost_tracker.py"
    source = cost_tracker_path.read_text(encoding="utf-8")

    # Old anti-pattern: `if "mini" in str(_model_id):`
    assert 'if "mini" in str(_model_id):' not in source, (
        "Old substring pricing logic still present — should use OPENAI_MODEL_PRICING dict"
    )
    # New pattern: model-keyed pricing dict must exist.
    assert "OPENAI_MODEL_PRICING" in source, (
        "OPENAI_MODEL_PRICING dict not found in cost_tracker.py"
    )
    assert "gpt-5.4-mini" in source, "gpt-5.4-mini pricing entry missing"
    assert "gpt-5.4-nano" in source, "gpt-5.4-nano pricing entry missing"

    # And orchestrator.py must NOT carry a duplicate pricing block any more.
    orchestrator_path = Path(__file__).resolve().parents[1] / "src" / "agents" / "orchestrator.py"
    orchestrator_source = orchestrator_path.read_text(encoding="utf-8")
    assert 'if "mini" in str(_model_id):' not in orchestrator_source, (
        "Old substring pricing logic re-introduced into orchestrator.py"
    )


# ── MOD-7 / De-duplicate ────────────────────────────────────────────────

def test_safety_agent_imports_from_action_policy():
    """safety_agent.py must import is_contextual_follow_up_confirmation from action_policy."""
    safety_path = Path(__file__).resolve().parents[1] / "src" / "agents" / "safety_agent.py"
    source = safety_path.read_text(encoding="utf-8")
    assert "from src.action_policy import is_contextual_follow_up_confirmation" in source, (
        "safety_agent.py must import is_contextual_follow_up_confirmation from action_policy"
    )
    # The local duplicate definition must be gone
    assert "def _is_contextual_follow_up_confirmation" not in source, (
        "Duplicate _is_contextual_follow_up_confirmation definition still in safety_agent.py"
    )


# ── QA-4 / MarketplaceSkill constraint ─────────────────────────────────

def test_marketplace_skill_unique_constraint_on_name_version():
    """MarketplaceSkill unique constraint must be on (name, version), not (id, version)."""
    from sqlalchemy import UniqueConstraint
    from src.db.models import MarketplaceSkill

    constraints = MarketplaceSkill.__table_args__
    uc = next((c for c in constraints if isinstance(c, UniqueConstraint)), None)
    assert uc is not None, "MarketplaceSkill must have a UniqueConstraint"
    col_names = {col.name for col in uc.columns}
    assert col_names == {"name", "version"}, (
        f"UniqueConstraint should be on (name, version), got: {col_names}"
    )


# ── FEAT-4 / SkillLoader default dir ────────────────────────────────────

def test_skill_loader_default_dir_not_hardcoded():
    """SkillLoader must not hardcode /app/src/user_skills as default dir."""
    from src.skills.loader import SkillLoader

    loader = SkillLoader()
    # Must be a resolved absolute path, not the hardcoded Docker path
    assert str(loader.user_skills_dir) != "/app/src/user_skills", (
        "SkillLoader default dir is still hardcoded to /app/src/user_skills"
    )
    assert loader.user_skills_dir.is_absolute(), (
        "SkillLoader.user_skills_dir must be an absolute path"
    )


def test_skill_loader_respects_settings():
    """SkillLoader default dir must reflect settings.user_skills_dir."""
    from src.settings import settings
    from src.skills.loader import SkillLoader

    loader = SkillLoader()
    # The final path segment(s) must include what settings says
    assert settings.user_skills_dir.replace("/", "\\") in str(loader.user_skills_dir) or \
           settings.user_skills_dir.replace("\\", "/") in str(loader.user_skills_dir).replace("\\", "/"), (
        f"SkillLoader dir '{loader.user_skills_dir}' does not reflect settings.user_skills_dir='{settings.user_skills_dir}'"
    )
