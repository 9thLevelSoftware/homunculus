from __future__ import annotations

import re
import warnings
from dataclasses import dataclass, field
from pathlib import Path
import tomllib


def _warn_on_unknown_keys(section: str, raw: dict, known: set[str]) -> None:
    """Emit a UserWarning for [section] keys not in the known set.

    Helps surface config drift without breaking existing TOML files.
    """
    unknown = set(raw.keys()) - known
    if unknown:
        warnings.warn(
            f"[{section}] config contains unknown keys: {sorted(unknown)} "
            "(silently ignored)",
            UserWarning,
            stacklevel=3,
        )


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


@dataclass(frozen=True)
class CompiledGuardrailRule:
    """A guardrail pattern with its regex pre-compiled at config load.

    ``pattern`` is the original string (kept for diagnostics / serialization
    round-trips). ``regex`` is the compiled counterpart used by
    :class:`GuardrailEngine`. Compilation happens once in
    :func:`_parse_rules` so a malformed pattern fails ``load_config``
    rather than the first episode.
    """

    pattern: str
    message: str
    regex: "re.Pattern[str]"


@dataclass
class GuardrailSettings:
    block_patterns: list[CompiledGuardrailRule] = field(default_factory=list)
    warn_patterns: list[CompiledGuardrailRule] = field(default_factory=list)


@dataclass
class DaemonSettings:
    enabled: bool = True
    cycle_interval_minutes: int = 480
    max_episodes_per_cycle: int = 5
    suggestions_dir: str = "suggestions"
    target_workspace: str = "self"
    auto_commit_on_accept: bool = True


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
class EvolutionSettings:
    enabled: bool = True
    auto_promote: bool = True
    auto_apply: bool = True
    auto_train_after_samples: int = 50
    auto_merge_after_loras: int = 5
    rollback_on_degradation: bool = True
    max_merge_attempts: int = 3
    validation_timeout_seconds: int = 300
    coherence_prompt: str = "Write a Python function that returns the nth Fibonacci number."
    coherence_min_tokens: int = 50
    merge_backend: str = "auto"  # "auto" | "mergekit" | "mlx"


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
    evolution: EvolutionSettings = field(default_factory=EvolutionSettings)


def _resolve(base: Path, value: str) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    return (base / path).resolve()


def _parse_rules(items: list[dict[str, str]] | None) -> list[CompiledGuardrailRule]:
    """Parse ``{pattern, message}`` TOML tables into CompiledGuardrailRule.

    Compiles each regex at load so a malformed pattern crashes
    ``load_config`` with :class:`re.error` rather than the first episode
    that matches (audit 2026-04-16 findings). Flags are the same as the
    pre-compile era (``IGNORECASE | MULTILINE``) for behavioral parity.
    """
    if not items:
        return []
    rules: list[CompiledGuardrailRule] = []
    for i, entry in enumerate(items):
        if not isinstance(entry, dict):
            raise ValueError(
                f"guardrails pattern #{i} must be a table with "
                f"'pattern' and 'message' keys"
            )
        pattern = entry.get("pattern")
        message = entry.get("message")
        if not isinstance(pattern, str) or not isinstance(message, str):
            raise ValueError(
                f"guardrails pattern #{i} requires string 'pattern' "
                f"and 'message' keys; got {entry!r}"
            )
        compiled = re.compile(pattern, re.IGNORECASE | re.MULTILINE)
        rules.append(CompiledGuardrailRule(
            pattern=pattern, message=message, regex=compiled
        ))
    return rules


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

    # Validate and apply defaults for intervals (must be >= 1)
    def _validate_interval(value: int, default: int) -> int:
        """Ensure interval is at least 1, otherwise use default."""
        return value if value >= 1 else default

    metrics_interval = _validate_interval(
        introspection_data.get("metrics_interval", 1), 1
    )
    critique_interval = _validate_interval(
        introspection_data.get("critique_interval", 3), 3
    )
    coverage_interval = _validate_interval(
        introspection_data.get("coverage_interval", 5), 5
    )
    comparative_interval = _validate_interval(
        introspection_data.get("comparative_interval", 3), 3
    )

    introspection = IntrospectionSettings(
        enabled=introspection_data.get("enabled", True),
        metrics_interval=metrics_interval,
        critique_interval=critique_interval,
        coverage_interval=coverage_interval,
        comparative_interval=comparative_interval,
        window_size=introspection_data.get("window_size", 50),
        critique_enabled=introspection_data.get("critique_enabled", True),
    )

    # Parse [evolution] with defaults if section missing
    evolution_raw = raw.get("evolution", {})
    evolution = EvolutionSettings(
        enabled=evolution_raw.get("enabled", True),
        auto_promote=evolution_raw.get("auto_promote", True),
        auto_apply=evolution_raw.get("auto_apply", True),
        auto_train_after_samples=evolution_raw.get("auto_train_after_samples", 50),
        auto_merge_after_loras=evolution_raw.get(
            "auto_merge_after_loras",
            evolution_raw.get("merge_after_loras", 5),  # back-compat alias
        ),
        rollback_on_degradation=evolution_raw.get("rollback_on_degradation", True),
        max_merge_attempts=evolution_raw.get("max_merge_attempts", 3),
        validation_timeout_seconds=evolution_raw.get("validation_timeout_seconds", 300),
        coherence_prompt=evolution_raw.get(
            "coherence_prompt",
            "Write a Python function that returns the nth Fibonacci number.",
        ),
        coherence_min_tokens=evolution_raw.get("coherence_min_tokens", 50),
        merge_backend=evolution_raw.get("merge_backend", "auto"),
    )
    _warn_on_unknown_keys("evolution", evolution_raw, {
        "enabled", "auto_promote", "auto_apply", "auto_train_after_samples",
        "auto_merge_after_loras", "merge_after_loras", "rollback_on_degradation",
        "max_merge_attempts", "validation_timeout_seconds", "coherence_prompt",
        "coherence_min_tokens", "merge_backend",
    })

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
        evolution=evolution,
    )
