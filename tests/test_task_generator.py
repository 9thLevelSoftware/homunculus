"""Tests for the task generator module."""
from __future__ import annotations

import unittest
from typing import Any

from homunculus.models import GeneratedTask, IntrospectionResult
from homunculus.task_generator import TaskGenerator


def _make_result(
    mode: str,
    findings: list[dict[str, Any]],
    recommendations: list[str] | None = None,
    metrics: dict[str, float] | None = None,
) -> IntrospectionResult:
    """Helper to create IntrospectionResult for testing."""
    return IntrospectionResult(
        mode=mode,
        timestamp="2026-04-15T00:00:00+00:00",
        findings=findings,
        summary="Test summary",
        metrics=metrics or {},
        recommendations=recommendations or [],
    )


class TestTaskGeneratorBasics(unittest.TestCase):
    """Basic tests for TaskGenerator functionality."""

    def test_init_without_store(self) -> None:
        """Test initialization without artifact store."""
        gen = TaskGenerator()
        self.assertIsNone(gen.store)

    def test_empty_results(self) -> None:
        """Test with empty results list."""
        gen = TaskGenerator()
        tasks = gen.generate_from_introspection([])
        self.assertEqual(tasks, [])

    def test_max_tasks_limit(self) -> None:
        """Test that max_tasks parameter limits output."""
        gen = TaskGenerator()
        # Create many high-severity findings
        findings = [
            {"type": "high_error_rate", "value": 0.5, "severity": "critical"}
            for _ in range(10)
        ]
        result = _make_result("metrics", findings)

        tasks = gen.generate_from_introspection([result], max_tasks=3)
        self.assertEqual(len(tasks), 3)

    def test_tasks_sorted_by_priority(self) -> None:
        """Test that tasks are returned sorted by priority (highest first)."""
        gen = TaskGenerator()
        findings = [
            {"type": "success_rate", "value": 0.4, "severity": "warning"},
            {"type": "high_error_rate", "value": 0.5, "severity": "critical"},
        ]
        result = _make_result("metrics", findings)

        tasks = gen.generate_from_introspection([result], max_tasks=10)
        # high_error_rate (critical) should come before success_rate (warning)
        self.assertGreaterEqual(tasks[0].priority, tasks[-1].priority)

    def test_unknown_mode_skipped(self) -> None:
        """Test that unknown mode is logged but doesn't crash."""
        gen = TaskGenerator()
        result = _make_result("unknown_mode", [{"type": "test"}])
        tasks = gen.generate_from_introspection([result])
        self.assertEqual(tasks, [])


class TestSeverityInference(unittest.TestCase):
    """Tests for _infer_severity helper."""

    def test_explicit_severity(self) -> None:
        """Test that explicit severity field is used."""
        gen = TaskGenerator()
        finding = {"severity": "critical"}
        self.assertEqual(gen._infer_severity(finding), "critical")

    def test_status_field(self) -> None:
        """Test fallback to status field."""
        gen = TaskGenerator()
        finding = {"status": "warning"}
        self.assertEqual(gen._infer_severity(finding), "warning")

    def test_type_weakness(self) -> None:
        """Test weakness type infers high severity."""
        gen = TaskGenerator()
        finding = {"type": "weakness"}
        self.assertEqual(gen._infer_severity(finding), "high")

    def test_type_gap(self) -> None:
        """Test gap type infers medium severity."""
        gen = TaskGenerator()
        finding = {"type": "gap"}
        self.assertEqual(gen._infer_severity(finding), "medium")

    def test_type_high_error_rate(self) -> None:
        """Test high_error_rate type infers critical."""
        gen = TaskGenerator()
        finding = {"type": "high_error_rate"}
        self.assertEqual(gen._infer_severity(finding), "critical")

    def test_impact_high(self) -> None:
        """Test impact field high infers high severity."""
        gen = TaskGenerator()
        finding = {"impact": "high"}
        self.assertEqual(gen._infer_severity(finding), "high")

    def test_default_low(self) -> None:
        """Test default severity is low."""
        gen = TaskGenerator()
        finding = {}
        self.assertEqual(gen._infer_severity(finding), "low")


class TestPriorityCalculation(unittest.TestCase):
    """Tests for _calculate_priority method."""

    def test_critical_metrics_highest(self) -> None:
        """Test critical finding in metrics mode gets highest priority."""
        gen = TaskGenerator()
        finding = {"severity": "critical"}
        priority = gen._calculate_priority(finding, "metrics")
        # 0.95 * 1.0 = 0.95
        self.assertAlmostEqual(priority, 0.95)

    def test_comparative_lower_weight(self) -> None:
        """Test comparative mode has lower weight."""
        gen = TaskGenerator()
        finding = {"severity": "critical"}
        priority = gen._calculate_priority(finding, "comparative")
        # 0.95 * 0.6 = 0.57
        self.assertAlmostEqual(priority, 0.57)

    def test_info_severity_low_priority(self) -> None:
        """Test info severity results in low priority."""
        gen = TaskGenerator()
        finding = {"severity": "info"}
        priority = gen._calculate_priority(finding, "metrics")
        # 0.2 * 1.0 = 0.2
        self.assertAlmostEqual(priority, 0.2)

    def test_clamped_to_one(self) -> None:
        """Test priority is clamped to max 1.0."""
        gen = TaskGenerator()
        finding = {"severity": "critical"}
        # Even with maximum values, should not exceed 1.0
        priority = gen._calculate_priority(finding, "metrics")
        self.assertLessEqual(priority, 1.0)


class TestMetricsMode(unittest.TestCase):
    """Tests for metrics mode task generation."""

    def test_low_success_rate_generates_task(self) -> None:
        """Test that low success rate generates a practice task."""
        gen = TaskGenerator()
        findings = [
            {"type": "success_rate", "value": 0.5, "severity": "warning"}
        ]
        result = _make_result("metrics", findings)

        tasks = gen.generate_from_introspection([result])

        self.assertEqual(len(tasks), 1)
        self.assertIn("success rate", tasks[0].prompt.lower())
        self.assertEqual(tasks[0].source, "introspection")
        self.assertEqual(tasks[0].introspection_mode, "metrics")

    def test_high_success_rate_no_task(self) -> None:
        """Test that high success rate (info severity) doesn't generate task."""
        gen = TaskGenerator()
        findings = [
            {"type": "success_rate", "value": 0.85, "severity": "info"}
        ]
        result = _make_result("metrics", findings)

        tasks = gen.generate_from_introspection([result])
        self.assertEqual(tasks, [])

    def test_high_error_rate_generates_task(self) -> None:
        """Test that high error rate generates investigation task."""
        gen = TaskGenerator()
        findings = [
            {"type": "high_error_rate", "value": 0.25, "severity": "critical"}
        ]
        result = _make_result("metrics", findings)

        tasks = gen.generate_from_introspection([result])

        self.assertEqual(len(tasks), 1)
        self.assertIn("error rate", tasks[0].prompt.lower())
        self.assertIn("25%", tasks[0].prompt)

    def test_high_retry_rate_generates_task(self) -> None:
        """Test that high retry rate generates improvement task."""
        gen = TaskGenerator()
        findings = [
            {"type": "high_retry_rate", "value": 0.4, "severity": "warning"}
        ]
        result = _make_result("metrics", findings)

        tasks = gen.generate_from_introspection([result])

        self.assertEqual(len(tasks), 1)
        self.assertIn("retry rate", tasks[0].prompt.lower())

    def test_failure_concentration_generates_task(self) -> None:
        """Test that failure concentration generates focus task."""
        gen = TaskGenerator()
        findings = [
            {
                "type": "failure_concentration",
                "value": 0.6,
                "severity": "warning",
                "detail": "Most failures occur at 'execute' stage (60% of episodes)",
            }
        ]
        result = _make_result("metrics", findings)

        tasks = gen.generate_from_introspection([result])

        self.assertEqual(len(tasks), 1)
        self.assertIn("execute", tasks[0].prompt.lower())


class TestCritiqueMode(unittest.TestCase):
    """Tests for critique mode task generation."""

    def test_pattern_generates_task(self) -> None:
        """Test that pattern finding generates analysis task."""
        gen = TaskGenerator()
        findings = [
            {
                "type": "pattern",
                "pattern": "Patches often miss edge cases",
                "frequency": "common",
                "severity": "warning",
            }
        ]
        result = _make_result("critique", findings)

        tasks = gen.generate_from_introspection([result])

        self.assertEqual(len(tasks), 1)
        self.assertIn("edge cases", tasks[0].prompt.lower())
        self.assertEqual(tasks[0].context["frequency"], "common")

    def test_weakness_generates_task(self) -> None:
        """Test that weakness finding generates improvement task."""
        gen = TaskGenerator()
        findings = [
            {
                "type": "weakness",
                "area": "error_handling",
                "description": "Error handling is inconsistent across modules",
                "impact": "high",
                "recommendation": "Standardize error handling patterns",
                "severity": "warning",
            }
        ]
        result = _make_result("critique", findings)

        tasks = gen.generate_from_introspection([result])

        self.assertEqual(len(tasks), 1)
        self.assertIn("error_handling", tasks[0].prompt)
        self.assertEqual(tasks[0].estimated_complexity, "large")  # high impact

    def test_strength_skipped(self) -> None:
        """Test that strength finding doesn't generate task."""
        gen = TaskGenerator()
        findings = [
            {"type": "strength", "description": "Good at testing", "severity": "info"}
        ]
        result = _make_result("critique", findings)

        tasks = gen.generate_from_introspection([result])
        self.assertEqual(tasks, [])

    def test_analysis_error_skipped(self) -> None:
        """Test that analysis_error finding doesn't generate task."""
        gen = TaskGenerator()
        findings = [
            {"type": "analysis_error", "error": "API timeout", "severity": "warning"}
        ]
        result = _make_result("critique", findings)

        tasks = gen.generate_from_introspection([result])
        self.assertEqual(tasks, [])

    def test_empty_pattern_skipped(self) -> None:
        """Test that pattern with empty pattern text is skipped."""
        gen = TaskGenerator()
        findings = [
            {"type": "pattern", "pattern": "", "frequency": "common", "severity": "warning"}
        ]
        result = _make_result("critique", findings)

        tasks = gen.generate_from_introspection([result])
        self.assertEqual(tasks, [])


class TestCoverageMode(unittest.TestCase):
    """Tests for coverage mode task generation."""

    def test_low_total_coverage_generates_task(self) -> None:
        """Test that low total coverage generates improvement task."""
        gen = TaskGenerator()
        findings = [
            {"type": "total_coverage", "percent": 55.0, "severity": "warning"}
        ]
        result = _make_result("coverage", findings)

        tasks = gen.generate_from_introspection([result])

        self.assertEqual(len(tasks), 1)
        self.assertIn("55%", tasks[0].prompt)
        self.assertIn("coverage", tasks[0].prompt.lower())

    def test_high_coverage_no_task(self) -> None:
        """Test that good coverage doesn't generate task."""
        gen = TaskGenerator()
        findings = [
            {"type": "total_coverage", "percent": 85.0, "severity": "info"}
        ]
        result = _make_result("coverage", findings)

        tasks = gen.generate_from_introspection([result])
        self.assertEqual(tasks, [])

    def test_low_coverage_files_generates_task(self) -> None:
        """Test that low coverage files generate targeted task."""
        gen = TaskGenerator()
        findings = [
            {
                "type": "low_coverage_files",
                "files": [
                    {"file": "module_a.py", "percent": 20.0},
                    {"file": "module_b.py", "percent": 35.0},
                    {"file": "module_c.py", "percent": 40.0},
                ],
                "count": 3,
                "severity": "warning",
            }
        ]
        result = _make_result("coverage", findings)

        tasks = gen.generate_from_introspection([result])

        self.assertEqual(len(tasks), 1)
        self.assertIn("module_a.py", tasks[0].prompt)
        self.assertEqual(tasks[0].target_files, ["module_a.py", "module_b.py", "module_c.py"])

    def test_significant_todo_count_generates_task(self) -> None:
        """Test that high TODO count (>= 10) generates cleanup task."""
        gen = TaskGenerator()
        findings = [
            {
                "type": "todo_count",
                "total": 15,
                "todos": [
                    {"file": "main.py", "line": 10, "type": "TODO", "text": "Fix this"},
                    {"file": "util.py", "line": 20, "type": "FIXME", "text": "Broken"},
                ],
                "breakdown": {"TODO": 10, "FIXME": 5},
                "severity": "warning",
            }
        ]
        result = _make_result("coverage", findings)

        tasks = gen.generate_from_introspection([result])

        self.assertEqual(len(tasks), 1)
        self.assertIn("15", tasks[0].prompt)
        self.assertIn("TODO", tasks[0].prompt)

    def test_low_todo_count_no_task(self) -> None:
        """Test that low TODO count doesn't generate task."""
        gen = TaskGenerator()
        findings = [
            {
                "type": "todo_count",
                "total": 5,
                "todos": [],
                "breakdown": {"TODO": 5},
                "severity": "info",
            }
        ]
        result = _make_result("coverage", findings)

        tasks = gen.generate_from_introspection([result])
        self.assertEqual(tasks, [])

    def test_many_untested_modules_generates_task(self) -> None:
        """Test that many untested modules generate test creation task."""
        gen = TaskGenerator()
        findings = [
            {
                "type": "untested_modules",
                "modules": ["mod_a", "mod_b", "mod_c", "mod_d", "mod_e"],
                "count": 5,
                "severity": "warning",
            }
        ]
        result = _make_result("coverage", findings)

        tasks = gen.generate_from_introspection([result])

        self.assertEqual(len(tasks), 1)
        self.assertIn("mod_a", tasks[0].prompt)

    def test_few_untested_modules_no_task(self) -> None:
        """Test that <= 3 untested modules doesn't generate task."""
        gen = TaskGenerator()
        findings = [
            {
                "type": "untested_modules",
                "modules": ["mod_a", "mod_b"],
                "count": 2,
                "severity": "info",
            }
        ]
        result = _make_result("coverage", findings)

        tasks = gen.generate_from_introspection([result])
        self.assertEqual(tasks, [])

    def test_coverage_error_skipped(self) -> None:
        """Test that coverage error finding doesn't generate task."""
        gen = TaskGenerator()
        findings = [
            {"type": "coverage_error", "reason": "pytest failed", "severity": "warning"}
        ]
        result = _make_result("coverage", findings)

        tasks = gen.generate_from_introspection([result])
        self.assertEqual(tasks, [])


class TestComparativeMode(unittest.TestCase):
    """Tests for comparative mode task generation."""

    def test_size_pattern_generates_task(self) -> None:
        """Test that size pattern generates learning task."""
        gen = TaskGenerator()
        findings = [
            {
                "type": "size_pattern",
                "severity": "info",
                "detail": "Winners average 50 lines vs losers 150 lines",
            }
        ]
        result = _make_result("comparative", findings)

        tasks = gen.generate_from_introspection([result])

        self.assertEqual(len(tasks), 1)
        self.assertIn("smaller", tasks[0].prompt.lower())

    def test_plan_pattern_generates_task(self) -> None:
        """Test that plan pattern generates learning task."""
        gen = TaskGenerator()
        findings = [
            {
                "type": "plan_pattern",
                "severity": "info",
                "detail": "Winners average 3 plan steps vs losers 8",
            }
        ]
        result = _make_result("comparative", findings)

        tasks = gen.generate_from_introspection([result])

        self.assertEqual(len(tasks), 1)
        self.assertIn("simpler", tasks[0].prompt.lower())

    def test_failure_stage_pattern_generates_task(self) -> None:
        """Test that failure stage pattern generates improvement task."""
        gen = TaskGenerator()
        findings = [
            {
                "type": "failure_stage_pattern",
                "severity": "warning",
                "detail": "Most common failure stage: 'execute' (70% of losers)",
            }
        ]
        result = _make_result("comparative", findings)

        tasks = gen.generate_from_introspection([result])

        self.assertEqual(len(tasks), 1)
        self.assertIn("execute", tasks[0].prompt.lower())

    def test_group_stats_skipped(self) -> None:
        """Test that group_stats finding doesn't generate task."""
        gen = TaskGenerator()
        findings = [
            {
                "type": "group_stats",
                "group_id": "task-A",
                "winners": 2,
                "losers": 1,
                "severity": "info",
            }
        ]
        result = _make_result("comparative", findings)

        tasks = gen.generate_from_introspection([result])
        self.assertEqual(tasks, [])


class TestDefensiveParsing(unittest.TestCase):
    """Tests for defensive handling of malformed findings."""

    def test_missing_value_field(self) -> None:
        """Test handling of missing value field in metrics."""
        gen = TaskGenerator()
        findings = [
            {"type": "success_rate", "severity": "warning"}  # missing 'value'
        ]
        result = _make_result("metrics", findings)

        # Should not crash, uses default 0.0
        tasks = gen.generate_from_introspection([result])
        # With value 0.0 < 0.7, should generate task
        self.assertEqual(len(tasks), 1)

    def test_extra_fields_ignored(self) -> None:
        """Test that unexpected fields are ignored."""
        gen = TaskGenerator()
        findings = [
            {
                "type": "success_rate",
                "value": 0.4,
                "severity": "warning",
                "unexpected_field": "ignored",
                "another_field": 12345,
            }
        ]
        result = _make_result("metrics", findings)

        tasks = gen.generate_from_introspection([result])
        self.assertEqual(len(tasks), 1)

    def test_empty_findings_list(self) -> None:
        """Test handling of empty findings list."""
        gen = TaskGenerator()
        result = _make_result("metrics", [])

        tasks = gen.generate_from_introspection([result])
        self.assertEqual(tasks, [])

    def test_none_values_handled(self) -> None:
        """Test handling of None values in findings."""
        gen = TaskGenerator()
        findings = [
            {
                "type": "weakness",
                "area": None,  # Should be handled
                "description": None,
                "recommendation": None,
                "severity": "warning",
            }
        ]
        result = _make_result("critique", findings)

        # Should not crash
        tasks = gen.generate_from_introspection([result])
        # With area None, prompt will show 'None'
        self.assertEqual(len(tasks), 1)


class TestMultipleResults(unittest.TestCase):
    """Tests for processing multiple introspection results."""

    def test_multiple_modes(self) -> None:
        """Test tasks from multiple modes are combined."""
        gen = TaskGenerator()

        metrics_result = _make_result(
            "metrics",
            [{"type": "high_error_rate", "value": 0.2, "severity": "critical"}],
        )
        critique_result = _make_result(
            "critique",
            [
                {
                    "type": "weakness",
                    "area": "testing",
                    "description": "Needs work",
                    "severity": "warning",
                }
            ],
        )

        tasks = gen.generate_from_introspection(
            [metrics_result, critique_result], max_tasks=10
        )

        self.assertEqual(len(tasks), 2)
        modes = {t.introspection_mode for t in tasks}
        self.assertEqual(modes, {"metrics", "critique"})

    def test_priority_ordering_across_modes(self) -> None:
        """Test that priority ordering works across modes."""
        gen = TaskGenerator()

        # Metrics critical should be higher than critique warning
        metrics_result = _make_result(
            "metrics",
            [{"type": "high_error_rate", "value": 0.2, "severity": "critical"}],
        )
        critique_result = _make_result(
            "critique",
            [
                {
                    "type": "weakness",
                    "area": "testing",
                    "description": "Needs work",
                    "severity": "warning",
                }
            ],
        )

        tasks = gen.generate_from_introspection(
            [critique_result, metrics_result], max_tasks=10
        )

        # Critical metrics should be first due to higher mode weight
        self.assertEqual(tasks[0].introspection_mode, "metrics")


class TestTaskFields(unittest.TestCase):
    """Tests for correct task field population."""

    def test_task_id_unique(self) -> None:
        """Test that each task gets a unique ID."""
        gen = TaskGenerator()
        findings = [
            {"type": "high_error_rate", "value": 0.2, "severity": "critical"},
            {"type": "high_retry_rate", "value": 0.4, "severity": "warning"},
        ]
        result = _make_result("metrics", findings)

        tasks = gen.generate_from_introspection([result])

        task_ids = [t.task_id for t in tasks]
        self.assertEqual(len(task_ids), len(set(task_ids)))

    def test_task_source_introspection(self) -> None:
        """Test that source is always 'introspection'."""
        gen = TaskGenerator()
        findings = [
            {"type": "high_error_rate", "value": 0.2, "severity": "critical"}
        ]
        result = _make_result("metrics", findings)

        tasks = gen.generate_from_introspection([result])

        self.assertEqual(tasks[0].source, "introspection")

    def test_context_contains_finding_info(self) -> None:
        """Test that context contains finding information."""
        gen = TaskGenerator()
        findings = [
            {"type": "high_error_rate", "value": 0.2, "severity": "critical"}
        ]
        result = _make_result("metrics", findings)

        tasks = gen.generate_from_introspection([result])

        self.assertEqual(tasks[0].context["finding_type"], "high_error_rate")
        self.assertEqual(tasks[0].context["value"], 0.2)


class TaskGeneratorSourceContractTests(unittest.TestCase):
    """Lock the ``source`` literal emitted by the task generator.

    Regressions manifested historically as B3: the reporter expected
    ``"generated"`` or ``"resonance"`` but the producer emitted
    ``"introspection"``, silently zeroing SC2. If you change the
    producer literal, update ``SELF_DIRECTED_SOURCES`` in
    ``homunculus.autonomy.sources`` *first*."""

    def test_metrics_findings_emit_introspection_source(self) -> None:
        from homunculus.task_generator import TaskGenerator
        from homunculus.autonomy.sources import SELF_DIRECTED_SOURCES

        gen = TaskGenerator()
        findings = [
            {"type": "high_error_rate", "value": 0.5, "severity": "critical"}
        ]
        result = _make_result("metrics", findings)
        tasks = gen.generate_from_introspection([result])
        self.assertTrue(tasks, "generator must yield at least one task")
        for task in tasks:
            self.assertIn(
                task.source,
                SELF_DIRECTED_SOURCES,
                f"producer emitted {task.source!r} which is not in "
                f"SELF_DIRECTED_SOURCES={SELF_DIRECTED_SOURCES}",
            )


if __name__ == "__main__":
    unittest.main()
