from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path
import unittest

from homunculus.config import load_config
from homunculus.daemon import Daemon
from homunculus.models import DaemonState, GeneratedTask
from homunculus.storage import ArtifactStore


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


if __name__ == "__main__":
    unittest.main()
