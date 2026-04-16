from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path

from .config import HomunculusConfig, load_config
from .models import GeneratedTask
from .suggestions import SuggestionReader


@dataclass
class DaemonCycleResult:
    status: str  # "idle" | "executed" | "error"
    tasks_executed: int = 0
    tasks_accepted: int = 0
    tasks_reverted: int = 0
    error: str | None = None


class Daemon:
    """Basic daemon that reads tasks and executes episodes."""

    def __init__(
        self,
        config: HomunculusConfig,
        suggestions_dir: Path | None = None,
    ) -> None:
        self.config = config
        self.suggestions_dir = suggestions_dir or (config.paths.root / "suggestions")
        self.suggestion_reader = SuggestionReader(self.suggestions_dir)

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
