"""Model router — selects the right model + reasoning effort for each task.

Usage:
    from src.models.router import select_model, ModelRole, TaskComplexity
    sel = select_model(ModelRole.ORCHESTRATOR, TaskComplexity.MEDIUM)
    Agent(name="...", model=sel.model_id, ...)
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional

from src.settings import settings


# ── Enums ──────────────────────────────────────────────────────────────

class TaskComplexity(str, Enum):
    NONE = "none"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    XHIGH = "xhigh"


class ModelRole(str, Enum):
    ORCHESTRATOR = "orchestrator"
    CODING = "coding"
    FAST = "fast"
    GENERAL = "general"
    SAFETY = "safety"
    REFLECTOR = "reflector"
    REPAIR = "repair"
    ROUTING = "routing"
    IMAGE_GEN = "image_gen"
    TTS = "tts"
    REALTIME = "realtime"
    STT = "stt"
    EMBEDDING = "embedding"


# ── Result ─────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ModelSelection:
    model_id: str
    reasoning_effort: Optional[str]
    api_docs_url: Optional[str] = None


# ── Routing matrix ─────────────────────────────────────────────────────
# Maps (role, complexity) → model_id.  Falls back to settings defaults
# for roles not in the matrix.

_ROUTING_MATRIX: dict[ModelRole, dict[TaskComplexity, str]] = {
    ModelRole.ORCHESTRATOR: {
        TaskComplexity.NONE:   "gpt-5.4-nano",
        TaskComplexity.LOW:    "gpt-5.4-mini",
        TaskComplexity.MEDIUM: "gpt-5.4",
        TaskComplexity.HIGH:   "gpt-5.4",
        TaskComplexity.XHIGH:  "gpt-5.4-pro",
    },
    ModelRole.CODING: {
        TaskComplexity.NONE:   "gpt-5.4-nano",
        TaskComplexity.LOW:    "gpt-5.4-mini",
        TaskComplexity.MEDIUM: "gpt-5.4-mini",
        TaskComplexity.HIGH:   "gpt-5.4",
        TaskComplexity.XHIGH:  "gpt-5.4",
    },
    ModelRole.REPAIR: {
        TaskComplexity.NONE:   "gpt-5.4-nano",
        TaskComplexity.LOW:    "gpt-5.4-mini",
        TaskComplexity.MEDIUM: "gpt-5.4-mini",
        TaskComplexity.HIGH:   "gpt-5.4",
        TaskComplexity.XHIGH:  "gpt-5.4",
    },
    ModelRole.SAFETY: {
        TaskComplexity.NONE:   "gpt-5.4-nano",
        TaskComplexity.LOW:    "gpt-5.4-nano",
        TaskComplexity.MEDIUM: "gpt-5.4-nano",
        TaskComplexity.HIGH:   "gpt-5.4-mini",
        TaskComplexity.XHIGH:  "gpt-5.4-mini",
    },
    ModelRole.REFLECTOR: {
        TaskComplexity.NONE:   "gpt-5.4-nano",
        TaskComplexity.LOW:    "gpt-5.4-nano",
        TaskComplexity.MEDIUM: "gpt-5.4-nano",
        TaskComplexity.HIGH:   "gpt-5.4-mini",
        TaskComplexity.XHIGH:  "gpt-5.4-mini",
    },
    ModelRole.FAST: {
        TaskComplexity.NONE:   "gpt-5.4-nano",
        TaskComplexity.LOW:    "gpt-5.4-nano",
        TaskComplexity.MEDIUM: "gpt-5.4-nano",
        TaskComplexity.HIGH:   "gpt-5.4-nano",
        TaskComplexity.XHIGH:  "gpt-5.4-mini",
    },
    ModelRole.ROUTING: {
        TaskComplexity.NONE:   "gpt-5.4-nano",
        TaskComplexity.LOW:    "gpt-5.4-nano",
        TaskComplexity.MEDIUM: "gpt-5.4-nano",
        TaskComplexity.HIGH:   "gpt-5.4-nano",
        TaskComplexity.XHIGH:  "gpt-5.4-mini",
    },
}

# Reasoning effort by complexity (for models that support it).
_EFFORT_MAP: dict[TaskComplexity, str] = {
    TaskComplexity.NONE:   "none",
    TaskComplexity.LOW:    "low",
    TaskComplexity.MEDIUM: "medium",
    TaskComplexity.HIGH:   "high",
    TaskComplexity.XHIGH:  "high",
}

# Models that accept the reasoning_effort parameter.
_REASONING_MODELS: frozenset[str] = frozenset({
    "gpt-5.4", "gpt-5.4-pro", "gpt-5.4-mini", "gpt-5.4-nano",
    "gpt-5", "gpt-5-pro", "gpt-5-mini", "gpt-5-nano",
    "gpt-5.1", "gpt-5.2", "gpt-5.2-pro",
    "o3", "o3-pro", "o3-mini", "o4-mini",
})

# API docs URL prefix.
_DOCS_BASE = "https://developers.openai.com/api/docs/models"


# ── Role → settings default mapping ───────────────────────────────────

def _settings_default(role: ModelRole) -> str:
    """Return the settings-level default model for a role."""
    _map = {
        ModelRole.ORCHESTRATOR: settings.model_orchestrator,
        ModelRole.CODING:       settings.model_coding,
        ModelRole.FAST:         settings.model_fast,
        ModelRole.GENERAL:      settings.model_general,
        ModelRole.SAFETY:       settings.model_safety,
        ModelRole.REFLECTOR:    settings.model_reflector,
        ModelRole.REPAIR:       settings.model_repair,
        ModelRole.ROUTING:      settings.model_routing,
    }
    return _map.get(role, settings.model_general)


# ── Public API ─────────────────────────────────────────────────────────

def select_model(
    role: ModelRole,
    complexity: Optional[TaskComplexity] = None,
) -> ModelSelection:
    """Pick the right model + reasoning effort for a given role and task
    complexity.

    If *complexity* is ``None``, the settings-level default model for
    the role is returned with the default reasoning effort.
    """
    if complexity is not None and role in _ROUTING_MATRIX:
        model_id = _ROUTING_MATRIX[role][complexity]
        effort = _EFFORT_MAP.get(complexity, settings.default_reasoning_effort)
    else:
        model_id = _settings_default(role)
        effort = settings.default_reasoning_effort

    reasoning: Optional[str] = effort if model_id in _REASONING_MODELS else None

    return ModelSelection(
        model_id=model_id,
        reasoning_effort=reasoning,
        api_docs_url=f"{_DOCS_BASE}/{model_id}",
    )
