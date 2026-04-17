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
    ThroughputPrecheck,
    Watchdog,
    WatchdogSnapshot,
    generate_report,
    run_precheck,
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
                "source": "introspection",
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
                "source": "user",
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
            # Last window rate = 1.0, first window rate = 0.5 → exact trend +0.5.
            _write_episodes(traces, 50, successes=25, start_offset_minutes=60)
            _write_episodes(traces, 50, successes=50, start_offset_minutes=0)

            report = generate_report(runtime, traces, models)

            self.assertEqual(report.episodes_total, 100)
            assert report.patch_success_rate_trend is not None
            # Strengthen from "is greater than 0.3" to the precise value
            # that the spec defines (last_rate - first_rate). Approximate
            # equality only because the underlying floats have the usual
            # IEEE rounding artifacts.
            self.assertAlmostEqual(
                report.patch_success_rate_trend, 0.5, places=4,
            )

    def test_report_trend_negative_when_quality_degrades(self) -> None:
        """Symmetric counterpart: first window rate=1.0, last=0.5 →
        trend exactly -0.5. Locks the sign convention so a future
        refactor cannot silently invert it."""
        with tempfile.TemporaryDirectory() as root:
            runtime = Path(root) / "runtime"
            traces = Path(root) / "traces"
            models = Path(root) / "models"
            # First 50: 100% success. Last 50: 50% success.
            _write_episodes(traces, 50, successes=50, start_offset_minutes=60)
            _write_episodes(traces, 50, successes=25, start_offset_minutes=0)

            report = generate_report(runtime, traces, models)

            self.assertEqual(report.episodes_total, 100)
            assert report.patch_success_rate_trend is not None
            self.assertAlmostEqual(
                report.patch_success_rate_trend, -0.5, places=4,
            )

    def test_reporter_keeps_records_missing_timestamp(self) -> None:
        """Fail-open contract: a row missing the ``timestamp`` field
        must still be counted by the reporter (a partial-write must not
        silently erase the tail of the record).

        This is the inverse of the precheck contract — precheck drops
        timestamp-less rows because it filters by lookback window;
        reporter aggregates without a window when ``since=None``."""
        with tempfile.TemporaryDirectory() as root:
            runtime = Path(root) / "runtime"
            traces = Path(root) / "traces"
            models = Path(root) / "models"
            traces.mkdir(parents=True, exist_ok=True)
            (traces / "episodes.jsonl").write_text(
                json.dumps({
                    "episode_id": "ep-no-ts",
                    "task_id": "task-1",
                    "outcome": "accepted",
                    # no timestamp field
                }) + "\n",
                encoding="utf-8",
            )
            report = generate_report(runtime, traces, models)
            self.assertEqual(report.episodes_total, 1)
            self.assertEqual(report.episodes_success, 1)

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

    def test_watchdog_persists_atomically_deterministic(self) -> None:
        """Round-trip and atomic-write invariants over a deterministic
        100-cycle alternating sequence.

        This is NOT a property-based test — it walks a fixed
        ``failed/executed`` alternation and asserts after every save:

        * the file exists,
        * the file parses as JSON and contains the expected schema key,
        * no ``.tmp`` sidecar leaks (the atomic-rename pattern leaves
          no temp residue on success).

        For the genuine concurrent-writer hazard, see
        :meth:`test_watchdog_concurrent_save_tolerates_race`.
        """
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

    def test_watchdog_concurrent_save_tolerates_race(self) -> None:
        """Two threads each call save() many times against the same
        watchdog file. Final state must be parseable and no .tmp sidecar
        may leak.

        The contract being verified: even though the watchdog is not
        designed for multi-writer use, the atomic-rename persistence
        pattern (write tmp, os.replace) must never leave a corrupt or
        half-written file behind. This catches regressions where a
        future refactor swaps in a non-atomic write.
        """
        import threading as _threading

        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "watchdog.json"
            tmp_path = path.with_suffix(path.suffix + ".tmp")
            w = Watchdog(path)
            # Seed once on the main thread so both writers operate on a
            # populated snapshot.
            w.tick({"status": "failed"})
            w.save()

            errors: list[BaseException] = []

            def hammer() -> None:
                try:
                    for _ in range(50):
                        w.save()
                except BaseException as exc:  # noqa: BLE001 — capture & re-raise
                    errors.append(exc)

            t1 = _threading.Thread(target=hammer)
            t2 = _threading.Thread(target=hammer)
            t1.start()
            t2.start()
            t1.join()
            t2.join()

            self.assertEqual(errors, [], f"save() raised under contention: {errors}")
            # Final invariants: file present, parseable, no tmp sidecar.
            self.assertTrue(path.exists())
            data = json.loads(path.read_text(encoding="utf-8"))
            self.assertIn("consecutive_cycle_failures", data)
            self.assertFalse(
                tmp_path.exists(),
                f"Stale .tmp sidecar leaked at {tmp_path}",
            )

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

    def test_preflight_teacher_reachable_fails_on_404(self) -> None:
        """A 404 response means the configured endpoint path is wrong.
        That is a misconfiguration the operator must fix before launch,
        not a 'reachable' state — so the gate must fail."""
        with tempfile.TemporaryDirectory() as temp_root:
            temp_path = Path(temp_root)
            repo_path = self._make_repo(temp_path)
            config_path = self._config_path(temp_path, repo_path)
            settings = load_config(config_path)

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

            from urllib.error import HTTPError

            def raise_404(*args, **kwargs):
                raise HTTPError(
                    url="http://example.invalid/v1/chat/completions",
                    code=404,
                    msg="Not Found",
                    hdrs=None,  # type: ignore[arg-type]
                    fp=None,
                )

            with mock.patch(
                "homunculus.autonomy.preflight.subprocess.run",
                side_effect=fake_run,
            ), mock.patch(
                "homunculus.autonomy.preflight.request.urlopen",
                side_effect=raise_404,
            ), mock.patch.dict(
                "os.environ", {settings.teacher.api_key_env: "test-token"}
            ):
                result = run_preflight(settings)

            self.assertFalse(result.passed)
            teacher_gate = result.gates["teacher_reachable"]
            self.assertFalse(
                teacher_gate.passed,
                f"404 must fail the teacher_reachable gate; got: {teacher_gate.detail}",
            )
            self.assertIn("404", teacher_gate.detail)


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
            self.assertIn("Foreign commits detected", sc6.evidence)

    def test_check_metrics_stable_coverage_trend_none_branch(self) -> None:
        """SC5 must pass when ``coverage_trend is None`` (no coverage
        data yet) AND ``patch_success_rate_trend`` is within tolerance.

        The contract: ``None`` for coverage_trend is treated as
        'no data, do not penalize' rather than as a failure. This locks
        the branch so future refactors cannot regress to penalizing
        missing coverage.
        """
        from homunculus.autonomy.acceptance import _check_metrics_stable

        report = _fixture_report(
            psr_trend=-0.01,
            coverage_trend=None,
        )
        result = _check_metrics_stable(report)
        self.assertTrue(
            result.passed,
            f"SC5 should pass with cov=None and psr=-0.01: {result.evidence}",
        )
        self.assertEqual(result.id, "SC5")

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

    # ----- SC6 real-git production-path tests ------------------------------

    def test_sc6_classifies_agent_commits_as_passed(self) -> None:
        """SC6 invoked directly on a repo whose only commits carry the
        Episode-ID/Task-ID footer (the format
        :meth:`TaskRunner.commit_to_source` produces) must pass.

        This exercises the real ``git log`` shell-out and
        :func:`_parse_git_log` parsing path that production runs hit —
        not the in-memory ``AutonomyReport`` fixture path.
        """
        from homunculus.autonomy.acceptance import _check_no_human_intervention

        with tempfile.TemporaryDirectory() as temp_root:
            repo = self._make_agent_repo(Path(temp_root), "phase-5/soak")
            result = _check_no_human_intervention(repo, "phase-5/soak")
            self.assertTrue(
                result.passed,
                f"SC6 should pass on all-agent repo: {result.evidence}",
            )
            self.assertEqual(result.id, "SC6")

    def test_sc6_classifies_foreign_commits_as_failed(self) -> None:
        """SC6 must fail when the soak branch contains a commit whose
        message lacks the ``Episode-ID:`` footer — the production
        signature for an operator-authored commit."""
        from homunculus.autonomy.acceptance import _check_no_human_intervention

        with tempfile.TemporaryDirectory() as temp_root:
            repo = self._make_agent_repo(Path(temp_root), "phase-5/soak")
            # Add a foreign commit on the soak branch.
            (repo / "f.py").write_text("# operator hotfix\n", encoding="utf-8")
            subprocess.run(
                ["git", "add", "."],
                cwd=repo, capture_output=True, check=True,
            )
            subprocess.run(
                ["git", "commit", "-m", "chore: operator-only edit, no footer"],
                cwd=repo, capture_output=True, check=True,
            )
            result = _check_no_human_intervention(repo, "phase-5/soak")
            self.assertFalse(result.passed)
            self.assertEqual(result.id, "SC6")

    def test_sc6_evidence_includes_offending_shas(self) -> None:
        """The SC6 failed-case evidence string must reference the
        foreign commit SHA so an operator can ``git show`` it directly.

        We assert a 7-character short SHA prefix (the format
        ``acceptance.py`` emits) is present in evidence.
        """
        from homunculus.autonomy.acceptance import _check_no_human_intervention

        with tempfile.TemporaryDirectory() as temp_root:
            repo = self._make_agent_repo(Path(temp_root), "phase-5/soak")
            (repo / "f.py").write_text("# operator hotfix\n", encoding="utf-8")
            subprocess.run(
                ["git", "add", "."],
                cwd=repo, capture_output=True, check=True,
            )
            subprocess.run(
                ["git", "commit", "-m", "chore: operator hotfix"],
                cwd=repo, capture_output=True, check=True,
            )
            head_sha = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=repo, capture_output=True, text=True, check=True,
            ).stdout.strip()
            short = head_sha[:7]

            result = _check_no_human_intervention(repo, "phase-5/soak")
            self.assertFalse(result.passed)
            self.assertIn(
                short, result.evidence,
                f"Expected short SHA {short} in evidence: {result.evidence!r}",
            )
            # Diagnostic re-run command must be included for the operator.
            self.assertIn("git log", result.evidence)
            self.assertIn("Episode-ID", result.evidence)

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

            # Report picks up the flag — exact format
            # ``cycle_failure:<count>`` (see Watchdog.active_flags).
            report = generate_report(
                runtime_dir=config.paths.runtime_dir,
                traces_dir=config.paths.traces_dir,
                models_dir=config.paths.models_dir,
            )
            self.assertIn(
                "cycle_failure:3", report.watchdog_flags,
                f"Expected cycle_failure:3 in {report.watchdog_flags}",
            )

    def test_watchdog_flag_clears_after_successful_cycle(self) -> None:
        """A successful tick after 3 failures must clear the
        ``cycle_failure:*`` flag from the next report.

        The watchdog tick semantics (see :meth:`Watchdog.tick`): any
        non-``failed``/``error`` status resets the consecutive counter
        to zero. With the counter at zero, the flag derivation in
        :func:`reporter._derive_flags` must not surface
        ``cycle_failure:*``.
        """
        from homunculus.daemon import Daemon, DaemonCycleResult

        with tempfile.TemporaryDirectory() as temp_root:
            temp_path = Path(temp_root)
            repo_path = self._make_repo(temp_path)
            config = load_config(self._config_path(temp_path, repo_path))

            daemon = Daemon(config)
            for _ in range(3):
                daemon._finalize_cycle(
                    DaemonCycleResult(status="failed")
                )
            # One successful cycle.
            daemon._finalize_cycle(DaemonCycleResult(status="executed"))

            persisted = json.loads(
                (config.paths.runtime_dir / "watchdog.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(persisted["consecutive_cycle_failures"], 0)

            report = generate_report(
                runtime_dir=config.paths.runtime_dir,
                traces_dir=config.paths.traces_dir,
                models_dir=config.paths.models_dir,
            )
            self.assertFalse(
                any(f.startswith("cycle_failure:") for f in report.watchdog_flags),
                f"Did not expect cycle_failure flag in {report.watchdog_flags}",
            )


# ---------------------------------------------------------------------------
# Throughput precheck tests
# ---------------------------------------------------------------------------


def _write_precheck_episodes(
    traces: Path,
    count: int,
    *,
    successes: int,
    minutes_back_start: int,
) -> None:
    """Write ``count`` episodes with ``timestamp`` field set to staggered
    UTC ISO-8601 timestamps going backward from ``minutes_back_start``
    minutes ago.
    """
    traces.mkdir(parents=True, exist_ok=True)
    path = traces / "episodes.jsonl"
    now = datetime.now(timezone.utc)
    lines: list[str] = []
    for i in range(count):
        ts = now - timedelta(minutes=minutes_back_start + i)
        outcome = "accepted" if i < successes else "reverted"
        record = {
            "episode_id": f"ep-{i:04d}",
            "task_id": f"task-{i:04d}",
            "outcome": outcome,
            "timestamp": ts.isoformat(),
        }
        lines.append(json.dumps(record))
    with path.open("a", encoding="utf-8") as handle:
        for line in lines:
            handle.write(line + "\n")


def _precheck_settings(temp_dir: Path) -> tuple[Path, "HomunculusConfig"]:  # type: ignore[name-defined]
    """Build a minimal config fixture suitable for run_precheck."""
    source = Path(
        "C:/Users/dasbl/Documents/homunculus/homunculus.example.toml"
    )
    content = source.read_text(encoding="utf-8").replace(
        'root = "."', f'root = "{temp_dir.as_posix()}"', 1
    )
    target = temp_dir / "homunculus.toml"
    target.write_text(content, encoding="utf-8")
    return target, load_config(target)


class ThroughputPrecheckTests(unittest.TestCase):
    """Tests for :func:`homunculus.autonomy.precheck.run_precheck`."""

    def test_blocks_with_empty_episodes(self) -> None:
        """No episode history → 0 projection → BLOCK."""
        with tempfile.TemporaryDirectory() as temp_root:
            temp = Path(temp_root)
            _, settings = _precheck_settings(temp)
            (temp / "traces").mkdir()
            result = run_precheck(settings)
            self.assertEqual(result.verdict, "BLOCK")
            self.assertEqual(result.episodes_window, 0)
            self.assertEqual(result.projected_loras_merged_soak, 0.0)
            self.assertEqual(result.margin_note, "below_safety_margin")

    def test_blocks_when_all_episodes_outside_lookback(self) -> None:
        """Episodes older than lookback window are ignored."""
        with tempfile.TemporaryDirectory() as temp_root:
            temp = Path(temp_root)
            _, settings = _precheck_settings(temp)
            _write_precheck_episodes(
                temp / "traces", 100,
                successes=100,
                minutes_back_start=60 * 24 * 30,  # 30 days back
            )
            result = run_precheck(settings, lookback_days=14)
            self.assertEqual(result.episodes_window, 0)
            self.assertEqual(result.verdict, "BLOCK")

    def test_passes_with_sufficient_throughput(self) -> None:
        """High episode rate + low thresholds → PASS + OK margin."""
        with tempfile.TemporaryDirectory() as temp_root:
            temp = Path(temp_root)
            _, settings = _precheck_settings(temp)
            # 140 successful episodes in 14-day window = 10/day
            # with min_samples=10, min_loras=2:
            # successful_soak = 10 * 7 * 1.0 = 70
            # loras_trained = 70 / 10 = 7
            # loras_merged = floor(7) / 2 = 3.5 → PASS (>=1.5 margin)
            _write_precheck_episodes(
                temp / "traces", 140,
                successes=140,
                minutes_back_start=60,  # all within last 24h of window
            )
            # Override thresholds: use lowered (10, 2) to reflect production
            # change made for Phase 5 feasibility.
            settings = load_config(temp / "homunculus.toml")
            settings.evolution.auto_train_after_samples = 10
            settings.evolution.auto_merge_after_loras = 2
            result = run_precheck(settings)
            self.assertEqual(result.verdict, "PASS")
            self.assertEqual(result.margin_note, "OK")
            self.assertGreaterEqual(result.projected_loras_merged_soak, 1.5)

    def test_pass_but_below_safety_margin(self) -> None:
        """Projection in [threshold_min, safety_margin) → PASS + below_safety_margin."""
        with tempfile.TemporaryDirectory() as temp_root:
            temp = Path(temp_root)
            _, settings = _precheck_settings(temp)
            # Target: projection in [1.0, 1.5). With min_samples=10,
            # min_loras=2: loras_merged = floor(success_soak/10)/2 = 1 when
            # success_soak ∈ [20, 30). Achieve by 35 successes in 14 days:
            # rate=2.5, soak_success=17.5 → not quite enough. Try 50 in 14d:
            # rate=3.57, soak_success=25 → floor(2.5)/2=1.0 exactly.
            _write_precheck_episodes(
                temp / "traces", 50,
                successes=50,
                minutes_back_start=60,
            )
            settings = load_config(temp / "homunculus.toml")
            settings.evolution.auto_train_after_samples = 10
            settings.evolution.auto_merge_after_loras = 2
            result = run_precheck(settings)
            self.assertEqual(result.verdict, "PASS")
            self.assertGreaterEqual(result.projected_loras_merged_soak, 1.0)
            self.assertLess(result.projected_loras_merged_soak, 1.5)
            self.assertEqual(result.margin_note, "below_safety_margin")

    def test_honours_custom_thresholds(self) -> None:
        """Callers can raise threshold_min to force BLOCK."""
        with tempfile.TemporaryDirectory() as temp_root:
            temp = Path(temp_root)
            _, settings = _precheck_settings(temp)
            _write_precheck_episodes(
                temp / "traces", 140,
                successes=140,
                minutes_back_start=60,
            )
            settings = load_config(temp / "homunculus.toml")
            settings.evolution.auto_train_after_samples = 10
            settings.evolution.auto_merge_after_loras = 2
            # Raise bar to 10 merges — same data now fails.
            result = run_precheck(settings, threshold_min=10.0)
            self.assertEqual(result.verdict, "BLOCK")

    def test_skips_malformed_lines(self) -> None:
        """Corrupt JSON lines are ignored, not fatal."""
        with tempfile.TemporaryDirectory() as temp_root:
            temp = Path(temp_root)
            _, settings = _precheck_settings(temp)
            traces = temp / "traces"
            _write_precheck_episodes(
                traces, 10, successes=10, minutes_back_start=60
            )
            # Append garbage
            with (traces / "episodes.jsonl").open("a", encoding="utf-8") as fh:
                fh.write("not json\n{\"oops\"\nanother garbage\n")
            result = run_precheck(settings)
            # Should see only the 10 well-formed records
            self.assertEqual(result.episodes_window, 10)

    def test_ignores_records_missing_timestamp(self) -> None:
        """Records without the ``timestamp`` field fall out of the window."""
        with tempfile.TemporaryDirectory() as temp_root:
            temp = Path(temp_root)
            _, settings = _precheck_settings(temp)
            traces = temp / "traces"
            traces.mkdir()
            # Write a record missing the timestamp field
            (traces / "episodes.jsonl").write_text(
                json.dumps({
                    "episode_id": "ep-no-ts",
                    "outcome": "accepted",
                    # no timestamp
                }) + "\n",
                encoding="utf-8",
            )
            result = run_precheck(settings)
            self.assertEqual(result.episodes_window, 0)
            self.assertEqual(result.verdict, "BLOCK")

    def test_cli_exit_codes(self) -> None:
        """CLI returns 0 on PASS, 2 on BLOCK — matches SOAK-PROTOCOL §2.2
        contract used by scripts/phase5/precheck.ps1."""
        with tempfile.TemporaryDirectory() as temp_root:
            temp = Path(temp_root)
            config_path, _ = _precheck_settings(temp)
            # BLOCK case: empty episodes
            (temp / "traces").mkdir()
            cli = _REAL_SUBPROCESS_RUN(
                [shutil.which("python") or "python",
                 "-m", "homunculus.cli", "autonomy-precheck",
                 "--config", str(config_path), "--json"],
                cwd="C:/Users/dasbl/Documents/homunculus",
                capture_output=True, text=True, timeout=60,
            )
            self.assertEqual(cli.returncode, 2, cli.stderr)
            payload = json.loads(cli.stdout)
            self.assertEqual(payload["verdict"], "BLOCK")


class AutonomySourcesVocabularyTests(unittest.TestCase):
    """The SC2 source-name vocabulary is the contract between
    producers (task_generator, suggestions) and the consumer
    (reporter). Lock it here so any future rename breaks this test."""

    def test_self_directed_matches_producer_emission(self):
        from homunculus.autonomy.sources import (
            SELF_DIRECTED_SOURCES,
            SUGGESTION_SOURCES,
            classify_source,
        )
        # Producers emit these literals today; see task_generator/generator.py
        # (source="introspection") and suggestions.py (source="user").
        self.assertIn("introspection", SELF_DIRECTED_SOURCES)
        self.assertIn("continuation", SELF_DIRECTED_SOURCES)
        self.assertIn("user", SUGGESTION_SOURCES)
        # No overlap.
        self.assertFalse(SELF_DIRECTED_SOURCES & SUGGESTION_SOURCES)

    def test_classify_source_normalizes_case_and_whitespace(self):
        from homunculus.autonomy.sources import classify_source
        self.assertEqual(classify_source("Introspection"), "self_directed")
        self.assertEqual(classify_source("  user  "), "suggestion")
        self.assertEqual(classify_source("continuation"), "self_directed")
        self.assertEqual(classify_source(""), "other")
        self.assertEqual(classify_source(None), "other")
        self.assertEqual(classify_source("unknown-source"), "other")


class ReporterSourceHarmonizationTests(unittest.TestCase):
    """B3 regression test — real producer literals must count."""

    def _entry(self, source: str, outcome: str) -> dict:
        return {
            "task_id": f"t-{source}-{outcome}",
            "outcome": outcome,
            "task": {"source": source},
        }

    def test_introspection_task_counts_as_self_directed(self):
        from homunculus.autonomy.reporter import _count_self_directed
        history = [self._entry("introspection", "success")]
        self.assertEqual(_count_self_directed(history), 1)

    def test_continuation_task_counts_as_self_directed(self):
        from homunculus.autonomy.reporter import _count_self_directed
        history = [self._entry("continuation", "success")]
        self.assertEqual(_count_self_directed(history), 1)

    def test_user_task_counts_as_suggestion(self):
        from homunculus.autonomy.reporter import _count_suggestion_tasks
        history = [self._entry("user", "success")]
        self.assertEqual(_count_suggestion_tasks(history), 1)

    def test_failed_outcome_never_counts(self):
        from homunculus.autonomy.reporter import (
            _count_self_directed,
            _count_suggestion_tasks,
        )
        history = [
            self._entry("introspection", "error"),
            self._entry("user", "blocked"),
        ]
        self.assertEqual(_count_self_directed(history), 0)
        self.assertEqual(_count_suggestion_tasks(history), 0)

    def test_legacy_literals_no_longer_counted(self):
        """The old hardcoded ``generated`` / ``resonance`` literals were
        never emitted by any producer. They must NOT be counted (they
        were the B3 symptom; leaving them would mask the fix)."""
        from homunculus.autonomy.reporter import _count_self_directed
        history = [
            self._entry("generated", "success"),
            self._entry("resonance", "success"),
        ]
        self.assertEqual(_count_self_directed(history), 0)


if __name__ == "__main__":
    unittest.main()
