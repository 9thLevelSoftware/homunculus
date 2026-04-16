from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


@dataclass
class VerificationResult:
    name: str
    command: str
    kind: str = "test"
    returncode: int = 0
    stdout: str = ""
    stderr: str = ""
    passed: bool = True

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "VerificationResult":
        return cls(**payload)


@dataclass
class MemoryRecord:
    id: str
    category: str
    content: str
    metadata: dict[str, Any] = field(default_factory=dict)
    score: float | None = None

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "MemoryRecord":
        return cls(
            id=str(payload.get("id", "")),
            category=str(payload.get("category", "fact")),
            content=str(payload.get("content", "")),
            metadata=dict(payload.get("metadata", {})),
            score=payload.get("score"),
        )


@dataclass
class GuardrailDecision:
    allowed: bool
    warnings: list[str] = field(default_factory=list)
    blocked_reasons: list[str] = field(default_factory=list)
    memory_refs: list[str] = field(default_factory=list)


@dataclass
class TaskRequest:
    task_id: str
    workspace: str
    prompt: str
    comparison_group: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class TeacherResponse:
    plan: list[str]
    candidate_patch: str | None = None
    rationale: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class StudentResponse:
    text: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class TaskExecutionResult:
    workspace_path: str
    diff_hash: str
    applied: bool
    reverted: bool
    verification_results: list[VerificationResult]
    canonical_patch: str | None = None


@dataclass
class CommitResult:
    committed: bool
    commit_sha: str | None = None
    message: str | None = None


@dataclass
class DaemonState:
    started_at: str = field(default_factory=utc_now)
    last_cycle_at: str | None = None
    cycles_completed: int = 0
    total_episodes: int = 0
    episodes_this_cycle: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "DaemonState":
        return cls(**payload)


@dataclass
class GeneratedTask:
    task_id: str
    source: str  # "introspection" | "user" | "continuation"
    prompt: str
    priority: float = 0.5  # 0.0 - 1.0
    introspection_mode: str | None = None
    context: dict[str, Any] = field(default_factory=dict)
    estimated_complexity: str = "medium"  # "trivial" | "small" | "medium" | "large"
    target_files: list[str] = field(default_factory=list)
    success_criteria: str = ""
    created_at: str = field(default_factory=utc_now)
    expires_at: str | None = None

    def to_task_request(self, workspace: str) -> TaskRequest:
        """Convert to TaskRequest for episode execution."""
        return TaskRequest(
            task_id=self.task_id,
            workspace=workspace,
            prompt=self.prompt,
            metadata={
                "source": self.source,
                "priority": self.priority,
                "introspection_mode": self.introspection_mode,
            }
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "GeneratedTask":
        return cls(**payload)


@dataclass
class EpisodeRecord:
    episode_id: str
    task_id: str
    workspace: str
    prompt: str
    plan: list[str]
    teacher_output: dict[str, Any]
    student_output: dict[str, Any]
    diff_hash: str
    test_results: list[VerificationResult]
    lint_results: list[VerificationResult]
    outcome: Literal["accepted", "reverted", "blocked", "error"]
    timestamp: str
    attempt_index: int = 1
    memory_refs: list[str] = field(default_factory=list)
    patch: str | None = None
    patch_path: str | None = None
    source: str = "self-generated"
    review_status: Literal["approved", "needs_review", "rejected"] = "approved"
    comparison_group: str | None = None
    failure_count: int = 0
    verification_passed: bool = False
    failure_stage: str | None = None
    error_type: str | None = None
    error_message: str | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["test_results"] = [asdict(item) for item in self.test_results]
        payload["lint_results"] = [asdict(item) for item in self.lint_results]
        return payload

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "EpisodeRecord":
        payload = dict(payload)
        payload["test_results"] = [VerificationResult.from_dict(item) for item in payload.get("test_results", [])]
        payload["lint_results"] = [VerificationResult.from_dict(item) for item in payload.get("lint_results", [])]
        return cls(**payload)


@dataclass
class SFTSample:
    messages: list[dict[str, str]]
    episode_id: str
    source: str
    verification: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "SFTSample":
        return cls(**payload)


@dataclass
class PreferencePair:
    prompt: str
    chosen: str
    rejected: str
    episode_ids: list[str]
    verification: dict[str, Any]
    source: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "PreferencePair":
        return cls(**payload)


@dataclass
class EvaluationMetrics:
    compile_pass_rate: float
    task_success_rate: float
    average_retries_to_success: float
    regression_count: int
    memory_usefulness_score: float
    tool_misuse_rate: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "EvaluationMetrics":
        return cls(
            compile_pass_rate=float(payload.get("compile_pass_rate", 0.0)),
            task_success_rate=float(payload.get("task_success_rate", 0.0)),
            average_retries_to_success=float(payload.get("average_retries_to_success", 0.0)),
            regression_count=int(payload.get("regression_count", 0)),
            memory_usefulness_score=float(payload.get("memory_usefulness_score", 0.0)),
            tool_misuse_rate=float(payload.get("tool_misuse_rate", 0.0)),
        )


@dataclass
class AdapterManifest:
    model_id: str
    base_model: str
    adapter_path: str
    dataset_snapshot: str
    snapshot_path: str | None
    trainer: str
    metrics: dict[str, Any]
    status: str
    created_at: str
    candidate_id: str | None = None
    lineage: list[str] = field(default_factory=list)
    promotion_reason: str | None = None
    training_command: list[str] = field(default_factory=list)
    sample_counts: dict[str, Any] = field(default_factory=dict)
    self_generated_ratio: float = 0.0
    evaluation_status: str = "pending"
    training_output: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "AdapterManifest":
        return cls(**payload)


@dataclass
class DatasetSnapshot:
    snapshot_id: str
    snapshot_path: str
    sample_counts: dict[str, Any]
    selected_episode_ids: dict[str, list[str]]
    self_generated_ratio: float
    config_hash: str
    created_at: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "DatasetSnapshot":
        return cls(**payload)
