"""Aggregate runtime artifacts into an :class:`AutonomyReport`.

Read-only: this module never mutates on-disk state. It streams JSONL
artifacts and JSON registries, classifies the records, and returns a
frozen report suitable for CLI display or JSON dump.

Graceful-missing contract: if the runtime / traces / models directories
don't exist yet (common on a fresh checkout before the daemon has run),
``generate_report`` returns a zero-valued :class:`AutonomyReport`
rather than raising. File-level corruption is tolerated the same way —
we log nothing here (caller owns logging) and treat a malformed file as
empty.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from .models import AutonomyReport, WatchdogSnapshot


# Episode-trend window size: the last-N vs first-N comparison used for
# ``patch_success_rate_trend``. Spec §4 and §11 call for 50/50.
_TREND_WINDOW = 50


# Episode-success states: only ``accepted`` represents a merged-into-workspace
# success per :class:`EpisodeRecord`. The other terminal outcomes
# (``reverted`` / ``blocked`` / ``error``) are failures for patch-success-rate
# purposes.
EPISODE_SUCCESS_STATES = frozenset({"accepted"})

# Task-history success states: queue/history rows accept either ``success``
# (SC2 literal) or ``accepted`` (the EpisodeRecord outcome the queue mirrors).
# Phase 3 didn't fully unify the vocabulary, so both are live in the fleet's
# on-disk artifacts; this set is intentionally broader than
# ``EPISODE_SUCCESS_STATES``.
TASK_HISTORY_SUCCESS_STATES = frozenset({"success", "accepted"})

# Candidate-status values that count as "merged into base". Source of truth:
# ``homunculus/trainer/manager.py`` writes ``status="merged"`` after a
# successful merge and ``status="promoted"`` after promotion. Other status
# values (``training`` / ``trained`` / ``failed`` / ``evaluated`` / ``rejected``
# / ``validated``) are not merge-into-base markers.
MERGED_CANDIDATE_STATES = frozenset({"merged", "promoted"})


def generate_report(
    runtime_dir: Path,
    traces_dir: Path,
    models_dir: Path,
    *,
    since: datetime | None = None,
) -> AutonomyReport:
    """Aggregate artifacts under the three directories into a report.

    Args:
        runtime_dir: ``runtime/`` path (daemon_state.json, watchdog.json,
            task_history.jsonl).
        traces_dir: ``traces/`` path (episodes.jsonl, introspection.jsonl,
            lineage.jsonl).
        models_dir: ``models/`` path (registry.json).
        since: Optional lower bound (inclusive) on episode timestamps. If
            provided, episodes with a parsable timestamp older than
            ``since`` are skipped. Episodes with unparseable timestamps
            are kept (fail-open) so a partial-write row can't silently
            drop the whole tail.

    Returns:
        An :class:`AutonomyReport`. Every field is populated; missing
        artifacts produce zero-valued fields, not exceptions.
    """
    generated_at = datetime.now(timezone.utc)
    started_at = _load_started_at(runtime_dir)
    uptime = generated_at - started_at if started_at else timedelta(0)
    cycles_completed = _load_cycles_completed(runtime_dir)

    episodes = _load_episodes(traces_dir, since=since)
    episodes_total = len(episodes)
    episodes_success = sum(1 for ep in episodes if _is_success(ep))
    episodes_failed = episodes_total - episodes_success

    task_history = _load_task_history(runtime_dir)
    self_directed = _count_self_directed(task_history)
    suggestion_tasks = _count_suggestion_tasks(task_history)

    loras_trained, loras_merged = _count_candidates(models_dir)
    current_base_generation = _load_current_generation(traces_dir)

    patch_success_rate, patch_success_rate_trend = _patch_success_rate_stats(
        episodes
    )

    coverage_percent, coverage_trend = _coverage_stats(traces_dir, since=since)

    watchdog_flags = _load_watchdog_flags(runtime_dir)

    return AutonomyReport(
        generated_at=generated_at,
        uptime=uptime,
        cycles_completed=cycles_completed,
        episodes_total=episodes_total,
        episodes_success=episodes_success,
        episodes_failed=episodes_failed,
        self_directed_tasks_completed=self_directed,
        suggestion_tasks_completed=suggestion_tasks,
        loras_trained=loras_trained,
        loras_merged=loras_merged,
        current_base_generation=current_base_generation,
        patch_success_rate=patch_success_rate,
        patch_success_rate_trend=patch_success_rate_trend,
        coverage_percent=coverage_percent,
        coverage_trend=coverage_trend,
        watchdog_flags=tuple(watchdog_flags),
    )


# ---------------------------------------------------------------------------
# Internal loaders
# ---------------------------------------------------------------------------


def _read_json(path: Path) -> dict[str, Any] | None:
    """Load a JSON file or return ``None`` if missing / malformed."""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError:
        return None
    if not text.strip():
        return None
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    """Stream a JSONL file into a list of dicts.

    Skips blank lines and malformed rows (fail-open — a single bad row
    shouldn't blank the whole report). Returns ``[]`` for missing files.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return []
    except OSError:
        return []
    rows: list[dict[str, Any]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            rows.append(payload)
    return rows


def _parse_iso(value: Any) -> datetime | None:
    """Parse an ISO-8601 string into a datetime; ``None`` on failure."""
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _load_started_at(runtime_dir: Path) -> datetime | None:
    data = _read_json(runtime_dir / "daemon_state.json")
    if not data:
        return None
    return _parse_iso(data.get("started_at"))


def _load_cycles_completed(runtime_dir: Path) -> int:
    data = _read_json(runtime_dir / "daemon_state.json")
    if not data:
        return 0
    try:
        return int(data.get("cycles_completed", 0) or 0)
    except (TypeError, ValueError):
        return 0


def _load_episodes(
    traces_dir: Path, *, since: datetime | None
) -> list[dict[str, Any]]:
    rows = _read_jsonl(traces_dir / "episodes.jsonl")
    if since is None:
        return rows
    filtered: list[dict[str, Any]] = []
    for row in rows:
        ts = _parse_iso(row.get("timestamp"))
        # Fail-open: keep rows we can't timestamp so a partial-write
        # doesn't erase the tail of the record.
        if ts is None or ts >= since:
            filtered.append(row)
    return filtered


def _is_success(episode: dict[str, Any]) -> bool:
    """A success is any episode whose outcome is in
    :data:`EPISODE_SUCCESS_STATES` (currently ``{"accepted"}``).

    The repository's four outcome states are accepted / reverted /
    blocked / error (see :class:`EpisodeRecord` in ``models.py``). Only
    ``accepted`` represents a merged-into-workspace success; everything
    else is a failure for patch-success-rate purposes.
    """
    outcome = str(episode.get("outcome", "")).lower()
    return outcome in EPISODE_SUCCESS_STATES


def _load_task_history(runtime_dir: Path) -> list[dict[str, Any]]:
    # Prefer the archived history, but fall back to the live queue in
    # case no tasks have been archived yet (e.g. early in a soak).
    rows = _read_jsonl(runtime_dir / "task_history.jsonl")
    if rows:
        return rows
    return _read_jsonl(runtime_dir / "task_queue.jsonl")


def _count_self_directed(history: Iterable[dict[str, Any]]) -> int:
    """Count successful tasks originating from the agent's own signals.

    Matches spec SC2: ``source in {generated, resonance}`` with
    ``outcome == "success"``. The queue entry wraps the task, so we
    look inside ``entry.task.source`` AND ``entry.outcome``.
    """
    count = 0
    for entry in history:
        if not _entry_outcome_success(entry):
            continue
        source = _entry_task_source(entry)
        if source in {"generated", "resonance"}:
            count += 1
    return count


def _count_suggestion_tasks(history: Iterable[dict[str, Any]]) -> int:
    """Count successful suggestion-sourced tasks."""
    count = 0
    for entry in history:
        if not _entry_outcome_success(entry):
            continue
        source = _entry_task_source(entry)
        if source == "suggestion":
            count += 1
    return count


def _entry_outcome_success(entry: dict[str, Any]) -> bool:
    """True if a queue/history entry represents a successful task.

    Consumes :data:`TASK_HISTORY_SUCCESS_STATES`, which is broader than
    :data:`EPISODE_SUCCESS_STATES` because the queue records both the
    SC2-literal ``success`` value and the EpisodeRecord-mirrored
    ``accepted`` value.
    """
    outcome = str(entry.get("outcome") or "").lower()
    return outcome in TASK_HISTORY_SUCCESS_STATES


def _entry_task_source(entry: dict[str, Any]) -> str:
    task = entry.get("task")
    if isinstance(task, dict):
        return str(task.get("source") or "").lower()
    return ""


def _count_candidates(models_dir: Path) -> tuple[int, int]:
    """Return ``(loras_trained, loras_merged)`` from ``registry.json``.

    ``loras_trained`` counts all candidate manifests ever registered;
    ``loras_merged`` counts those whose status is in
    :data:`MERGED_CANDIDATE_STATES` (exact match — substring matching
    on ``"merge"`` would incorrectly count statuses such as
    ``merge_pending`` or ``merge_failed``).
    """
    data = _read_json(models_dir / "registry.json")
    if not data:
        return 0, 0
    candidates = data.get("candidates")
    if not isinstance(candidates, list):
        return 0, 0
    loras_trained = len(candidates)
    loras_merged = 0
    for item in candidates:
        if not isinstance(item, dict):
            continue
        status = str(item.get("status") or "").lower()
        if status in MERGED_CANDIDATE_STATES:
            loras_merged += 1
    return loras_trained, loras_merged


def _load_current_generation(traces_dir: Path) -> int:
    """Max ``generation`` across lineage records, default 0."""
    rows = _read_jsonl(traces_dir / "lineage.jsonl")
    highest = 0
    for row in rows:
        try:
            value = int(row.get("generation", 0) or 0)
        except (TypeError, ValueError):
            continue
        if value > highest:
            highest = value
    return highest


def _patch_success_rate_stats(
    episodes: list[dict[str, Any]],
) -> tuple[float, float | None]:
    """Return ``(last_50_rate, trend_vs_first_50)``.

    Trend is ``None`` when there are fewer than ``2 * _TREND_WINDOW``
    episodes — spec §11 calls for explicit n/a in that case rather than
    a noisy delta.
    """
    if not episodes:
        return 0.0, None
    last_window = episodes[-_TREND_WINDOW:]
    last_rate = _success_rate(last_window)
    if len(episodes) < 2 * _TREND_WINDOW:
        return last_rate, None
    first_window = episodes[:_TREND_WINDOW]
    first_rate = _success_rate(first_window)
    return last_rate, last_rate - first_rate


def _success_rate(episodes: list[dict[str, Any]]) -> float:
    if not episodes:
        return 0.0
    successes = sum(1 for ep in episodes if _is_success(ep))
    return successes / len(episodes)


def _coverage_stats(
    traces_dir: Path, *, since: datetime | None
) -> tuple[float | None, float | None]:
    """Return ``(last_coverage_percent, trend_vs_soak_start)``.

    Reads ``traces/introspection.jsonl`` and filters to ``mode ==
    "coverage"``. The last such record's ``metrics["coverage_percent"]``
    (fall back to ``percent``) is the current value; the first record at
    or after ``since`` is the baseline for trend. If coverage hasn't run
    yet, both fields are ``None``.
    """
    rows = _read_jsonl(traces_dir / "introspection.jsonl")
    coverage_rows = [r for r in rows if str(r.get("mode", "")).lower() == "coverage"]
    if not coverage_rows:
        return None, None

    # The jsonl is append-only so the last entry is the most recent.
    current = _extract_coverage_percent(coverage_rows[-1])

    baseline: float | None = None
    for row in coverage_rows:
        ts = _parse_iso(row.get("timestamp"))
        if since is not None and ts is not None and ts < since:
            continue
        baseline = _extract_coverage_percent(row)
        if baseline is not None:
            break

    if current is None:
        return None, None
    if baseline is None or baseline == current:
        return current, None
    return current, current - baseline


def _extract_coverage_percent(row: dict[str, Any]) -> float | None:
    metrics = row.get("metrics")
    if not isinstance(metrics, dict):
        return None
    for key in ("coverage_percent", "percent", "coverage"):
        raw = metrics.get(key)
        if raw is None:
            continue
        try:
            return float(raw)
        except (TypeError, ValueError):
            continue
    return None


def _load_watchdog_flags(runtime_dir: Path) -> list[str]:
    """Read ``watchdog.json`` and derive active flags.

    Reused here (rather than importing :class:`Watchdog`) to keep the
    reporter read-only and free of circular imports: reporter is a leaf
    module and :class:`Watchdog` depends on :class:`WatchdogSnapshot`
    which is already a peer in ``autonomy.models``.
    """
    path = runtime_dir / "watchdog.json"
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return []
    except OSError:
        return []
    if not text.strip():
        return []
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return []
    if not isinstance(payload, dict):
        return []
    snapshot = WatchdogSnapshot.from_dict(payload)
    return _derive_flags(snapshot)


def _derive_flags(snapshot: WatchdogSnapshot) -> list[str]:
    """Mirror :meth:`Watchdog.active_flags` so the reporter doesn't
    depend on the watchdog module at import time.

    Thresholds read off :class:`WatchdogSnapshot` class-vars so there's
    a single source of truth for the ``>= N`` boundaries.
    """
    flags: list[str] = []
    if snapshot.consecutive_cycle_failures >= WatchdogSnapshot.FAILURE_THRESHOLD_CYCLE:
        flags.append(f"cycle_failure:{snapshot.consecutive_cycle_failures}")
    if snapshot.consecutive_merge_failures >= WatchdogSnapshot.FAILURE_THRESHOLD_MERGE:
        flags.append(f"merge_failure:{snapshot.consecutive_merge_failures}")
    threshold = WatchdogSnapshot.FAILURE_THRESHOLD_TASK_REVERT
    for task_id, count in sorted(snapshot.repeated_task_reverts.items()):
        if count >= threshold:
            flags.append(f"repeat_revert:{task_id}")
    return flags
