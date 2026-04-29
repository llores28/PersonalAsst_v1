"""Pure-Python subtask predicates for the repair pipeline (Wave 2.6).

The existing ``quality_control_agent`` collapses every QA failure into a
single ``GO|NO_GO|NEEDS_REVISION`` decision. That's coarse: when QA rejects,
the user can't tell whether the patch failed dry-run, tripped a security
pattern, touched files outside its scope, or scheduled a non-allowlisted
test command. This module exposes each of those checks as an individually
runnable predicate so callers (the FSM verifier hook, the dashboard, future
adversarial eval) can show finer-grained results.

Layered on top of (not replacing) ``quality_control_agent``:
- The LLM-driven QA agent still produces the ultimate human-readable
  ``revision_feedback`` and the rolled-up decision.
- This module provides the deterministic floor: 4 pure-Python predicates
  that always run, never call an LLM, and can gate the FSM's
  ``Phase.OBSERVE → Phase.ACT`` (sandbox) transition deterministically.

Why deterministic floor: the LLM-driven QA agent occasionally produces
inconsistent decisions on identical inputs. Having a deterministic gate
underneath means a security regression (e.g., a patch with ``eval(``)
ALWAYS fails verification, regardless of LLM mood.
"""

from __future__ import annotations

import enum
import re
from dataclasses import dataclass, field
from typing import Optional


class CheckStatus(str, enum.Enum):
    """Outcome of a single subtask check."""

    PASS = "pass"
    FAIL = "fail"
    SKIP = "skip"  # input was missing or not applicable


class CheckSeverity(str, enum.Enum):
    """How seriously a failed check should affect the aggregate decision.

    ``BLOCKER`` failures force NO_GO. ``WARN`` failures degrade to
    NEEDS_REVISION but don't outright reject. ``INFO`` is reported but
    doesn't influence the decision.
    """

    BLOCKER = "blocker"
    WARN = "warn"
    INFO = "info"


@dataclass
class SubtaskCheckResult:
    """One predicate's verdict on the proposed patch."""

    name: str
    status: CheckStatus
    severity: CheckSeverity = CheckSeverity.WARN
    message: str = ""
    evidence: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return self.status == CheckStatus.PASS

    @property
    def failed(self) -> bool:
        return self.status == CheckStatus.FAIL

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "status": self.status.value,
            "severity": self.severity.value,
            "message": self.message,
            "evidence": self.evidence,
        }


# Mirrors quality_control_agent._SECURITY_PATTERNS — kept in this module
# (rather than imported) so this module has zero dependency on the agents
# SDK. If the canonical list ever drifts, the test suite catches it.
_SECURITY_PATTERNS: dict[str, re.Pattern[str]] = {
    "eval_usage": re.compile(r'\beval\s*\(', re.IGNORECASE),
    "exec_usage": re.compile(r'\bexec\s*\(', re.IGNORECASE),
    "shell_true": re.compile(r'shell\s*=\s*True', re.IGNORECASE),
    "subprocess_shell": re.compile(r'subprocess\.call.*shell', re.IGNORECASE),
    "os_system": re.compile(r'os\.system\s*\(', re.IGNORECASE),
    "hardcoded_secret": re.compile(
        r'(password|secret|key|token)\s*=\s*["\'][^"\']{8,}["\']', re.IGNORECASE,
    ),
    "pickle_loads": re.compile(r'pickle\.loads?\s*\(', re.IGNORECASE),
    "yaml_unsafe": re.compile(r'yaml\.load\s*\([^)]*\)(?!.*Loader)', re.IGNORECASE),
}


_ALLOWLISTED_TEST_PREFIXES: tuple[str, ...] = (
    "python -m pytest",
    "pytest",
    "python -m ruff check",
    "ruff check",
    "python -m ruff format --check",
    "ruff format --check",
    "python -m mypy",
    "mypy",
    "python -m bandit",
    "bandit",
)


# ── Individual predicates ───────────────────────────────────────────────


def check_patch_applies(dry_run_output: Optional[str]) -> SubtaskCheckResult:
    """Did ``git apply --check`` (or equivalent) report a clean apply?

    Conservative: missing/empty dry-run output is SKIP, not PASS — we don't
    want to wave a patch through just because the runner forgot to invoke
    the dry-run.
    """
    if not dry_run_output or not dry_run_output.strip():
        return SubtaskCheckResult(
            name="patch_applies_cleanly",
            status=CheckStatus.SKIP,
            severity=CheckSeverity.BLOCKER,
            message="dry-run output not provided",
        )

    lowered = dry_run_output.lower()
    if "error" in lowered or "fail" in lowered or "rejected" in lowered:
        return SubtaskCheckResult(
            name="patch_applies_cleanly",
            status=CheckStatus.FAIL,
            severity=CheckSeverity.BLOCKER,
            message="dry-run reported errors",
            evidence=[dry_run_output[:300]],
        )
    return SubtaskCheckResult(
        name="patch_applies_cleanly",
        status=CheckStatus.PASS,
        severity=CheckSeverity.BLOCKER,
        message="dry-run clean",
    )


def check_security_patterns(diff_content: str) -> SubtaskCheckResult:
    """Scan ADDED lines in the diff for known-dangerous patterns. Removed
    or context lines are ignored — a patch that *removes* an ``eval(`` is
    cleanup, not a regression."""
    if not diff_content:
        return SubtaskCheckResult(
            name="security_patterns",
            status=CheckStatus.SKIP,
            severity=CheckSeverity.BLOCKER,
            message="empty diff",
        )

    issues: list[str] = []
    for line_no, line in enumerate(diff_content.splitlines(), 1):
        if not line.startswith("+") or line.startswith("+++"):
            continue
        for issue_name, pattern in _SECURITY_PATTERNS.items():
            if pattern.search(line):
                issues.append(f"L{line_no} {issue_name}: {line.strip()[:120]}")

    if issues:
        return SubtaskCheckResult(
            name="security_patterns",
            status=CheckStatus.FAIL,
            severity=CheckSeverity.BLOCKER,
            message=f"{len(issues)} security pattern(s) matched",
            evidence=issues[:10],  # cap to keep payload bounded
        )
    return SubtaskCheckResult(
        name="security_patterns",
        status=CheckStatus.PASS,
        severity=CheckSeverity.BLOCKER,
        message="no dangerous patterns detected",
    )


def check_test_commands_allowlisted(test_plan: list[str]) -> SubtaskCheckResult:
    """Each command in ``test_plan`` must start with an allowlisted
    prefix (pytest/ruff/mypy/bandit). Anything else gets rejected so the
    sandbox doesn't run an arbitrary shell command."""
    if not test_plan:
        return SubtaskCheckResult(
            name="test_commands_allowlisted",
            status=CheckStatus.SKIP,
            severity=CheckSeverity.WARN,
            message="no test plan provided",
        )

    rejected: list[str] = []
    for cmd in test_plan:
        if not isinstance(cmd, str) or not cmd.strip():
            continue
        normalized = cmd.strip()
        if not any(normalized.startswith(prefix) for prefix in _ALLOWLISTED_TEST_PREFIXES):
            rejected.append(normalized[:120])

    if rejected:
        return SubtaskCheckResult(
            name="test_commands_allowlisted",
            status=CheckStatus.FAIL,
            severity=CheckSeverity.BLOCKER,
            message=f"{len(rejected)} non-allowlisted test command(s)",
            evidence=rejected,
        )
    return SubtaskCheckResult(
        name="test_commands_allowlisted",
        status=CheckStatus.PASS,
        severity=CheckSeverity.BLOCKER,
        message="all test commands allowlisted",
    )


def check_files_in_scope(
    diff_content: str,
    declared_affected_files: list[str],
) -> SubtaskCheckResult:
    """Every file actually mutated by the diff must appear in
    ``declared_affected_files``. Catches programmer-agent drift where the
    patch silently touches unrelated files."""
    if not diff_content or not declared_affected_files:
        return SubtaskCheckResult(
            name="files_in_scope",
            status=CheckStatus.SKIP,
            severity=CheckSeverity.WARN,
            message="diff or affected_files missing",
        )

    declared = {p.replace("\\", "/").lstrip("./") for p in declared_affected_files}
    actual: set[str] = set()
    for line in diff_content.splitlines():
        if line.startswith("+++ b/") or line.startswith("--- a/"):
            path = line.split(" ", 1)[1].lstrip()
            for prefix in ("a/", "b/"):
                if path.startswith(prefix):
                    path = path[len(prefix):]
                    break
            actual.add(path.replace("\\", "/"))
        elif line.startswith("diff --git "):
            parts = line.split()
            if len(parts) >= 3:
                # diff --git a/path b/path — pick the b-side
                b_path = parts[-1]
                if b_path.startswith("b/"):
                    b_path = b_path[2:]
                actual.add(b_path.replace("\\", "/"))

    out_of_scope = sorted(actual - declared)
    if out_of_scope:
        return SubtaskCheckResult(
            name="files_in_scope",
            status=CheckStatus.FAIL,
            severity=CheckSeverity.WARN,
            message=f"{len(out_of_scope)} file(s) modified outside declared scope",
            evidence=out_of_scope[:10],
        )
    return SubtaskCheckResult(
        name="files_in_scope",
        status=CheckStatus.PASS,
        severity=CheckSeverity.WARN,
        message="all modified files within declared scope",
    )


# ── Aggregator + FSM verifier hook ──────────────────────────────────────


def run_subtask_checks(
    *,
    diff_content: str,
    declared_affected_files: list[str],
    test_plan: list[str],
    dry_run_output: Optional[str] = None,
) -> list[SubtaskCheckResult]:
    """Run all four predicates against a fix proposal and return the
    individual results. Order is stable so dashboards can render them
    consistently."""
    return [
        check_patch_applies(dry_run_output),
        check_security_patterns(diff_content),
        check_test_commands_allowlisted(test_plan),
        check_files_in_scope(diff_content, declared_affected_files),
    ]


def aggregate_decision(results: list[SubtaskCheckResult]) -> str:
    """Roll up subtask results into the existing GO|NO_GO|NEEDS_REVISION
    vocabulary so the QA flow stays backward-compatible.

    - Any BLOCKER fail → ``NO_GO``
    - Any WARN fail (no blockers) → ``NEEDS_REVISION``
    - Otherwise → ``GO``
    """
    has_blocker_fail = any(
        r.failed and r.severity == CheckSeverity.BLOCKER for r in results
    )
    if has_blocker_fail:
        return "NO_GO"
    has_warn_fail = any(
        r.failed and r.severity == CheckSeverity.WARN for r in results
    )
    if has_warn_fail:
        return "NEEDS_REVISION"
    return "GO"


def make_repair_verifier(
    *,
    diff_content: str,
    declared_affected_files: list[str],
    test_plan: list[str],
    dry_run_output: Optional[str] = None,
):
    """Return a callable compatible with ``src.agents.fsm.TransitionVerifier``.

    The verifier rejects ``Phase.OBSERVE → Phase.ACT`` transitions when any
    BLOCKER subtask check fails — i.e., the patch can't proceed to the
    sandbox-test ACT phase if it's unsafe. Other transitions are allowed
    through; deterministic gating only happens on the OBSERVE→ACT edge.
    """
    from src.agents.fsm import Phase, VerifyResult

    results = run_subtask_checks(
        diff_content=diff_content,
        declared_affected_files=declared_affected_files,
        test_plan=test_plan,
        dry_run_output=dry_run_output,
    )

    def verifier(from_phase: Phase, to_phase: Phase, _payload: dict) -> "VerifyResult":
        # Only gate the post-QA → sandbox-act transition. Other transitions
        # are LLM-decided and don't get a deterministic veto here.
        if not (from_phase == Phase.OBSERVE and to_phase == Phase.ACT):
            return VerifyResult(allowed=True)

        blockers = [r for r in results if r.failed and r.severity == CheckSeverity.BLOCKER]
        if blockers:
            names = ", ".join(b.name for b in blockers)
            return VerifyResult(
                allowed=False,
                reason=f"deterministic subtask checks blocking: {names}",
            )
        return VerifyResult(allowed=True)

    return verifier
