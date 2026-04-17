"""Phase 5 acceptance validation — SC1..SC6 predicates.

Given an :class:`AutonomyReport` (produced by :func:`generate_report`)
and the soak-branch / workspace pointers, this module runs six
criterion predicates and returns an :class:`AcceptanceVerdict`.

Verdict semantics (spec §10): ``overall == "PASS"`` iff every
``CriterionResult.passed`` is True — no partial credit. Each predicate
fills ``evidence`` with a human-readable proof and ``raw`` with the
raw metrics dict so downstream audit can re-derive the verdict.

The six predicates:

* ``SC1`` — uptime ≥ 7 days.
* ``SC2`` — ≥10 self-directed tasks completed (generated + resonance).
* ``SC3`` — ≥1 LoRA merged AND base generation incremented.
* ``SC4`` — test suite exits 0 at acceptance time (fresh run).
* ``SC5`` — ``patch_success_rate_trend >= -0.02`` AND
  (``coverage_trend`` is None OR ``>= -0.02``).
* ``SC6`` — no human commits on the soak branch (agent commits are
  identified by the ``Episode-ID:`` / ``Task-ID:`` message footer
  produced by :meth:`TaskRunner.commit_to_source`).
"""
from __future__ import annotations

import logging
import re
import subprocess
import sys
from datetime import timedelta
from pathlib import Path
from typing import Any

from .models import AcceptanceVerdict, AutonomyReport, CriterionResult

logger = logging.getLogger(__name__)

# Spec §2 thresholds.
MIN_UPTIME = timedelta(days=7)
MIN_SELF_DIRECTED_TASKS = 10

# Spec §11 — "stable or improving" defined as ≤2% regression.
METRIC_TOLERANCE = -0.02

_TEST_SUITE_TIMEOUT_SECONDS = 600
_GIT_TIMEOUT_SECONDS = 60

# Agent-commit signature: every commit produced by
# TaskRunner.commit_to_source ends with "Episode-ID: …\nTask-ID: …".
# We match on that footer rather than author identity because the git
# author is whatever the workspace has configured, not a stable agent
# marker. Spec §11: "pattern match on commit message prefix from
# commit_to_source".
_AGENT_COMMIT_PATTERN = re.compile(
    r"Episode-ID:\s*\S+", re.MULTILINE
)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def validate_acceptance(
    report: AutonomyReport,
    *,
    soak_branch: str,
    workspace_root: Path,
) -> AcceptanceVerdict:
    """Evaluate all six acceptance criteria.

    Args:
        report: Fresh :class:`AutonomyReport` from
            :func:`homunculus.autonomy.generate_report`.
        soak_branch: Git branch name to inspect for SC6.
        workspace_root: Path to the workspace repo containing the
            soak branch. SC6 runs ``git log`` here; SC4 resolves the
            cwd from the report's root (not this path), because the
            test suite lives alongside the agent source.

    Returns:
        :class:`AcceptanceVerdict` with all six criterion results.
    """
    criteria: list[CriterionResult] = [
        _check_uptime(report),
        _check_self_directed_tasks(report),
        _check_lora_merged(report),
        _check_tests_pass(),
        _check_metrics_stable(report),
        _check_no_human_intervention(workspace_root, soak_branch),
    ]
    overall = "PASS" if all(c.passed for c in criteria) else "FAIL"
    return AcceptanceVerdict(overall=overall, criteria=criteria)


# ---------------------------------------------------------------------------
# Individual predicates
# ---------------------------------------------------------------------------

def _check_uptime(report: AutonomyReport) -> CriterionResult:
    """SC1: soak must have run continuously for ≥ 7 days."""
    observed = report.uptime
    passed = observed >= MIN_UPTIME
    days = observed.total_seconds() / 86400.0
    threshold_days = MIN_UPTIME.total_seconds() / 86400.0
    evidence = (
        f"Uptime {days:.2f}d "
        f"(threshold {threshold_days:.0f}d, cycles={report.cycles_completed})."
    )
    return CriterionResult(
        id="SC1",
        name="1+ week unattended operation",
        passed=passed,
        evidence=evidence,
        raw={
            "uptime_seconds": observed.total_seconds(),
            "threshold_seconds": MIN_UPTIME.total_seconds(),
            "cycles_completed": report.cycles_completed,
        },
    )


def _check_self_directed_tasks(report: AutonomyReport) -> CriterionResult:
    """SC2: at least 10 self-directed tasks completed successfully."""
    observed = report.self_directed_tasks_completed
    passed = observed >= MIN_SELF_DIRECTED_TASKS
    evidence = (
        f"Self-directed tasks completed: {observed} "
        f"(threshold {MIN_SELF_DIRECTED_TASKS})."
    )
    return CriterionResult(
        id="SC2",
        name="10+ self-directed tasks completed",
        passed=passed,
        evidence=evidence,
        raw={
            "self_directed_tasks_completed": observed,
            "threshold": MIN_SELF_DIRECTED_TASKS,
            "suggestion_tasks_completed": report.suggestion_tasks_completed,
        },
    )


def _check_lora_merged(report: AutonomyReport) -> CriterionResult:
    """SC3: at least one LoRA merged AND base generation advanced.

    Requires two signals: ``loras_merged >= 1`` alone could be
    satisfied by a merge that failed validation and was rolled back
    without advancing the generation pointer. Also asserting
    ``current_base_generation > 0`` confirms the merge actually
    changed the active base — spec §2 wording: "trained and merged".
    """
    merged = report.loras_merged
    generation = report.current_base_generation
    passed = merged >= 1 and generation > 0
    evidence = (
        f"LoRAs merged: {merged}, current base generation: {generation} "
        f"(need merged>=1 AND generation>0)."
    )
    return CriterionResult(
        id="SC3",
        name="1+ LoRA trained and merged",
        passed=passed,
        evidence=evidence,
        raw={
            "loras_trained": report.loras_trained,
            "loras_merged": merged,
            "current_base_generation": generation,
        },
    )


def _check_tests_pass(cwd: Path | None = None) -> CriterionResult:
    """SC4: re-run ``python -m unittest discover`` at acceptance time.

    We run a fresh test pass rather than trust a stale preflight
    result because SC4 is about end-of-soak state. Failure captures
    the trailing output for diagnostics.

    Args:
        cwd: Directory to run tests in. Defaults to ``Path.cwd()`` so
            normal CLI invocation "just works"; tests inject a
            temp dir.
    """
    run_dir = Path(cwd) if cwd is not None else Path.cwd()
    try:
        completed = subprocess.run(
            [sys.executable, "-m", "unittest", "discover", "-q"],
            cwd=str(run_dir),
            capture_output=True,
            text=True,
            timeout=_TEST_SUITE_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return CriterionResult(
            id="SC4",
            name="Test suite passes",
            passed=False,
            evidence="Test suite timed out at 10 minutes.",
            raw={"timeout_seconds": _TEST_SUITE_TIMEOUT_SECONDS},
        )
    except OSError as exc:
        return CriterionResult(
            id="SC4",
            name="Test suite passes",
            passed=False,
            evidence=f"Could not spawn test runner: {exc}",
            raw={"error": str(exc)},
        )
    combined = (completed.stderr or "") + (completed.stdout or "")
    tail = _tail(combined, lines=5)
    passed = completed.returncode == 0
    evidence = (
        f"unittest discover returned {completed.returncode}."
        + (f"\n{tail}" if tail else "")
    )
    return CriterionResult(
        id="SC4",
        name="Test suite passes",
        passed=passed,
        evidence=evidence,
        raw={
            "returncode": completed.returncode,
            "output_tail": tail,
        },
    )


def _check_metrics_stable(report: AutonomyReport) -> CriterionResult:
    """SC5: patch success and coverage trends within tolerance.

    Passes when:
      * ``patch_success_rate_trend`` exists and is ``>= -0.02``, AND
      * ``coverage_trend`` is either None (no coverage data) or
        ``>= -0.02``.

    An absent ``patch_success_rate_trend`` (None — insufficient
    episodes) fails the criterion: without a trend we cannot assert
    stability.
    """
    psr_trend = report.patch_success_rate_trend
    cov_trend = report.coverage_trend
    psr_ok = psr_trend is not None and psr_trend >= METRIC_TOLERANCE
    cov_ok = cov_trend is None or cov_trend >= METRIC_TOLERANCE
    passed = bool(psr_ok and cov_ok)
    psr_str = f"{psr_trend:+.3f}" if psr_trend is not None else "n/a"
    cov_str = f"{cov_trend:+.3f}" if cov_trend is not None else "n/a"
    evidence = (
        f"patch_success_rate_trend={psr_str}, coverage_trend={cov_str} "
        f"(tolerance {METRIC_TOLERANCE:+.2f})."
    )
    return CriterionResult(
        id="SC5",
        name="Metrics stable or improving",
        passed=passed,
        evidence=evidence,
        raw={
            "patch_success_rate": report.patch_success_rate,
            "patch_success_rate_trend": psr_trend,
            "coverage_percent": report.coverage_percent,
            "coverage_trend": cov_trend,
            "tolerance": METRIC_TOLERANCE,
        },
    )


def _check_no_human_intervention(
    workspace_root: Path, soak_branch: str
) -> CriterionResult:
    """SC6: every commit on the soak branch is an agent commit.

    Agent commits are identified by the ``Episode-ID:`` footer that
    :meth:`TaskRunner.commit_to_source` appends to every message.
    A commit without that footer on the soak branch is a human commit
    and fails the criterion.

    Fails closed: git errors are reported as failure. If the branch
    has no commits at all, the criterion passes with "no commits"
    evidence — the caller (acceptance report) should combine with
    SC1/SC2 to interpret that state.
    """
    repo = Path(workspace_root)
    if not repo.exists():
        return CriterionResult(
            id="SC6",
            name="No human intervention required",
            passed=False,
            evidence=f"Workspace root {repo} does not exist.",
            raw={"workspace_root": str(repo), "soak_branch": soak_branch},
        )
    # Separator \x1e (record separator) so commit bodies with blank
    # lines do not confuse parsing.
    sep = "\x1e"
    try:
        completed = subprocess.run(
            [
                "git",
                "log",
                soak_branch,
                f"--pretty=format:%H%n%an <%ae>%n%B{sep}",
            ],
            cwd=str(repo),
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return CriterionResult(
            id="SC6",
            name="No human intervention required",
            passed=False,
            evidence="git log timed out.",
            raw={"soak_branch": soak_branch},
        )
    except OSError as exc:
        return CriterionResult(
            id="SC6",
            name="No human intervention required",
            passed=False,
            evidence=f"git log failed: {exc}",
            raw={"error": str(exc), "soak_branch": soak_branch},
        )
    if completed.returncode != 0:
        return CriterionResult(
            id="SC6",
            name="No human intervention required",
            passed=False,
            evidence=(
                f"git log returned {completed.returncode}: "
                f"{(completed.stderr or '').strip()[:200]}"
            ),
            raw={
                "soak_branch": soak_branch,
                "returncode": completed.returncode,
            },
        )
    commits = _parse_git_log(completed.stdout, sep)
    human_commits: list[dict[str, str]] = []
    for commit in commits:
        if not _AGENT_COMMIT_PATTERN.search(commit["body"]):
            human_commits.append(
                {
                    "sha": commit["sha"],
                    "author": commit["author"],
                    "subject": commit["body"].splitlines()[0] if commit["body"] else "",
                }
            )
    total = len(commits)
    passed = not human_commits
    if passed:
        evidence = (
            f"All {total} commit(s) on {soak_branch} carry agent signature."
            if total
            else f"No commits on {soak_branch}."
        )
    else:
        # Surface the offending SHAs (up to 5, short form) directly in the
        # evidence string so the operator can `git show <sha>` immediately.
        # Also include the diagnostic `git log` invocation that re-derives
        # the foreign-commit list — useful when the operator doesn't have
        # the full report payload at hand.
        sha_preview = ", ".join(c["sha"][:7] for c in human_commits[:5])
        if len(human_commits) > 5:
            sha_preview = f"{sha_preview}, ..."
        evidence = (
            f"Foreign commits detected (no Episode-ID footer) on "
            f"{soak_branch}: {len(human_commits)}/{total} commit(s) lack "
            f"agent signature: {sha_preview}. "
            f"Re-run: git log {soak_branch} --pretty=%h%n%B | "
            f"grep -B1 -L Episode-ID"
        )
    return CriterionResult(
        id="SC6",
        name="No human intervention required",
        passed=passed,
        evidence=evidence,
        raw={
            "soak_branch": soak_branch,
            "commits_total": total,
            "human_commits": human_commits,
        },
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_git_log(raw: str, separator: str) -> list[dict[str, str]]:
    """Parse the ``\\x1e``-delimited ``git log`` output."""
    commits: list[dict[str, str]] = []
    for chunk in raw.split(separator):
        chunk = chunk.strip("\n\r")
        if not chunk:
            continue
        lines = chunk.split("\n")
        if len(lines) < 2:
            continue
        sha = lines[0].strip()
        author = lines[1].strip()
        body = "\n".join(lines[2:])
        commits.append({"sha": sha, "author": author, "body": body})
    return commits


def _tail(text: str, *, lines: int) -> str:
    """Return the last ``lines`` non-empty lines of ``text``."""
    if not text:
        return ""
    buf = [ln for ln in text.splitlines() if ln.strip()]
    return "\n".join(buf[-lines:])


# ---------------------------------------------------------------------------
# Markdown rendering
# ---------------------------------------------------------------------------

def render_acceptance_markdown(
    verdict: AcceptanceVerdict,
    *,
    report: AutonomyReport | None = None,
    soak_branch: str | None = None,
) -> str:
    """Render the acceptance verdict as a markdown report.

    Layout (per spec §7 template):

        # Phase 5 Acceptance

        **Overall**: PASS/FAIL

        ## Criteria

        | ID | Name | Status | Evidence |
        ...
    """
    lines: list[str] = []
    lines.append("# Phase 5 Acceptance")
    lines.append("")
    lines.append(f"**Overall**: {verdict.overall}")
    if soak_branch:
        lines.append("")
        lines.append(f"**Soak branch**: `{soak_branch}`")
    if report is not None:
        lines.append("")
        lines.append(
            f"**Report generated at**: {report.generated_at.isoformat()}"
        )
        lines.append(
            f"**Uptime**: {report.uptime.total_seconds() / 86400.0:.2f} days"
        )
    lines.append("")
    lines.append("## Criteria")
    lines.append("")
    lines.append("| ID | Name | Status | Evidence |")
    lines.append("|---|---|---|---|")
    for criterion in verdict.criteria:
        status = "PASS" if criterion.passed else "FAIL"
        evidence = _escape_markdown_cell(criterion.evidence)
        lines.append(
            f"| {criterion.id} | {criterion.name} | {status} | {evidence} |"
        )
    lines.append("")
    return "\n".join(lines)


def _escape_markdown_cell(value: str) -> str:
    """Collapse newlines and escape pipes for a markdown table cell."""
    return value.replace("\n", " ").replace("|", "\\|")


__all__ = [
    "MIN_UPTIME",
    "MIN_SELF_DIRECTED_TASKS",
    "METRIC_TOLERANCE",
    "validate_acceptance",
    "render_acceptance_markdown",
]
