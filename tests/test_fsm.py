"""Tests for Wave 2.4 — plan→act→observe→revise FSM module.

This is the foundation for Wave 2.5 (checkpoint/resume) and Wave 2.6
(verification-aware subtask checks). The FSM contract these tests pin:

1. **Transition log is append-only** — every transition (including rejected
   ones) lands in ``state.history`` so the audit trail is complete.
2. **Snapshots round-trip** — JSON-serializing a state and re-hydrating it
   must produce an equivalent runner. Without this, Wave 2.5 can't store
   FSM state in Redis.
3. **Verifier gate** — when a verifier rejects a transition, the FSM stays
   in the current phase but logs the rejection. Wave 2.6 uses this to encode
   per-step pass/fail predicates.
4. **Terminal phases stop progression** — once ``DONE`` or ``FAILED`` is
   reached, ``is_terminal`` flips and downstream consumers can break out.
5. **Payload merge semantics** — payloads on each transition merge into the
   running state, last-write-wins.
"""

from __future__ import annotations

import pytest

# No ``agents`` SDK stubbing here — ``src/agents/fsm.py`` is pure Python with
# no dependency on the OpenAI Agents SDK. Stubbing would silently pollute
# sys.modules and break downstream tests (e.g. ``test_repair_agent.py``)
# that DO need the real SDK to inspect ``Agent.tools`` correctly.


# --------------------------------------------------------------------------
# Basic transitions
# --------------------------------------------------------------------------


class TestBasicTransitions:
    def test_new_runner_starts_in_plan(self) -> None:
        from src.agents.fsm import new_runner, Phase

        runner = new_runner("flow-1")
        assert runner.phase == Phase.PLAN
        # The constructor logs the start transition so history isn't empty
        assert len(runner.state.history) == 1
        assert runner.state.history[0].from_phase is None
        assert runner.state.history[0].to_phase == Phase.PLAN

    def test_canonical_loop_plan_act_observe_revise_done(self) -> None:
        from src.agents.fsm import new_runner, Phase

        runner = new_runner("repair-42")
        assert runner.transition(Phase.ACT, reason="executing fix")
        assert runner.transition(Phase.OBSERVE, reason="reading qa output")
        assert runner.transition(Phase.REVISE, reason="qa requested revision")
        assert runner.transition(Phase.PLAN, reason="re-planning after revise")
        assert runner.transition(Phase.ACT, reason="retry")
        assert runner.transition(Phase.DONE, reason="qa passed")

        assert runner.phase == Phase.DONE
        assert runner.state.is_terminal

        # Step counter equals the number of explicit transitions (start
        # transition counts as step 0; first explicit move is step 1).
        assert runner.state.step_id == 6

    def test_payload_merges_last_write_wins(self) -> None:
        from src.agents.fsm import new_runner, Phase

        runner = new_runner("flow-1", initial_payload={"a": 1, "b": 2})
        runner.transition(Phase.ACT, payload={"b": 99, "c": 3})
        assert runner.state.payload == {"a": 1, "b": 99, "c": 3}

    def test_fail_and_complete_helpers(self) -> None:
        from src.agents.fsm import new_runner, Phase

        runner = new_runner("flow-1")
        runner.fail("verifier rejected patch", attempt=2)
        assert runner.phase == Phase.FAILED
        assert runner.state.is_terminal

        runner2 = new_runner("flow-2")
        runner2.complete("qa passed", confidence=0.92)
        assert runner2.phase == Phase.DONE
        assert runner2.state.payload == {"confidence": 0.92}


# --------------------------------------------------------------------------
# Snapshot round-trip — required for Wave 2.5 checkpoint/resume
# --------------------------------------------------------------------------


class TestSnapshotRoundTrip:
    def test_to_json_then_from_json_preserves_phase_and_history(self) -> None:
        from src.agents.fsm import new_runner, resume_runner, FSMState, Phase

        runner = new_runner("ticket-77")
        runner.transition(Phase.ACT, reason="generating patch")
        runner.transition(Phase.OBSERVE, reason="qa pending")

        raw = runner.state.to_json()
        rehydrated = FSMState.from_json(raw)
        runner2 = resume_runner(rehydrated)

        assert runner2.phase == Phase.OBSERVE
        assert runner2.state.flow_id == "ticket-77"
        assert runner2.state.step_id == 2
        # History preserves order + reasons
        reasons = [t.reason for t in runner2.state.history]
        assert "flow start" in reasons
        assert "generating patch" in reasons
        assert "qa pending" in reasons

    def test_resumed_runner_can_continue_transitions(self) -> None:
        """The whole point of resume — picking up where we left off."""
        from src.agents.fsm import new_runner, resume_runner, FSMState, Phase

        runner = new_runner("ticket-99")
        runner.transition(Phase.ACT)
        runner.transition(Phase.OBSERVE)

        # Simulate a container restart
        snapshot_raw = runner.state.to_json()
        del runner

        runner2 = resume_runner(FSMState.from_json(snapshot_raw))
        runner2.transition(Phase.REVISE, reason="post-restart")
        runner2.complete("done after restart")

        assert runner2.phase == Phase.DONE
        # All four explicit transitions logged: ACT, OBSERVE, REVISE, DONE
        # plus the synthetic start transition from new_runner.
        explicit_transitions = [t for t in runner2.state.history if t.from_phase is not None]
        assert len(explicit_transitions) == 4


# --------------------------------------------------------------------------
# Verifier gate — Wave 2.6 hook point
# --------------------------------------------------------------------------


class TestVerifierGate:
    def test_rejection_keeps_phase_and_logs_attempt(self) -> None:
        from src.agents.fsm import new_runner, Phase, VerifyResult

        def block_act(from_p, to_p, payload):
            if to_p == Phase.ACT:
                return VerifyResult(allowed=False, reason="patch confidence too low")
            return VerifyResult(allowed=True)

        runner = new_runner("flow-1", verifier=block_act)
        ok = runner.transition(Phase.ACT, reason="want to patch")
        assert ok is False
        assert runner.phase == Phase.PLAN  # stayed put

        rejected_entries = [t for t in runner.state.history if t.reason.startswith("REJECTED")]
        assert len(rejected_entries) == 1
        assert "patch confidence too low" in rejected_entries[0].reason
        assert rejected_entries[0].payload.get("attempted_to") == "act"

    def test_verifier_can_allow_transitions_through(self) -> None:
        from src.agents.fsm import new_runner, Phase, VerifyResult

        def allow_all(from_p, to_p, payload):
            return VerifyResult(allowed=True, reason="checked OK")

        runner = new_runner("flow-1", verifier=allow_all)
        assert runner.transition(Phase.ACT)
        assert runner.transition(Phase.OBSERVE)
        assert runner.phase == Phase.OBSERVE

    def test_on_transition_hook_fires_for_both_accepts_and_rejects(self) -> None:
        from src.agents.fsm import new_runner, Phase, VerifyResult

        seen: list[tuple[str, str]] = []

        def hook(t):
            from_v = t.from_phase.value if t.from_phase else None
            seen.append((from_v, t.to_phase.value, t.reason))

        def reject_act(from_p, to_p, payload):
            if to_p == Phase.ACT:
                return VerifyResult(allowed=False, reason="nope")
            return VerifyResult(allowed=True)

        runner = new_runner("flow-1", verifier=reject_act, on_transition=hook)
        runner.transition(Phase.ACT)  # rejected
        runner.transition(Phase.OBSERVE)  # accepted

        # 2 hook fires (1 reject + 1 accept). The synthetic "flow start"
        # transition is logged in history but doesn't fire the hook because
        # it happens during runner construction, before hook registration
        # would have been useful — tests here pin the actual user-driven
        # transitions only.
        assert len(seen) == 2
        assert seen[0][2].startswith("REJECTED")
        assert seen[1][1] == "observe"


# --------------------------------------------------------------------------
# Terminal phase semantics
# --------------------------------------------------------------------------


class TestTerminalPhases:
    def test_done_marks_terminal(self) -> None:
        from src.agents.fsm import new_runner, Phase

        runner = new_runner("flow-1")
        runner.complete("all good")
        assert runner.state.is_terminal

    def test_failed_marks_terminal(self) -> None:
        from src.agents.fsm import new_runner

        runner = new_runner("flow-1")
        runner.fail("rolled back")
        assert runner.state.is_terminal

    def test_intermediate_phases_not_terminal(self) -> None:
        from src.agents.fsm import new_runner, Phase

        runner = new_runner("flow-1")
        for phase in (Phase.ACT, Phase.OBSERVE, Phase.REVISE, Phase.PLAN):
            runner.transition(phase)
            assert not runner.state.is_terminal, f"{phase.value} should not be terminal"


# --------------------------------------------------------------------------
# Hook side-effect isolation — observability must never break the FSM
# --------------------------------------------------------------------------


class TestHookErrorIsolation:
    def test_hook_exception_does_not_break_transition(self) -> None:
        """A buggy on_transition hook (e.g., Redis down during checkpoint
        write) must not abort the FSM. The transition still applies."""
        from src.agents.fsm import new_runner, Phase

        def boom(_t):
            raise RuntimeError("simulated logging failure")

        runner = new_runner("flow-1", on_transition=boom)
        ok = runner.transition(Phase.ACT, reason="should still apply")
        assert ok is True
        assert runner.phase == Phase.ACT
