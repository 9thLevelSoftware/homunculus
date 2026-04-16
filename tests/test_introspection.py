"""Comprehensive tests for the introspection module."""
from __future__ import annotations

import unittest
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from unittest import mock

from homunculus.config import (
    HomunculusConfig,
    IntrospectionSettings,
    PathSettings,
    TeacherSettings,
    WorkspaceSettings,
)
from homunculus.introspection import (
    ComparativeMode,
    CoverageMode,
    CritiqueMode,
    IntrospectionContext,
    IntrospectionScheduler,
    MetricsMode,
    ScheduledModes,
    get_introspection_mode,
)
from homunculus.models import (
    EpisodeRecord,
    IntrospectionResult,
    TeacherResponse,
    VerificationResult,
)


def _make_episode(
    episode_id: str = "ep-001",
    task_id: str = "task-001",
    outcome: str = "accepted",
    attempt_index: int = 1,
    failure_stage: str | None = None,
    source: str = "self-generated",
    comparison_group: str | None = None,
    patch: str | None = None,
    plan: list[str] | None = None,
    error_message: str | None = None,
    prompt: str = "Fix the bug",
) -> EpisodeRecord:
    """Helper to create test episode records."""
    return EpisodeRecord(
        episode_id=episode_id,
        task_id=task_id,
        workspace="self",
        prompt=prompt,
        plan=plan or ["Step 1", "Step 2"],
        teacher_output={},
        student_output={},
        diff_hash="abc123",
        test_results=[],
        lint_results=[],
        outcome=outcome,
        timestamp="2026-04-15T00:00:00+00:00",
        attempt_index=attempt_index,
        patch=patch,
        source=source,
        comparison_group=comparison_group,
        failure_stage=failure_stage,
        error_message=error_message,
    )


class MockArtifactStore:
    """Mock artifact store for testing."""

    def __init__(self, episodes: list[EpisodeRecord] | None = None):
        self._episodes = episodes or []

    def load_episodes(self) -> list[EpisodeRecord]:
        return self._episodes


@dataclass
class MockConfig:
    """Minimal mock config for introspection tests."""

    introspection: IntrospectionSettings = field(
        default_factory=lambda: IntrospectionSettings(enabled=True)
    )
    workspaces: dict[str, WorkspaceSettings] = field(default_factory=dict)
    teacher: TeacherSettings | None = None
    paths: PathSettings | None = None


class TestMetricsMode(unittest.TestCase):
    """Tests for MetricsMode."""

    def test_name_property(self):
        """Test mode identifier."""
        mode = MetricsMode()
        self.assertEqual(mode.name, "metrics")

    def test_empty_episodes(self):
        """Test behavior with no episodes."""
        mode = MetricsMode()
        store = MockArtifactStore([])
        config = MockConfig()
        context = IntrospectionContext(
            store=store,
            config=config,
            cycle_number=1,
            window_size=50,
        )

        result = mode.run(context)

        self.assertEqual(result.mode, "metrics")
        self.assertEqual(result.summary, "No episodes in analysis window")
        self.assertEqual(result.metrics, {})
        self.assertEqual(result.findings, [])
        self.assertIn("Generate some episodes", result.recommendations[0])

    def test_normal_episodes_success_rate(self):
        """Test metrics calculation with mixed outcomes."""
        episodes = [
            _make_episode("ep-1", outcome="accepted"),
            _make_episode("ep-2", outcome="accepted"),
            _make_episode("ep-3", outcome="reverted"),
            _make_episode("ep-4", outcome="error"),
        ]
        mode = MetricsMode()
        store = MockArtifactStore(episodes)
        config = MockConfig()
        context = IntrospectionContext(
            store=store,
            config=config,
            cycle_number=1,
            window_size=50,
        )

        result = mode.run(context)

        self.assertEqual(result.metrics["success_rate"], 0.5)
        self.assertEqual(result.metrics["revert_rate"], 0.25)
        self.assertEqual(result.metrics["error_rate"], 0.25)

    def test_retry_statistics(self):
        """Test retry metrics calculation."""
        episodes = [
            _make_episode("ep-1", outcome="accepted", attempt_index=1),
            _make_episode("ep-2", outcome="accepted", attempt_index=3),
            _make_episode("ep-3", outcome="accepted", attempt_index=2),
            _make_episode("ep-4", outcome="reverted", attempt_index=1),
        ]
        mode = MetricsMode()
        store = MockArtifactStore(episodes)
        config = MockConfig()
        context = IntrospectionContext(
            store=store,
            config=config,
            cycle_number=1,
            window_size=50,
        )

        result = mode.run(context)

        # 2 out of 4 episodes required retries (attempt_index > 1)
        self.assertEqual(result.metrics["retry_rate"], 0.5)
        # Average of attempt_index for retry episodes: (3 + 2) / 2 = 2.5
        self.assertEqual(result.metrics["avg_attempts_when_retried"], 2.5)

    def test_failure_stage_distribution(self):
        """Test failure stage metrics."""
        episodes = [
            _make_episode("ep-1", outcome="accepted"),
            _make_episode("ep-2", outcome="reverted", failure_stage="execute"),
            _make_episode("ep-3", outcome="reverted", failure_stage="execute"),
            _make_episode("ep-4", outcome="error", failure_stage="plan"),
        ]
        mode = MetricsMode()
        store = MockArtifactStore(episodes)
        config = MockConfig()
        context = IntrospectionContext(
            store=store,
            config=config,
            cycle_number=1,
            window_size=50,
        )

        result = mode.run(context)

        self.assertEqual(result.metrics["failure_execute"], 0.5)
        self.assertEqual(result.metrics["failure_plan"], 0.25)

    def test_high_error_rate_finding(self):
        """Test that high error rate generates critical finding."""
        episodes = [
            _make_episode("ep-1", outcome="error"),
            _make_episode("ep-2", outcome="error"),
            _make_episode("ep-3", outcome="accepted"),
        ]
        mode = MetricsMode()
        store = MockArtifactStore(episodes)
        config = MockConfig()
        context = IntrospectionContext(
            store=store,
            config=config,
            cycle_number=1,
            window_size=50,
        )

        result = mode.run(context)

        critical_findings = [f for f in result.findings if f.get("severity") == "critical"]
        self.assertEqual(len(critical_findings), 1)
        self.assertEqual(critical_findings[0]["type"], "high_error_rate")

    def test_high_retry_rate_finding(self):
        """Test that high retry rate generates warning finding."""
        episodes = [
            _make_episode("ep-1", outcome="accepted", attempt_index=2),
            _make_episode("ep-2", outcome="accepted", attempt_index=3),
            _make_episode("ep-3", outcome="accepted", attempt_index=1),
        ]
        mode = MetricsMode()
        store = MockArtifactStore(episodes)
        config = MockConfig()
        context = IntrospectionContext(
            store=store,
            config=config,
            cycle_number=1,
            window_size=50,
        )

        result = mode.run(context)

        # retry_rate = 2/3 > 0.3, should generate warning
        warning_findings = [
            f for f in result.findings
            if f.get("type") == "high_retry_rate" and f.get("severity") == "warning"
        ]
        self.assertEqual(len(warning_findings), 1)

    def test_low_success_rate_recommendation(self):
        """Test recommendation for low success rate."""
        episodes = [
            _make_episode("ep-1", outcome="reverted"),
            _make_episode("ep-2", outcome="reverted"),
            _make_episode("ep-3", outcome="error"),
            _make_episode("ep-4", outcome="accepted"),
        ]
        mode = MetricsMode()
        store = MockArtifactStore(episodes)
        config = MockConfig()
        context = IntrospectionContext(
            store=store,
            config=config,
            cycle_number=1,
            window_size=50,
        )

        result = mode.run(context)

        # success_rate = 0.25 < 0.5
        self.assertTrue(any("below 50%" in r for r in result.recommendations))

    def test_window_size_limits_episodes(self):
        """Test that window_size limits analyzed episodes."""
        episodes = [_make_episode(f"ep-{i}", outcome="accepted") for i in range(100)]
        mode = MetricsMode()
        store = MockArtifactStore(episodes)
        config = MockConfig()
        context = IntrospectionContext(
            store=store,
            config=config,
            cycle_number=1,
            window_size=10,
        )

        result = mode.run(context)

        # Should analyze last 10 episodes
        self.assertIn("10 episodes", result.summary)


class TestCoverageMode(unittest.TestCase):
    """Tests for CoverageMode."""

    def test_name_property(self):
        """Test mode identifier."""
        mode = CoverageMode()
        self.assertEqual(mode.name, "coverage")

    @mock.patch("homunculus.introspection.coverage.subprocess.run")
    def test_todo_scanning(self, mock_run):
        """Test TODO/FIXME comment scanning with mocked subprocess."""
        # Mock subprocess for dependency check
        mock_run.side_effect = [
            # First call: dependency check - fail to skip coverage
            subprocess.CalledProcessError(1, "check"),
        ]

        # Create temp directory structure
        import tempfile

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            source_dir = temp_path / "homunculus"
            source_dir.mkdir()

            # Create test file with TODO comments
            test_file = source_dir / "test_module.py"
            test_file.write_text(
                "# TODO: Fix this bug\n"
                "def foo():\n"
                "    # FIXME: Improve performance\n"
                "    pass\n"
                "# XXX: Temporary workaround\n"
                "# HACK: This is ugly\n",
                encoding="utf-8",
            )

            mode = CoverageMode()
            config = MockConfig(
                workspaces={"self": WorkspaceSettings(path=temp_path)}
            )
            store = MockArtifactStore([])
            context = IntrospectionContext(
                store=store,
                config=config,
                cycle_number=1,
                window_size=50,
            )

            result = mode.run(context)

            # Check TODO scanning results
            todo_finding = next(
                (f for f in result.findings if f.get("type") == "todo_count"),
                None,
            )
            self.assertIsNotNone(todo_finding)
            self.assertEqual(todo_finding["total"], 4)
            self.assertEqual(todo_finding["breakdown"]["TODO"], 1)
            self.assertEqual(todo_finding["breakdown"]["FIXME"], 1)
            self.assertEqual(todo_finding["breakdown"]["XXX"], 1)
            self.assertEqual(todo_finding["breakdown"]["HACK"], 1)

    @mock.patch("homunculus.introspection.coverage.subprocess.run")
    def test_coverage_skipped_when_not_installed(self, mock_run):
        """Test coverage is skipped when pytest/coverage not installed."""
        import subprocess as real_subprocess

        mock_run.side_effect = real_subprocess.CalledProcessError(1, "check")

        mode = CoverageMode()
        config = MockConfig(workspaces={"self": WorkspaceSettings(path=Path.cwd())})
        store = MockArtifactStore([])
        context = IntrospectionContext(
            store=store,
            config=config,
            cycle_number=1,
            window_size=50,
        )

        result = mode.run(context)

        # Should have coverage_skipped finding
        skip_finding = next(
            (f for f in result.findings if f.get("type") == "coverage_skipped"),
            None,
        )
        self.assertIsNotNone(skip_finding)

    @mock.patch("homunculus.introspection.coverage.subprocess.run")
    def test_coverage_exception_handling(self, mock_run):
        """Test exception handling in coverage analysis includes exception type."""
        import subprocess as real_subprocess

        # First call succeeds (dependency check), subsequent calls raise
        mock_run.side_effect = [
            mock.Mock(returncode=0),  # Dependency check passes
            RuntimeError("Test error"),  # Coverage fails with exception
        ]

        mode = CoverageMode()
        config = MockConfig(workspaces={"self": WorkspaceSettings(path=Path.cwd())})
        store = MockArtifactStore([])
        context = IntrospectionContext(
            store=store,
            config=config,
            cycle_number=1,
            window_size=50,
        )

        result = mode.run(context)

        # Should have error finding with exception type
        error_finding = next(
            (f for f in result.findings if f.get("type") == "coverage_error"),
            None,
        )
        self.assertIsNotNone(error_finding)
        self.assertIn("RuntimeError", error_finding.get("reason", ""))


class TestCritiqueMode(unittest.TestCase):
    """Tests for CritiqueMode."""

    def test_name_property(self):
        """Test mode identifier."""
        mode = CritiqueMode()
        self.assertEqual(mode.name, "critique")

    def test_disabled_config(self):
        """Test critique mode when disabled in config."""
        mode = CritiqueMode()
        config = MockConfig(
            introspection=IntrospectionSettings(enabled=True, critique_enabled=False)
        )
        store = MockArtifactStore([])
        context = IntrospectionContext(
            store=store,
            config=config,
            cycle_number=1,
            window_size=50,
        )

        result = mode.run(context)

        self.assertEqual(result.summary, "Critique mode disabled in config")
        self.assertEqual(result.findings, [])

    def test_insufficient_episodes(self):
        """Test critique mode with too few episodes (< 3)."""
        episodes = [
            _make_episode("ep-1", outcome="accepted"),
            _make_episode("ep-2", outcome="reverted"),
        ]
        mode = CritiqueMode()
        config = MockConfig(
            introspection=IntrospectionSettings(enabled=True, critique_enabled=True)
        )
        store = MockArtifactStore(episodes)
        context = IntrospectionContext(
            store=store,
            config=config,
            cycle_number=1,
            window_size=50,
        )

        result = mode.run(context)

        self.assertIn("Insufficient episodes", result.summary)
        self.assertEqual(result.metrics["episodes_analyzed"], 2.0)
        self.assertIn("Run more episodes", result.recommendations[0])

    def test_with_injected_teacher(self):
        """Test critique mode with injected mock teacher."""

        class MockTeacher:
            def generate(self, task, memories, student_hint):
                return TeacherResponse(
                    plan=["analyze"],
                    rationale='{"patterns": [], "weaknesses": [], "strengths": ["Good at testing"], "summary": "Overall good"}',
                    raw={
                        "choices": [
                            {
                                "message": {
                                    "content": '{"patterns": [], "weaknesses": [], "strengths": ["Good at testing"], "summary": "Overall good"}'
                                }
                            }
                        ]
                    },
                )

        episodes = [
            _make_episode("ep-1", outcome="accepted"),
            _make_episode("ep-2", outcome="accepted"),
            _make_episode("ep-3", outcome="reverted"),
        ]
        mode = CritiqueMode(teacher=MockTeacher())
        config = MockConfig(
            introspection=IntrospectionSettings(enabled=True, critique_enabled=True)
        )
        store = MockArtifactStore(episodes)
        context = IntrospectionContext(
            store=store,
            config=config,
            cycle_number=1,
            window_size=50,
        )

        result = mode.run(context)

        self.assertEqual(result.mode, "critique")
        self.assertEqual(result.metrics["episodes_analyzed"], 3.0)
        # Should have extracted the strength
        strength_findings = [f for f in result.findings if f.get("type") == "strength"]
        self.assertEqual(len(strength_findings), 1)

    def test_analysis_failure_includes_exception_type(self):
        """Test that analysis failures include exception type."""

        class FailingTeacher:
            def generate(self, task, memories, student_hint):
                raise ValueError("API error")

        episodes = [
            _make_episode("ep-1"),
            _make_episode("ep-2"),
            _make_episode("ep-3"),
        ]
        mode = CritiqueMode(teacher=FailingTeacher())
        config = MockConfig(
            introspection=IntrospectionSettings(enabled=True, critique_enabled=True)
        )
        store = MockArtifactStore(episodes)
        context = IntrospectionContext(
            store=store,
            config=config,
            cycle_number=1,
            window_size=50,
        )

        result = mode.run(context)

        # Should have analysis error finding
        self.assertIn("ValueError", result.summary)


class TestComparativeMode(unittest.TestCase):
    """Tests for ComparativeMode."""

    def test_name_property(self):
        """Test mode identifier."""
        mode = ComparativeMode()
        self.assertEqual(mode.name, "comparative")

    def test_no_episodes(self):
        """Test with no episodes."""
        mode = ComparativeMode()
        store = MockArtifactStore([])
        config = MockConfig()
        context = IntrospectionContext(
            store=store,
            config=config,
            cycle_number=1,
            window_size=50,
        )

        result = mode.run(context)

        self.assertEqual(result.summary, "No episodes in analysis window")

    def test_no_comparison_groups(self):
        """Test with episodes but no comparison_group set."""
        episodes = [
            _make_episode("ep-1", outcome="accepted"),
            _make_episode("ep-2", outcome="reverted"),
        ]
        mode = ComparativeMode()
        store = MockArtifactStore(episodes)
        config = MockConfig()
        context = IntrospectionContext(
            store=store,
            config=config,
            cycle_number=1,
            window_size=50,
        )

        result = mode.run(context)

        self.assertIn("No episodes with comparison_group", result.summary)
        self.assertIn("Use comparison_group", result.recommendations[0])

    def test_groups_without_pairs(self):
        """Test groups that don't have both winners and losers."""
        episodes = [
            _make_episode("ep-1", outcome="accepted", comparison_group="task-A"),
            _make_episode("ep-2", outcome="accepted", comparison_group="task-A"),
            _make_episode("ep-3", outcome="reverted", comparison_group="task-B"),
        ]
        mode = ComparativeMode()
        store = MockArtifactStore(episodes)
        config = MockConfig()
        context = IntrospectionContext(
            store=store,
            config=config,
            cycle_number=1,
            window_size=50,
        )

        result = mode.run(context)

        # Found groups but none comparable
        no_comparable = next(
            (f for f in result.findings if f.get("type") == "no_comparable_groups"),
            None,
        )
        self.assertIsNotNone(no_comparable)
        self.assertEqual(result.metrics["groups_found"], 2)
        self.assertEqual(result.metrics["comparable_groups"], 0)

    def test_valid_comparison_pair(self):
        """Test with valid winner/loser pairs."""
        episodes = [
            _make_episode(
                "ep-1",
                outcome="accepted",
                comparison_group="task-A",
                patch="+ new line\n- old line",
                plan=["Step 1"],
            ),
            _make_episode(
                "ep-2",
                outcome="reverted",
                comparison_group="task-A",
                patch="+ very long patch\n" * 20,
                plan=["Step 1", "Step 2", "Step 3"],
                failure_stage="execute",
            ),
        ]
        mode = ComparativeMode()
        store = MockArtifactStore(episodes)
        config = MockConfig()
        context = IntrospectionContext(
            store=store,
            config=config,
            cycle_number=1,
            window_size=50,
        )

        result = mode.run(context)

        # Should have group stats finding
        group_stats = next(
            (f for f in result.findings if f.get("type") == "group_stats"),
            None,
        )
        self.assertIsNotNone(group_stats)
        self.assertEqual(group_stats["winners"], 1)
        self.assertEqual(group_stats["losers"], 1)

        # Metrics should be floats
        self.assertIsInstance(result.metrics.get("total_winners"), float)
        self.assertIsInstance(result.metrics.get("total_losers"), float)

    def test_type_consistency_in_metrics(self):
        """Test that total_winners and total_losers are floats."""
        episodes = [
            _make_episode("ep-1", outcome="accepted", comparison_group="task-A"),
            _make_episode("ep-2", outcome="reverted", comparison_group="task-A"),
        ]
        mode = ComparativeMode()
        store = MockArtifactStore(episodes)
        config = MockConfig()
        context = IntrospectionContext(
            store=store,
            config=config,
            cycle_number=1,
            window_size=50,
        )

        result = mode.run(context)

        # Both should be floats for type consistency
        self.assertEqual(result.metrics["total_winners"], 1.0)
        self.assertEqual(result.metrics["total_losers"], 1.0)


class ComparativeTypeContractTests(unittest.TestCase):
    """Verify ComparativeMode.run() returns dict[str, float], not ints.

    Regression: groups_found and comparable_groups in the
    no-comparable-pairs branch were emitted as int (via len()),
    breaking the IntrospectionResult.metrics dict[str, float] contract
    relied on by introspection persistence and downstream consumers.
    """

    def _run_mode(self, episodes: list[EpisodeRecord]) -> IntrospectionResult:
        mode = ComparativeMode()
        store = MockArtifactStore(episodes)
        config = MockConfig()
        context = IntrospectionContext(
            store=store,
            config=config,
            cycle_number=1,
            window_size=50,
        )
        return mode.run(context)

    def _assert_all_floats(self, result: IntrospectionResult) -> None:
        for key, value in result.metrics.items():
            with self.subTest(key=key):
                # bool is a subclass of int in Python, exclude it explicitly
                self.assertNotIsInstance(
                    value, bool,
                    f"metric {key!r} is bool, expected float",
                )
                self.assertIsInstance(
                    value, float,
                    f"metric {key!r} is {type(value).__name__}, expected float",
                )

    def test_no_comparable_pairs_metrics_are_floats(self):
        """groups_found / comparable_groups must be float in the
        'groups present but no winner+loser pairs' branch."""
        episodes = [
            _make_episode("ep-1", outcome="accepted", comparison_group="task-A"),
            _make_episode("ep-2", outcome="accepted", comparison_group="task-A"),
            _make_episode("ep-3", outcome="reverted", comparison_group="task-B"),
        ]
        result = self._run_mode(episodes)
        # Confirm we are exercising the right branch
        self.assertTrue(
            any(f.get("type") == "no_comparable_groups" for f in result.findings),
            "expected to hit the no_comparable_groups branch",
        )
        self.assertIn("groups_found", result.metrics)
        self.assertIn("comparable_groups", result.metrics)
        self._assert_all_floats(result)

    def test_valid_pair_metrics_are_floats(self):
        """All metrics in the aggregated branch must be float."""
        episodes = [
            _make_episode(
                "ep-1",
                outcome="accepted",
                comparison_group="task-A",
                patch="+ new line\n- old line",
                plan=["Step 1"],
            ),
            _make_episode(
                "ep-2",
                outcome="reverted",
                comparison_group="task-A",
                patch="+ very long patch\n" * 20,
                plan=["Step 1", "Step 2", "Step 3"],
                failure_stage="execute",
            ),
        ]
        result = self._run_mode(episodes)
        self.assertTrue(result.metrics, "expected non-empty metrics in aggregated branch")
        self._assert_all_floats(result)


class TestIntrospectionScheduler(unittest.TestCase):
    """Tests for IntrospectionScheduler."""

    def _make_config(
        self,
        enabled: bool = True,
        metrics_interval: int = 1,
        critique_interval: int = 3,
        coverage_interval: int = 5,
        comparative_interval: int = 3,
        critique_enabled: bool = True,
    ) -> MockConfig:
        """Create mock config with introspection settings."""
        return MockConfig(
            introspection=IntrospectionSettings(
                enabled=enabled,
                metrics_interval=metrics_interval,
                critique_interval=critique_interval,
                coverage_interval=coverage_interval,
                comparative_interval=comparative_interval,
                critique_enabled=critique_enabled,
            )
        )

    def test_cycle_0_skipping(self):
        """Test that cycle 0 is skipped (modulo edge case)."""
        config = self._make_config()
        scheduler = IntrospectionScheduler(config)

        modes = scheduler.get_scheduled_modes(0)

        # All modes should be False for cycle 0
        self.assertFalse(modes.metrics)
        self.assertFalse(modes.critique)
        self.assertFalse(modes.coverage)
        self.assertFalse(modes.comparative)
        self.assertFalse(modes.any_scheduled())

    def test_introspection_disabled(self):
        """Test that disabled introspection returns no modes."""
        config = self._make_config(enabled=False)
        scheduler = IntrospectionScheduler(config)

        modes = scheduler.get_scheduled_modes(1)

        self.assertFalse(modes.any_scheduled())

    def test_interval_rotation(self):
        """Test that modes rotate based on intervals."""
        config = self._make_config(
            metrics_interval=1,
            critique_interval=2,
            coverage_interval=3,
            comparative_interval=2,
        )
        scheduler = IntrospectionScheduler(config)

        # Cycle 1: metrics only (1 % 1 == 0)
        modes_1 = scheduler.get_scheduled_modes(1)
        self.assertTrue(modes_1.metrics)
        self.assertFalse(modes_1.critique)
        self.assertFalse(modes_1.coverage)
        self.assertFalse(modes_1.comparative)

        # Cycle 2: metrics, critique, comparative
        modes_2 = scheduler.get_scheduled_modes(2)
        self.assertTrue(modes_2.metrics)
        self.assertTrue(modes_2.critique)
        self.assertFalse(modes_2.coverage)
        self.assertTrue(modes_2.comparative)

        # Cycle 3: metrics, coverage
        modes_3 = scheduler.get_scheduled_modes(3)
        self.assertTrue(modes_3.metrics)
        self.assertFalse(modes_3.critique)
        self.assertTrue(modes_3.coverage)
        self.assertFalse(modes_3.comparative)

        # Cycle 6: all modes
        modes_6 = scheduler.get_scheduled_modes(6)
        self.assertTrue(modes_6.metrics)
        self.assertTrue(modes_6.critique)
        self.assertTrue(modes_6.coverage)
        self.assertTrue(modes_6.comparative)

    def test_critique_can_be_disabled_independently(self):
        """Test that critique can be disabled even when interval matches."""
        config = self._make_config(critique_interval=1, critique_enabled=False)
        scheduler = IntrospectionScheduler(config)

        modes = scheduler.get_scheduled_modes(1)

        self.assertTrue(modes.metrics)
        self.assertFalse(modes.critique)  # Disabled independently

    def test_scheduled_names(self):
        """Test scheduled_names returns correct list."""
        modes = ScheduledModes(metrics=True, critique=False, coverage=True, comparative=False)

        names = modes.scheduled_names()

        self.assertEqual(names, ["metrics", "coverage"])


class TestIntrospectionResult(unittest.TestCase):
    """Tests for IntrospectionResult serialization."""

    def test_to_dict(self):
        """Test serialization to dict."""
        result = IntrospectionResult(
            mode="metrics",
            timestamp="2026-04-15T00:00:00+00:00",
            findings=[{"type": "test", "severity": "info"}],
            summary="Test summary",
            metrics={"success_rate": 0.75},
            recommendations=["Do something"],
        )

        data = result.to_dict()

        self.assertEqual(data["mode"], "metrics")
        self.assertEqual(data["timestamp"], "2026-04-15T00:00:00+00:00")
        self.assertEqual(data["findings"], [{"type": "test", "severity": "info"}])
        self.assertEqual(data["summary"], "Test summary")
        self.assertEqual(data["metrics"], {"success_rate": 0.75})
        self.assertEqual(data["recommendations"], ["Do something"])

    def test_from_dict(self):
        """Test deserialization from dict."""
        data = {
            "mode": "coverage",
            "timestamp": "2026-04-15T12:00:00+00:00",
            "findings": [{"type": "coverage", "percent": 80.0}],
            "summary": "Coverage summary",
            "metrics": {"total_coverage": 80.0},
            "recommendations": [],
        }

        result = IntrospectionResult.from_dict(data)

        self.assertEqual(result.mode, "coverage")
        self.assertEqual(result.timestamp, "2026-04-15T12:00:00+00:00")
        self.assertEqual(result.findings, [{"type": "coverage", "percent": 80.0}])
        self.assertEqual(result.summary, "Coverage summary")
        self.assertEqual(result.metrics, {"total_coverage": 80.0})
        self.assertEqual(result.recommendations, [])

    def test_round_trip(self):
        """Test serialization round trip."""
        original = IntrospectionResult(
            mode="critique",
            timestamp="2026-04-15T06:00:00+00:00",
            findings=[{"type": "pattern", "pattern": "test"}],
            summary="Round trip test",
            metrics={"patterns_found": 1.0},
            recommendations=["Recommendation A", "Recommendation B"],
        )

        data = original.to_dict()
        restored = IntrospectionResult.from_dict(data)

        self.assertEqual(restored.mode, original.mode)
        self.assertEqual(restored.timestamp, original.timestamp)
        self.assertEqual(restored.findings, original.findings)
        self.assertEqual(restored.summary, original.summary)
        self.assertEqual(restored.metrics, original.metrics)
        self.assertEqual(restored.recommendations, original.recommendations)


# Import subprocess for exception type
import subprocess


class TestGetIntrospectionMode(unittest.TestCase):
    """Tests for the get_introspection_mode factory function."""

    def test_get_metrics_mode(self):
        """Test getting metrics mode."""
        mode = get_introspection_mode("metrics")
        self.assertIsInstance(mode, MetricsMode)
        self.assertEqual(mode.name, "metrics")

    def test_get_critique_mode(self):
        """Test getting critique mode."""
        mode = get_introspection_mode("critique")
        self.assertIsInstance(mode, CritiqueMode)
        self.assertEqual(mode.name, "critique")

    def test_get_coverage_mode(self):
        """Test getting coverage mode."""
        mode = get_introspection_mode("coverage")
        self.assertIsInstance(mode, CoverageMode)
        self.assertEqual(mode.name, "coverage")

    def test_get_comparative_mode(self):
        """Test getting comparative mode."""
        mode = get_introspection_mode("comparative")
        self.assertIsInstance(mode, ComparativeMode)
        self.assertEqual(mode.name, "comparative")

    def test_unknown_mode_raises(self):
        """Test that unknown mode name raises ValueError."""
        with self.assertRaises(ValueError) as context:
            get_introspection_mode("unknown")

        self.assertIn("Unknown introspection mode", str(context.exception))
        self.assertIn("unknown", str(context.exception))
        self.assertIn("Valid modes", str(context.exception))


if __name__ == "__main__":
    unittest.main()
