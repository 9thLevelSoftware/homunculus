from __future__ import annotations

import argparse
import json
import os
import threading
from dataclasses import dataclass
from pathlib import Path

from .config import HomunculusConfig, load_config
from .models import DaemonState, GeneratedTask
from .suggestions import SuggestionReader


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
        suggestions_dir: Path | None = None,
    ) -> None:
        self.config = config
        self.suggestions_dir = suggestions_dir or (config.paths.root / config.daemon.suggestions_dir)
        self.suggestion_reader = SuggestionReader(self.suggestions_dir)
        self._shutdown_event = threading.Event()

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

    def get_pending_tasks(self) -> list[GeneratedTask]:
        """Get all pending tasks from suggestion directory."""
        return self.suggestion_reader.read_pending()

    def run_once(self) -> DaemonCycleResult:
        """Execute one daemon cycle: get tasks, run episodes, return."""
        tasks = self.get_pending_tasks()

        if not tasks:
            return DaemonCycleResult(status="idle", tasks_executed=0)

        # For Phase 0, we just return that we found tasks.
        # Full episode execution will be wired up when testing with real teacher.
        return DaemonCycleResult(
            status="executed",
            tasks_executed=len(tasks),
        )


def main() -> int:
    parser = argparse.ArgumentParser(prog="homunculus.daemon")
    parser.add_argument("--config", required=True, help="Path to config file")
    parser.add_argument("--once", action="store_true", help="Run one cycle and exit")
    parser.add_argument("--suggestions-dir", help="Override suggestions directory")
    args = parser.parse_args()

    config = load_config(args.config)
    suggestions_dir = Path(args.suggestions_dir) if args.suggestions_dir else None
    daemon = Daemon(config, suggestions_dir=suggestions_dir)

    if args.once:
        result = daemon.run_once()
        print(f"Cycle complete: {result.status}, {result.tasks_executed} tasks")
        return 0

    # Continuous mode will be implemented in Phase 1 by the agent itself
    print("Continuous daemon mode not yet implemented. Use --once for single cycle.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
