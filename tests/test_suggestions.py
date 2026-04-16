from __future__ import annotations

import tempfile
from pathlib import Path
import unittest

from homunculus.suggestions import SuggestionReader
from homunculus.models import GeneratedTask, IntrospectionResult


class SuggestionReaderTests(unittest.TestCase):
    def test_parse_suggestion_markdown(self) -> None:
        with tempfile.TemporaryDirectory() as temp_root:
            temp_path = Path(temp_root)
            suggestions_dir = temp_path / "suggestions"
            suggestions_dir.mkdir()

            suggestion_file = suggestions_dir / "add-feature.md"
            suggestion_file.write_text("""# Add Feature X

## Priority
HIGH

## What
Add a new feature that does X.

## Why
This improves Y.

## Success Criteria
Tests pass and feature works.

## Hints
- Look at module Z
- Check existing patterns
""", encoding="utf-8")

            reader = SuggestionReader(suggestions_dir)
            tasks = reader.read_pending()

            self.assertEqual(len(tasks), 1)
            task = tasks[0]
            self.assertEqual(task.source, "user")
            self.assertIn("Add a new feature that does X", task.prompt)
            self.assertEqual(task.priority, 1.0)  # HIGH = 1.0
            self.assertIn("Tests pass", task.success_criteria)

    def test_empty_suggestions_directory_returns_empty_list(self) -> None:
        with tempfile.TemporaryDirectory() as temp_root:
            temp_path = Path(temp_root)
            suggestions_dir = temp_path / "suggestions"
            suggestions_dir.mkdir()

            reader = SuggestionReader(suggestions_dir)
            tasks = reader.read_pending()

            self.assertEqual(tasks, [])

    def test_archive_suggestion_moves_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_root:
            temp_path = Path(temp_root)
            suggestions_dir = temp_path / "suggestions"
            suggestions_dir.mkdir()

            suggestion_file = suggestions_dir / "test-task.md"
            suggestion_file.write_text("# Test\n\n## What\nTest task", encoding="utf-8")

            reader = SuggestionReader(suggestions_dir)
            reader.archive("test-task.md", "accepted")

            self.assertFalse(suggestion_file.exists())
            archive_dir = suggestions_dir / "archive"
            self.assertTrue((archive_dir / "test-task.accepted.md").exists())

    def test_resonance_keywords_has_10_categories(self) -> None:
        self.assertEqual(len(SuggestionReader.RESONANCE_KEYWORDS), 10)
        expected = {
            "error", "testing", "async", "performance", "security",
            "documentation", "refactor", "patching", "planning", "lifecycle"
        }
        self.assertEqual(set(SuggestionReader.RESONANCE_KEYWORDS.keys()), expected)

    def test_extract_keywords_finds_categories(self) -> None:
        with tempfile.TemporaryDirectory() as temp_root:
            reader = SuggestionReader(Path(temp_root))

            # Test error category
            keywords = reader._extract_keywords("Add retry logic for exception handling")
            self.assertIn("error", keywords)

            # Test testing category
            keywords = reader._extract_keywords("Increase test coverage")
            self.assertIn("testing", keywords)

            # Test multiple categories
            keywords = reader._extract_keywords("Refactor to simplify and add tests")
            self.assertIn("refactor", keywords)
            self.assertIn("testing", keywords)

            # Test no matches
            keywords = reader._extract_keywords("Do something unrelated")
            self.assertEqual(keywords, set())

    def test_score_resonance_returns_zero_for_empty_results(self) -> None:
        with tempfile.TemporaryDirectory() as temp_root:
            reader = SuggestionReader(Path(temp_root))
            task = GeneratedTask(
                task_id="test-1",
                source="user",
                prompt="Add error handling",
                priority=0.5,
            )
            score = reader.score_resonance(task, [])
            self.assertEqual(score, 0.0)

    def test_score_resonance_returns_zero_for_no_keyword_match(self) -> None:
        with tempfile.TemporaryDirectory() as temp_root:
            reader = SuggestionReader(Path(temp_root))
            task = GeneratedTask(
                task_id="test-1",
                source="user",
                prompt="Do something generic",
                priority=0.5,
            )
            introspection = IntrospectionResult(
                mode="metrics",
                timestamp="2026-04-15T00:00:00Z",
                findings=[],
                summary="All good",
                metrics={},
                recommendations=["Focus on error handling"],
            )
            score = reader.score_resonance(task, [introspection])
            self.assertEqual(score, 0.0)

    def test_score_resonance_positive_for_aligned_task(self) -> None:
        with tempfile.TemporaryDirectory() as temp_root:
            reader = SuggestionReader(Path(temp_root))
            task = GeneratedTask(
                task_id="test-1",
                source="user",
                prompt="Add retry logic with exception handling",
                priority=0.5,
            )
            introspection = IntrospectionResult(
                mode="metrics",
                timestamp="2026-04-15T00:00:00Z",
                findings=[{"area": "error handling", "description": "Error rate is high"}],
                summary="Errors are critical",
                metrics={"error_rate": 0.1},
                recommendations=["Focus on error handling and fail recovery"],
            )
            score = reader.score_resonance(task, [introspection])
            self.assertGreater(score, 0.0)
            self.assertLessEqual(score, 1.0)

    def test_score_resonance_uses_success_criteria(self) -> None:
        with tempfile.TemporaryDirectory() as temp_root:
            reader = SuggestionReader(Path(temp_root))
            task = GeneratedTask(
                task_id="test-1",
                source="user",
                prompt="Implement feature X",
                priority=0.5,
                success_criteria="All tests pass with good coverage",
            )
            introspection = IntrospectionResult(
                mode="coverage",
                timestamp="2026-04-15T00:00:00Z",
                findings=[],
                summary="Test coverage gap",
                metrics={},
                recommendations=["Increase test coverage"],
            )
            score = reader.score_resonance(task, [introspection])
            self.assertGreater(score, 0.0)

    def test_read_pending_with_resonance_boosts_priority(self) -> None:
        with tempfile.TemporaryDirectory() as temp_root:
            temp_path = Path(temp_root)
            suggestions_dir = temp_path / "suggestions"
            suggestions_dir.mkdir()

            # Create a suggestion about error handling
            suggestion_file = suggestions_dir / "error-task.md"
            suggestion_file.write_text("""# Error Handling

## Priority
LOW

## What
Add retry logic with exception handling
""", encoding="utf-8")

            reader = SuggestionReader(suggestions_dir)

            # Without resonance: priority should be LOW = 0.2
            tasks = reader.read_pending()
            self.assertEqual(len(tasks), 1)
            self.assertEqual(tasks[0].priority, 0.2)

            # With resonance from aligned introspection: priority should be boosted
            introspection = IntrospectionResult(
                mode="metrics",
                timestamp="2026-04-15T00:00:00Z",
                findings=[{"area": "error handling"}],
                summary="Focus on errors",
                metrics={},
                recommendations=["Improve error handling"],
            )
            tasks = reader.read_pending_with_resonance([introspection], resonance_boost=0.3)
            self.assertEqual(len(tasks), 1)
            self.assertGreater(tasks[0].priority, 0.2)

    def test_read_pending_with_resonance_clamps_to_one(self) -> None:
        with tempfile.TemporaryDirectory() as temp_root:
            temp_path = Path(temp_root)
            suggestions_dir = temp_path / "suggestions"
            suggestions_dir.mkdir()

            # Create a HIGH priority suggestion
            suggestion_file = suggestions_dir / "high-task.md"
            suggestion_file.write_text("""# Test Coverage

## Priority
HIGH

## What
Add test coverage
""", encoding="utf-8")

            reader = SuggestionReader(suggestions_dir)

            # With high resonance boost, priority should be clamped to 1.0
            introspection = IntrospectionResult(
                mode="coverage",
                timestamp="2026-04-15T00:00:00Z",
                findings=[{"description": "test gap"}],
                summary="Need tests",
                metrics={},
                recommendations=["Add more tests"],
            )
            tasks = reader.read_pending_with_resonance([introspection], resonance_boost=0.5)
            self.assertEqual(len(tasks), 1)
            self.assertEqual(tasks[0].priority, 1.0)  # Clamped


if __name__ == "__main__":
    unittest.main()
