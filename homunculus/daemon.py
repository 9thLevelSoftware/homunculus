from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from .autonomy import Watchdog
from .config import HomunculusConfig, load_config
from .models import DaemonState, GeneratedTask, TaskQueueEntry, utc_now
from .suggestions import SuggestionReader
from .task_generator import TaskGenerator, TaskPrioritizer

if TYPE_CHECKING:
    from .models import IntrospectionResult
    from .orchestrator.loop import EpisodeOrchestrator
    from .storage import ArtifactStore

logger = logging.getLogger(__name__)


@dataclass
class DaemonCycleResult:
    status: str  # "idle" | "executed" | "error"
    tasks_executed: int = 0
    tasks_accepted: int = 0
    tasks_reverted: int = 0
    error: str | None = None


def _pid_alive(pid: int) -> bool:
    """Return True if `pid` corresponds to a running process. Cross-platform."""
    if pid <= 0:
        return False
    try:
        import psutil  # type: ignore
        return psutil.pid_exists(pid)
    except ImportError:
        pass
    if os.name == "posix":
        try:
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            return True  # process exists but we can't signal it
        except OSError:
            return False
    # Windows fallback without psutil: be conservative — assume alive.
    # This means stale Windows locks won't be auto-cleaned without psutil,
    # but it prevents false-positive overwrites of live locks.
    return True


class Daemon:
    """Daemon that reads tasks, executes episodes, and manages state."""

    def __init__(
        self,
        config: HomunculusConfig,
        orchestrator: "EpisodeOrchestrator | None" = None,
        suggestions_dir: Path | None = None,
        store: "ArtifactStore | None" = None,
    ) -> None:
        self.config = config
        self.orchestrator = orchestrator
        self.suggestions_dir = suggestions_dir or (config.paths.root / config.daemon.suggestions_dir)
        self.suggestion_reader = SuggestionReader(self.suggestions_dir)
        self._shutdown_event = threading.Event()
        self.store = store
        self.task_generator = TaskGenerator(store) if store else None
        self.prioritizer = TaskPrioritizer()

        # In-memory daemon state. Populated lazily on first access so that
        # callers using only run_once() (e.g., tests, --once mode) can still
        # supply a cycle_number for introspection. run_continuous() owns the
        # persistence side-effects via load_state()/save_state().
        self.state: DaemonState = DaemonState()

        # Background merge worker. Merges can take up to validation_timeout_seconds
        # (default 300s) and historically blocked the cycle thread. We now run them
        # on a daemon Thread with a single-flight guard: at most one merge is in
        # flight at a time, and result processing happens on the next cycle that
        # observes the worker has finished.
        self._merge_thread: "threading.Thread | None" = None
        self._last_merge_result: "object | None" = None  # MergeResult, deferred import

        # Introspection scheduler — Phase 2 → Phase 3 integration. Gated on
        # config.introspection.enabled AND store availability: modes need a
        # store in their context, so without one we skip execution entirely.
        if config.introspection.enabled and store is not None:
            from .introspection import IntrospectionScheduler
            self.introspection_scheduler: "IntrospectionScheduler | None" = (
                IntrospectionScheduler(config, store=store)
            )
        else:
            self.introspection_scheduler = None

        # Phase 5 watchdog — advisory failure-signal tracker. Persists to
        # runtime/watchdog.json via atomic write; never stops the daemon.
        # Constructed unconditionally so ``run_once``-only callers (tests,
        # --once mode) exercise the same path as ``run_continuous``.
        self._watchdog: Watchdog = Watchdog(
            self.config.paths.runtime_dir / "watchdog.json"
        )

    @property
    def state_path(self) -> Path:
        return self.config.paths.runtime_dir / "daemon_state.json"

    @property
    def lock_path(self) -> Path:
        return self.config.paths.runtime_dir / "daemon.pid"

    @property
    def stop_file_path(self) -> Path:
        """Path to the stop-file sentinel.

        Operator-side scripts (e.g. ``scripts/phase5/stop-soak.ps1``)
        drop a file at this path to request a graceful shutdown on
        Windows, where ``Stop-Process`` does not deliver SIGINT to
        Python. The file is consumed (deleted) when the daemon honors
        the request so the next launch does not exit immediately.
        """
        return self.config.paths.runtime_dir / "STOP"

    def _check_stop_file(self) -> bool:
        """Return True if the stop-file exists.

        Stat-only check: never raises, never logs at debug level inside
        the cycle hot-path. Missing file is the steady-state.
        """
        try:
            return self.stop_file_path.exists()
        except OSError:
            return False

    def _consume_stop_file(self) -> None:
        """Delete the stop-file after honoring it.

        Idempotent: missing or already-removed file is a no-op. We
        catch ``OSError`` rather than the narrower ``FileNotFoundError``
        because Windows can raise ``PermissionError`` if another process
        has the file open at the moment of unlink.

        Persistence across crash is intentional: if the daemon crashes
        after :meth:`_check_stop_file` returns ``True`` but before this
        method runs, the file survives and the next launch honors the
        operator's stop intent by exiting cleanly on cycle 1 — no
        episodes run, no state mutation. Fail-safe, not fail-silent.
        """
        try:
            self.stop_file_path.unlink()
        except FileNotFoundError:
            return
        except OSError as exc:
            logger.warning(
                "Could not remove stop-file at %s (%s); next launch may "
                "exit immediately. Inspect and delete manually.",
                self.stop_file_path, exc,
            )

    def load_state(self) -> DaemonState:
        """Load daemon state from disk, or return fresh state if missing/corrupt."""
        if self.state_path.exists():
            try:
                data = json.loads(self.state_path.read_text(encoding="utf-8"))
                return DaemonState.from_dict(data)
            except (json.JSONDecodeError, TypeError, KeyError):
                pass
        return DaemonState()

    def save_state(self, state: DaemonState) -> None:
        """Save state atomically using write-to-temp-then-rename."""
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self.state_path.with_suffix(".json.tmp")
        tmp_path.write_text(
            json.dumps(state.to_dict(), indent=2),
            encoding="utf-8"
        )
        # os.replace is atomic on both Unix and Windows
        os.replace(tmp_path, self.state_path)

    def acquire_lock(self) -> bool:
        """Acquire exclusive daemon lock. Returns False if another instance is running.

        Refuses to overwrite a corrupt lock file — operator must inspect.
        Distinguishes "lock vanished mid-read" (proceed) from "lock corrupt" (refuse).
        """
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        if self.lock_path.exists():
            pid_text = ""
            try:
                pid_text = self.lock_path.read_text(encoding="utf-8").strip()
            except FileNotFoundError:
                # Lock vanished between exists() and read — proceed as if no lock
                pass
            except OSError as exc:
                logger.error(
                    "Lock file %s unreadable (%s). Refusing to start.",
                    self.lock_path, exc,
                )
                return False
            else:
                try:
                    pid = int(pid_text)
                except ValueError:
                    logger.error(
                        "Lock file %s is corrupt (content=%r). Refusing to start. "
                        "Inspect and delete manually if no daemon is running.",
                        self.lock_path, pid_text,
                    )
                    return False
                if _pid_alive(pid):
                    return False
                logger.info("Removing stale lock for dead PID %d", pid)
        self.lock_path.write_text(str(os.getpid()), encoding="utf-8")
        return True

    def release_lock(self) -> None:
        """Release daemon lock — only if we own it.

        Note: there is a small TOCTOU window between reading the PID and unlinking;
        another process could take ownership in between. Risk is low because the
        PID would have to match os.getpid(), and we trust other daemons to also
        respect the ownership check.
        """
        if not self.lock_path.exists():
            return
        try:
            owner_pid = int(self.lock_path.read_text(encoding="utf-8").strip())
        except (ValueError, OSError):
            return  # corrupt or vanished lock — don't touch it
        if owner_pid != os.getpid():
            return  # not ours
        try:
            self.lock_path.unlink()
        except OSError:
            pass

    @property
    def shutdown_requested(self) -> bool:
        return self._shutdown_event.is_set()

    def request_shutdown(self) -> None:
        """Request graceful shutdown."""
        self._shutdown_event.set()

    def _setup_signal_handlers(self) -> None:
        """Set up graceful shutdown on SIGINT/SIGTERM using threading.Event.

        Only works in main thread (Python limitation). Silently skips in other threads.
        """
        # Signal handlers can only be set in the main thread
        if threading.current_thread() is not threading.main_thread():
            return

        def handle_shutdown(signum: int, frame: object) -> None:
            signal_name = signal.Signals(signum).name
            print(f"\nReceived {signal_name}. Finishing current episode and shutting down...")
            self._shutdown_event.set()

        signal.signal(signal.SIGINT, handle_shutdown)
        # SIGTERM only on Unix
        if hasattr(signal, "SIGTERM"):
            signal.signal(signal.SIGTERM, handle_shutdown)

    def _run_introspection(self) -> None:
        """Run any due introspection modes for the current cycle and persist them.

        Phase 2 → Phase 3 integration: this is what makes the
        introspection → task-generation → episode loop actually close in
        production. Without this call, no introspection results are ever
        written at runtime, and Phase 3's task generator (which reads recent
        introspection) has nothing to work with.

        Failures in scheduling or any single mode are logged and swallowed —
        introspection is opportunistic and must never crash the daemon cycle.
        Persistence failures are likewise logged per-result, not propagated.
        """
        if self.introspection_scheduler is None or self.store is None:
            return
        try:
            results = self.introspection_scheduler.run_due_modes(
                cycle_number=self.state.cycles_completed,
            )
        except Exception as exc:
            logger.warning("Introspection cycle failed: %s", exc)
            return
        for result in results or []:
            try:
                self.store.append_introspection_result(result)
            except Exception as exc:
                logger.warning(
                    "Failed to persist introspection result (mode=%s): %s",
                    getattr(result, "mode", "<unknown>"), exc,
                )

    def _get_recent_introspection(self) -> list["IntrospectionResult"]:
        """Load recent introspection results for task generation.

        Returns up to 5 most recent results for use in task generation
        and resonance scoring.

        Returns:
            List of recent IntrospectionResult objects, newest first
        """
        if self.store is None:
            return []
        try:
            return self.store.load_introspection_results()[-5:]
        except Exception as e:
            logger.warning("Failed to load introspection results: %s", e)
            return []

    def _mark_queue_status(
        self,
        task_id: str,
        *,
        status: str,
        outcome: str | None = None,
        last_error: str | None = None,
        increment_attempts: bool = False,
    ) -> None:
        """Update a task's queue entry, swallowing storage errors.

        Queue persistence is a best-effort restart-safety mechanism, not
        a correctness guarantee. If the write fails (disk full, race),
        the daemon still finishes the cycle rather than crashing. The
        worst case is that a duplicate entry gets re-dispatched on
        restart, which is safe because episode execution is idempotent
        per task_id (the task runner re-derives workspace state).
        """
        if self.store is None:
            return
        try:
            self.store.update_queue_entry(
                task_id,
                status=status,
                outcome=outcome,
                last_error=last_error,
                increment_attempts=increment_attempts,
            )
        except Exception as exc:
            logger.warning(
                "Failed to update queue status for task %s (status=%s): %s",
                task_id, status, exc,
            )

    def _archive_queue_safely(self) -> None:
        """Sweep completed/failed entries to history; swallow errors."""
        if self.store is None:
            return
        try:
            self.store.archive_completed_tasks()
        except Exception as exc:
            logger.warning("Failed to archive completed queue entries: %s", exc)

    def get_pending_tasks(self) -> list[GeneratedTask]:
        """Get all pending tasks from the queue, introspection, and suggestions.

        Restart-safe flow:
        1. Load any ``status="pending"`` entries from the task queue — these
           are in-flight tasks that survived a crash, SIGTERM, or clean
           shutdown between cycles. They take precedence because the
           originating suggestion file may already be archived (or may
           never have existed, e.g. introspection-generated tasks).
        2. Generate fresh tasks from introspection + suggestion sources.
        3. Persist every fresh task (not already in the queue) to
           ``runtime/task_queue.jsonl`` BEFORE returning. De-duplicate by
           ``task_id`` so re-reading the same suggestion file across
           cycles doesn't create duplicate queue entries.
        4. Prioritize the combined set and return.

        Returns:
            Prioritized list of GeneratedTask objects. Every returned
            task has a persisted queue entry with status="pending".
        """
        introspection_results = self._get_recent_introspection()

        # Step 1: surface queued-but-not-yet-executed tasks first.
        queued_tasks: list[GeneratedTask] = []
        queued_ids: set[str] = set()
        if self.store is not None:
            try:
                queued_tasks = [e.task for e in self.store.load_queue()]
                queued_ids = {t.task_id for t in queued_tasks}
            except Exception as exc:
                logger.warning("Failed to load task queue: %s", exc)

        # Step 2: generate fresh tasks from introspection (if any)
        generated: list[GeneratedTask] = []
        if self.task_generator and introspection_results:
            try:
                generated = self.task_generator.generate_from_introspection(
                    introspection_results,
                    max_tasks=3,
                )
            except Exception as e:
                logger.warning("Failed to generate introspection tasks: %s", e)

        # Step 2 (cont'd): read suggestions, with resonance boost if we
        # have introspection context.
        if introspection_results:
            suggestions = self.suggestion_reader.read_pending_with_resonance(
                introspection_results
            )
        else:
            suggestions = self.suggestion_reader.read_pending()

        fresh_tasks = generated + suggestions

        # Step 3: persist fresh tasks (skip duplicates already queued).
        if self.store is not None:
            now = utc_now()
            for task in fresh_tasks:
                if task.task_id in queued_ids:
                    continue
                entry = TaskQueueEntry(
                    task_id=task.task_id,
                    task=task,
                    queued_at=now,
                    status="pending",
                )
                try:
                    self.store.append_to_queue(entry)
                    queued_ids.add(task.task_id)
                except Exception as exc:
                    # If persistence fails, we still execute the task —
                    # better to make forward progress than to drop work —
                    # but log loudly so operators notice restart-safety
                    # has degraded.
                    logger.warning(
                        "Failed to enqueue task %s: %s (executing without "
                        "queue persistence; restart-safety degraded)",
                        task.task_id, exc,
                    )

        # Step 4: prioritize queued + fresh (de-duplicated by task_id,
        # fresh tasks win so their newer priority/metadata is used).
        fresh_ids = {t.task_id for t in fresh_tasks}
        carry_over = [t for t in queued_tasks if t.task_id not in fresh_ids]
        all_tasks = carry_over + fresh_tasks
        return self.prioritizer.prioritize(all_tasks, introspection_results)

    def run_once(self) -> DaemonCycleResult:
        """Execute one daemon cycle: introspection → tasks → episodes → evolution check."""
        # Run introspection first so that task generation (which reads recent
        # introspection results) sees freshly-computed signals, not stale ones.
        self._run_introspection()

        tasks = self.get_pending_tasks()

        if not tasks:
            return DaemonCycleResult(status="idle", tasks_executed=0)

        # No orchestrator = dry run mode (for testing)
        if not self.orchestrator:
            return DaemonCycleResult(status="executed", tasks_executed=len(tasks))

        executed = 0
        accepted = 0
        reverted = 0

        target_workspace = self.config.daemon.target_workspace
        for task in tasks[:self.config.daemon.max_episodes_per_cycle]:
            if self.shutdown_requested:
                break

            # Mark in_progress + bump attempts BEFORE execution so a crash
            # mid-episode leaves an honest record (attempts=N, status=in_progress)
            # rather than the pre-crash "pending" illusion.
            self._mark_queue_status(
                task.task_id, status="in_progress", increment_attempts=True,
            )

            try:
                task_request = task.to_task_request(target_workspace)
                episode = self.orchestrator.run_episode(task_request)
                executed += 1
                outcome = (getattr(episode, "outcome", "") or "").lower()
                if outcome == "accepted":
                    accepted += 1
                elif outcome == "reverted":
                    reverted += 1
                    # Phase-5 watchdog: record the revert so
                    # repeat_revert:<task_id> can surface once the
                    # per-task threshold is crossed. Wrapped so a
                    # watchdog I/O failure never crashes a cycle.
                    try:
                        self._watchdog.record_task_revert(task.task_id)
                        self._watchdog.save()
                    except Exception as exc:  # noqa: BLE001
                        logger.warning(
                            "Watchdog revert recording failed for %s: %s",
                            task.task_id, exc,
                        )
                # Mark queue entry completed with the episode outcome. Even
                # outcomes like "blocked" or "error" are terminal from the
                # queue's perspective — the task ran, produced a verdict,
                # and should not be re-dispatched.
                self._mark_queue_status(
                    task.task_id, status="completed", outcome=outcome or None,
                )
                # Archive on EVERY terminal outcome so poison inputs (blocked
                # by guardrails, or orchestrator errors) don't loop forever
                # in the suggestions queue. Wrapped in try/except so an
                # archive failure (disk full, permission, race) can't crash
                # the cycle.
                if outcome in {"accepted", "reverted", "blocked", "error"}:
                    filename = task.context.get("filename", "")
                    if filename:
                        try:
                            self.suggestion_reader.archive(filename, outcome)
                        except Exception as exc:
                            logger.warning(
                                "Failed to archive suggestion %s (outcome=%s): %s",
                                filename, outcome, exc,
                            )
            except Exception as e:
                # Orchestrator raised — mark the task failed so it doesn't
                # loop (it already consumed an attempts++), then bail out.
                self._mark_queue_status(
                    task.task_id, status="failed", last_error=str(e),
                )
                self._archive_queue_safely()
                return DaemonCycleResult(
                    status="error",
                    tasks_executed=executed,
                    tasks_accepted=accepted,
                    tasks_reverted=reverted,
                    error=str(e),
                )

        # After episodes complete, check evolution (which may enqueue
        # merge-failure investigation tasks of its own — those stay
        # "pending" for the next cycle).
        self._check_evolution()

        # Sweep completed/failed entries to task_history.jsonl so the
        # live queue stays bounded and restarts only replay truly
        # in-flight work.
        self._archive_queue_safely()

        return DaemonCycleResult(
            status="executed",
            tasks_executed=executed,
            tasks_accepted=accepted,
            tasks_reverted=reverted,
        )

    def _check_evolution(self) -> None:
        """Check if evolution actions (merge) should run.

        Merges run on a background thread so cycles never block on the
        merge + validation pipeline (which can take minutes). State machine:

        1. If a merge thread is alive, return immediately — single-flight.
        2. If a merge thread finished since last cycle, process its result
           (events, failure-task enqueue), then clear the slot.
        3. If trainer.should_merge(), spawn a new background worker and return.
        """
        if not self.config.evolution.enabled:
            return

        if self.store is None:
            return

        # Step 1: previous merge still running — skip this cycle
        if self._merge_thread is not None and self._merge_thread.is_alive():
            return

        # Step 2: previous merge finished — process its result
        if self._merge_thread is not None and not self._merge_thread.is_alive():
            try:
                self._process_merge_result(self._last_merge_result)
            finally:
                self._merge_thread = None
                self._last_merge_result = None

        # Lazy import to avoid circular dependencies
        from .trainer.manager import TrainingManager
        from .dataset_builder.builder import DatasetBuilder

        builder = DatasetBuilder(self.config, self.store)
        trainer = TrainingManager(self.config, self.store, builder)

        # Step 3: maybe start a new merge
        if trainer.should_merge():
            self.store.append_event("evolution_merge_started", {"timestamp": utc_now()})
            self._merge_thread = threading.Thread(
                target=self._run_merge_in_thread,
                args=(trainer,),
                daemon=True,
                name="merge-worker",
            )
            self._merge_thread.start()

    def _run_merge_in_thread(self, trainer: "TrainingManager") -> None:
        """Background worker entry point: run the merge and stash the result.

        Exceptions are captured into ``_last_merge_result`` as a synthetic
        failure so the main cycle can react. We never let an unhandled
        exception crash the worker silently.
        """
        try:
            self._last_merge_result = trainer.run_merge()
        except Exception as exc:  # noqa: BLE001 — surface ANY merge crash
            from .trainer.manager import MergeResult
            self._last_merge_result = MergeResult(
                success=False,
                merge_manifest=None,
                error_message=f"merge worker crashed: {exc!r}",
            )

    def _process_merge_result(self, result: "object | None") -> None:
        """Process a completed background merge result on the cycle thread.

        Called on the cycle that observes the merge worker has finished.
        Emits the appropriate event and, on failure, enqueues an
        investigation task subject to the same persistence safety rules
        as before.
        """
        if result is None or self.store is None:
            return

        if result.success:
            self.store.append_event("evolution_merge_completed", {
                "merge_id": result.merge_manifest.merge_id if result.merge_manifest else None,
                "timestamp": utc_now(),
            })
            return

        self.store.append_event("evolution_merge_failed", {
            "error": result.error_message,
            "timestamp": utc_now(),
        })

        # Check if we should generate a failure investigation task. We need
        # a fresh trainer here because the previous one belongs to the
        # background thread's stack frame.
        from .trainer.manager import TrainingManager
        from .dataset_builder.builder import DatasetBuilder

        builder = DatasetBuilder(self.config, self.store)
        trainer = TrainingManager(self.config, self.store, builder)

        if trainer.should_generate_merge_failure_task():
            from .task_generator.generator import TaskGenerator
            from .models import TaskQueueEntry

            generator = TaskGenerator(self.store)
            task = generator.generate_merge_failure_task(
                failure_count=trainer._get_consecutive_merge_failures(),
                last_error=result.error_message,
            )
            # Add to task queue. Only reset the failure counter on
            # successful enqueue — if persistence fails (disk full,
            # permission, race), we must NOT zero the counter,
            # otherwise the introspection trigger silently dies and
            # the merge failure goes uninvestigated.
            entry = TaskQueueEntry(
                task_id=task.task_id,
                task=task,
                queued_at=utc_now(),
                status="pending",
            )
            try:
                self.store.append_to_queue(entry)
            except Exception as exc:
                logger.warning(
                    "Failed to enqueue merge-failure investigation task: %s. "
                    "Counter NOT reset; will retry next cycle.",
                    exc,
                )
            else:
                trainer.reset_merge_failure_count()

    def _finalize_cycle(self, outcome: "DaemonCycleResult") -> None:
        """Run end-of-cycle hooks that must see the cycle outcome.

        Kept deliberately minimal: the watchdog is the only current
        consumer. We mirror the current consecutive merge-failure count
        (read-only aggregation — spec §5: watchdog does not mutate the
        evolution counter) before persisting so the flag derivation
        stays local. All operations fail-safe: any watchdog error is
        logged and swallowed — a broken watchdog must never block the
        cycle loop.
        """
        try:
            self._watchdog.tick(outcome)
            try:
                self._watchdog.merge_failures(self._read_merge_failure_count())
            except Exception as exc:  # noqa: BLE001 — advisory only
                logger.warning(
                    "Watchdog could not read merge-failure count: %s", exc,
                )
            self._watchdog.save()
        except Exception as exc:  # noqa: BLE001 — advisory only
            logger.warning("Watchdog tick failed: %s", exc)

    def _read_merge_failure_count(self) -> int:
        """Read the evolution layer's consecutive merge-failure count.

        Returns 0 when evolution is disabled, the store is absent, or
        the trainer declines to report (e.g., a MagicMock in tests).
        We deliberately build a fresh trainer rather than caching one
        so tests that replace ``store`` between cycles still see the
        right counter.
        """
        if not self.config.evolution.enabled or self.store is None:
            return 0
        try:
            from .dataset_builder.builder import DatasetBuilder
            from .trainer.manager import TrainingManager

            builder = DatasetBuilder(self.config, self.store)
            trainer = TrainingManager(self.config, self.store, builder)
            value = trainer._get_consecutive_merge_failures()
        except Exception:
            return 0
        try:
            return int(value)
        except (TypeError, ValueError):
            return 0

    def run_continuous(self) -> None:
        """Run daemon continuously with configured interval between cycles."""
        self._setup_signal_handlers()
        # Mirror persisted state into self.state so run_once()'s introspection
        # call sees the right cycle_number across daemon restarts.
        self.state = self.load_state()
        state = self.state
        interval_seconds = self.config.daemon.cycle_interval_minutes * 60

        print(f"Daemon started. Cycle interval: {self.config.daemon.cycle_interval_minutes} minutes")
        print(f"State: {state.cycles_completed} cycles, {state.total_episodes} episodes")
        print("Press Ctrl+C to stop gracefully.")

        while not self.shutdown_requested:
            # Stop-file check at top of cycle: an operator may have dropped
            # the sentinel between our previous interval-wait and now.
            # Honor it before doing more work.
            if self._check_stop_file():
                print("Stop-file detected; graceful shutdown requested.")
                self._shutdown_event.set()
                break

            # Increment BEFORE running the cycle so the introspection scheduler
            # sees a 1-indexed cycle number (cycle 0 is skipped by the scheduler
            # to avoid the modulo-zero edge case).
            state.cycles_completed += 1

            # Run a cycle
            result = self.run_once()

            # Update remaining state
            state.last_cycle_at = utc_now()
            state.episodes_this_cycle = result.tasks_executed
            state.total_episodes += result.tasks_executed
            self.save_state(state)
            self._finalize_cycle(result)

            print(f"Cycle {state.cycles_completed}: {result.status}, "
                  f"{result.tasks_executed} tasks, "
                  f"{result.tasks_accepted} accepted, "
                  f"{result.tasks_reverted} reverted")

            # Stop-file check at end of cycle: operator may have dropped
            # the sentinel during the cycle. Honor it before sleeping so
            # the daemon does not block on the interval wait.
            if self._check_stop_file():
                print("Stop-file detected; graceful shutdown requested.")
                self._shutdown_event.set()
                break

            if self.shutdown_requested:
                break

            # Use Event.wait() for responsive shutdown — wakes immediately on signal
            self._shutdown_event.wait(timeout=interval_seconds)

        # Final state save on shutdown
        self.save_state(state)
        # Consume the stop-file (if any) so subsequent launches start fresh.
        self._consume_stop_file()
        print(f"Daemon stopped. Final state: {state.cycles_completed} cycles, {state.total_episodes} episodes")


def main() -> int:
    parser = argparse.ArgumentParser(prog="homunculus.daemon")
    parser.add_argument("--config", required=True, help="Path to config file")
    parser.add_argument("--once", action="store_true", help="Run one cycle and exit")
    parser.add_argument("--dry-run", action="store_true", help="Run without orchestrator (no real episode execution)")
    parser.add_argument("--suggestions-dir", help="Override suggestions directory")
    args = parser.parse_args()

    config = load_config(args.config)
    suggestions_dir = Path(args.suggestions_dir) if args.suggestions_dir else None

    # Validate target workspace exists
    if config.daemon.target_workspace not in config.workspaces:
        print(f"Error: Target workspace '{config.daemon.target_workspace}' not found in config.")
        print(f"Available workspaces: {list(config.workspaces.keys())}")
        return 1

    # Build orchestrator for real execution (unless dry-run)
    orchestrator = None
    store = None
    if not args.dry_run:
        from .runtime import build_runtime
        _, store, _, _, orchestrator, _, _ = build_runtime(args.config)

    daemon = Daemon(config, orchestrator=orchestrator, suggestions_dir=suggestions_dir, store=store)

    # Check for existing daemon instance
    if not daemon.acquire_lock():
        print("Error: Another daemon instance is already running.")
        return 1

    try:
        if args.once:
            result = daemon.run_once()
            print(f"Cycle complete: {result.status}, {result.tasks_executed} tasks")
            return 0

        # Continuous mode
        daemon.run_continuous()
        return 0
    finally:
        daemon.release_lock()


if __name__ == "__main__":
    raise SystemExit(main())
