from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from ..models import VerificationResult, utc_now


@dataclass(frozen=True)
class BlockerRef:
    id: str | None = None
    identifier: str | None = None
    state: str | None = None
    created_at: str | None = None
    updated_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "BlockerRef":
        return cls(**payload)


@dataclass(frozen=True)
class IssueRecord:
    id: str
    identifier: str
    title: str
    description: str | None = None
    priority: int | None = None
    state: str = "Todo"
    branch_name: str | None = None
    url: str | None = None
    labels: list[str] = field(default_factory=list)
    blocked_by: list[BlockerRef] = field(default_factory=list)
    created_at: str | None = None
    updated_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["blocked_by"] = [item.to_dict() for item in self.blocked_by]
        return payload

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "IssueRecord":
        data = dict(payload)
        data["blocked_by"] = [
            item if isinstance(item, BlockerRef) else BlockerRef.from_dict(item)
            for item in data.get("blocked_by", [])
        ]
        data["labels"] = list(data.get("labels", []))
        return cls(**data)


@dataclass(frozen=True)
class WorkflowDefinition:
    path: Path
    config: dict[str, Any]
    prompt_template: str


@dataclass(frozen=True)
class TrackerConfig:
    kind: str
    project_slug: str
    endpoint: str = "https://api.linear.app/graphql"
    api_key: str | None = None
    api_key_env: str | None = None
    active_states: tuple[str, ...] = ("Todo", "In Progress")
    terminal_states: tuple[str, ...] = ("Closed", "Cancelled", "Canceled", "Duplicate", "Done")
    label: str = "symphony"


@dataclass(frozen=True)
class PollingConfig:
    interval_ms: int = 30000


@dataclass(frozen=True)
class WorkspaceConfig:
    root: Path = Path("/home/homunculus/workspaces")


@dataclass(frozen=True)
class HooksConfig:
    after_create: str | None = None
    before_run: str | None = None
    after_run: str | None = None
    before_remove: str | None = None
    timeout_ms: int = 60000


@dataclass(frozen=True)
class AgentConfig:
    max_concurrent_agents: int = 10
    max_turns: int = 20
    max_retry_backoff_ms: int = 300000
    max_concurrent_agents_by_state: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class CodexConfig:
    command: str = "codex app-server"
    approval_policy: str | None = None
    thread_sandbox: str | None = None
    turn_sandbox_policy: dict[str, Any] | str | None = None
    turn_timeout_ms: int = 3600000
    read_timeout_ms: int = 5000
    stall_timeout_ms: int = 300000


@dataclass(frozen=True)
class HomunculusSymphonyConfig:
    config_path: Path = Path("homunculus.toml")
    source_workspace: Path = Path(".")
    base_branch: str = "master"
    branch_prefix: str = "codex/"
    auto_merge: bool = True
    artifact_curation: bool = True
    runner: str = "homunculus"
    fallback_runner: str = "homunculus"
    done_state: str = "Done"
    in_progress_state: str = "In Progress"
    failed_state: str | None = "Rework"
    merge_gates: tuple[str, ...] = (
        "python -m homunculus.cli harness-check --strict",
        "python -m unittest discover -q",
    )
    verification_workspace: str = "self"


@dataclass(frozen=True)
class SymphonyConfig:
    workflow_path: Path
    prompt_template: str
    raw_config: dict[str, Any]
    tracker: TrackerConfig
    polling: PollingConfig = field(default_factory=PollingConfig)
    workspace: WorkspaceConfig = field(default_factory=WorkspaceConfig)
    hooks: HooksConfig = field(default_factory=HooksConfig)
    agent: AgentConfig = field(default_factory=AgentConfig)
    codex: CodexConfig = field(default_factory=CodexConfig)
    homunculus: HomunculusSymphonyConfig = field(default_factory=HomunculusSymphonyConfig)

    @property
    def runtime_dir(self) -> Path:
        return self.homunculus.source_workspace / "runtime"

    @property
    def state_path(self) -> Path:
        return self.runtime_dir / "symphony_state.json"

    @property
    def runs_path(self) -> Path:
        return self.runtime_dir / "symphony_runs.jsonl"

    @property
    def logs_dir(self) -> Path:
        return self.runtime_dir / "symphony_logs"

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["workflow_path"] = str(self.workflow_path)
        payload["workspace"]["root"] = str(self.workspace.root)
        payload["homunculus"]["config_path"] = str(self.homunculus.config_path)
        payload["homunculus"]["source_workspace"] = str(self.homunculus.source_workspace)
        return payload

    def dispatch_ready_errors(self) -> list[str]:
        errors: list[str] = []
        if self.tracker.kind != "linear":
            errors.append(f"unsupported tracker.kind: {self.tracker.kind}")
        if not self.tracker.project_slug:
            errors.append("tracker.project_slug is required")
        if not self.tracker.api_key:
            source = self.tracker.api_key_env or "LINEAR_API_KEY"
            errors.append(f"tracker API key is missing ({source})")
        if not self.codex.command:
            errors.append("codex.command is required")
        if not self.homunculus.config_path.exists():
            errors.append(f"homunculus.config_path is missing: {self.homunculus.config_path}")
        if not (self.homunculus.source_workspace / ".git").exists():
            errors.append(
                f"homunculus.source_workspace is not a git repo: {self.homunculus.source_workspace}"
            )
        return errors


@dataclass(frozen=True)
class WorkspaceRecord:
    path: Path
    workspace_key: str
    branch_name: str
    created_now: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": str(self.path),
            "workspace_key": self.workspace_key,
            "branch_name": self.branch_name,
            "created_now": self.created_now,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "WorkspaceRecord":
        return cls(
            path=Path(payload["path"]),
            workspace_key=payload["workspace_key"],
            branch_name=payload["branch_name"],
            created_now=bool(payload.get("created_now", False)),
        )


@dataclass
class LiveSession:
    session_id: str = "-"
    thread_id: str = "-"
    turn_id: str = "-"
    codex_app_server_pid: str | None = None
    last_codex_event: str | None = None
    last_codex_timestamp: str | None = None
    last_codex_message: str | None = None
    codex_input_tokens: int = 0
    codex_output_tokens: int = 0
    codex_total_tokens: int = 0
    turn_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "LiveSession":
        return cls(**payload)


@dataclass
class RetryEntry:
    issue_id: str
    identifier: str
    attempt: int
    due_at_ms: int
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "RetryEntry":
        return cls(**payload)


@dataclass
class MergeGateResult:
    name: str
    command: str
    passed: bool
    returncode: int = 0
    stdout: str = ""
    stderr: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "MergeGateResult":
        return cls(**payload)


@dataclass
class AgentResult:
    status: str
    message: str = ""
    episode_id: str | None = None
    commit_sha: str | None = None
    verification_results: list[VerificationResult] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def succeeded(self) -> bool:
        return self.status == "succeeded"

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "message": self.message,
            "episode_id": self.episode_id,
            "commit_sha": self.commit_sha,
            "verification_results": [asdict(item) for item in self.verification_results],
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "AgentResult":
        return cls(
            status=payload["status"],
            message=payload.get("message", ""),
            episode_id=payload.get("episode_id"),
            commit_sha=payload.get("commit_sha"),
            verification_results=[
                VerificationResult.from_dict(item)
                for item in payload.get("verification_results", [])
            ],
            metadata=dict(payload.get("metadata", {})),
        )


@dataclass
class RunAttempt:
    issue_id: str
    issue_identifier: str
    attempt: int | None
    workspace_path: str
    branch_name: str
    started_at: str = field(default_factory=utc_now)
    completed_at: str | None = None
    status: str = "preparing"
    error: str | None = None
    agent_result: AgentResult | None = None
    merge_gates: list[MergeGateResult] = field(default_factory=list)
    merged: bool = False
    merge_commit: str | None = None

    def complete(
        self,
        status: str,
        *,
        error: str | None = None,
        agent_result: AgentResult | None = None,
        merge_gates: list[MergeGateResult] | None = None,
        merged: bool | None = None,
        merge_commit: str | None = None,
    ) -> None:
        self.status = status
        self.completed_at = utc_now()
        self.error = error
        if agent_result is not None:
            self.agent_result = agent_result
        if merge_gates is not None:
            self.merge_gates = merge_gates
        if merged is not None:
            self.merged = merged
        if merge_commit is not None:
            self.merge_commit = merge_commit

    def to_dict(self) -> dict[str, Any]:
        return {
            "issue_id": self.issue_id,
            "issue_identifier": self.issue_identifier,
            "attempt": self.attempt,
            "workspace_path": self.workspace_path,
            "branch_name": self.branch_name,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "status": self.status,
            "error": self.error,
            "agent_result": self.agent_result.to_dict() if self.agent_result else None,
            "merge_gates": [item.to_dict() for item in self.merge_gates],
            "merged": self.merged,
            "merge_commit": self.merge_commit,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "RunAttempt":
        run = cls(
            issue_id=payload["issue_id"],
            issue_identifier=payload["issue_identifier"],
            attempt=payload.get("attempt"),
            workspace_path=payload["workspace_path"],
            branch_name=payload["branch_name"],
            started_at=payload.get("started_at") or utc_now(),
            completed_at=payload.get("completed_at"),
            status=payload.get("status", "preparing"),
            error=payload.get("error"),
            agent_result=(
                AgentResult.from_dict(payload["agent_result"])
                if payload.get("agent_result")
                else None
            ),
            merge_gates=[
                MergeGateResult.from_dict(item)
                for item in payload.get("merge_gates", [])
            ],
            merged=bool(payload.get("merged", False)),
            merge_commit=payload.get("merge_commit"),
        )
        return run
