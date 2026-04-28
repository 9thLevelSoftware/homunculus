"""Failure-signal tracker persisted to ``runtime/watchdog.json``.

The watchdog is **advisory**. It observes cycle outcomes, merge-failure
counts (read from the evolution counter — mirror, not source of truth),
and repeated task reverts. When any signal crosses its threshold the
corresponding flag surfaces via :meth:`Watchdog.active_flags`. The
daemon is **never** stopped by the watchdog — soak abort conditions
(spec §6) are an operator responsibility.

Persistence: atomic temp-file + :func:`os.replace` (same pattern as
:meth:`Daemon.save_state` and :meth:`ArtifactStore.update_merge`).
Corrupt JSON is recovered by logging a warning and starting fresh; the
watchdog must never crash the daemon.
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
from dataclasses import replace as _dc_replace  # noqa: F401 — kept for future
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from .models import WatchdogSnapshot

logger = logging.getLogger(__name__)


class Watchdog:
    """Advisory failure-signal tracker.

    The class owns the on-disk snapshot at ``state_path`` and exposes
    mutate-then-save helpers for the daemon. All writes go through
    :meth:`save`, which persists atomically. Reads return the in-memory
    snapshot — callers who need a durable read after an external writer
    must call :meth:`load` first.
    """

    _save_lock = threading.Lock()

    def __init__(self, state_path: Path) -> None:
        self.state_path = state_path
        # Load lazily on construction so ``active_flags()`` works even
        # before any tick has been recorded.
        self._snapshot: WatchdogSnapshot = self.load()

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def load(self) -> WatchdogSnapshot:
        """Load the snapshot from ``state_path``.

        Missing file → fresh snapshot. Corrupt JSON → log warning and
        fresh snapshot (the daemon must not crash on an unreadable
        watchdog file).
        """
        if not self.state_path.exists():
            self._snapshot = WatchdogSnapshot()
            return self._snapshot
        try:
            text = self.state_path.read_text(encoding="utf-8")
        except OSError as exc:
            logger.warning(
                "Watchdog state unreadable at %s (%s); using fresh snapshot.",
                self.state_path, exc,
            )
            self._snapshot = WatchdogSnapshot()
            return self._snapshot
        if not text.strip():
            self._snapshot = WatchdogSnapshot()
            return self._snapshot
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            logger.warning(
                "Watchdog state %s corrupt (%s); starting fresh.",
                self.state_path, exc,
            )
            self._snapshot = WatchdogSnapshot()
            return self._snapshot
        if not isinstance(payload, dict):
            logger.warning(
                "Watchdog state %s has unexpected shape (%s); starting fresh.",
                self.state_path, type(payload).__name__,
            )
            self._snapshot = WatchdogSnapshot()
            return self._snapshot
        self._snapshot = WatchdogSnapshot.from_dict(payload)
        return self._snapshot

    def save(self) -> None:
        """Persist the current snapshot atomically.

        Writes to ``<state_path>.tmp`` then :func:`os.replace` onto the
        final path so readers never observe a half-written file. Parent
        directory is created on demand; this means callers do not need
        to prepare ``runtime/`` ahead of time.
        """
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        payload = self._snapshot.to_dict()
        tmp_path: str | None = None
        with type(self)._save_lock:
            try:
                fd, tmp_path = tempfile.mkstemp(
                    dir=self.state_path.parent,
                    prefix=".watchdog_",
                    suffix=".tmp",
                )
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    handle.write(json.dumps(payload, indent=2, ensure_ascii=True))
                os.replace(tmp_path, self.state_path)
            except OSError as exc:
                logger.warning(
                    "Failed to persist watchdog state to %s: %s", self.state_path, exc,
                )
                # Clean up the temp file on failure — we don't want a
                # dangling *.tmp file confusing future reads.
                try:
                    if tmp_path and Path(tmp_path).exists():
                        Path(tmp_path).unlink()
                except OSError:
                    pass

    # ------------------------------------------------------------------
    # Mutations
    # ------------------------------------------------------------------

    def tick(self, cycle_outcome: Any) -> None:
        """Record the outcome of one daemon cycle.

        Accepts either an object with a ``.status`` attribute
        (e.g. :class:`DaemonCycleResult`) or a mapping with a
        ``"status"`` key. ``status`` values of ``"failed"`` or
        ``"error"`` increment the consecutive cycle-failure counter;
        any other status (including ``idle``, ``executed``, unknown)
        resets it to zero — success paths clear the fault.

        The watchdog must never raise from this method: malformed
        inputs are treated as non-failure and the counter is reset.
        """
        status = _extract_status(cycle_outcome)
        if status in {"failed", "error"}:
            self._snapshot.consecutive_cycle_failures += 1
        else:
            self._snapshot.consecutive_cycle_failures = 0
        self._snapshot.last_updated = datetime.now(timezone.utc)

    def record_task_revert(self, task_id: str) -> None:
        """Increment the revert counter for a specific task id."""
        if not task_id:
            return
        current = self._snapshot.repeated_task_reverts.get(task_id, 0)
        self._snapshot.repeated_task_reverts[task_id] = current + 1
        self._snapshot.last_updated = datetime.now(timezone.utc)

    def merge_failures(self, count: int) -> None:
        """Mirror the evolution layer's consecutive merge-failure count.

        The evolution module is the source of truth; we snapshot its
        value here so the watchdog's flag derivation can stay local.
        Mutations of the evolution counter are *not* performed from
        here (spec §5: read-only aggregation).
        """
        try:
            self._snapshot.consecutive_merge_failures = max(0, int(count))
        except (TypeError, ValueError):
            # Defensive: unparseable counts leave the mirror untouched.
            return
        self._snapshot.last_updated = datetime.now(timezone.utc)

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def active_flags(self) -> list[str]:
        """Return the list of active watchdog flags.

        Flag vocabulary (spec §5):

        - ``cycle_failure:{N}`` — N consecutive failed cycles, N >= threshold.
        - ``merge_failure:{N}`` — N consecutive failed merges, N >= threshold.
        - ``repeat_revert:{task_id}`` — one per task whose revert count
          has hit the threshold.

        Output is deterministic: cycle first, then merge, then reverts
        in ``task_id`` sort order (makes diffs stable across report runs).
        """
        snap = self._snapshot
        flags: list[str] = []
        if snap.consecutive_cycle_failures >= WatchdogSnapshot.FAILURE_THRESHOLD_CYCLE:
            flags.append(f"cycle_failure:{snap.consecutive_cycle_failures}")
        if snap.consecutive_merge_failures >= WatchdogSnapshot.FAILURE_THRESHOLD_MERGE:
            flags.append(f"merge_failure:{snap.consecutive_merge_failures}")
        threshold = WatchdogSnapshot.FAILURE_THRESHOLD_TASK_REVERT
        for task_id, count in sorted(snap.repeated_task_reverts.items()):
            if count >= threshold:
                flags.append(f"repeat_revert:{task_id}")
        return flags

    @property
    def snapshot(self) -> WatchdogSnapshot:
        """Return the in-memory snapshot (for testing / inspection)."""
        return self._snapshot


def _extract_status(cycle_outcome: Any) -> str:
    """Pull a normalized ``status`` string out of a cycle-outcome value.

    Accepts the :class:`DaemonCycleResult` object (has ``.status``) OR a
    plain ``dict`` with ``"status"``. Anything else yields the empty
    string, which is treated as a non-failure by :meth:`Watchdog.tick`.
    """
    status = getattr(cycle_outcome, "status", None)
    if status is None and isinstance(cycle_outcome, Mapping):
        status = cycle_outcome.get("status")
    if not isinstance(status, str):
        return ""
    return status.strip().lower()
