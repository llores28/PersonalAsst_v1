"""Plan → Act → Observe → Revise finite-state machine (Wave 2.4).

Atlas already has multi-step agent flows — the repair pipeline (Debugger →
Programmer → QualityControl → Sandbox), the calendar/Gmail short-circuits
(parse → tool → format), and the meta-reflector (gather → reflect → propose).
Each one re-implements its own ad-hoc state tracking and logs transitions
inconsistently or not at all.

This module provides the missing abstraction: a tiny, generic FSM with named
phases, an append-only transition history, and a serializable snapshot. It's
intentionally minimal — no graph engine, no scheduling, no checkpointing
itself. Those concerns belong to the callers (Wave 2.5 will use the snapshot
for Redis checkpoint/resume; Wave 2.6 will gate each transition on per-step
verification predicates).

The four canonical phases (chosen to match the 2026 "Plan-and-Act" agent
research line, see https://openreview.net/forum?id=ybA4EcMmUZ):

- ``PLAN``    — gather inputs, classify the task, sketch a strategy
- ``ACT``     — execute the strategy (call tools, emit patches, send messages)
- ``OBSERVE`` — read the result of the action; collect evidence
- ``REVISE``  — decide what to change based on observation; loop back to PLAN
                or transition to DONE/FAILED
- ``DONE`` / ``FAILED`` are terminal

The FSM doesn't enforce transition legality at runtime — callers can move
between phases in whatever order makes sense for their domain. The audit
log captures every transition with its reason and timestamp, which is the
real value for debugging and the eventual checkpoint/resume protocol.
"""

from __future__ import annotations

import enum
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)


class Phase(str, enum.Enum):
    """Canonical FSM phases. String values so JSON serialization round-trips."""

    PLAN = "plan"
    ACT = "act"
    OBSERVE = "observe"
    REVISE = "revise"
    DONE = "done"
    FAILED = "failed"


# Phases past which a runner is considered finished. Useful for callers that
# poll status without caring about the exact terminal state.
TERMINAL_PHASES: frozenset[Phase] = frozenset({Phase.DONE, Phase.FAILED})


@dataclass
class Transition:
    """One entry in the FSM's append-only transition log."""

    from_phase: Optional[Phase]
    to_phase: Phase
    reason: str = ""
    payload: dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "from_phase": self.from_phase.value if self.from_phase else None,
            "to_phase": self.to_phase.value,
            "reason": self.reason,
            "payload": self.payload,
            "timestamp": self.timestamp,
        }


@dataclass
class FSMState:
    """Snapshot of an FSM at one point in time. Serializable for checkpoints
    (Wave 2.5 will pickle this into Redis under ``agent_session:{user_id}``)."""

    flow_id: str  # opaque identifier — typically a UUID or ticket id
    phase: Phase = Phase.PLAN
    step_id: int = 0  # monotonically increases on every transition
    payload: dict[str, Any] = field(default_factory=dict)
    history: list[Transition] = field(default_factory=list)
    started_at: float = field(default_factory=time.time)

    @property
    def is_terminal(self) -> bool:
        return self.phase in TERMINAL_PHASES

    def to_dict(self) -> dict[str, Any]:
        return {
            "flow_id": self.flow_id,
            "phase": self.phase.value,
            "step_id": self.step_id,
            "payload": self.payload,
            "history": [t.to_dict() for t in self.history],
            "started_at": self.started_at,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), default=str)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "FSMState":
        history = [
            Transition(
                from_phase=Phase(t["from_phase"]) if t.get("from_phase") else None,
                to_phase=Phase(t["to_phase"]),
                reason=t.get("reason", ""),
                payload=t.get("payload", {}),
                timestamp=t.get("timestamp", 0.0),
            )
            for t in data.get("history", [])
        ]
        return cls(
            flow_id=data["flow_id"],
            phase=Phase(data["phase"]),
            step_id=int(data.get("step_id", 0)),
            payload=data.get("payload", {}),
            history=history,
            started_at=float(data.get("started_at", time.time())),
        )

    @classmethod
    def from_json(cls, raw: str) -> "FSMState":
        return cls.from_dict(json.loads(raw))


# Optional gate: callers can register a verifier that runs before each
# transition. Used by Wave 2.6 to encode pass/fail predicates per phase.
TransitionVerifier = Callable[[Phase, Phase, dict[str, Any]], "VerifyResult"]


@dataclass
class VerifyResult:
    """Outcome of a pre-transition verification check."""

    allowed: bool
    reason: str = ""


class FSMRunner:
    """Drives a single ``FSMState`` through transitions with audit logging.

    Designed for flows that span seconds-to-minutes: a repair attempt, a
    meta-reflection cycle, a long-running calendar query. Not designed for
    sub-millisecond hot paths.

    Concurrency: not thread-safe. Each runner is intended to be owned by one
    asyncio task at a time (or one Redis-locked owner across processes).
    """

    def __init__(
        self,
        state: FSMState,
        *,
        verifier: Optional[TransitionVerifier] = None,
        on_transition: Optional[Callable[[Transition], None]] = None,
    ) -> None:
        self.state = state
        self._verifier = verifier
        self._on_transition = on_transition

    @property
    def phase(self) -> Phase:
        return self.state.phase

    def set_verifier(self, verifier: Optional[TransitionVerifier]) -> None:
        """Install or replace the transition verifier after construction.

        The repair pipeline can't supply a verifier at runner-creation time
        because the patch + dry-run output don't exist yet. It calls this
        once the Programmer + dry-run stages produce the artifacts the
        verifier needs to inspect."""
        self._verifier = verifier

    def transition(
        self,
        new_phase: Phase,
        *,
        reason: str = "",
        payload: Optional[dict[str, Any]] = None,
    ) -> bool:
        """Move the FSM to ``new_phase``. Returns True on success.

        Returns False if a registered verifier rejects the transition. The
        FSM stays in the current phase and the rejected attempt is logged
        as a no-op transition so audit trails capture the attempted move.
        """
        from_phase = self.state.phase
        merged_payload = {**(payload or {})}

        if self._verifier is not None:
            verdict = self._verifier(from_phase, new_phase, merged_payload)
            if not verdict.allowed:
                rejection = Transition(
                    from_phase=from_phase,
                    to_phase=from_phase,  # no-op transition: stayed put
                    reason=f"REJECTED: {verdict.reason or 'verifier denied transition'}",
                    payload={**merged_payload, "attempted_to": new_phase.value},
                )
                self.state.history.append(rejection)
                logger.info(
                    "FSM[%s] transition %s→%s rejected: %s",
                    self.state.flow_id, from_phase.value, new_phase.value, verdict.reason,
                )
                if self._on_transition is not None:
                    try:
                        self._on_transition(rejection)
                    except Exception as exc:  # noqa: BLE001
                        logger.debug("FSM on_transition hook raised (ignored): %s", exc)
                return False

        # Apply the transition
        self.state.phase = new_phase
        self.state.step_id += 1
        if payload:
            self.state.payload = {**self.state.payload, **payload}

        entry = Transition(
            from_phase=from_phase,
            to_phase=new_phase,
            reason=reason,
            payload=merged_payload,
        )
        self.state.history.append(entry)

        logger.debug(
            "FSM[%s] %s→%s (step %d): %s",
            self.state.flow_id, from_phase.value, new_phase.value,
            self.state.step_id, reason or "(no reason)",
        )

        if self._on_transition is not None:
            try:
                self._on_transition(entry)
            except Exception as exc:  # noqa: BLE001 — observability is best-effort
                logger.debug("FSM on_transition hook raised (ignored): %s", exc)

        return True

    def fail(self, reason: str, **payload: Any) -> None:
        """Convenience: transition to FAILED, logging the reason."""
        self.transition(Phase.FAILED, reason=reason, payload=payload)

    def complete(self, reason: str = "done", **payload: Any) -> None:
        """Convenience: transition to DONE."""
        self.transition(Phase.DONE, reason=reason, payload=payload)

    def snapshot(self) -> FSMState:
        """Return the current state — typed as the same dataclass so callers
        can pickle/JSON it without inspecting internals. The state is shared
        (not deep-copied) so further transitions are reflected; callers that
        need a frozen view should ``copy.deepcopy`` themselves."""
        return self.state


# ── Convenience constructors ────────────────────────────────────────────


def new_runner(
    flow_id: str,
    *,
    verifier: Optional[TransitionVerifier] = None,
    on_transition: Optional[Callable[[Transition], None]] = None,
    initial_payload: Optional[dict[str, Any]] = None,
) -> FSMRunner:
    """Build a fresh FSMRunner starting in ``Phase.PLAN``.

    Most callers should use this rather than constructing ``FSMState`` and
    ``FSMRunner`` separately — it bakes in the canonical entrypoint phase.
    """
    state = FSMState(flow_id=flow_id, phase=Phase.PLAN, payload=initial_payload or {})
    state.history.append(Transition(
        from_phase=None,
        to_phase=Phase.PLAN,
        reason="flow start",
        payload={},
    ))
    return FSMRunner(state, verifier=verifier, on_transition=on_transition)


def resume_runner(
    snapshot: FSMState,
    *,
    verifier: Optional[TransitionVerifier] = None,
    on_transition: Optional[Callable[[Transition], None]] = None,
) -> FSMRunner:
    """Rehydrate a runner from a stored snapshot. Wave 2.5 uses this to
    resume a partially-applied repair after a container restart."""
    return FSMRunner(snapshot, verifier=verifier, on_transition=on_transition)


# ── Repair-pipeline phase mapping ───────────────────────────────────────


# Maps the existing repair-pipeline ``PipelineStage`` values to the abstract
# four-phase FSM. Kept here (rather than in ``src/repair/models.py``) so the
# FSM module stays the single source of truth for phase semantics, and the
# repair engine doesn't have to re-encode the same mapping inline.
#
# The same enum values appear here as raw strings so this module doesn't
# import the repair models (avoids a circular dep — repair imports agents,
# and agents depending on repair would close that loop).
_REPAIR_STAGE_TO_PHASE: dict[str, Phase] = {
    "error_detected": Phase.PLAN,
    "debugging": Phase.PLAN,
    "debug_analysis_ready": Phase.PLAN,
    "ticket_created": Phase.PLAN,
    "programming": Phase.ACT,
    "fix_generated": Phase.ACT,
    "qa_validation": Phase.OBSERVE,
    "qa_passed": Phase.OBSERVE,
    "qa_failed": Phase.REVISE,
    "sandbox_testing": Phase.ACT,
    "sandbox_passed": Phase.OBSERVE,
    "sandbox_failed": Phase.REVISE,
    "awaiting_approval": Phase.OBSERVE,
    "approved": Phase.ACT,
    "deployed": Phase.DONE,
    "failed": Phase.FAILED,
}


def map_repair_stage(stage_value: str) -> Phase:
    """Return the canonical Phase for a repair-pipeline stage value.

    Unknown stages map to ``Phase.PLAN`` — we'd rather under-classify than
    crash the audit trail on a stage we forgot to add to the table.
    """
    return _REPAIR_STAGE_TO_PHASE.get(stage_value, Phase.PLAN)
