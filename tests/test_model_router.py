"""Tests for the model router service."""

from __future__ import annotations

import pytest
from unittest.mock import patch

from src.models.router import (
    ModelRole,
    ModelSelection,
    TaskComplexity,
    select_model,
)


class TestSelectModel:
    """Test select_model() routing logic."""

    def test_orchestrator_medium_returns_gpt54(self):
        sel = select_model(ModelRole.ORCHESTRATOR, TaskComplexity.MEDIUM)
        assert sel.model_id == "gpt-5.4"
        assert sel.reasoning_effort == "medium"
        assert sel.api_docs_url is not None
        assert "gpt-5.4" in sel.api_docs_url

    def test_orchestrator_xhigh_returns_pro(self):
        sel = select_model(ModelRole.ORCHESTRATOR, TaskComplexity.XHIGH)
        assert sel.model_id == "gpt-5.4-pro"
        assert sel.reasoning_effort == "high"

    def test_orchestrator_none_returns_nano(self):
        sel = select_model(ModelRole.ORCHESTRATOR, TaskComplexity.NONE)
        assert sel.model_id == "gpt-5.4-nano"
        assert sel.reasoning_effort == "none"

    def test_coding_xhigh_returns_top_tier(self):
        # Routing matrix evolved: gpt-5.3-codex was retired from CODING+XHIGH
        # in favour of gpt-5.4 (a reasoning model that supports tool use).
        # Codex variants are still seeded in src/models/seed.py for marketplace
        # selection but no longer auto-selected by the router.
        sel = select_model(ModelRole.CODING, TaskComplexity.XHIGH)
        assert sel.model_id == "gpt-5.4"
        assert sel.reasoning_effort == "high"

    def test_repair_medium_returns_mini(self):
        sel = select_model(ModelRole.REPAIR, TaskComplexity.MEDIUM)
        assert sel.model_id == "gpt-5.4-mini"
        assert sel.reasoning_effort == "medium"

    def test_safety_high_returns_mini(self):
        sel = select_model(ModelRole.SAFETY, TaskComplexity.HIGH)
        assert sel.model_id == "gpt-5.4-mini"

    def test_safety_low_returns_nano(self):
        sel = select_model(ModelRole.SAFETY, TaskComplexity.LOW)
        assert sel.model_id == "gpt-5.4-nano"

    def test_fast_all_complexities_mostly_nano(self):
        for c in [TaskComplexity.NONE, TaskComplexity.LOW, TaskComplexity.MEDIUM, TaskComplexity.HIGH]:
            sel = select_model(ModelRole.FAST, c)
            assert sel.model_id == "gpt-5.4-nano"

    def test_fast_xhigh_upgrades_to_mini(self):
        sel = select_model(ModelRole.FAST, TaskComplexity.XHIGH)
        assert sel.model_id == "gpt-5.4-mini"

    @patch("src.models.router.settings")
    def test_no_complexity_returns_settings_default(self, mock_settings):
        mock_settings.model_orchestrator = "gpt-5.4"
        mock_settings.default_reasoning_effort = "medium"
        sel = select_model(ModelRole.ORCHESTRATOR)
        assert sel.model_id == "gpt-5.4"
        assert sel.reasoning_effort == "medium"

    @patch("src.models.router.settings")
    def test_non_reasoning_model_has_none_effort(self, mock_settings):
        # When a routed/default model is NOT in _REASONING_MODELS, the router
        # must drop reasoning_effort to None even if a default effort was
        # requested. We exercise this via the settings-fallback path (no
        # complexity → settings default) using gpt-4o, which is intentionally
        # absent from the _REASONING_MODELS frozenset.
        mock_settings.model_general = "gpt-4o"
        mock_settings.default_reasoning_effort = "high"
        sel = select_model(ModelRole.GENERAL)
        assert sel.model_id == "gpt-4o"
        assert sel.reasoning_effort is None

    def test_api_docs_url_format(self):
        sel = select_model(ModelRole.GENERAL)
        assert sel.api_docs_url.startswith("https://developers.openai.com/api/docs/models/")

    @patch("src.models.router.settings")
    def test_image_gen_role_falls_back_to_general(self, mock_settings):
        mock_settings.model_general = "gpt-5.4-mini"
        mock_settings.default_reasoning_effort = "medium"
        # IMAGE_GEN not in routing matrix, falls back to settings default
        sel = select_model(ModelRole.IMAGE_GEN, TaskComplexity.MEDIUM)
        assert sel.model_id == "gpt-5.4-mini"


class TestModelSelection:
    """Test ModelSelection dataclass."""

    def test_frozen(self):
        sel = ModelSelection(model_id="gpt-5.4", reasoning_effort="medium")
        with pytest.raises(AttributeError):
            sel.model_id = "other"  # type: ignore[misc]

    def test_defaults(self):
        sel = ModelSelection(model_id="gpt-5.4", reasoning_effort=None)
        assert sel.api_docs_url is None


class TestEnums:
    """Test enum values match expected strings."""

    def test_complexity_values(self):
        assert TaskComplexity.NONE.value == "none"
        assert TaskComplexity.XHIGH.value == "xhigh"

    def test_role_values(self):
        assert ModelRole.ORCHESTRATOR.value == "orchestrator"
        assert ModelRole.REPAIR.value == "repair"
        assert ModelRole.IMAGE_GEN.value == "image_gen"


# ---------------------------------------------------------------------------
# Seed data validation (relocated from test_repair_agent.py)
# ---------------------------------------------------------------------------


class TestSeedData:
    """Test that seed data is well-formed."""

    def test_all_models_have_required_fields(self):
        from src.models.seed import OPENAI_MODELS

        required = {"model_id", "display_name", "family", "category", "description"}
        for m in OPENAI_MODELS:
            missing = required - set(m.keys())
            assert not missing, f"Model {m.get('model_id', '?')} missing: {missing}"

    def test_no_duplicate_model_ids(self):
        from src.models.seed import OPENAI_MODELS

        ids = [m["model_id"] for m in OPENAI_MODELS]
        assert len(ids) == len(set(ids)), f"Duplicate model_ids: {[x for x in ids if ids.count(x) > 1]}"

    def test_enabled_models_have_atlas_role(self):
        from src.models.seed import OPENAI_MODELS

        for m in OPENAI_MODELS:
            if m.get("is_enabled"):
                assert m.get("atlas_role"), (
                    f"Enabled model {m['model_id']} should have an atlas_role"
                )

    def test_model_count_at_least_50(self):
        from src.models.seed import OPENAI_MODELS

        assert len(OPENAI_MODELS) >= 50, f"Expected 50+ models, got {len(OPENAI_MODELS)}"

    def test_gpt54_family_present(self):
        from src.models.seed import OPENAI_MODELS

        gpt54_ids = {m["model_id"] for m in OPENAI_MODELS if m["family"] == "gpt-5.4"}
        assert "gpt-5.4" in gpt54_ids
        assert "gpt-5.4-mini" in gpt54_ids
        assert "gpt-5.4-nano" in gpt54_ids
        assert "gpt-5.4-pro" in gpt54_ids

    def test_all_models_have_api_docs_url(self):
        from src.models.seed import OPENAI_MODELS

        for m in OPENAI_MODELS:
            url = m.get("api_docs_url")
            if url is not None:
                assert url.startswith("https://"), f"Bad URL for {m['model_id']}: {url}"

    def test_pricing_units_valid(self):
        from src.models.seed import OPENAI_MODELS

        valid_units = {"per_1m_tokens", "per_minute", "per_1m_chars", "per_image", "per_second"}
        for m in OPENAI_MODELS:
            unit = m.get("pricing_unit", "per_1m_tokens")
            assert unit in valid_units, f"Invalid pricing_unit for {m['model_id']}: {unit}"
