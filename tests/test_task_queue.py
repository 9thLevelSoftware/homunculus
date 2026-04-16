"""Tests for task queue infrastructure."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path
import unittest

from homunculus.config import load_config
from homunculus.models import GeneratedTask, TaskQueueEntry, utc_now
from homunculus.storage import ArtifactStore


class TaskQueueEntryTests(unittest.TestCase):
    """Tests for TaskQueueEntry dataclass serialization."""

    def test_round_trip_minimal(self) -> None:
        """Test round-trip with minimal fields."""
        task = GeneratedTask(
            task_id="test-123",
            source="introspection",
            prompt="Fix the bug",
        )
        entry = TaskQueueEntry(
            task_id=task.task_id,
            task=task,
            queued_at=utc_now(),
            status="pending",
        )

        data = entry.to_dict()
        restored = TaskQueueEntry.from_dict(data)

        self.assertEqual(restored.task_id, entry.task_id)
        self.assertEqual(restored.task.prompt, task.prompt)
        self.assertEqual(restored.status, "pending")
        self.assertEqual(restored.attempts, 0)
        self.assertIsNone(restored.last_error)
        self.assertIsNone(restored.completed_at)
        self.assertIsNone(restored.outcome)

    def test_round_trip_with_all_fields(self) -> None:
        """Test round-trip with all optional fields populated."""
        task = GeneratedTask(
            task_id="test-456",
            source="user",
            prompt="Refactor module",
            priority=0.9,
            introspection_mode="metrics",
            context={"finding_id": "f1"},
            estimated_complexity="large",
            target_files=["src/main.py", "src/utils.py"],
            success_criteria="Tests pass and coverage >= 80%",
        )
        entry = TaskQueueEntry(
            task_id=task.task_id,
            task=task,
            queued_at="2026-04-15T12:00:00+00:00",
            status="completed",
            attempts=3,
            last_error="Retry after timeout",
            completed_at="2026-04-15T12:30:00+00:00",
            outcome="accepted",
        )

        data = entry.to_dict()
        restored = TaskQueueEntry.from_dict(data)

        self.assertEqual(restored.task_id, "test-456")
        self.assertEqual(restored.task.priority, 0.9)
        self.assertEqual(restored.task.target_files, ["src/main.py", "src/utils.py"])
        self.assertEqual(restored.attempts, 3)
        self.assertEqual(restored.last_error, "Retry after timeout")
        self.assertEqual(restored.completed_at, "2026-04-15T12:30:00+00:00")
        self.assertEqual(restored.outcome, "accepted")

    def test_nested_task_serialization(self) -> None:
        """Verify nested GeneratedTask is properly serialized and deserialized."""
        task = GeneratedTask(
            task_id="nested-test",
            source="introspection",
            prompt="Test nested serialization",
            context={"nested": {"deep": "value"}},
        )
        entry = TaskQueueEntry(
            task_id=task.task_id,
            task=task,
            queued_at=utc_now(),
            status="pending",
        )

        data = entry.to_dict()

        # Verify nested structure in dict
        self.assertIn("task", data)
        self.assertEqual(data["task"]["task_id"], "nested-test")
        self.assertEqual(data["task"]["context"]["nested"]["deep"], "value")

        # Verify restoration
        restored = TaskQueueEntry.from_dict(data)
        self.assertEqual(restored.task.context["nested"]["deep"], "value")


class TaskQueueStorageTests(unittest.TestCase):
    """Tests for task queue persistence in ArtifactStore."""

    def _config_path(self, temp_dir: Path) -> Path:
        source = Path("C:/Users/dasbl/Documents/homunculus/homunculus.example.toml")
        target = temp_dir / "config.toml"
        target.write_text(
            source.read_text(encoding="utf-8").replace(
                'path = "."', f'path = "{temp_dir.as_posix()}"', 1
            ),
            encoding="utf-8",
        )
        return target

    def _make_task(self, task_id: str) -> GeneratedTask:
        return GeneratedTask(
            task_id=task_id,
            source="introspection",
            prompt=f"Task {task_id}",
        )

    def test_append_and_load_queue(self) -> None:
        """Test appending entries and loading pending ones."""
        with tempfile.TemporaryDirectory() as temp_root:
            config = load_config(self._config_path(Path(temp_root)))
            store = ArtifactStore(config)
            store.ensure_layout()

            # Add entries with different statuses
            entry1 = TaskQueueEntry(
                task_id="task-001",
                task=self._make_task("task-001"),
                queued_at=utc_now(),
                status="pending",
            )
            entry2 = TaskQueueEntry(
                task_id="task-002",
                task=self._make_task("task-002"),
                queued_at=utc_now(),
                status="pending",
            )
            entry3 = TaskQueueEntry(
                task_id="task-003",
                task=self._make_task("task-003"),
                queued_at=utc_now(),
                status="in_progress",
            )

            store.append_to_queue(entry1)
            store.append_to_queue(entry2)
            store.append_to_queue(entry3)

            # load_queue returns only pending
            pending = store.load_queue()
            self.assertEqual(len(pending), 2)
            self.assertTrue(all(e.status == "pending" for e in pending))

            # load_all_queue_entries returns all
            all_entries = store.load_all_queue_entries()
            self.assertEqual(len(all_entries), 3)

    def test_update_queue_entry_status(self) -> None:
        """Test updating an entry's status."""
        with tempfile.TemporaryDirectory() as temp_root:
            config = load_config(self._config_path(Path(temp_root)))
            store = ArtifactStore(config)
            store.ensure_layout()

            entry = TaskQueueEntry(
                task_id="task-update",
                task=self._make_task("task-update"),
                queued_at=utc_now(),
                status="pending",
            )
            store.append_to_queue(entry)

            # Update to completed
            store.update_queue_entry("task-update", "completed", outcome="accepted")

            entries = store.load_all_queue_entries()
            self.assertEqual(len(entries), 1)
            self.assertEqual(entries[0].status, "completed")
            self.assertEqual(entries[0].outcome, "accepted")
            self.assertIsNotNone(entries[0].completed_at)

    def test_update_queue_entry_with_error(self) -> None:
        """Test updating with error and attempt increment."""
        with tempfile.TemporaryDirectory() as temp_root:
            config = load_config(self._config_path(Path(temp_root)))
            store = ArtifactStore(config)
            store.ensure_layout()

            entry = TaskQueueEntry(
                task_id="task-error",
                task=self._make_task("task-error"),
                queued_at=utc_now(),
                status="pending",
            )
            store.append_to_queue(entry)

            # Update with error
            store.update_queue_entry(
                "task-error",
                "failed",
                outcome="error",
                last_error="Connection timeout",
                increment_attempts=True,
            )

            entries = store.load_all_queue_entries()
            self.assertEqual(entries[0].status, "failed")
            self.assertEqual(entries[0].last_error, "Connection timeout")
            self.assertEqual(entries[0].attempts, 1)
            self.assertIsNotNone(entries[0].completed_at)

    def test_archive_completed_tasks(self) -> None:
        """Test archiving completed/failed entries to history."""
        with tempfile.TemporaryDirectory() as temp_root:
            config = load_config(self._config_path(Path(temp_root)))
            store = ArtifactStore(config)
            store.ensure_layout()

            # Create mixed entries
            entries = [
                TaskQueueEntry(
                    task_id="keep-1",
                    task=self._make_task("keep-1"),
                    queued_at=utc_now(),
                    status="pending",
                ),
                TaskQueueEntry(
                    task_id="archive-1",
                    task=self._make_task("archive-1"),
                    queued_at=utc_now(),
                    status="completed",
                    outcome="accepted",
                ),
                TaskQueueEntry(
                    task_id="keep-2",
                    task=self._make_task("keep-2"),
                    queued_at=utc_now(),
                    status="in_progress",
                ),
                TaskQueueEntry(
                    task_id="archive-2",
                    task=self._make_task("archive-2"),
                    queued_at=utc_now(),
                    status="failed",
                    outcome="error",
                ),
            ]
            for entry in entries:
                store.append_to_queue(entry)

            # Archive
            archived_count = store.archive_completed_tasks()
            self.assertEqual(archived_count, 2)

            # Verify queue only has non-archived entries
            remaining = store.load_all_queue_entries()
            self.assertEqual(len(remaining), 2)
            remaining_ids = {e.task_id for e in remaining}
            self.assertEqual(remaining_ids, {"keep-1", "keep-2"})

            # Verify history has archived entries
            history = store.load_jsonl(store._history_path())
            self.assertEqual(len(history), 2)
            archived_ids = {e["task_id"] for e in history}
            self.assertEqual(archived_ids, {"archive-1", "archive-2"})

    def test_archive_no_completed_tasks(self) -> None:
        """Test archive returns 0 when nothing to archive."""
        with tempfile.TemporaryDirectory() as temp_root:
            config = load_config(self._config_path(Path(temp_root)))
            store = ArtifactStore(config)
            store.ensure_layout()

            entry = TaskQueueEntry(
                task_id="pending-only",
                task=self._make_task("pending-only"),
                queued_at=utc_now(),
                status="pending",
            )
            store.append_to_queue(entry)

            archived_count = store.archive_completed_tasks()
            self.assertEqual(archived_count, 0)

            # Queue unchanged
            remaining = store.load_all_queue_entries()
            self.assertEqual(len(remaining), 1)

    def test_load_queue_empty_file(self) -> None:
        """Test loading from non-existent queue file."""
        with tempfile.TemporaryDirectory() as temp_root:
            config = load_config(self._config_path(Path(temp_root)))
            store = ArtifactStore(config)
            store.ensure_layout()

            # File doesn't exist yet
            pending = store.load_queue()
            self.assertEqual(pending, [])

    def test_atomic_update_preserves_other_entries(self) -> None:
        """Verify update doesn't corrupt other queue entries."""
        with tempfile.TemporaryDirectory() as temp_root:
            config = load_config(self._config_path(Path(temp_root)))
            store = ArtifactStore(config)
            store.ensure_layout()

            # Add multiple entries
            for i in range(5):
                entry = TaskQueueEntry(
                    task_id=f"task-{i:03d}",
                    task=self._make_task(f"task-{i:03d}"),
                    queued_at=utc_now(),
                    status="pending",
                )
                store.append_to_queue(entry)

            # Update middle entry
            store.update_queue_entry("task-002", "completed", outcome="accepted")

            # Verify all entries present and correct
            entries = store.load_all_queue_entries()
            self.assertEqual(len(entries), 5)

            for entry in entries:
                if entry.task_id == "task-002":
                    self.assertEqual(entry.status, "completed")
                else:
                    self.assertEqual(entry.status, "pending")


if __name__ == "__main__":
    unittest.main()
