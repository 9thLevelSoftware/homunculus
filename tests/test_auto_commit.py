from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path
import unittest

from homunculus.config import WorkspaceSettings
from homunculus.task_runner.runner import TaskRunner


@unittest.skipUnless(shutil.which("git"), "git is required")
class AutoCommitTests(unittest.TestCase):
    def _make_repo(self, temp_path: Path) -> Path:
        repo_path = temp_path / "repo"
        repo_path.mkdir()
        subprocess.run(["git", "init"], cwd=repo_path, capture_output=True, check=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo_path, capture_output=True, check=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=repo_path, capture_output=True, check=True)
        (repo_path / "file.txt").write_text("initial", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=repo_path, capture_output=True, check=True)
        subprocess.run(["git", "commit", "-m", "initial"], cwd=repo_path, capture_output=True, check=True)
        return repo_path

    def test_commit_to_source_creates_commit_with_message(self) -> None:
        with tempfile.TemporaryDirectory() as temp_root:
            temp_path = Path(temp_root)
            repo_path = self._make_repo(temp_path)
            runner = TaskRunner(temp_path / "runtime")

            # Make a change
            (repo_path / "file.txt").write_text("modified", encoding="utf-8")

            # Commit it
            workspace = WorkspaceSettings(path=repo_path)
            result = runner.commit_to_source(
                workspace,
                task_id="test-task",
                episode_id="ep-123",
                message="test commit message"
            )

            self.assertTrue(result.committed)
            self.assertIsNotNone(result.commit_sha)

            # Verify commit exists
            log = subprocess.run(
                ["git", "log", "--oneline", "-1"],
                cwd=repo_path,
                capture_output=True,
                text=True,
                check=True
            )
            self.assertIn("test commit message", log.stdout)

    def test_commit_to_source_with_no_changes_returns_false(self) -> None:
        with tempfile.TemporaryDirectory() as temp_root:
            temp_path = Path(temp_root)
            repo_path = self._make_repo(temp_path)
            runner = TaskRunner(temp_path / "runtime")

            workspace = WorkspaceSettings(path=repo_path)
            result = runner.commit_to_source(
                workspace,
                task_id="test-task",
                episode_id="ep-123",
                message="nothing to commit"
            )

            self.assertFalse(result.committed)
            self.assertIsNone(result.commit_sha)


if __name__ == "__main__":
    unittest.main()
