"""Phase 5 autonomy tests — reporter, watchdog, preflight, acceptance.

Structure mirrors :mod:`tests.test_evolution`: one TestCase per module
surface, ``TemporaryDirectory`` for isolation, no network calls. All
git-requiring tests guard with ``@unittest.skipUnless(shutil.which("git"))``.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from homunculus.autonomy import (
    AutonomyReport,
    CriterionResult,
    GateResult,
    PreflightResult,
    Watchdog,
    WatchdogSnapshot,
    generate_report,
)
from homunculus.autonomy.acceptance import (
    METRIC_TOLERANCE,
    MIN_SELF_DIRECTED_TASKS,
    MIN_UPTIME,
    render_acceptance_markdown,
    validate_acceptance,
)
from homunculus.autonomy.preflight import run_preflight
from homunculus.config import load_config

# Bind the real subprocess.run once at import so side-effects can reach
# it even when ``subprocess.run`` itself has been patched to a mock.
_REAL_SUBPROCESS_RUN = subprocess.run


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

def _write_daemon_state(runtime: Path, *, started_at: datetime, cycles: int) -> None:
    runtime.mkdir(parents=True, exist_ok=True)
    payload = {
        "started_at": started_at.isoformat(),
        "last_cycle_at": None,
        "cycles_completed": cycles,
        "total_episodes": 0,
        "episodes_this_cycle": 0,
    }
    (runtime / "daemon_state.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )


def _write_episodes(
    traces: Path,
    count: int,
    *,
    successes: int,
    start_offset_minutes: int = 0,
) -> None:
    traces.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    for i in range(count):
        ts = datetime.now(timezone.utc) - timedelta(
            minutes=start_offset_minutes + (count - i)
        )
        outcome = "accepted" if i < successes else "reverted"
        record = {
            "episode_id": f"ep-{i:04d}",
            "task_id": f"task-{i:04d}",
            "workspace": "self",
            "outcome": outcome,
            "created_at": ts.isoformat(),
            "verification_results": [],
        }
        lines.append(json.dumps(record))
    path = traces / "episodes.jsonl"
    with path.open("a", encoding="utf-8") as handle:
        for line in lines:
            handle.write(line + "\n")


def _write_task_history(
    runtime: Path, *, self_directed: int, suggestion: int
) -> None:
    runtime.mkdir(parents=True, exist_ok=True)
    path = runtime / "task_history.jsonl"
    lines: list[str] = []
    for i in range(self_directed):
        entry = {
            "task_id": f"gen-{i}",
            "task": {
                "task_id": f"gen-{i}",
                "source": "generated",
                "prompt": "auto",
                "priority": 0.5,
                "introspection_mode": None,
                "context": {},
                "estimated_complexity": "small",
                "target_files": [],
                "success_criteria": "",
            },
            "queued_at": datetime.now(timezone.utc).isoformat(),
            "status": "completed",
            "attempts": 1,
            "last_error": None,
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "outcome": "accepted",
        }
        lines.append(json.dumps(entry))
    for i in range(suggestion):
        entry = {
            "task_id": f"user-{i}",
            "task": {
                "task_id": f"user-{i}",
                "source": "suggestion",
                "prompt": "human",
                "priority": 0.5,
                "introspection_mode": None,
                "context": {},
                "estimated_complexity": "small",
                "target_files": [],
                "success_criteria": "",
            },
            "queued_at": datetime.now(timezone.utc).isoformat(),
            "status": "completed",
            "attempts": 1,
            "last_error": None,
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "outcome": "accepted",
        }
        lines.append(json.dumps(entry))
    with path.open("a", encoding="utf-8") as handle:
        for line in lines:
            handle.write(line + "\n")


def _write_registry(models: Path, *, active_generation: int) -> None:
    models.mkdir(parents=True, exist_ok=True)
    payload = {
        "active": {"candidate_id": "cand-1"} if active_generation else None,
        "candidates": [],
        "history": [],
    }
    (models / "registry.json").write_text(json.dumps(payload), encoding="utf-8")


def _write_lineage(traces: Path, *, generations: list[int]) -> None:
    traces.mkdir(parents=True, exist_ok=True)
    path = traces / "lineage.jsonl"
    lines: list[str] = []
    for g in generations:
        record = {
            "record_id": f"rec-{g}",
            "record_type": "merge" if g > 0 else "base",
            "generation": g,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "parent_record_ids": [],
            "metadata": {},
        }
        lines.append(json.dumps(record))
    with path.open("a", encoding="utf-8") as handle:
        for line in lines:
            handle.write(line + "\n")


# ---------------------------------------------------------------------------
# Reporter tests
# ---------------------------------------------------------------------------

class ReporterTests(unittest.TestCase):
    """Tests for :func:`generate_report`."""

    def test_report_aggregates_events_and_episodes(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            runtime = Path(root) / "runtime"
            traces = Path(root) / "traces"
            models = Path(root) / "models"
            _write_daemon_state(
                runtime,
                started_at=datetime.now(timezone.utc) - timedelta(days=3),
                cycles=5,
            )
            _write_episodes(traces, 30, successes=20)
            _write_task_history(runtime, self_directed=4, suggestion=2)
            _write_registry(models, active_generation=1)
            _write_lineage(traces, generations=[0, 1])

            report = generate_report(runtime, traces, models)

            self.assertEqual(report.episodes_total, 30)
            self.assertEqual(report.episodes_success, 20)
            self.assertEqual(report.episodes_failed, 10)
            self.assertEqual(report.self_directed_tasks_completed, 4)
            self.assertEqual(report.suggestion_tasks_completed, 2)
            self.assertEqual(report.cycles_completed, 5)
            self.assertGreater(report.uptime.total_seconds(), 86400 * 2)

    def test_report_computes_success_rate_trend(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            runtime = Path(root) / "runtime"
            traces = Path(root) / "traces"
            models = Path(root) / "models"
            # First 50: 50% success. Last 50: 100% success.
            # Trend should be > 0.
            _write_episodes(traces, 50, successes=25, start_offset_minutes=60)
            _write_episodes(traces, 50, successes=50, start_offset_minutes=0)

            report = generate_report(runtime, traces, models)

            self.assertEqual(report.episodes_total, 100)
            self.assertIsNotNone(report.patch_success_rate_trend)
            # Last window rate == 1.0, first window rate == 0.5 → trend +0.5
            assert report.patch_success_rate_trend is not None
            self.assertGreater(report.patch_success_rate_trend, 0.3)

    def test_report_trend_none_when_insufficient_episodes(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            runtime = Path(root) / "runtime"
            traces = Path(root) / "traces"
            models = Path(root) / "models"
            _write_episodes(traces, 40, successes=20)

            report = generate_report(runtime, traces, models)

            self.assertEqual(report.episodes_total, 40)
            self.assertIsNone(report.patch_success_rate_trend)


# ---------------------------------------------------------------------------
# Watchdog tests
# ---------------------------------------------------------------------------

class WatchdogTests(unittest.TestCase):
    """Tests for :class:`Watchdog`."""

    def test_watchdog_increments_and_resets_on_success(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "watchdog.json"
            w = Watchdog(path)
            for _ in range(3):
                w.tick({"status": "failed"})
            self.assertEqual(w.snapshot.consecutive_cycle_failures, 3)
            w.tick({"status": "executed"})
            self.assertEqual(w.snapshot.consecutive_cycle_failures, 0)

    def test_watchdog_persists_atomically(self) -> None:
        """After every save(), the JSON file parses and no .tmp is left."""
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "watchdog.json"
            tmp_path = path.with_suffix(path.suffix + ".tmp")
            w = Watchdog(path)
            for i in range(100):
                w.tick({"status": "failed" if i % 2 == 0 else "executed"})
                w.save()
                # Invariant 1: file parses as valid JSON.
                self.assertTrue(path.exists())
                data = json.loads(path.read_text(encoding="utf-8"))
                self.assertIn("consecutive_cycle_failures", data)
                # Invariant 2: no leftover .tmp file.
                self.assertFalse(tmp_path.exists())

    def test_watchdog_recovers_corrupted_json(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "watchdog.json"
            path.write_text("not-json-at-all{{{", encoding="utf-8")
            w = Watchdog(path)
            # Should recover with a fresh snapshot, not raise.
            self.assertEqual(w.snapshot.consecutive_cycle_failures, 0)
            self.assertEqual(w.snapshot.consecutive_merge_failures, 0)
            self.assertEqual(w.snapshot.repeated_task_reverts, {})


# ---------------------------------------------------------------------------
# Preflight tests
# ---------------------------------------------------------------------------

@unittest.skipUnless(shutil.which("git"), "git is required")
class PreflightTests(unittest.TestCase):
    """Tests for :func:`run_preflight`."""

    def _make_repo(self, temp_path: Path) -> Path:
        repo_path = temp_path / "repo"
        repo_path.mkdir()
        subprocess.run(
            ["git", "init"], cwd=repo_path, capture_output=True, check=True
        )
        subprocess.run(
            ["git", "config", "user.email", "test@test.com"],
            cwd=repo_path, capture_output=True, check=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "Test"],
            cwd=repo_path, capture_output=True, check=True,
        )
        (repo_path / "file.py").write_text("# initial\n", encoding="utf-8")
        subprocess.run(
            ["git", "add", "."], cwd=repo_path, capture_output=True, check=True
        )
        subprocess.run(
            ["git", "commit", "-m", "initial"],
            cwd=repo_path, capture_output=True, check=True,
        )
        return repo_path

    def _config_path(self, temp_dir: Path, repo_path: Path) -> Path:
        source = Path(
            "C:/Users/dasbl/Documents/homunculus/homunculus.example.toml"
        )
        content = source.read_text(encoding="utf-8")
        content = content.replace(
            'path = "."', f'path = "{repo_path.as_posix()}"', 1
        )
        # Rewrite [paths].root so artifacts live in the temp dir, not the
        # real repo. Each path is already relative, so we only need to
        # rewrite root.
        content = content.replace(
            'root = "."', f'root = "{temp_dir.as_posix()}"', 1
        )
        target = temp_dir / "homunculus.toml"
        target.write_text(content, encoding="utf-8")
        return target

    def _fake_pass_result(self, gate_name: str) -> GateResult:
        return GateResult(name=gate_name, passed=True, detail="ok")

    def test_preflight_all_gates_pass(self) -> None:
        """All gates return True when every dependency is mocked healthy."""
        with tempfile.TemporaryDirectory() as temp_root:
            temp_path = Path(temp_root)
            repo_path = self._make_repo(temp_path)
            config_path = self._config_path(temp_path, repo_path)
            settings = load_config(config_path)

            # Distinguish subprocess callers by argv[0]:
            #   - git status --porcelain → empty stdout (clean)
            #   - doctor              → JSON OK
            #   - unittest discover    → zero returncode, OK output
            def fake_run(cmd, **kwargs):
                if cmd and cmd[0] == "git":
                    return subprocess.CompletedProcess(
                        args=cmd, returncode=0, stdout="", stderr=""
                    )
                if any("unittest" in str(arg) for arg in cmd):
                    return subprocess.CompletedProcess(
                        args=cmd, returncode=0, stdout="OK", stderr=""
                    )
                return subprocess.CompletedProcess(
                    args=cmd, returncode=0,
                    stdout='{"ok": true, "failed": []}', stderr="",
                )

            fake_teacher = mock.MagicMock()
            fake_teacher.__enter__ = mock.MagicMock(
                return_value=mock.MagicMock(status=200)
            )
            fake_teacher.__exit__ = mock.MagicMock(return_value=False)

            with mock.patch(
                "homunculus.autonomy.preflight.subprocess.run",
                side_effect=fake_run,
            ), mock.patch(
                "homunculus.autonomy.preflight.request.urlopen",
                return_value=fake_teacher,
            ), mock.patch.dict(
                "os.environ", {settings.teacher.api_key_env: "test-token"}
            ):
                result = run_preflight(settings)

            self.assertIsInstance(result, PreflightResult)
            for name, gate in result.gates.items():
                self.assertTrue(
                    gate.passed,
                    f"Gate {name} failed unexpectedly: {gate.detail}",
                )
            self.assertTrue(result.passed)

    def test_preflight_fails_when_worktree_dirty(self) -> None:
        """A stale worktree directory fails the worktrees_clean gate."""
        with tempfile.TemporaryDirectory() as temp_root:
            temp_path = Path(temp_root)
            repo_path = self._make_repo(temp_path)
            config_path = self._config_path(temp_path, repo_path)
            settings = load_config(config_path)

            # Seed a stale worktree dir.
            stale = settings.paths.runtime_dir / "worktrees" / "episode-stale"
            stale.mkdir(parents=True, exist_ok=True)
            (stale / "marker.txt").write_text("stale", encoding="utf-8")

            def fake_run(cmd, **kwargs):
                if cmd and cmd[0] == "git":
                    return subprocess.CompletedProcess(
                        args=cmd, returncode=0, stdout="", stderr=""
                    )
                if any("unittest" in str(arg) for arg in cmd):
                    return subprocess.CompletedProcess(
                        args=cmd, returncode=0, stdout="OK", stderr=""
                    )
                return subprocess.CompletedProcess(
                    args=cmd, returncode=0,
                    stdout='{"ok": true, "failed": []}', stderr="",
                )

            fake_teacher = mock.MagicMock()
            fake_teacher.__enter__ = mock.MagicMock(
                return_value=mock.MagicMock(status=200)
            )
            fake_teacher.__exit__ = mock.MagicMock(return_value=False)

            with mock.patch(
                "homunculus.autonomy.preflight.subprocess.run",
                side_effect=fake_run,
            ), mock.patch(
                "homunculus.autonomy.preflight.request.urlopen",
                return_value=fake_teacher,
            ), mock.patch.dict(
                "os.environ", {settings.teacher.api_key_env: "test-token"}
            ):
                result = run_preflight(settings)

            self.assertFalse(result.passed)
            self.assertFalse(result.gates["worktrees_clean"].passed)
            self.assertIn("episode-stale", result.gates["worktrees_clean"].detail)

    def test_preflight_fails_when_tests_fail(self) -> None:
        """Test suite gate fails when subprocess returns non-zero."""
        with tempfile.TemporaryDirectory() as temp_root:
            temp_path = Path(temp_root)
            repo_path = self._make_repo(temp_path)
            config_path = self._config_path(temp_path, repo_path)
            settings = load_config(config_path)

            def fake_run(cmd, **kwargs):
                # Fail the test-suite call; pass everything else.
                if any("unittest" in str(arg) for arg in cmd):
                    return subprocess.CompletedProcess(
                        args=cmd,
                        returncode=1,
                        stdout="",
                        stderr="FAILED (failures=2)",
                    )
                if cmd and cmd[0] == "git":
                    return subprocess.CompletedProcess(
                        args=cmd, returncode=0, stdout="", stderr=""
                    )
                return subprocess.CompletedProcess(
                    args=cmd,
                    returncode=0,
                    stdout='{"ok": true, "failed": []}',
                    stderr="",
                )

            fake_teacher = mock.MagicMock()
            fake_teacher.__enter__ = mock.MagicMock(
                return_value=mock.MagicMock(status=200)
            )
            fake_teacher.__exit__ = mock.MagicMock(return_value=False)

            with mock.patch(
                "homunculus.autonomy.preflight.subprocess.run",
                side_effect=fake_run,
            ), mock.patch(
                "homunculus.autonomy.preflight.request.urlopen",
                return_value=fake_teacher,
            ), mock.patch.dict(
                "os.environ", {settings.teacher.api_key_env: "test-token"}
            ):
                result = run_preflight(settings)

            self.assertFalse(result.passed)
            self.assertFalse(result.gates["test_suite_passes"].passed)
            self.assertIn(
                "FAILED", result.gates["test_suite_passes"].detail
            )


# ---------------------------------------------------------------------------
# Acceptance tests
# ---------------------------------------------------------------------------

def _fixture_report(
    *,
    uptime_days: float = 8.0,
    self_directed: int = 12,
    loras_merged: int = 2,
    current_generation: int = 2,
    psr_trend: float | None = 0.05,
    coverage_trend: float | None = 0.01,
) -> AutonomyReport:
    return AutonomyReport(
        generated_at=datetime.now(timezone.utc),
        uptime=timedelta(days=uptime_days),
        cycles_completed=20,
        episodes_total=200,
        episodes_success=150,
        episodes_failed=50,
        self_directed_tasks_completed=self_directed,
        suggestion_tasks_completed=3,
        loras_trained=3,
        loras_merged=loras_merged,
        current_base_generation=current_generation,
        patch_success_rate=0.75,
        patch_success_rate_trend=psr_trend,
        coverage_percent=82.5,
        coverage_trend=coverage_trend,
        watchdog_flags=(),
    )


@unittest.skipUnless(shutil.which("git"), "git is required")
class AcceptanceTests(unittest.TestCase):
    """Tests for :func:`validate_acceptance`."""

    def _make_agent_repo(self, temp_path: Path, branch: str) -> Path:
        """Build a repo whose only commits are agent commits."""
        repo_path = temp_path / "agent-repo"
        repo_path.mkdir()
        subprocess.run(
            ["git", "init"], cwd=repo_path, capture_output=True, check=True
        )
        subprocess.run(
            ["git", "config", "user.email", "test@test.com"],
            cwd=repo_path, capture_output=True, check=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "Test"],
            cwd=repo_path, capture_output=True, check=True,
        )
        # Initial commit — also an agent commit so SC6 is satisfied when
        # branch == initial.
        (repo_path / "f.py").write_text("# v0\n", encoding="utf-8")
        subprocess.run(
            ["git", "add", "."], cwd=repo_path, capture_output=True, check=True
        )
        agent_msg = (
            "feat: initial agent commit\n\n"
            "Episode-ID: ep-0000\nTask-ID: task-0000"
        )
        subprocess.run(
            ["git", "commit", "-m", agent_msg],
            cwd=repo_path, capture_output=True, check=True,
        )
        subprocess.run(
            ["git", "checkout", "-b", branch],
            cwd=repo_path, capture_output=True, check=True,
        )
        (repo_path / "f.py").write_text("# v1\n", encoding="utf-8")
        subprocess.run(
            ["git", "add", "."], cwd=repo_path, capture_output=True, check=True
        )
        agent_msg2 = (
            "fix: agent iteration\n\n"
            "Episode-ID: ep-0001\nTask-ID: task-0001"
        )
        subprocess.run(
            ["git", "commit", "-m", agent_msg2],
            cwd=repo_path, capture_output=True, check=True,
        )
        return repo_path

    def test_acceptance_all_criteria_met(self) -> None:
        with tempfile.TemporaryDirectory() as temp_root:
            temp_path = Path(temp_root)
            repo_path = self._make_agent_repo(temp_path, "phase-5/soak")
            report = _fixture_report()
            # Stub SC4's subprocess call so we don't recursively run
            # the entire suite inside one test.
            fake_ok = subprocess.CompletedProcess(
                args=[], returncode=0, stdout="OK", stderr=""
            )
            with mock.patch(
                "homunculus.autonomy.acceptance.subprocess.run"
            ) as run_mock:
                run_mock.side_effect = self._build_subprocess_side_effect(
                    tests_exit=0, git_cwd=repo_path
                )
                verdict = validate_acceptance(
                    report,
                    soak_branch="phase-5/soak",
                    workspace_root=repo_path,
                )
            self.assertEqual(verdict.overall, "PASS")
            self.assertEqual(len(verdict.criteria), 6)
            for criterion in verdict.criteria:
                self.assertTrue(
                    criterion.passed,
                    f"{criterion.id} failed: {criterion.evidence}",
                )

    def test_acceptance_fails_when_uptime_insufficient(self) -> None:
        with tempfile.TemporaryDirectory() as temp_root:
            temp_path = Path(temp_root)
            repo_path = self._make_agent_repo(temp_path, "phase-5/soak")
            report = _fixture_report(uptime_days=3.0)
            with mock.patch(
                "homunculus.autonomy.acceptance.subprocess.run"
            ) as run_mock:
                run_mock.side_effect = self._build_subprocess_side_effect(
                    tests_exit=0, git_cwd=repo_path
                )
                verdict = validate_acceptance(
                    report,
                    soak_branch="phase-5/soak",
                    workspace_root=repo_path,
                )
            self.assertEqual(verdict.overall, "FAIL")
            sc1 = next(c for c in verdict.criteria if c.id == "SC1")
            self.assertFalse(sc1.passed)
            self.assertIn("3.00d", sc1.evidence)

    def test_acceptance_fails_when_tasks_below_threshold(self) -> None:
        with tempfile.TemporaryDirectory() as temp_root:
            temp_path = Path(temp_root)
            repo_path = self._make_agent_repo(temp_path, "phase-5/soak")
            report = _fixture_report(self_directed=9)
            with mock.patch(
                "homunculus.autonomy.acceptance.subprocess.run"
            ) as run_mock:
                run_mock.side_effect = self._build_subprocess_side_effect(
                    tests_exit=0, git_cwd=repo_path
                )
                verdict = validate_acceptance(
                    report,
                    soak_branch="phase-5/soak",
                    workspace_root=repo_path,
                )
            self.assertEqual(verdict.overall, "FAIL")
            sc2 = next(c for c in verdict.criteria if c.id == "SC2")
            self.assertFalse(sc2.passed)
            self.assertIn("9", sc2.evidence)
            self.assertIn(str(MIN_SELF_DIRECTED_TASKS), sc2.evidence)

    def test_acceptance_no_human_intervention_detection(self) -> None:
        """A foreign-author commit (no Episode-ID footer) fails SC6."""
        with tempfile.TemporaryDirectory() as temp_root:
            temp_path = Path(temp_root)
            repo_path = self._make_agent_repo(temp_path, "phase-5/soak")
            # Add a human commit without the agent footer.
            (repo_path / "f.py").write_text("# human edit\n", encoding="utf-8")
            subprocess.run(
                ["git", "add", "."],
                cwd=repo_path, capture_output=True, check=True,
            )
            subprocess.run(
                ["git", "commit", "-m", "chore: manual tweak by operator"],
                cwd=repo_path, capture_output=True, check=True,
            )

            report = _fixture_report()

            def maybe_mock(cmd, **kwargs):
                if cmd and cmd[0] != "git":
                    # SC4 test-suite invocation.
                    return subprocess.CompletedProcess(
                        args=cmd, returncode=0, stdout="OK", stderr=""
                    )
                return _REAL_SUBPROCESS_RUN(cmd, **kwargs)

            with mock.patch(
                "homunculus.autonomy.acceptance.subprocess.run",
                side_effect=maybe_mock,
            ):
                verdict = validate_acceptance(
                    report,
                    soak_branch="phase-5/soak",
                    workspace_root=repo_path,
                )

            self.assertEqual(verdict.overall, "FAIL")
            sc6 = next(c for c in verdict.criteria if c.id == "SC6")
            self.assertFalse(sc6.passed)
            self.assertIn("lack", sc6.evidence)

    def test_acceptance_markdown_renders(self) -> None:
        """Markdown rendering includes overall verdict + all criteria."""
        criteria = [
            CriterionResult(
                id=f"SC{i}",
                name=f"criterion {i}",
                passed=True,
                evidence=f"ev {i}",
                raw={"i": i},
            )
            for i in range(1, 7)
        ]
        from homunculus.autonomy.models import AcceptanceVerdict

        verdict = AcceptanceVerdict(overall="PASS", criteria=criteria)
        md = render_acceptance_markdown(verdict, soak_branch="phase-5/soak")
        self.assertIn("# Phase 5 Acceptance", md)
        self.assertIn("**Overall**: PASS", md)
        self.assertIn("| SC1 |", md)
        self.assertIn("| SC6 |", md)
        self.assertIn("phase-5/soak", md)

    # ----- helpers ---------------------------------------------------------

    def _build_subprocess_side_effect(
        self, *, tests_exit: int, git_cwd: Path
    ):
        """Return a side_effect that passes through git, mocks tests."""
        def side_effect(cmd, **kwargs):
            if cmd and cmd[0] == "git":
                return _REAL_SUBPROCESS_RUN(cmd, **kwargs)
            return subprocess.CompletedProcess(
                args=cmd, returncode=tests_exit, stdout="OK", stderr=""
            )

        return side_effect


# ---------------------------------------------------------------------------
# Integration test
# ---------------------------------------------------------------------------

@unittest.skipUnless(shutil.which("git"), "git is required")
class DaemonWatchdogIntegrationTests(unittest.TestCase):
    """End-to-end: daemon cycle surfaces watchdog flags in the report."""

    def _make_repo(self, temp_path: Path) -> Path:
        repo_path = temp_path / "repo"
        repo_path.mkdir()
        subprocess.run(
            ["git", "init"], cwd=repo_path, capture_output=True, check=True
        )
        subprocess.run(
            ["git", "config", "user.email", "test@test.com"],
            cwd=repo_path, capture_output=True, check=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "Test"],
            cwd=repo_path, capture_output=True, check=True,
        )
        (repo_path / "file.py").write_text("# initial\n", encoding="utf-8")
        subprocess.run(
            ["git", "add", "."], cwd=repo_path, capture_output=True, check=True
        )
        subprocess.run(
            ["git", "commit", "-m", "initial"],
            cwd=repo_path, capture_output=True, check=True,
        )
        return repo_path

    def _config_path(self, temp_dir: Path, repo_path: Path) -> Path:
        source = Path(
            "C:/Users/dasbl/Documents/homunculus/homunculus.example.toml"
        )
        content = source.read_text(encoding="utf-8")
        content = content.replace(
            'path = "."', f'path = "{repo_path.as_posix()}"', 1
        )
        content = content.replace(
            'root = "."', f'root = "{temp_dir.as_posix()}"', 1
        )
        target = temp_dir / "homunculus.toml"
        target.write_text(content, encoding="utf-8")
        return target

    def test_daemon_tick_updates_watchdog(self) -> None:
        from homunculus.daemon import Daemon, DaemonCycleResult

        with tempfile.TemporaryDirectory() as temp_root:
            temp_path = Path(temp_root)
            repo_path = self._make_repo(temp_path)
            config = load_config(self._config_path(temp_path, repo_path))

            daemon = Daemon(config)
            # Simulate 3 consecutive failed cycles.
            for _ in range(3):
                daemon._finalize_cycle(
                    DaemonCycleResult(status="failed")
                )
            watchdog_path = config.paths.runtime_dir / "watchdog.json"
            self.assertTrue(watchdog_path.exists())
            persisted = json.loads(
                watchdog_path.read_text(encoding="utf-8")
            )
            self.assertEqual(persisted["consecutive_cycle_failures"], 3)

            # Report picks up the flag.
            report = generate_report(
                runtime_dir=config.paths.runtime_dir,
                traces_dir=config.paths.traces_dir,
                models_dir=config.paths.models_dir,
            )
            self.assertTrue(
                any(f.startswith("cycle_failure:") for f in report.watchdog_flags),
                f"Expected cycle_failure flag in {report.watchdog_flags}",
            )


if __name__ == "__main__":
    unittest.main()
