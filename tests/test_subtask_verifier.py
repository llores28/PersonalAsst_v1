"""Tests for Wave 2.6 — verification-aware subtask checks.

The deterministic floor under ``quality_control_agent``: four pure-Python
predicates that gate the FSM's OBSERVE→ACT (sandbox) transition without
calling an LLM. These tests pin both the individual predicates and the
aggregate decision logic.

Why this matters: the LLM-driven QA agent occasionally produces inconsistent
decisions on identical inputs (same patch, different runs → GO once, NO_GO
the next). Having a deterministic gate underneath means a security
regression (e.g., a patch with ``eval(``) ALWAYS fails verification.
"""

from __future__ import annotations

import pytest


# --------------------------------------------------------------------------
# Predicate: patch_applies_cleanly
# --------------------------------------------------------------------------


class TestPatchAppliesPredicate:
    def test_clean_dry_run_passes(self) -> None:
        from src.agents.subtask_verifier import check_patch_applies, CheckStatus

        result = check_patch_applies("Checking patch src/foo.py...\nDone.\n")
        assert result.status == CheckStatus.PASS

    def test_error_in_dry_run_fails(self) -> None:
        from src.agents.subtask_verifier import check_patch_applies, CheckStatus

        result = check_patch_applies("error: patch fragment without header at line 12")
        assert result.status == CheckStatus.FAIL
        assert result.evidence  # has the dry-run output for the user

    def test_rejected_keyword_fails(self) -> None:
        from src.agents.subtask_verifier import check_patch_applies, CheckStatus

        result = check_patch_applies("patch rejected: file does not exist")
        assert result.status == CheckStatus.FAIL

    def test_missing_dry_run_skips(self) -> None:
        """Conservative: empty/missing dry-run output skips, doesn't pass.
        The aggregator treats SKIP as failure-equivalent for blocker checks."""
        from src.agents.subtask_verifier import check_patch_applies, CheckStatus

        for empty in (None, "", "   "):
            result = check_patch_applies(empty)
            assert result.status == CheckStatus.SKIP


# --------------------------------------------------------------------------
# Predicate: security_patterns
# --------------------------------------------------------------------------


class TestSecurityPatternsPredicate:
    def test_eval_in_added_line_fails(self) -> None:
        from src.agents.subtask_verifier import check_security_patterns, CheckStatus

        diff = (
            "--- a/src/foo.py\n"
            "+++ b/src/foo.py\n"
            "@@ -1,3 +1,3 @@\n"
            "-result = compute(x)\n"
            "+result = eval(x)\n"
            " return result\n"
        )
        result = check_security_patterns(diff)
        assert result.status == CheckStatus.FAIL
        assert any("eval_usage" in e for e in result.evidence)

    def test_eval_in_removed_line_passes(self) -> None:
        """Removing an ``eval(`` is a security improvement, not a regression."""
        from src.agents.subtask_verifier import check_security_patterns, CheckStatus

        diff = (
            "--- a/src/foo.py\n"
            "+++ b/src/foo.py\n"
            "@@ -1,3 +1,3 @@\n"
            "-result = eval(x)\n"
            "+result = ast.literal_eval(x)\n"
            " return result\n"
        )
        result = check_security_patterns(diff)
        assert result.status == CheckStatus.PASS

    def test_shell_true_caught(self) -> None:
        from src.agents.subtask_verifier import check_security_patterns, CheckStatus

        diff = "+    subprocess.run(cmd, shell=True)\n"
        result = check_security_patterns(diff)
        assert result.status == CheckStatus.FAIL

    def test_hardcoded_secret_caught(self) -> None:
        from src.agents.subtask_verifier import check_security_patterns, CheckStatus

        diff = '+    api_token = "sk-abcdef1234567890"\n'
        result = check_security_patterns(diff)
        assert result.status == CheckStatus.FAIL

    def test_clean_diff_passes(self) -> None:
        from src.agents.subtask_verifier import check_security_patterns, CheckStatus

        diff = (
            "--- a/src/foo.py\n"
            "+++ b/src/foo.py\n"
            "@@ -1 +1 @@\n"
            "-x = 1\n"
            "+x = 2\n"
        )
        result = check_security_patterns(diff)
        assert result.status == CheckStatus.PASS

    def test_empty_diff_skips(self) -> None:
        from src.agents.subtask_verifier import check_security_patterns, CheckStatus

        result = check_security_patterns("")
        assert result.status == CheckStatus.SKIP


# --------------------------------------------------------------------------
# Predicate: test_commands_allowlisted
# --------------------------------------------------------------------------


class TestTestCommandsAllowlistedPredicate:
    def test_allowlisted_passes(self) -> None:
        from src.agents.subtask_verifier import check_test_commands_allowlisted, CheckStatus

        result = check_test_commands_allowlisted([
            "python -m pytest tests/test_foo.py -v",
            "ruff check src/",
        ])
        assert result.status == CheckStatus.PASS

    def test_arbitrary_shell_command_fails(self) -> None:
        from src.agents.subtask_verifier import check_test_commands_allowlisted, CheckStatus

        result = check_test_commands_allowlisted([
            "rm -rf /etc/passwd",
            "python -m pytest",
        ])
        assert result.status == CheckStatus.FAIL
        assert any("rm -rf" in e for e in result.evidence)

    def test_empty_test_plan_skips(self) -> None:
        from src.agents.subtask_verifier import check_test_commands_allowlisted, CheckStatus

        result = check_test_commands_allowlisted([])
        assert result.status == CheckStatus.SKIP


# --------------------------------------------------------------------------
# Predicate: files_in_scope
# --------------------------------------------------------------------------


class TestFilesInScopePredicate:
    def test_in_scope_passes(self) -> None:
        from src.agents.subtask_verifier import check_files_in_scope, CheckStatus

        diff = (
            "diff --git a/src/foo.py b/src/foo.py\n"
            "--- a/src/foo.py\n"
            "+++ b/src/foo.py\n"
        )
        result = check_files_in_scope(diff, ["src/foo.py"])
        assert result.status == CheckStatus.PASS

    def test_out_of_scope_fails(self) -> None:
        from src.agents.subtask_verifier import check_files_in_scope, CheckStatus

        diff = (
            "diff --git a/src/foo.py b/src/foo.py\n"
            "--- a/src/foo.py\n"
            "+++ b/src/foo.py\n"
            "diff --git a/src/sneaky.py b/src/sneaky.py\n"
            "--- a/src/sneaky.py\n"
            "+++ b/src/sneaky.py\n"
        )
        result = check_files_in_scope(diff, ["src/foo.py"])
        assert result.status == CheckStatus.FAIL
        assert "src/sneaky.py" in result.evidence


# --------------------------------------------------------------------------
# Aggregator decision
# --------------------------------------------------------------------------


class TestAggregateDecision:
    def test_all_pass_is_go(self) -> None:
        from src.agents.subtask_verifier import (
            run_subtask_checks, aggregate_decision,
        )

        results = run_subtask_checks(
            diff_content="--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-1\n+2\n",
            declared_affected_files=["x.py"],
            test_plan=["pytest tests/test_x.py"],
            dry_run_output="Checking patch x.py...\nDone.\n",
        )
        assert aggregate_decision(results) == "GO"

    def test_blocker_fail_is_no_go(self) -> None:
        from src.agents.subtask_verifier import (
            run_subtask_checks, aggregate_decision,
        )

        results = run_subtask_checks(
            diff_content="+    eval(user_input)\n",
            declared_affected_files=["x.py"],
            test_plan=["pytest"],
            dry_run_output="Done.\n",
        )
        assert aggregate_decision(results) == "NO_GO"

    def test_only_warn_fail_is_needs_revision(self) -> None:
        """A scope drift is WARN-severity (not BLOCKER), so it degrades to
        NEEDS_REVISION rather than NO_GO."""
        from src.agents.subtask_verifier import (
            run_subtask_checks, aggregate_decision,
        )

        diff = (
            "diff --git a/src/foo.py b/src/foo.py\n"
            "--- a/src/foo.py\n"
            "+++ b/src/foo.py\n"
            "diff --git a/src/elsewhere.py b/src/elsewhere.py\n"
            "--- a/src/elsewhere.py\n"
            "+++ b/src/elsewhere.py\n"
        )
        results = run_subtask_checks(
            diff_content=diff,
            declared_affected_files=["src/foo.py"],  # elsewhere.py NOT declared
            test_plan=["pytest"],
            dry_run_output="Done.\n",
        )
        assert aggregate_decision(results) == "NEEDS_REVISION"


# --------------------------------------------------------------------------
# FSM integration — make_repair_verifier
# --------------------------------------------------------------------------


class TestFSMVerifier:
    def test_blocker_fail_rejects_observe_to_act_transition(self) -> None:
        """Patch with ``eval(`` must NOT progress to sandbox testing."""
        from src.agents.fsm import new_runner, Phase
        from src.agents.subtask_verifier import make_repair_verifier

        verifier = make_repair_verifier(
            diff_content="+result = eval(input())\n",
            declared_affected_files=["x.py"],
            test_plan=["pytest"],
            dry_run_output="Done.\n",
        )
        runner = new_runner("repair-1", verifier=verifier)
        runner.transition(Phase.ACT, reason="generate patch")
        runner.transition(Phase.OBSERVE, reason="qa pending")

        # Now try to advance to sandbox-act — verifier should reject
        ok = runner.transition(Phase.ACT, reason="run sandbox")
        assert ok is False
        assert runner.phase == Phase.OBSERVE  # stayed put
        rejected = [t for t in runner.state.history if t.reason.startswith("REJECTED")]
        assert any("eval_usage" in t.reason or "security_patterns" in t.reason for t in rejected)

    def test_clean_patch_allows_observe_to_act_transition(self) -> None:
        from src.agents.fsm import new_runner, Phase
        from src.agents.subtask_verifier import make_repair_verifier

        verifier = make_repair_verifier(
            diff_content=(
                "diff --git a/x.py b/x.py\n"
                "--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-1\n+2\n"
            ),
            declared_affected_files=["x.py"],
            test_plan=["pytest tests/test_x.py"],
            dry_run_output="Checking patch x.py...\nDone.\n",
        )
        runner = new_runner("repair-2", verifier=verifier)
        runner.transition(Phase.ACT)
        runner.transition(Phase.OBSERVE)
        ok = runner.transition(Phase.ACT, reason="run sandbox")
        assert ok is True
        assert runner.phase == Phase.ACT

    def test_other_transitions_unaffected_by_verifier(self) -> None:
        """The verifier ONLY gates OBSERVE→ACT (post-QA → sandbox). Earlier
        phases must transition freely even with a bad diff."""
        from src.agents.fsm import new_runner, Phase
        from src.agents.subtask_verifier import make_repair_verifier

        verifier = make_repair_verifier(
            diff_content="+exec(user_code)\n",  # would block sandbox
            declared_affected_files=["x.py"],
            test_plan=["pytest"],
            dry_run_output="Done.\n",
        )
        runner = new_runner("repair-3", verifier=verifier)
        # PLAN → ACT (initial fix generation) is allowed
        assert runner.transition(Phase.ACT) is True
        # ACT → OBSERVE (running QA) is allowed
        assert runner.transition(Phase.OBSERVE) is True
        # Only OBSERVE → ACT (sandbox) gets blocked
        assert runner.transition(Phase.ACT) is False
