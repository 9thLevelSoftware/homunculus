from __future__ import annotations

from dataclasses import replace
import subprocess
from typing import Protocol

from ..config import HomunculusConfig, WorkspaceSettings, load_config
from ..dataset_builder.builder import DatasetBuilder
from ..memory_client.in_memory import InMemoryMemoryClient
from ..models import TaskRequest
from ..orchestrator.loop import EpisodeOrchestrator
from ..orchestrator.student import LocalStudentRunner
from ..orchestrator.teacher import OpenAICompatibleTeacher
from ..policy import GuardrailEngine
from ..storage import ArtifactStore
from ..task_runner.runner import TaskRunner
from .models import AgentResult, IssueRecord, SymphonyConfig, WorkspaceRecord
from .workflow import render_prompt


class AgentRunner(Protocol):
    def run_issue(
        self,
        issue: IssueRecord,
        workspace: WorkspaceRecord,
        *,
        prompt: str,
        attempt: int | None,
    ) -> AgentResult:
        ...


class HomunculusEpisodeRunner:
    """Fallback runner that routes a Linear issue through the existing episode loop."""

    def __init__(self, config: SymphonyConfig) -> None:
        self.config = config

    def run_issue(
        self,
        issue: IssueRecord,
        workspace: WorkspaceRecord,
        *,
        prompt: str,
        attempt: int | None,
    ) -> AgentResult:
        hom_config = self._load_workspace_config(workspace)
        store = ArtifactStore(hom_config)
        builder = DatasetBuilder(hom_config, store)
        orchestrator = EpisodeOrchestrator(
            hom_config,
            store,
            InMemoryMemoryClient(),
            OpenAICompatibleTeacher(hom_config.teacher),
            LocalStudentRunner(hom_config.student),
            TaskRunner(hom_config.paths.runtime_dir),
            builder,
            GuardrailEngine(hom_config.guardrails),
        )
        task = TaskRequest(
            task_id=issue.identifier,
            workspace="symphony",
            prompt=prompt,
            metadata={
                "source": "linear",
                "linear_issue_id": issue.id,
                "linear_identifier": issue.identifier,
                "attempt": attempt,
            },
        )
        episode = orchestrator.run_episode(task)
        status = "succeeded" if episode.outcome == "accepted" else "failed"
        commit_sha = episode.commit_sha
        if episode.outcome == "accepted":
            commit_sha = self._commit_workspace_artifacts(workspace, issue) or commit_sha
        else:
            self._reset_failed_attempt_artifacts(workspace)
        return AgentResult(
            status=status,
            message=episode.error_message or episode.outcome,
            episode_id=episode.episode_id,
            commit_sha=commit_sha,
            verification_results=[*episode.test_results, *episode.lint_results],
            metadata={"outcome": episode.outcome},
        )

    def _load_workspace_config(self, workspace: WorkspaceRecord) -> HomunculusConfig:
        hom_config = load_config(self.config.homunculus.config_path)
        verification_workspace = self.config.homunculus.verification_workspace
        source_workspace = hom_config.workspaces.get(verification_workspace)
        verification_commands = list(source_workspace.verification_commands) if source_workspace else []
        root = workspace.path
        hom_config.paths = replace(
            hom_config.paths,
            root=root,
            traces_dir=root / "traces",
            datasets_dir=root / "datasets",
            models_dir=root / "models",
            runtime_dir=root / "runtime",
            seed_sft_path=root / "datasets" / "seed" / "sft_seed.jsonl",
            seed_dpo_path=root / "datasets" / "seed" / "dpo_seed.jsonl",
        )
        hom_config.workspaces["symphony"] = WorkspaceSettings(
            path=root,
            verification_commands=verification_commands,
        )
        hom_config.daemon.auto_commit_on_accept = True
        return hom_config

    def _commit_workspace_artifacts(
        self, workspace: WorkspaceRecord, issue: IssueRecord
    ) -> str | None:
        status = _git(workspace.path, ["status", "--porcelain"], check=True).stdout.strip()
        if not status:
            return _git(workspace.path, ["rev-parse", "HEAD"], check=True).stdout.strip()
        _git(workspace.path, ["add", "-A"], check=True)
        _git(
            workspace.path,
            [
                "commit",
                "-m",
                f"chore: record Symphony artifacts for {issue.identifier}",
            ],
            check=True,
        )
        return _git(workspace.path, ["rev-parse", "HEAD"], check=True).stdout.strip()

    def _reset_failed_attempt_artifacts(self, workspace: WorkspaceRecord) -> None:
        _git(workspace.path, ["reset", "--hard", "HEAD"], check=False)
        _git(workspace.path, ["clean", "-fd"], check=False)


class CodexAppServerSmokeRunner:
    """Minimal smoke runner for validating a configured Codex command.

    Full app-server event streaming is intentionally isolated behind the runner
    interface. Until the VM smoke is proven, unattended production dispatch should
    use ``HomunculusEpisodeRunner``.
    """

    def __init__(self, config: SymphonyConfig) -> None:
        self.config = config

    def run_issue(
        self,
        issue: IssueRecord,
        workspace: WorkspaceRecord,
        *,
        prompt: str,
        attempt: int | None,
    ) -> AgentResult:
        del issue, prompt, attempt
        command = f"{self.config.codex.command} generate-json-schema --out .codex-app-server-schema-smoke"
        completed = subprocess.run(
            command,
            cwd=workspace.path,
            shell=True,
            capture_output=True,
            text=True,
            check=False,
            timeout=min(max(self.config.codex.read_timeout_ms / 1000, 5), 60),
        )
        if completed.returncode != 0:
            return AgentResult(
                status="failed",
                message="codex app-server smoke failed",
                metadata={
                    "stdout": completed.stdout,
                    "stderr": completed.stderr,
                    "returncode": completed.returncode,
                },
            )
        return AgentResult(
            status="succeeded",
            message="codex app-server smoke passed",
            metadata={"stdout": completed.stdout},
        )


def build_agent_runner(config: SymphonyConfig) -> AgentRunner:
    runner = config.homunculus.runner.lower()
    if runner in {"codex-app-server", "app-server"}:
        return CodexAppServerSmokeRunner(config)
    return HomunculusEpisodeRunner(config)


def render_issue_prompt(config: SymphonyConfig, issue: IssueRecord, attempt: int | None) -> str:
    prompt = render_prompt(config.prompt_template, issue=issue, attempt=attempt)
    if prompt:
        return prompt
    return f"You are working on Linear issue {issue.identifier}: {issue.title}"


def _git(
    cwd,
    args: list[str],
    *,
    check: bool,
) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    if check and completed.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {completed.stderr}")
    return completed
