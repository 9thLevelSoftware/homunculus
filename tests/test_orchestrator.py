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
            # This test asserts source-repo isolation; opt out of auto-commit
            # so the source remains untouched after acceptance.
            config.daemon.auto_commit_on_accept = False
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
            self.assertIsNone(episode.commit_sha)

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


@unittest.skipUnless(shutil.which("git"), "git is required")
class AutoCommitWiringTests(unittest.TestCase):
    """Verify accepted episodes auto-commit when daemon.auto_commit_on_accept=True."""

    def _config_path(self, temp_dir: Path) -> Path:
        source = Path("C:/Users/dasbl/Documents/homunculus/homunculus.example.toml")
        target = temp_dir / "config.toml"
        target.write_text(
            source.read_text(encoding="utf-8").replace('path = "."', f'path = "{temp_dir.as_posix()}"', 1),
            encoding="utf-8",
        )
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

    def _build_orchestrator(self, repo: Path, diff: str, temp_path: Path) -> tuple[EpisodeOrchestrator, "ArtifactStore", "object"]:
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
        return orchestrator, store, config

    def test_accepted_episode_invokes_commit_to_source(self) -> None:
        """When auto_commit_on_accept=True, an accepted episode invokes
        commit_to_source exactly once with the correct workspace + ids and
        records the resulting commit SHA on the episode."""
        with tempfile.TemporaryDirectory() as temp_root:
            temp_path = Path(temp_root)
            repo, diff = self._make_repo(temp_path)
            orchestrator, store, config = self._build_orchestrator(repo, diff, temp_path)
            self.assertTrue(config.daemon.auto_commit_on_accept, "default should be True")

            calls: list[dict] = []
            real_commit = orchestrator.task_runner.commit_to_source

            def spy(workspace, task_id, episode_id, message):
                result = real_commit(
                    workspace=workspace,
                    task_id=task_id,
                    episode_id=episode_id,
                    message=message,
                )
                calls.append({
                    "workspace_path": workspace.path,
                    "task_id": task_id,
                    "episode_id": episode_id,
                    "message": message,
                    "result": result,
                })
                return result

            orchestrator.task_runner.commit_to_source = spy  # type: ignore[assignment]

            episode = orchestrator.run_episode(
                TaskRequest(task_id="auto-commit-task", workspace="self", prompt="Update example file")
            )

            self.assertEqual(episode.outcome, "accepted")
            self.assertEqual(len(calls), 1, f"expected exactly one commit_to_source call, got {len(calls)}")
            call = calls[0]
            self.assertEqual(call["workspace_path"], repo)
            self.assertEqual(call["task_id"], "auto-commit-task")
            self.assertEqual(call["episode_id"], episode.episode_id)
            self.assertTrue(call["message"], "commit message must be non-empty")
            self.assertTrue(call["result"].committed)
            self.assertIsNotNone(call["result"].commit_sha)

            # Episode record carries the SHA.
            self.assertEqual(episode.commit_sha, call["result"].commit_sha)

            # Source repo now contains the patched content + a new commit.
            self.assertEqual((repo / "example.txt").read_text(encoding="utf-8"), "new\n")
            log = subprocess.run(
                ["git", "log", "--oneline"],
                cwd=repo, capture_output=True, text=True, check=True,
            ).stdout.strip().splitlines()
            self.assertEqual(len(log), 2, f"expected 2 commits (init + auto-commit), got {len(log)}: {log}")

            # An "auto_commit" event was appended.
            events = store.load_jsonl(store.traces_dir / "events.jsonl")
            auto_events = [e for e in events if e.get("type") == "auto_commit"]
            self.assertEqual(len(auto_events), 1)
            self.assertEqual(auto_events[0]["commit_sha"], call["result"].commit_sha)

    def test_accepted_episode_skips_commit_when_disabled(self) -> None:
        """When auto_commit_on_accept=False, commit_to_source is not invoked
        and the source repo is left untouched (worktree-only execution)."""
        with tempfile.TemporaryDirectory() as temp_root:
            temp_path = Path(temp_root)
            repo, diff = self._make_repo(temp_path)
            orchestrator, store, config = self._build_orchestrator(repo, diff, temp_path)
            config.daemon.auto_commit_on_accept = False

            calls: list[dict] = []
            real_commit = orchestrator.task_runner.commit_to_source

            def spy(workspace, task_id, episode_id, message):
                calls.append({"task_id": task_id, "episode_id": episode_id})
                return real_commit(
                    workspace=workspace,
                    task_id=task_id,
                    episode_id=episode_id,
                    message=message,
                )

            orchestrator.task_runner.commit_to_source = spy  # type: ignore[assignment]

            episode = orchestrator.run_episode(
                TaskRequest(task_id="no-commit-task", workspace="self", prompt="Update example file")
            )

            self.assertEqual(episode.outcome, "accepted")
            self.assertEqual(calls, [], "commit_to_source must not be called when auto_commit_on_accept=False")
            self.assertIsNone(episode.commit_sha)
            # Source repo unchanged: file reverted, no new commit on the branch.
            self.assertEqual((repo / "example.txt").read_text(encoding="utf-8"), "old\n")
            log = subprocess.run(
                ["git", "log", "--oneline"],
                cwd=repo, capture_output=True, text=True, check=True,
            ).stdout.strip().splitlines()
            self.assertEqual(len(log), 1, "no auto-commit should be present")


if __name__ == "__main__":
    unittest.main()
