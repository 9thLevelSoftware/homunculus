# Phase 1: Daemon Mode — Context

## Phase Goal

Enable continuous autonomous operation with proper lifecycle management. The daemon should run indefinitely, executing episode cycles at configurable intervals, and shut down gracefully on SIGTERM/SIGINT.

## Requirements

| ID | Requirement | Priority |
|----|-------------|----------|
| R1.1 | Configurable cycle interval from `[daemon].cycle_interval_minutes` | Must Have |
| R1.2 | Multi-episode cycles (up to `max_episodes_per_cycle`) | Must Have |
| R1.3 | SIGTERM/SIGINT graceful shutdown (finish current episode, save state, exit) | Must Have |
| R1.4 | State persistence to `runtime/daemon_state.json` | Must Have |

## Existing Assets

### Files to Modify

| File | Current State | Changes Needed |
|------|---------------|----------------|
| `homunculus/daemon.py` | Basic stub with `--once` mode only | Add continuous loop, signal handlers, state management |
| `homunculus/config.py` | Has `[daemon]` TOML section but no typed parsing | Add `DaemonSettings` dataclass |
| `homunculus/models.py` | Core dataclasses | Add `DaemonState` dataclass |
| `tests/test_daemon.py` | Basic tests for `--once` mode | Add state persistence and signal tests |

### Relevant Existing Code

- `homunculus/daemon.py:21-50` — Existing `Daemon` class with `run_once()` method
- `homunculus/config.py:151-196` — `load_config()` function pattern for parsing TOML
- `homunculus/models.py:8-9` — `utc_now()` helper for timestamps
- `homunculus.example.toml:58-63` — Existing `[daemon]` config section

### Risk Areas (from CODEBASE.md)

| Area | Risk | Mitigation |
|------|------|------------|
| daemon.py:52-70 | Incomplete continuous mode stub | This phase completes it |
| config.py | Missing DaemonSettings | Plan 01 addresses this |
| Signal handling | Cross-platform differences | Use `signal` module, test on Windows and Unix |

## Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| State file location | `runtime/daemon_state.json` | Consistent with other runtime artifacts |
| Signal handling | Python `signal` module + `threading.Event` | Cross-platform, responsive (critique finding) |
| Sleep mechanism | `threading.Event.wait()` | Immediate wakeup on signal (critique: sleep loop was slow) |
| State fields | started_at, last_cycle_at, cycles_completed, total_episodes | Minimal viable state for restart recovery |
| State write pattern | Atomic (write-to-temp, os.replace) | Prevents corruption on crash (critique finding) |
| Single instance | PID lock file at `runtime/daemon.pid` | Prevents conflicts (critique finding) |
| Workspace target | Configurable via `daemon.target_workspace` | Avoids hardcoded "self" (critique finding) |
| Runtime construction | Shared `runtime.py` module | Avoids circular import (critique finding) |

## Critique Findings Addressed

| Finding | Severity | Mitigation in Plans |
|---------|----------|---------------------|
| Circular import daemon.py → cli.py | CRITICAL | Plan 02: New runtime.py module |
| State file corruption on interrupted write | CRITICAL | Plan 01: Atomic write pattern |
| Windows signal handling inefficient | CRITICAL | Plan 03: threading.Event instead of sleep loop |
| Hardcoded "self" workspace | CAUTION | Plan 01+02: Configurable target_workspace |
| No lock file for single instance | CAUTION | Plan 01+02: PID lock file |

## Plan Structure

| Plan | Wave | Dependencies | Agent |
|------|------|--------------|-------|
| 01-config-state | 1 | None | Senior Developer |
| 02-continuous-loop | 2 | Plan 01 | Senior Developer |
| 03-signal-handling | 3 | Plan 02 | Senior Developer |

## Success Criteria

- [ ] `python -m homunculus.daemon --config homunculus.toml` runs continuously
- [ ] Ctrl+C stops gracefully after current episode completes
- [ ] State persists across restarts (cycles_completed, total_episodes, last_cycle_at)
- [ ] Config interval is respected between cycles
- [ ] Tests cover signal handling and state persistence
- [ ] All 19 existing tests still pass
