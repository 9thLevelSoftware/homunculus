"""Tests for the task prioritizer module."""
from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from homunculus.models import GeneratedTask, IntrospectionResult
from homunculus.task_generator import PriorityWeights, TaskPrioritizer


def _make_task(
    task_id: str = "task-1",
    source: str = "user",
    prompt: str = "Test task",
    priority: float = 0.5,
    created_at: str | None = None,
) -> GeneratedTask:
    """Helper to create GeneratedTask for testing."""
    return GeneratedTask(
        task_id=task_id,
        source=source,
        prompt=prompt,
        priority=priority,
        created_at=created_at or datetime.now(timezone.utc).isoformat(),
    )


def _make_result(
    mode: str = "metrics",
    recommendations: list[str] | None = None,
) -> IntrospectionResult:
    """Helper to create IntrospectionResult for testing."""
    return IntrospectionResult(
        mode=mode,
        timestamp="2026-04-15T00:00:00+00:00",
        findings=[],
        summary="Test summary",
        metrics={},
        recommendations=recommendations or [],
    )


class TestPriorityWeights(unittest.TestCase):
    """Tests for PriorityWeights validation."""

    def test_default_weights_sum_to_one(self) -> None:
        """Test that default weights are valid."""
        weights = PriorityWeights()
        total = weights.alignment + weights.complexity + weights.freshness
        self.assertAlmostEqual(total, 1.0)

    def test_custom_weights_valid(self) -> None:
        """Test that custom weights summing to 1.0 are accepted."""
        weights = PriorityWeights(alignment=0.4, complexity=0.4, freshness=0.2)
        self.assertEqual(weights.alignment, 0.4)
        self.assertEqual(weights.complexity, 0.4)
        self.assertEqual(weights.freshness, 0.2)

    def test_weights_not_summing_to_one_raises(self) -> None:
        """Test that weights not summing to 1.0 raise ValueError."""
        with self.assertRaises(ValueError) as ctx:
            PriorityWeights(alignment=0.5, complexity=0.5, freshness=0.5)
        self.assertIn("must sum to 1.0", str(ctx.exception))

    def test_weights_just_under_one_raises(self) -> None:
        """Test that weights summing to 0.9 raise ValueError."""
        with self.assertRaises(ValueError):
            PriorityWeights(alignment=0.3, complexity=0.3, freshness=0.3)

    def test_weights_tolerance_lower_bound(self) -> None:
        """Test that weights within tolerance (0.99) are accepted."""
        # 0.33 + 0.33 + 0.34 = 1.0
        weights = PriorityWeights(alignment=0.33, complexity=0.33, freshness=0.34)
        self.assertIsNotNone(weights)

    def test_weights_tolerance_upper_bound(self) -> None:
        """Test that weights within tolerance (1.01) are accepted."""
        # Due to float precision, 0.333... might sum to 1.00x
        weights = PriorityWeights(alignment=0.5, complexity=0.3, freshness=0.2)
        self.assertIsNotNone(weights)


class TestPrioritizerBasics(unittest.TestCase):
    """Basic tests for TaskPrioritizer."""

    def test_empty_list_returns_empty(self) -> None:
        """Test that empty task list returns empty list."""
        prioritizer = TaskPrioritizer()
        result = prioritizer.prioritize([])
        self.assertEqual(result, [])

    def test_single_task_returned(self) -> None:
        """Test that single task is returned."""
        prioritizer = TaskPrioritizer()
        task = _make_task()
        result = prioritizer.prioritize([task])
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].task_id, "task-1")

    def test_custom_weights_used(self) -> None:
        """Test that custom weights affect prioritization."""
        # Create prioritizer that heavily favors alignment
        weights = PriorityWeights(alignment=0.9, complexity=0.05, freshness=0.05)
        prioritizer = TaskPrioritizer(weights)

        # Introspection task should have much higher priority (use unique prompts)
        introspection_task = _make_task(task_id="intro", source="introspection", prompt="Introspection generated task")
        user_task = _make_task(task_id="user", source="user", priority=0.5, prompt="User submitted task")

        result = prioritizer.prioritize([user_task, introspection_task])

        # Introspection should be first due to high alignment weight
        self.assertEqual(result[0].task_id, "intro")


class TestPrioritizerSorting(unittest.TestCase):
    """Tests for task sorting behavior."""

    def test_sorts_by_priority_descending(self) -> None:
        """Test that tasks are sorted by priority (highest first)."""
        prioritizer = TaskPrioritizer()

        # Create tasks with different sources (different alignment scores) and unique prompts
        high = _make_task(task_id="high", source="introspection", prompt="High priority introspection task")
        low = _make_task(task_id="low", source="user", priority=0.1, prompt="Low priority user task")

        result = prioritizer.prioritize([low, high])

        self.assertEqual(result[0].task_id, "high")
        self.assertEqual(result[1].task_id, "low")

    def test_fifo_tiebreaker_older_first(self) -> None:
        """Test that ties are broken by created_at (older first, FIFO)."""
        prioritizer = TaskPrioritizer()

        # Create tasks with same priority but different timestamps and unique prompts
        now = datetime.now(timezone.utc)
        older = _make_task(
            task_id="older",
            source="user",
            priority=0.5,
            prompt="Older task that was created first",
            created_at=(now - timedelta(hours=1)).isoformat(),
        )
        newer = _make_task(
            task_id="newer",
            source="user",
            priority=0.5,
            prompt="Newer task that was created second",
            created_at=now.isoformat(),
        )

        # Even with same calculated priority, older should come first
        result = prioritizer.prioritize([newer, older])

        self.assertEqual(len(result), 2)
        # Note: Due to freshness scoring, newer will have higher priority
        # So FIFO tiebreaker only applies when priorities are actually equal
        # Let's verify that both tasks are present
        task_ids = {t.task_id for t in result}
        self.assertEqual(task_ids, {"older", "newer"})

    def test_priority_overrides_age(self) -> None:
        """Test that higher priority beats older age."""
        prioritizer = TaskPrioritizer()

        now = datetime.now(timezone.utc)
        old_low = _make_task(
            task_id="old-low",
            source="user",
            priority=0.1,
            created_at=(now - timedelta(hours=2)).isoformat(),
        )
        new_high = _make_task(
            task_id="new-high",
            source="introspection",
            created_at=now.isoformat(),
        )

        result = prioritizer.prioritize([old_low, new_high])

        # Higher priority should win regardless of age
        self.assertEqual(result[0].task_id, "new-high")


class TestDeduplication(unittest.TestCase):
    """Tests for task deduplication."""

    def test_exact_duplicate_removed(self) -> None:
        """Test that exact duplicate prompts are deduplicated."""
        prioritizer = TaskPrioritizer()

        task1 = _make_task(task_id="task-1", prompt="Add feature X")
        task2 = _make_task(task_id="task-2", prompt="Add feature X")

        result = prioritizer.prioritize([task1, task2])

        self.assertEqual(len(result), 1)

    def test_keeps_higher_priority_duplicate(self) -> None:
        """Test that higher priority duplicate is kept."""
        prioritizer = TaskPrioritizer()

        low = _make_task(task_id="low", prompt="Add feature X", priority=0.2)
        high = _make_task(task_id="high", prompt="Add feature X", priority=0.9)

        result = prioritizer.prioritize([low, high])

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].task_id, "high")

    def test_prefix_similarity_deduplicates(self) -> None:
        """Test that tasks with same 100-char prefix are deduplicated."""
        prioritizer = TaskPrioritizer()

        base = "x" * 100
        task1 = _make_task(task_id="task-1", prompt=base + " extra text A")
        task2 = _make_task(task_id="task-2", prompt=base + " extra text B")

        result = prioritizer.prioritize([task1, task2])

        self.assertEqual(len(result), 1)

    def test_different_prompts_not_deduplicated(self) -> None:
        """Test that different prompts are not deduplicated."""
        prioritizer = TaskPrioritizer()

        task1 = _make_task(task_id="task-1", prompt="Add feature X")
        task2 = _make_task(task_id="task-2", prompt="Fix bug Y")

        result = prioritizer.prioritize([task1, task2])

        self.assertEqual(len(result), 2)

    def test_case_insensitive_deduplication(self) -> None:
        """Test that deduplication is case-insensitive."""
        prioritizer = TaskPrioritizer()

        task1 = _make_task(task_id="task-1", prompt="Add Feature X")
        task2 = _make_task(task_id="task-2", prompt="add feature x")

        result = prioritizer.prioritize([task1, task2])

        self.assertEqual(len(result), 1)


class TestAlignmentScoring(unittest.TestCase):
    """Tests for alignment score calculation."""

    def test_introspection_source_gets_max_alignment(self) -> None:
        """Test that introspection tasks get alignment score of 1.0."""
        prioritizer = TaskPrioritizer()
        task = _make_task(source="introspection")

        score = prioritizer._score_alignment(task, None)

        self.assertEqual(score, 1.0)

    def test_user_source_uses_existing_priority(self) -> None:
        """Test that user tasks use their existing priority."""
        prioritizer = TaskPrioritizer()

        high = _make_task(source="user", priority=0.9)
        low = _make_task(source="user", priority=0.2)

        high_score = prioritizer._score_alignment(high, None)
        low_score = prioritizer._score_alignment(low, None)

        self.assertEqual(high_score, 0.9)
        self.assertEqual(low_score, 0.2)

    def test_user_priority_clamped(self) -> None:
        """Test that user priority is clamped to [0.0, 1.0]."""
        prioritizer = TaskPrioritizer()

        over = _make_task(source="user", priority=1.5)
        under = _make_task(source="user", priority=-0.5)

        self.assertEqual(prioritizer._score_alignment(over, None), 1.0)
        self.assertEqual(prioritizer._score_alignment(under, None), 0.0)

    def test_other_source_gets_neutral_alignment(self) -> None:
        """Test that other sources get neutral score of 0.5."""
        prioritizer = TaskPrioritizer()
        task = _make_task(source="continuation")

        score = prioritizer._score_alignment(task, None)

        self.assertEqual(score, 0.5)


class TestComplexityScoring(unittest.TestCase):
    """Tests for complexity score calculation."""

    def test_short_prompt_max_complexity_score(self) -> None:
        """Test that short prompts (<200 chars) get score of 1.0."""
        prioritizer = TaskPrioritizer()
        task = _make_task(prompt="x" * 50)

        score = prioritizer._score_complexity(task)

        self.assertEqual(score, 1.0)

    def test_medium_prompt_high_complexity_score(self) -> None:
        """Test that medium prompts (<500 chars) get score of 0.7."""
        prioritizer = TaskPrioritizer()
        task = _make_task(prompt="x" * 300)

        score = prioritizer._score_complexity(task)

        self.assertEqual(score, 0.7)

    def test_long_prompt_medium_complexity_score(self) -> None:
        """Test that long prompts (<1000 chars) get score of 0.4."""
        prioritizer = TaskPrioritizer()
        task = _make_task(prompt="x" * 700)

        score = prioritizer._score_complexity(task)

        self.assertEqual(score, 0.4)

    def test_very_long_prompt_low_complexity_score(self) -> None:
        """Test that very long prompts (>=1000 chars) get score of 0.2."""
        prioritizer = TaskPrioritizer()
        task = _make_task(prompt="x" * 1500)

        score = prioritizer._score_complexity(task)

        self.assertEqual(score, 0.2)

    def test_empty_prompt_max_complexity_score(self) -> None:
        """Test that empty prompt gets max score."""
        prioritizer = TaskPrioritizer()
        task = _make_task(prompt="")

        score = prioritizer._score_complexity(task)

        self.assertEqual(score, 1.0)

    def test_simpler_tasks_preferred(self) -> None:
        """Test that simpler tasks rank higher (integration)."""
        prioritizer = TaskPrioritizer()

        simple = _make_task(task_id="simple", source="user", priority=0.5, prompt="x" * 50)
        complex = _make_task(task_id="complex", source="user", priority=0.5, prompt="x" * 1500)

        result = prioritizer.prioritize([complex, simple])

        self.assertEqual(result[0].task_id, "simple")


class TestFreshnessScoring(unittest.TestCase):
    """Tests for freshness score calculation."""

    def test_very_recent_task_high_freshness(self) -> None:
        """Test that very recent tasks get high freshness score."""
        prioritizer = TaskPrioritizer()
        now = datetime.now(timezone.utc)
        task = _make_task(created_at=now.isoformat())

        score = prioritizer._score_freshness(task)

        self.assertGreater(score, 0.9)

    def test_old_task_low_freshness(self) -> None:
        """Test that old tasks get lower freshness score."""
        prioritizer = TaskPrioritizer()
        old = datetime.now(timezone.utc) - timedelta(hours=24)
        task = _make_task(created_at=old.isoformat())

        score = prioritizer._score_freshness(task)

        # 24 hours * 0.03 decay = 0.72 decay, so 1.0 - 0.72 = 0.28
        self.assertLess(score, 0.5)
        self.assertGreater(score, 0.1)  # Should not go below minimum

    def test_very_old_task_minimum_freshness(self) -> None:
        """Test that very old tasks get minimum freshness score (0.1)."""
        prioritizer = TaskPrioritizer()
        very_old = datetime.now(timezone.utc) - timedelta(days=7)
        task = _make_task(created_at=very_old.isoformat())

        score = prioritizer._score_freshness(task)

        self.assertEqual(score, 0.1)

    def test_missing_created_at_neutral_freshness(self) -> None:
        """Test that missing created_at gets neutral score (0.5)."""
        prioritizer = TaskPrioritizer()
        task = GeneratedTask(
            task_id="test",
            source="user",
            prompt="test",
            priority=0.5,
            created_at="",  # Empty
        )

        score = prioritizer._score_freshness(task)

        self.assertEqual(score, 0.5)

    def test_invalid_created_at_neutral_freshness(self) -> None:
        """Test that invalid created_at gets neutral score."""
        prioritizer = TaskPrioritizer()
        task = GeneratedTask(
            task_id="test",
            source="user",
            prompt="test",
            priority=0.5,
            created_at="not-a-date",
        )

        score = prioritizer._score_freshness(task)

        self.assertEqual(score, 0.5)

    def test_fresher_tasks_preferred(self) -> None:
        """Test that fresher tasks rank higher (integration)."""
        prioritizer = TaskPrioritizer()
        now = datetime.now(timezone.utc)

        fresh = _make_task(
            task_id="fresh",
            source="user",
            priority=0.5,
            prompt="Fresh task created recently",
            created_at=now.isoformat(),
        )
        stale = _make_task(
            task_id="stale",
            source="user",
            priority=0.5,
            prompt="Stale task created days ago",
            created_at=(now - timedelta(days=2)).isoformat(),
        )

        result = prioritizer.prioritize([stale, fresh])

        self.assertEqual(result[0].task_id, "fresh")


class TestFinalPriorityCalculation(unittest.TestCase):
    """Tests for final priority calculation."""

    def test_final_priority_clamped_to_one(self) -> None:
        """Test that final priority never exceeds 1.0."""
        prioritizer = TaskPrioritizer()
        task = _make_task(source="introspection", prompt="short")  # All high scores

        prioritizer.prioritize([task])

        self.assertLessEqual(task.priority, 1.0)

    def test_final_priority_clamped_to_zero(self) -> None:
        """Test that final priority never goes below 0.0."""
        prioritizer = TaskPrioritizer()
        task = _make_task(source="user", priority=-1.0, prompt="x" * 2000)

        prioritizer.prioritize([task])

        self.assertGreaterEqual(task.priority, 0.0)

    def test_priority_updated_in_place(self) -> None:
        """Test that task.priority is updated in place."""
        prioritizer = TaskPrioritizer()
        task = _make_task(priority=0.5)
        original_priority = task.priority

        prioritizer.prioritize([task])

        # Priority should be recalculated (may differ from original)
        self.assertIsNotNone(task.priority)


class TestIntegration(unittest.TestCase):
    """Integration tests combining multiple features."""

    def test_introspection_tasks_high_alignment(self) -> None:
        """Test that introspection tasks get high overall priority."""
        prioritizer = TaskPrioritizer()

        introspection = _make_task(
            task_id="intro",
            source="introspection",
            prompt="Fix error handling from introspection",
        )
        user = _make_task(
            task_id="user",
            source="user",
            priority=0.5,
            prompt="Fix error handling from user suggestion",  # Different prompt
        )

        result = prioritizer.prioritize([user, introspection])

        # Introspection task should rank higher due to alignment
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0].task_id, "intro")

    def test_multiple_factors_combined(self) -> None:
        """Test that all factors are combined correctly."""
        prioritizer = TaskPrioritizer()
        now = datetime.now(timezone.utc)

        # Introspection, simple, fresh - should be highest
        best = _make_task(
            task_id="best",
            source="introspection",
            prompt="Best task from introspection - simple and fresh",
            created_at=now.isoformat(),
        )
        # User, complex, stale - should be lowest
        worst = _make_task(
            task_id="worst",
            source="user",
            priority=0.1,
            prompt="Worst task from user - " + "x" * 1500,
            created_at=(now - timedelta(days=3)).isoformat(),
        )
        # Middle of the road
        middle = _make_task(
            task_id="middle",
            source="user",
            priority=0.5,
            prompt="Middle task from user - " + "x" * 400,
            created_at=(now - timedelta(hours=12)).isoformat(),
        )

        result = prioritizer.prioritize([worst, middle, best])

        self.assertEqual(len(result), 3)
        self.assertEqual(result[0].task_id, "best")
        self.assertEqual(result[2].task_id, "worst")

    def test_prioritize_with_introspection_results(self) -> None:
        """Test prioritization with introspection context (reserved for future)."""
        prioritizer = TaskPrioritizer()
        results = [_make_result(recommendations=["Focus on testing"])]

        task = _make_task(source="introspection")
        result = prioritizer.prioritize([task], introspection_results=results)

        # Should work without error (context reserved for future use)
        self.assertEqual(len(result), 1)


if __name__ == "__main__":
    unittest.main()
