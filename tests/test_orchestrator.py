from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path
import unittest

from homunculus.config import VerificationCommand, load_config
from homunculus.dataset_builder.builder import DatasetBuilder
from homunculus.memory_client.in_memory import InMemoryMemoryClient
from homunculus.models import TaskRequest, TeacherResponse
from homunculus.orchestrator.loop import EpisodeOrchestrator
from homunculus.orchestrator.student import StaticStudent
from homunculus.orchestrator.teacher import StaticTeacher
from homunculus.policy import GuardrailEngine
from homunculus.storage import ArtifactStore
from homunculus.task_runner.runner import TaskRunner


class FailingMemoryClient(InMemoryMemoryClient):
    def get_active_context(self, task_scope: str, limit: int = 8):
        raise RuntimeError("memory offline")


@unittest.skipUnless(shutil.which("git"), "git is required")
class OrchestratorTests(unittest.TestCase):
    def _config_path(self, temp_dir: Path) -> Path:
        source = Path("C:/Users/dasbl/Documents/homunculus/homunculus.example.toml")
        target = temp_dir / "config.toml"
        target.write_text(source.read_text(encoding="utf-8").replace('path = "."', f'path = "{temp_dir.as_posix()}"', 1), encoding="utf-8")
        return target

    def _make_repo(self, temp_path: Path) -> tuple[Path, str]:
        repo = temp_path / "repo"
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

    def test_run_episode_persists_patch_and_keeps_source_clean(self) -> None:
        with tempfile.TemporaryDirectory() as temp_root:
            temp_path = Path(temp_root)
            repo, diff = self._make_repo(temp_path)
            config = load_config(self._config_path(temp_path))
            config.workspaces["self"].path = repo
            config.workspaces["self"].verification_commands = [
                VerificationCommand(
                    name="pass",
                    kind="test",
                    command='python -c "import pathlib,sys; sys.exit(0 if pathlib.Path(\'example.txt\').read_text()==\'new\\n\' else 1)"',
                )
            ]
            store = ArtifactStore(config)
            memory = InMemoryMemoryClient()
            memory.store_memory("decision", "Prefer safe fixes", {"task": "episode"})
            builder = DatasetBuilder(config, store)
            orchestrator = EpisodeOrchestrator(
                config=config,
                store=store,
                memory_client=memory,
                teacher=StaticTeacher(TeacherResponse(plan=["patch"], candidate_patch=diff, rationale="apply diff")),
                student=StaticStudent("student hint"),
                task_runner=TaskRunner(config.paths.runtime_dir),
                dataset_builder=builder,
                guardrails=GuardrailEngine(config.guardrails),
            )
            episode = orchestrator.run_episode(TaskRequest(task_id="episode-1", workspace="self", prompt="Update example file"))
            self.assertEqual(episode.outcome, "accepted")
            self.assertTrue(episode.verification_passed)
            self.assertEqual(len(store.load_episodes()), 1)
            self.assertTrue(Path(episode.patch_path or "").exists())
            self.assertEqual((repo / "example.txt").read_text(encoding="utf-8"), "old\n")

    def test_dirty_workspace_becomes_blocked_episode(self) -> None:
        with tempfile.TemporaryDirectory() as temp_root:
            temp_path = Path(temp_root)
            repo, diff = self._make_repo(temp_path)
            (repo / "notes.txt").write_text("dirty\n", encoding="utf-8")
            config = load_config(self._config_path(temp_path))
            config.workspaces["self"].path = repo
            store = ArtifactStore(config)
            memory = InMemoryMemoryClient()
            builder = DatasetBuilder(config, store)
            orchestrator = EpisodeOrchestrator(
                config=config,
                store=store,
                memory_client=memory,
                teacher=StaticTeacher(TeacherResponse(plan=["patch"], candidate_patch=diff, rationale="apply diff")),
                student=StaticStudent("student hint"),
                task_runner=TaskRunner(config.paths.runtime_dir),
                dataset_builder=builder,
                guardrails=GuardrailEngine(config.guardrails),
            )
            episode = orchestrator.run_episode(TaskRequest(task_id="episode-2", workspace="self", prompt="Update example file"))
            self.assertEqual(episode.outcome, "blocked")
            self.assertEqual(episode.failure_stage, "preflight")

    def test_runtime_failure_persists_error_episode(self) -> None:
        with tempfile.TemporaryDirectory() as temp_root:
            temp_path = Path(temp_root)
            repo, _ = self._make_repo(temp_path)
            config = load_config(self._config_path(temp_path))
            config.workspaces["self"].path = repo
            store = ArtifactStore(config)
            memory = FailingMemoryClient()
            builder = DatasetBuilder(config, store)
            orchestrator = EpisodeOrchestrator(
                config=config,
                store=store,
                memory_client=memory,
                teacher=StaticTeacher(TeacherResponse(plan=["patch"], candidate_patch=None, rationale="noop")),
                student=StaticStudent("student hint"),
                task_runner=TaskRunner(config.paths.runtime_dir),
                dataset_builder=builder,
                guardrails=GuardrailEngine(config.guardrails),
            )
            episode = orchestrator.run_episode(TaskRequest(task_id="episode-3", workspace="self", prompt="Update example file"))
            self.assertEqual(episode.outcome, "error")
            self.assertEqual(episode.failure_stage, "recall")
            events = store.load_jsonl(store.traces_dir / "events.jsonl")
            self.assertTrue(any(item["type"] == "episode_failed" for item in events))


if __name__ == "__main__":
    unittest.main()
