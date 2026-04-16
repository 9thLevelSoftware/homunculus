from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path
import unittest

from homunculus.config import VerificationCommand, WorkspaceSettings
from homunculus.task_runner.runner import TaskRunner, WorkspacePreflightError


@unittest.skipUnless(shutil.which("git"), "git is required")
class TaskRunnerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.temp_path = Path(self.temp_dir.name)
        self.runner = TaskRunner(self.temp_path / "runtime")

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _make_repo(self) -> tuple[Path, str]:
        repo = self.temp_path / "repo"
        repo.mkdir()
        subprocess.run(["git", "init"], cwd=repo, capture_output=True, text=True, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.name", "Test User"], cwd=repo, check=True)
        target = repo / "example.txt"
        target.write_text("old\n", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=repo, capture_output=True, text=True, check=True)
        target.write_text("new\n", encoding="utf-8")
        diff = subprocess.run(["git", "diff"], cwd=repo, capture_output=True, text=True, check=True).stdout
        subprocess.run(["git", "checkout", "--", "example.txt"], cwd=repo, check=True)
        return repo, diff

    def test_execute_patch_uses_isolated_worktree(self) -> None:
        repo, diff = self._make_repo()
        workspace = WorkspaceSettings(
            path=repo,
            verification_commands=[
                VerificationCommand(
                    name="pass",
                    kind="test",
                    command='python -c "import pathlib,sys; sys.exit(0 if pathlib.Path(\'example.txt\').read_text()==\'new\\n\' else 1)"',
                )
            ],
        )
        result = self.runner.execute_patch(workspace, "episode-1", diff)
        self.assertFalse(result.reverted)
        self.assertEqual((repo / "example.txt").read_text(encoding="utf-8"), "old\n")
        self.assertFalse((self.temp_path / "runtime" / "worktrees" / "episode-1").exists())
        self.assertIn("example.txt", result.canonical_patch or "")

    def test_dirty_workspace_is_blocked(self) -> None:
        repo, _ = self._make_repo()
        (repo / "notes.txt").write_text("dirty\n", encoding="utf-8")
        workspace = WorkspaceSettings(path=repo, verification_commands=[])
        with self.assertRaises(WorkspacePreflightError):
            self.runner.require_clean_workspace(workspace)

    def test_apply_episode_patch_reverts_failed_verification(self) -> None:
        repo, diff = self._make_repo()
        workspace = WorkspaceSettings(
            path=repo,
            verification_commands=[VerificationCommand(name="fail", kind="test", command='python -c "import sys; sys.exit(1)"')],
        )
        with self.assertRaises(RuntimeError):
            self.runner.apply_episode_patch(workspace, diff)
        self.assertEqual((repo / "example.txt").read_text(encoding="utf-8"), "old\n")


if __name__ == "__main__":
    unittest.main()
