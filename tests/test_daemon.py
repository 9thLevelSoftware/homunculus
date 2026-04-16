from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
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


if __name__ == "__main__":
    unittest.main()
