from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path
from unittest.mock import patch
import unittest

from homunculus.config import load_config
from homunculus.daemon import Daemon
from homunculus.models import DaemonState, GeneratedTask, IntrospectionResult
from homunculus.storage import ArtifactStore
from homunculus.task_generator import TaskPrioritizer


@unittest.skipUnless(shutil.which("git"), "git is required")
class DaemonTests(unittest.TestCase):
    def _make_repo(self, temp_path: Path) -> Path:
        repo_path = temp_path / "repo"
        repo_path.mkdir()
        subprocess.run(["git", "init"], cwd=repo_path, capture_output=True, check=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo_path, capture_output=True, check=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=repo_path, capture_output=True, check=True)
        (repo_path / "file.py").write_text("# initial\n", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=repo_path, capture_output=True, check=True)
        subprocess.run(["git", "commit", "-m", "initial"], cwd=repo_path, capture_output=True, check=True)
        return repo_path

    def _config_path(self, temp_dir: Path, repo_path: Path) -> Path:
        source = Path("C:/Users/dasbl/Documents/homunculus/homunculus.example.toml")
        content = source.read_text(encoding="utf-8")
        content = content.replace('path = "."', f'path = "{repo_path.as_posix()}"', 1)
        target = temp_dir / "config.toml"
        target.write_text(content, encoding="utf-8")
        return target

    def test_daemon_run_once_with_no_tasks_completes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_root:
            temp_path = Path(temp_root)
            repo_path = self._make_repo(temp_path)
            config = load_config(self._config_path(temp_path, repo_path))

            daemon = Daemon(config)
            result = daemon.run_once()

            self.assertEqual(result.tasks_executed, 0)
            self.assertEqual(result.status, "idle")

    def test_daemon_picks_up_suggestion_task(self) -> None:
        with tempfile.TemporaryDirectory() as temp_root:
            temp_path = Path(temp_root)
            repo_path = self._make_repo(temp_path)
            config = load_config(self._config_path(temp_path, repo_path))

            # Create suggestions directory with a task
            suggestions_dir = temp_path / "suggestions"
            suggestions_dir.mkdir()
            (suggestions_dir / "test-task.md").write_text("""# Test Task

## Priority
HIGH

## What
Add a comment to file.py
""", encoding="utf-8")

            daemon = Daemon(config, suggestions_dir=suggestions_dir)
            tasks = daemon.get_pending_tasks()

            self.assertEqual(len(tasks), 1)
            self.assertEqual(tasks[0].source, "user")
            self.assertIn("Add a comment", tasks[0].prompt)

    def test_daemon_state_persistence_roundtrip(self) -> None:
        """Test that state can be saved and loaded correctly."""
        with tempfile.TemporaryDirectory() as temp_root:
            temp_path = Path(temp_root)
            repo_path = self._make_repo(temp_path)
            config = load_config(self._config_path(temp_path, repo_path))

            daemon = Daemon(config)

            # Create state with specific values
            state = DaemonState(
                started_at="2026-04-15T12:00:00+00:00",
                last_cycle_at="2026-04-15T14:00:00+00:00",
                cycles_completed=5,
                total_episodes=23,
                episodes_this_cycle=3,
            )

            # Save and load
            daemon.save_state(state)
            loaded = daemon.load_state()

            # Verify all fields
            self.assertEqual(loaded.started_at, state.started_at)
            self.assertEqual(loaded.last_cycle_at, state.last_cycle_at)
            self.assertEqual(loaded.cycles_completed, state.cycles_completed)
            self.assertEqual(loaded.total_episodes, state.total_episodes)
            self.assertEqual(loaded.episodes_this_cycle, state.episodes_this_cycle)

    def test_daemon_state_fresh_start(self) -> None:
        """Test that missing state file results in fresh state."""
        with tempfile.TemporaryDirectory() as temp_root:
            temp_path = Path(temp_root)
            repo_path = self._make_repo(temp_path)
            config = load_config(self._config_path(temp_path, repo_path))

            daemon = Daemon(config)

            # Load without saving first
            state = daemon.load_state()

            # Should have default values
            self.assertEqual(state.cycles_completed, 0)
            self.assertEqual(state.total_episodes, 0)
            self.assertIsNone(state.last_cycle_at)

    def test_daemon_state_corrupted_file_recovery(self) -> None:
        """Test that corrupted state file results in fresh state."""
        with tempfile.TemporaryDirectory() as temp_root:
            temp_path = Path(temp_root)
            repo_path = self._make_repo(temp_path)
            config = load_config(self._config_path(temp_path, repo_path))

            daemon = Daemon(config)

            # Write corrupted JSON
            daemon.state_path.parent.mkdir(parents=True, exist_ok=True)
            daemon.state_path.write_text("{ invalid json", encoding="utf-8")

            # Should return fresh state, not crash
            state = daemon.load_state()
            self.assertEqual(state.cycles_completed, 0)

    def test_daemon_lock_acquisition(self) -> None:
        """Test that lock can be acquired and released."""
        with tempfile.TemporaryDirectory() as temp_root:
            temp_path = Path(temp_root)
            repo_path = self._make_repo(temp_path)
            config = load_config(self._config_path(temp_path, repo_path))

            daemon = Daemon(config)

            # Should acquire lock
            self.assertTrue(daemon.acquire_lock())
            self.assertTrue(daemon.lock_path.exists())

            # Release and verify
            daemon.release_lock()
            self.assertFalse(daemon.lock_path.exists())

    def test_daemon_shutdown_event_stops_loop(self) -> None:
        """Test that setting shutdown event stops continuous loop."""
        with tempfile.TemporaryDirectory() as temp_root:
            temp_path = Path(temp_root)
            repo_path = self._make_repo(temp_path)
            config = load_config(self._config_path(temp_path, repo_path))
            # Use tiny interval for test speed
            config.daemon.cycle_interval_minutes = 0.001  # ~60ms

            daemon = Daemon(config)

            # Set shutdown event before starting
            daemon.request_shutdown()

            # Run continuous should exit immediately
            completed_event = threading.Event()

            def run_daemon() -> None:
                daemon.run_continuous()
                completed_event.set()

            thread = threading.Thread(target=run_daemon)
            thread.start()

            # Wait for completion with timeout
            completed = completed_event.wait(timeout=5)
            self.assertTrue(completed, "Daemon should have stopped due to shutdown event")
            thread.join(timeout=1)

    def test_daemon_saves_state_on_shutdown(self) -> None:
        """Test that state is saved when daemon shuts down."""
        with tempfile.TemporaryDirectory() as temp_root:
            temp_path = Path(temp_root)
            repo_path = self._make_repo(temp_path)
            config = load_config(self._config_path(temp_path, repo_path))
            config.daemon.cycle_interval_minutes = 0.001  # ~60ms

            daemon = Daemon(config)

            # Schedule shutdown after a brief delay
            def shutdown_soon() -> None:
                import time
                time.sleep(0.1)  # Let one cycle run
                daemon.request_shutdown()

            shutdown_thread = threading.Thread(target=shutdown_soon)
            shutdown_thread.start()

            # Run continuous (will exit after shutdown)
            daemon.run_continuous()
            shutdown_thread.join()

            # State file should exist
            self.assertTrue(daemon.state_path.exists())

            # State should have at least one cycle recorded
            state = daemon.load_state()
            self.assertGreaterEqual(state.cycles_completed, 1)

    def test_daemon_continuous_updates_state(self) -> None:
        """Test that continuous mode updates state after each cycle."""
        with tempfile.TemporaryDirectory() as temp_root:
            temp_path = Path(temp_root)
            repo_path = self._make_repo(temp_path)
            config = load_config(self._config_path(temp_path, repo_path))
            config.daemon.cycle_interval_minutes = 0.001  # ~60ms

            daemon = Daemon(config)

            # Create a suggestion so we have non-idle cycles
            suggestions_dir = config.paths.root / "suggestions"
            suggestions_dir.mkdir(exist_ok=True)
            (suggestions_dir / "task.md").write_text("# Test\n\n## What\nTest task", encoding="utf-8")

            # Schedule shutdown after 2 cycles worth of time
            def shutdown_after_cycles() -> None:
                import time
                time.sleep(0.2)
                daemon.request_shutdown()

            shutdown_thread = threading.Thread(target=shutdown_after_cycles)
            shutdown_thread.start()

            daemon.run_continuous()
            shutdown_thread.join()

            # Load final state
            state = daemon.load_state()

            # Should have completed at least 1 cycle
            self.assertGreaterEqual(state.cycles_completed, 1)
            # Should have a last_cycle_at timestamp
            self.assertIsNotNone(state.last_cycle_at)

    def test_daemon_constructor_backward_compatible(self) -> None:
        """Test that Daemon constructor works without store parameter."""
        with tempfile.TemporaryDirectory() as temp_root:
            temp_path = Path(temp_root)
            repo_path = self._make_repo(temp_path)
            config = load_config(self._config_path(temp_path, repo_path))

            # Old-style construction (no store)
            daemon = Daemon(config)

            self.assertIsNone(daemon.store)
            self.assertIsNone(daemon.task_generator)
            self.assertIsInstance(daemon.prioritizer, TaskPrioritizer)

    def test_daemon_with_store_has_task_generator(self) -> None:
        """Test that Daemon with store gets a TaskGenerator."""
        with tempfile.TemporaryDirectory() as temp_root:
            temp_path = Path(temp_root)
            repo_path = self._make_repo(temp_path)
            config = load_config(self._config_path(temp_path, repo_path))
            store = ArtifactStore(config)

            daemon = Daemon(config, store=store)

            self.assertIsNotNone(daemon.store)
            self.assertIsNotNone(daemon.task_generator)
            self.assertIsInstance(daemon.prioritizer, TaskPrioritizer)

    def test_daemon_get_recent_introspection_returns_empty_without_store(self) -> None:
        """Test that _get_recent_introspection returns empty list without store."""
        with tempfile.TemporaryDirectory() as temp_root:
            temp_path = Path(temp_root)
            repo_path = self._make_repo(temp_path)
            config = load_config(self._config_path(temp_path, repo_path))

            daemon = Daemon(config)
            results = daemon._get_recent_introspection()

            self.assertEqual(results, [])

    def test_daemon_get_recent_introspection_loads_results(self) -> None:
        """Test that _get_recent_introspection loads from store."""
        with tempfile.TemporaryDirectory() as temp_root:
            temp_path = Path(temp_root)
            repo_path = self._make_repo(temp_path)
            config = load_config(self._config_path(temp_path, repo_path))
            store = ArtifactStore(config)
            store.ensure_layout()

            # Add some introspection results
            for i in range(3):
                result = IntrospectionResult(
                    mode="metrics",
                    timestamp=f"2026-04-15T0{i}:00:00+00:00",
                    findings=[],
                    summary=f"Result {i}",
                    metrics={},
                    recommendations=[],
                )
                store.append_introspection_result(result)

            daemon = Daemon(config, store=store)
            results = daemon._get_recent_introspection()

            self.assertEqual(len(results), 3)

    def test_daemon_get_recent_introspection_limits_to_five(self) -> None:
        """Test that _get_recent_introspection returns at most 5 results."""
        with tempfile.TemporaryDirectory() as temp_root:
            temp_path = Path(temp_root)
            repo_path = self._make_repo(temp_path)
            config = load_config(self._config_path(temp_path, repo_path))
            store = ArtifactStore(config)
            store.ensure_layout()

            # Add 10 introspection results
            for i in range(10):
                result = IntrospectionResult(
                    mode="metrics",
                    timestamp=f"2026-04-15T{i:02d}:00:00+00:00",
                    findings=[],
                    summary=f"Result {i}",
                    metrics={},
                    recommendations=[],
                )
                store.append_introspection_result(result)

            daemon = Daemon(config, store=store)
            results = daemon._get_recent_introspection()

            self.assertEqual(len(results), 5)

    def test_daemon_get_pending_tasks_combines_sources(self) -> None:
        """Test that get_pending_tasks combines introspection and suggestions."""
        with tempfile.TemporaryDirectory() as temp_root:
            temp_path = Path(temp_root)
            repo_path = self._make_repo(temp_path)
            config = load_config(self._config_path(temp_path, repo_path))
            store = ArtifactStore(config)
            store.ensure_layout()

            # Add introspection result with actionable finding
            result = IntrospectionResult(
                mode="metrics",
                timestamp="2026-04-15T00:00:00+00:00",
                findings=[
                    {"type": "high_error_rate", "value": 0.25, "severity": "critical"}
                ],
                summary="High error rate",
                metrics={"error_rate": 0.25},
                recommendations=["Fix errors"],
            )
            store.append_introspection_result(result)

            # Create suggestions directory with a task
            suggestions_dir = temp_path / "suggestions"
            suggestions_dir.mkdir()
            (suggestions_dir / "test-task.md").write_text("""# Test Task

## Priority
HIGH

## What
Add a feature
""", encoding="utf-8")

            daemon = Daemon(config, suggestions_dir=suggestions_dir, store=store)
            tasks = daemon.get_pending_tasks()

            # Should have both introspection-generated and suggestion tasks
            self.assertGreater(len(tasks), 0)
            sources = {t.source for t in tasks}
            # Should include both sources
            self.assertTrue("introspection" in sources or "user" in sources)

    def test_daemon_get_pending_tasks_returns_prioritized_list(self) -> None:
        """Test that get_pending_tasks returns tasks sorted by priority."""
        with tempfile.TemporaryDirectory() as temp_root:
            temp_path = Path(temp_root)
            repo_path = self._make_repo(temp_path)
            config = load_config(self._config_path(temp_path, repo_path))

            # Create suggestions with different priorities
            suggestions_dir = temp_path / "suggestions"
            suggestions_dir.mkdir()
            (suggestions_dir / "low-task.md").write_text("""# Low Task

## Priority
LOW

## What
Low priority task
""", encoding="utf-8")
            (suggestions_dir / "high-task.md").write_text("""# High Task

## Priority
HIGH

## What
High priority task
""", encoding="utf-8")

            daemon = Daemon(config, suggestions_dir=suggestions_dir)
            tasks = daemon.get_pending_tasks()

            self.assertEqual(len(tasks), 2)
            # Tasks should be sorted by priority (highest first)
            self.assertGreaterEqual(tasks[0].priority, tasks[1].priority)

    def test_daemon_run_once_returns_error_on_orchestrator_exception(self) -> None:
        """Test that DaemonCycleResult has status='error' when orchestrator raises."""
        with tempfile.TemporaryDirectory() as temp_root:
            temp_path = Path(temp_root)
            repo_path = self._make_repo(temp_path)
            config = load_config(self._config_path(temp_path, repo_path))

            # Create a suggestion so there's a task to execute
            suggestions_dir = temp_path / "suggestions"
            suggestions_dir.mkdir()
            (suggestions_dir / "test-task.md").write_text("""# Test Task

## Priority
HIGH

## What
Test task that will fail
""", encoding="utf-8")

            # Create a mock orchestrator that raises an exception
            class FailingOrchestrator:
                def run_episode(self, task_request: object) -> object:
                    raise RuntimeError("Simulated orchestrator failure")

            failing_orchestrator = FailingOrchestrator()
            daemon = Daemon(
                config,
                orchestrator=failing_orchestrator,  # type: ignore
                suggestions_dir=suggestions_dir,
            )

            result = daemon.run_once()

            self.assertEqual(result.status, "error")
            self.assertIsNotNone(result.error)
            self.assertIn("Simulated orchestrator failure", result.error)


@unittest.skipUnless(shutil.which("git"), "git is required")
class LockSafetyTests(unittest.TestCase):
    """Verify single-instance lock is robust against corrupt content and
    foreign-PID release."""

    def _make_repo(self, temp_path: Path) -> Path:
        repo_path = temp_path / "repo"
        repo_path.mkdir()
        subprocess.run(["git", "init"], cwd=repo_path, capture_output=True, check=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo_path, capture_output=True, check=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=repo_path, capture_output=True, check=True)
        (repo_path / "file.py").write_text("# initial\n", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=repo_path, capture_output=True, check=True)
        subprocess.run(["git", "commit", "-m", "initial"], cwd=repo_path, capture_output=True, check=True)
        return repo_path

    def _config_path(self, temp_dir: Path, repo_path: Path) -> Path:
        source = Path("C:/Users/dasbl/Documents/homunculus/homunculus.example.toml")
        content = source.read_text(encoding="utf-8")
        content = content.replace('path = "."', f'path = "{repo_path.as_posix()}"', 1)
        target = temp_dir / "config.toml"
        target.write_text(content, encoding="utf-8")
        return target

    def test_corrupt_lock_does_not_overwrite_silently(self) -> None:
        """A corrupt PID file (unparseable) should NOT be silently overwritten."""
        with tempfile.TemporaryDirectory() as temp_root:
            temp_path = Path(temp_root)
            repo_path = self._make_repo(temp_path)
            config = load_config(self._config_path(temp_path, repo_path))
            daemon = Daemon(config)

            daemon.lock_path.parent.mkdir(parents=True, exist_ok=True)
            daemon.lock_path.write_text("not-a-pid", encoding="utf-8")
            original = daemon.lock_path.read_text(encoding="utf-8")

            result = daemon.acquire_lock()

            self.assertFalse(result, "corrupt lock content must NOT be treated as stale")
            self.assertEqual(
                daemon.lock_path.read_text(encoding="utf-8"),
                original,
                "lock file must not be overwritten when content is corrupt",
            )

    def test_lock_vanishing_between_exists_and_read_proceeds(self) -> None:
        """If lock file is removed between exists() check and read, treat as no lock."""
        with tempfile.TemporaryDirectory() as temp_root:
            temp_path = Path(temp_root)
            repo_path = self._make_repo(temp_path)
            config = load_config(self._config_path(temp_path, repo_path))
            daemon = Daemon(config)

            daemon.lock_path.parent.mkdir(parents=True, exist_ok=True)
            # Create the lock then patch read_text to raise FileNotFoundError
            daemon.lock_path.write_text(str(os.getpid()), encoding="utf-8")

            def vanishing_read(self_path, *args, **kwargs):
                raise FileNotFoundError(f"vanished: {self_path}")

            with patch.object(type(daemon.lock_path), "read_text", vanishing_read):
                result = daemon.acquire_lock()

            self.assertTrue(
                result, "vanishing lock must be treated as 'no lock', not corrupt"
            )

    def test_release_lock_only_removes_own_pid(self) -> None:
        """release_lock must not delete a lock owned by another process."""
        with tempfile.TemporaryDirectory() as temp_root:
            temp_path = Path(temp_root)
            repo_path = self._make_repo(temp_path)
            config = load_config(self._config_path(temp_path, repo_path))
            daemon = Daemon(config)

            self.assertTrue(daemon.acquire_lock())

            # Simulate another process taking ownership of the lock file.
            daemon.lock_path.write_text("99999", encoding="utf-8")
            daemon.release_lock()

            self.assertTrue(
                daemon.lock_path.exists(),
                "lock owned by another PID must NOT be removed",
            )


if __name__ == "__main__":
    unittest.main()
