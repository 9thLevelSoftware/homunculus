from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import tomllib


@dataclass
class TeacherSettings:
    provider: str
    model: str
    base_url: str
    endpoint: str
    api_key_env: str
    temperature: float = 0.0
    max_tokens: int = 4000
    timeout_seconds: int = 60


@dataclass
class StudentSettings:
    model_id: str
    generate_command: list[str]
    train_command: list[str]
    max_tokens: int = 800
    batch_size: int = 1
    grad_accumulation_steps: int = 8
    prompt_masking: bool = True
    qlora: bool = True
    adapter_root: str = "models/adapters"
    timeout_seconds: int = 60
    train_timeout_seconds: int = 3600


@dataclass
class MemorySettings:
    base_url: str
    search_endpoint: str
    store_endpoint: str
    bearer_token_env: str
    timeout_seconds: int = 10


@dataclass
class ThresholdSettings:
    train_after_samples: int
    train_after_days: int
    max_self_generated_ratio: float
    min_eval_success_delta: float
    failure_growth_threshold: int = 2


@dataclass
class PromotionSettings:
    allow_zero_canary_regressions: bool
    min_task_success_delta: float
    max_tool_misuse_increase: float
    max_retry_increase: float = 0.0


@dataclass
class PathSettings:
    root: Path
    traces_dir: Path
    datasets_dir: Path
    models_dir: Path
    runtime_dir: Path
    seed_sft_path: Path
    seed_dpo_path: Path


@dataclass
class DPOSettings:
    enabled: bool = True
    min_successful_sft_promotions: int = 3
    env: dict[str, str] = field(default_factory=dict)


@dataclass
class PatternRule:
    pattern: str
    message: str


@dataclass
class GuardrailSettings:
    block_patterns: list[PatternRule] = field(default_factory=list)
    warn_patterns: list[PatternRule] = field(default_factory=list)


@dataclass
class DaemonSettings:
    enabled: bool = True
    cycle_interval_minutes: int = 480
    max_episodes_per_cycle: int = 5
    suggestions_dir: str = "suggestions"
    target_workspace: str = "self"


@dataclass
class IntrospectionSettings:
    """Settings for introspection system."""

    enabled: bool = True
    metrics_interval: int = 1  # Run every N cycles
    critique_interval: int = 3
    coverage_interval: int = 5
    comparative_interval: int = 3
    window_size: int = 50  # Episodes to analyze
    critique_enabled: bool = True  # Can disable to save API costs


@dataclass
class VerificationCommand:
    name: str
    command: str
    kind: str = "test"
    timeout_seconds: int = 300


@dataclass
class WorkspaceSettings:
    path: Path
    repo_url: str | None = None
    branch: str | None = None
    verification_commands: list[VerificationCommand] = field(default_factory=list)


@dataclass
class CanaryCommand:
    name: str
    command: str


@dataclass
class HomunculusConfig:
    teacher: TeacherSettings
    student: StudentSettings
    memory: MemorySettings
    thresholds: ThresholdSettings
    promotion: PromotionSettings
    paths: PathSettings
    dpo: DPOSettings
    guardrails: GuardrailSettings
    workspaces: dict[str, WorkspaceSettings]
    canary_commands: list[CanaryCommand]
    source_path: Path
    daemon: DaemonSettings = field(default_factory=DaemonSettings)
    introspection: IntrospectionSettings = field(default_factory=IntrospectionSettings)


def _resolve(base: Path, value: str) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    return (base / path).resolve()


def _parse_rules(items: list[dict[str, str]] | None) -> list[PatternRule]:
    return [PatternRule(pattern=item["pattern"], message=item["message"]) for item in (items or [])]


def _parse_verification(items: list[dict[str, str]] | None) -> list[VerificationCommand]:
    return [
        VerificationCommand(
            name=item["name"],
            command=item["command"],
            kind=item.get("kind", "test"),
            timeout_seconds=int(item.get("timeout_seconds", 300)),
        )
        for item in (items or [])
    ]


def load_config(path: str | Path) -> HomunculusConfig:
    config_path = Path(path).resolve()
    base = config_path.parent
    with config_path.open("rb") as handle:
        raw = tomllib.load(handle)

    paths = PathSettings(
        root=_resolve(base, raw["paths"]["root"]),
        traces_dir=_resolve(base, raw["paths"]["traces_dir"]),
        datasets_dir=_resolve(base, raw["paths"]["datasets_dir"]),
        models_dir=_resolve(base, raw["paths"]["models_dir"]),
        runtime_dir=_resolve(base, raw["paths"].get("runtime_dir", "runtime")),
        seed_sft_path=_resolve(base, raw["paths"]["seed_sft_path"]),
        seed_dpo_path=_resolve(base, raw["paths"]["seed_dpo_path"]),
    )

    workspaces: dict[str, WorkspaceSettings] = {}
    for name, item in raw.get("workspaces", {}).items():
        workspaces[name] = WorkspaceSettings(
            path=_resolve(base, item["path"]),
            repo_url=item.get("repo_url"),
            branch=item.get("branch"),
            verification_commands=_parse_verification(item.get("verification_commands")),
        )

    canary_commands = [
        CanaryCommand(name=item["name"], command=item["command"])
        for item in raw.get("canary", {}).get("commands", [])
    ]

    # Parse [introspection] with defaults if section missing
    introspection_data = raw.get("introspection", {})
    introspection = IntrospectionSettings(
        enabled=introspection_data.get("enabled", True),
        metrics_interval=introspection_data.get("metrics_interval", 1),
        critique_interval=introspection_data.get("critique_interval", 3),
        coverage_interval=introspection_data.get("coverage_interval", 5),
        comparative_interval=introspection_data.get("comparative_interval", 3),
        window_size=introspection_data.get("window_size", 50),
        critique_enabled=introspection_data.get("critique_enabled", True),
    )

    return HomunculusConfig(
        teacher=TeacherSettings(**raw["teacher"]),
        student=StudentSettings(**raw["student"]),
        memory=MemorySettings(**raw["memory"]),
        thresholds=ThresholdSettings(**raw["thresholds"]),
        promotion=PromotionSettings(**raw["promotion"]),
        paths=paths,
        dpo=DPOSettings(**raw.get("dpo", {})),
        guardrails=GuardrailSettings(
            block_patterns=_parse_rules(raw.get("guardrails", {}).get("block_patterns")),
            warn_patterns=_parse_rules(raw.get("guardrails", {}).get("warn_patterns")),
        ),
        workspaces=workspaces,
        canary_commands=canary_commands,
        source_path=config_path,
        daemon=DaemonSettings(**raw.get("daemon", {})),
        introspection=introspection,
    )
