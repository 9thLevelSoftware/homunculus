from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Protocol

from ..config import WorkspaceSettings, load_config
from ..models import TaskRequest
from ..runtime import build_runtime
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
        hom_config, _store, _builder, _trainer, orchestrator, _task_runner, _memory = build_runtime(
            str(self.config.homunculus.config_path)
        )
        verification_workspace = self.config.homunculus.verification_workspace
        source_workspace = hom_config.workspaces.get(verification_workspace)
        verification_commands = list(source_workspace.verification_commands) if source_workspace else []
        hom_config.workspaces["symphony"] = WorkspaceSettings(
            path=workspace.path,
            verification_commands=verification_commands,
        )
        hom_config.daemon.auto_commit_on_accept = True
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
        return AgentResult(
            status=status,
            message=episode.error_message or episode.outcome,
            episode_id=episode.episode_id,
            commit_sha=episode.commit_sha,
            verification_results=[*episode.test_results, *episode.lint_results],
            metadata={"outcome": episode.outcome},
        )


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
