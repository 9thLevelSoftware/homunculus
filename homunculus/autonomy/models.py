"""Dataclasses for Phase 5 autonomy instrumentation.

Six frozen public dataclasses per Phase 5 spec §4:

- ``AutonomyReport`` — aggregate snapshot of daemon health.
- ``WatchdogSnapshot`` — persisted failure-signal state (mutable; stored
  to ``runtime/watchdog.json``).
- ``PreflightResult`` + ``GateResult`` — pre-soak gate outcomes.
- ``AcceptanceVerdict`` + ``CriterionResult`` — final SC1..SC6 verdict.

These are read-only surfaces for the reporter / CLI; ``WatchdogSnapshot``
is the one mutable exception because the watchdog mutates counters in
place between atomic saves.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, ClassVar, Literal


@dataclass(frozen=True)
class AutonomyReport:
    """Aggregate health snapshot of a running (or completed) soak.

    Fields mirror spec §4. All numeric fields default to 0 so a reporter
    run against a missing artifact directory returns a coherent zero-valued
    report rather than raising.
    """

    generated_at: datetime
    uptime: timedelta
    cycles_completed: int
    episodes_total: int
    episodes_success: int
    episodes_failed: int
    self_directed_tasks_completed: int
    suggestion_tasks_completed: int
    loras_trained: int
    loras_merged: int
    current_base_generation: int
    patch_success_rate: float
    patch_success_rate_trend: float | None
    coverage_percent: float | None
    coverage_trend: float | None
    watchdog_flags: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe serialization for CLI output.

        ``datetime`` and ``timedelta`` are not JSON-native, so they are
        rendered as ISO-8601 string and seconds (float) respectively.
        """
        return {
            "generated_at": self.generated_at.isoformat(),
            "uptime_seconds": self.uptime.total_seconds(),
            "cycles_completed": self.cycles_completed,
            "episodes_total": self.episodes_total,
            "episodes_success": self.episodes_success,
            "episodes_failed": self.episodes_failed,
            "self_directed_tasks_completed": self.self_directed_tasks_completed,
            "suggestion_tasks_completed": self.suggestion_tasks_completed,
            "loras_trained": self.loras_trained,
            "loras_merged": self.loras_merged,
            "current_base_generation": self.current_base_generation,
            "patch_success_rate": self.patch_success_rate,
            "patch_success_rate_trend": self.patch_success_rate_trend,
            "coverage_percent": self.coverage_percent,
            "coverage_trend": self.coverage_trend,
            "watchdog_flags": list(self.watchdog_flags),
        }


@dataclass
class WatchdogSnapshot:
    """Persisted failure-signal state.

    Intentionally mutable: the :class:`Watchdog` reads the snapshot,
    mutates counters, and writes it back atomically. The ``frozen=True``
    discipline used elsewhere in this module does not apply here because
    serial read-modify-write is the documented contract (spec §5).

    Thresholds are :data:`ClassVar` so they are not treated as dataclass
    fields — overriding them at runtime is not supported; change them
    at the spec level if the semantics shift.
    """

    consecutive_cycle_failures: int = 0
    consecutive_merge_failures: int = 0
    repeated_task_reverts: dict[str, int] = field(default_factory=dict)
    last_updated: datetime | None = None

    FAILURE_THRESHOLD_CYCLE: ClassVar[int] = 3
    FAILURE_THRESHOLD_MERGE: ClassVar[int] = 3
    FAILURE_THRESHOLD_TASK_REVERT: ClassVar[int] = 3

    def to_dict(self) -> dict[str, Any]:
        return {
            "consecutive_cycle_failures": self.consecutive_cycle_failures,
            "consecutive_merge_failures": self.consecutive_merge_failures,
            "repeated_task_reverts": dict(self.repeated_task_reverts),
            "last_updated": (
                self.last_updated.isoformat() if self.last_updated else None
            ),
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "WatchdogSnapshot":
        last = payload.get("last_updated")
        parsed_last: datetime | None = None
        if isinstance(last, str) and last:
            try:
                parsed_last = datetime.fromisoformat(last)
            except ValueError:
                parsed_last = None
        reverts = payload.get("repeated_task_reverts") or {}
        # Defensive: coerce values to int, skip malformed entries rather
        # than raise — watchdog must never crash the daemon.
        clean_reverts: dict[str, int] = {}
        if isinstance(reverts, dict):
            for key, value in reverts.items():
                try:
                    clean_reverts[str(key)] = int(value)
                except (TypeError, ValueError):
                    continue
        return cls(
            consecutive_cycle_failures=int(
                payload.get("consecutive_cycle_failures", 0) or 0
            ),
            consecutive_merge_failures=int(
                payload.get("consecutive_merge_failures", 0) or 0
            ),
            repeated_task_reverts=clean_reverts,
            last_updated=parsed_last,
        )


@dataclass(frozen=True)
class GateResult:
    """Single preflight gate outcome."""

    name: str
    passed: bool
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "passed": self.passed, "detail": self.detail}


@dataclass(frozen=True)
class PreflightResult:
    """Aggregate preflight outcome.

    ``passed`` is a summary flag; callers should still inspect ``gates``
    to produce actionable failure detail. Gate names are defined in
    spec §4.
    """

    passed: bool
    gates: dict[str, GateResult]

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "gates": {name: gate.to_dict() for name, gate in self.gates.items()},
        }


@dataclass(frozen=True)
class CriterionResult:
    """Single SC1..SC6 criterion outcome."""

    id: str
    name: str
    passed: bool
    evidence: str
    raw: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "passed": self.passed,
            "evidence": self.evidence,
            "raw": dict(self.raw),
        }


@dataclass(frozen=True)
class AcceptanceVerdict:
    """Final Phase 5 acceptance verdict.

    ``overall`` is ``PASS`` only when every :class:`CriterionResult` in
    ``criteria`` has ``passed=True`` (spec §10 resolution: no partial
    credit).
    """

    overall: Literal["PASS", "FAIL"]
    criteria: list[CriterionResult]

    def to_dict(self) -> dict[str, Any]:
        return {
            "overall": self.overall,
            "criteria": [c.to_dict() for c in self.criteria],
        }
