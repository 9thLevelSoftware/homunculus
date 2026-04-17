"""Phase 5 throughput pre-check.

Implements the SOAK-PROTOCOL.md §2.2 data-starvation gate: projects how many
LoRA merges the soak is likely to produce given historical episode throughput
and the current ``[evolution]`` thresholds. Fails closed when the projection
is below the minimum floor so the operator does not burn a 7-day window
guaranteed to fail SC3.
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal

from ..config import HomunculusConfig
from .reporter import EPISODE_SUCCESS_STATES


@dataclass(frozen=True)
class ThroughputPrecheck:
    """Result of the throughput pre-check.

    Projection fields use the most natural type for the quantity:

    * ``projected_successful_episodes_soak`` and
      ``projected_loras_trained_soak`` are continuous quantities (rate
      times time, divided by a sample threshold) and are rounded to 4
      decimal places for stable JSON serialization.
    * ``projected_loras_merged_soak`` is a discrete count — you cannot
      complete a fractional merge — so the value is the integer floor
      of (projected_loras_trained_soak / min_loras_for_merge). The
      verdict is derived from this integer.
    """

    lookback_days: int
    soak_days: int
    threshold_min: float
    threshold_safety_margin: float
    episodes_window: int
    episodes_success_window: int
    episodes_per_day: float
    success_rate: float
    min_samples_for_train: int
    min_loras_for_merge: int
    projected_successful_episodes_soak: float
    projected_loras_trained_soak: float
    projected_loras_merged_soak: int
    verdict: Literal["PASS", "BLOCK"]
    margin_note: Literal["OK", "below_safety_margin"]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _parse_episode_timestamp(raw: str) -> datetime | None:
    """Parse ``EpisodeRecord.timestamp`` (ISO-8601 UTC).

    Falls through ``Z``-suffixed variants to match both stdlib-emitted and
    any legacy test fixtures. Returns ``None`` if the string is unparseable.
    """
    if not raw:
        return None
    candidate = raw.strip()
    if candidate.endswith("Z"):
        candidate = candidate[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _iter_episode_records(episodes_path: Path) -> list[dict[str, Any]]:
    """Read every episode line, tolerant of blank lines / JSON corruption."""
    if not episodes_path.exists() or episodes_path.stat().st_size == 0:
        return []
    records: list[dict[str, Any]] = []
    for line in episodes_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        try:
            records.append(json.loads(stripped))
        except json.JSONDecodeError:
            # Skip malformed lines — never crash the gate
            continue
    return records


def run_precheck(
    settings: HomunculusConfig,
    *,
    lookback_days: int = 14,
    soak_days: int = 7,
    threshold_min: float = 1.0,
    safety_margin: float = 1.5,
    now: datetime | None = None,
) -> ThroughputPrecheck:
    """Compute the throughput projection and verdict.

    :param settings: Parsed homunculus config (provides traces_dir + evolution
        thresholds).
    :param lookback_days: Window size for the historical rate calculation.
    :param soak_days: Intended soak duration.
    :param threshold_min: Minimum projected-merges floor for PASS verdict.
    :param safety_margin: Higher floor used only for ``margin_note`` — does
        not affect verdict.
    :param now: Injected clock for tests. Default: ``datetime.now(UTC)``.

    Uses the same success definition as ``reporter.py`` —
    :data:`EPISODE_SUCCESS_STATES`.
    """
    current = now or datetime.now(timezone.utc)
    window_start = current - timedelta(days=lookback_days)

    traces_dir = Path(settings.paths.traces_dir)
    episodes_path = traces_dir / "episodes.jsonl"
    records = _iter_episode_records(episodes_path)

    total = 0
    successful = 0
    for record in records:
        timestamp_raw = record.get("timestamp")
        if not isinstance(timestamp_raw, str):
            continue
        parsed = _parse_episode_timestamp(timestamp_raw)
        if parsed is None or parsed < window_start:
            continue
        total += 1
        outcome = str(record.get("outcome", "")).lower()
        if outcome in EPISODE_SUCCESS_STATES:
            successful += 1

    evo = settings.evolution
    min_samples = max(int(evo.auto_train_after_samples), 1)
    min_loras = max(int(evo.auto_merge_after_loras), 1)

    episodes_per_day = total / max(lookback_days, 1)
    success_rate = (successful / total) if total else 0.0
    projected_successful_soak = episodes_per_day * soak_days * success_rate
    projected_loras_trained = projected_successful_soak / min_samples
    # Floor on the FINAL ratio. A fractional value here is meaningless: you
    # cannot ship a fractional merge during a soak. Previously we floored
    # the numerator before dividing, which produced misleading half-merge
    # values (e.g. 7 trained / 2 = 3.5).
    projected_loras_merged = math.floor(projected_loras_trained / min_loras)

    verdict: Literal["PASS", "BLOCK"] = (
        "PASS" if projected_loras_merged >= threshold_min else "BLOCK"
    )
    margin_note: Literal["OK", "below_safety_margin"] = (
        "OK" if projected_loras_merged >= safety_margin else "below_safety_margin"
    )

    return ThroughputPrecheck(
        lookback_days=lookback_days,
        soak_days=soak_days,
        threshold_min=threshold_min,
        threshold_safety_margin=safety_margin,
        episodes_window=total,
        episodes_success_window=successful,
        episodes_per_day=round(episodes_per_day, 4),
        success_rate=round(success_rate, 4),
        min_samples_for_train=min_samples,
        min_loras_for_merge=min_loras,
        projected_successful_episodes_soak=round(projected_successful_soak, 4),
        projected_loras_trained_soak=round(projected_loras_trained, 4),
        projected_loras_merged_soak=int(projected_loras_merged),
        verdict=verdict,
        margin_note=margin_note,
    )


def format_precheck_table(result: ThroughputPrecheck) -> str:
    """Human-readable summary for CLI output."""
    rows = [
        ("lookback_days", str(result.lookback_days)),
        ("soak_days", str(result.soak_days)),
        ("threshold_min", f"{result.threshold_min}"),
        ("threshold_safety_margin", f"{result.threshold_safety_margin}"),
        ("episodes_window", str(result.episodes_window)),
        ("episodes_success_window", str(result.episodes_success_window)),
        ("episodes_per_day", f"{result.episodes_per_day}"),
        ("success_rate", f"{result.success_rate}"),
        ("min_samples_for_train", str(result.min_samples_for_train)),
        ("min_loras_for_merge", str(result.min_loras_for_merge)),
        (
            "projected_successful_episodes_soak",
            f"{result.projected_successful_episodes_soak}",
        ),
        ("projected_loras_trained_soak", f"{result.projected_loras_trained_soak}"),
        ("projected_loras_merged_soak", f"{result.projected_loras_merged_soak}"),
        ("verdict", result.verdict),
        ("margin_note", result.margin_note),
    ]
    width = max(len(key) for key, _ in rows)
    lines = [f"{key.ljust(width)}  {value}" for key, value in rows]
    return "\n".join(lines)


__all__ = ["ThroughputPrecheck", "run_precheck", "format_precheck_table"]
