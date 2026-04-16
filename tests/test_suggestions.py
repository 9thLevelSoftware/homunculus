from __future__ import annotations

import tempfile
from pathlib import Path
import unittest

from homunculus.suggestions import SuggestionReader
from homunculus.models import GeneratedTask


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


if __name__ == "__main__":
    unittest.main()
