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

from .config import HomunculusConfig, load_config
from .models import DaemonState, GeneratedTask, utc_now
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

    @property
    def state_path(self) -> Path:
        return self.config.paths.runtime_dir / "daemon_state.json"

    @property
    def lock_path(self) -> Path:
        return self.config.paths.runtime_dir / "daemon.pid"

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
        """Acquire exclusive daemon lock. Returns False if another instance is running."""
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        if self.lock_path.exists():
            try:
                pid = int(self.lock_path.read_text(encoding="utf-8").strip())
                # Check if process is still running (cross-platform)
                try:
                    os.kill(pid, 0)  # Signal 0 checks if process exists
                    return False  # Process exists, lock is held
                except OSError:
                    pass  # Process doesn't exist, stale lock
            except (ValueError, OSError):
                pass
        self.lock_path.write_text(str(os.getpid()), encoding="utf-8")
        return True

    def release_lock(self) -> None:
        """Release daemon lock."""
        if self.lock_path.exists():
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

    def get_pending_tasks(self) -> list[GeneratedTask]:
        """Get all pending tasks from introspection and suggestions.

        Combines tasks from three sources:
        1. Generated from introspection insights (if store available)
        2. User suggestions with resonance scoring (if introspection available)
        3. Plain user suggestions (fallback)

        All tasks are then ranked by the prioritizer using alignment,
        complexity, and freshness factors.

        Returns:
            Prioritized list of GeneratedTask objects
        """
        introspection_results = self._get_recent_introspection()

        # Generate tasks from introspection if we have a generator and results
        generated: list[GeneratedTask] = []
        if self.task_generator and introspection_results:
            try:
                generated = self.task_generator.generate_from_introspection(
                    introspection_results,
                    max_tasks=3,
                )
            except Exception as e:
                logger.warning("Failed to generate introspection tasks: %s", e)

        # Read suggestions with resonance boost if we have introspection context
        if introspection_results:
            suggestions = self.suggestion_reader.read_pending_with_resonance(
                introspection_results
            )
        else:
            suggestions = self.suggestion_reader.read_pending()

        # Combine and prioritize all tasks
        all_tasks = generated + suggestions
        return self.prioritizer.prioritize(all_tasks, introspection_results)

    def run_once(self) -> DaemonCycleResult:
        """Execute one daemon cycle: get tasks, run episodes, return."""
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

            try:
                task_request = task.to_task_request(target_workspace)
                episode = self.orchestrator.run_episode(task_request)
                executed += 1
                filename = task.context.get("filename", "")
                if episode.outcome == "accepted":
                    accepted += 1
                    self.suggestion_reader.archive(filename, "accepted")
                else:
                    reverted += 1
                    if episode.outcome == "reverted":
                        self.suggestion_reader.archive(filename, "reverted")
            except Exception as e:
                return DaemonCycleResult(
                    status="error",
                    tasks_executed=executed,
                    tasks_accepted=accepted,
                    tasks_reverted=reverted,
                    error=str(e),
                )

        return DaemonCycleResult(
            status="executed",
            tasks_executed=executed,
            tasks_accepted=accepted,
            tasks_reverted=reverted,
        )

    def run_continuous(self) -> None:
        """Run daemon continuously with configured interval between cycles."""
        self._setup_signal_handlers()
        state = self.load_state()
        interval_seconds = self.config.daemon.cycle_interval_minutes * 60

        print(f"Daemon started. Cycle interval: {self.config.daemon.cycle_interval_minutes} minutes")
        print(f"State: {state.cycles_completed} cycles, {state.total_episodes} episodes")
        print("Press Ctrl+C to stop gracefully.")

        while not self.shutdown_requested:
            # Run a cycle
            result = self.run_once()

            # Update state
            state.cycles_completed += 1
            state.last_cycle_at = utc_now()
            state.episodes_this_cycle = result.tasks_executed
            state.total_episodes += result.tasks_executed
            self.save_state(state)

            print(f"Cycle {state.cycles_completed}: {result.status}, "
                  f"{result.tasks_executed} tasks, "
                  f"{result.tasks_accepted} accepted, "
                  f"{result.tasks_reverted} reverted")

            if self.shutdown_requested:
                break

            # Use Event.wait() for responsive shutdown — wakes immediately on signal
            self._shutdown_event.wait(timeout=interval_seconds)

        # Final state save on shutdown
        self.save_state(state)
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
