# Plan 05-01 Summary

## Status
Complete

## Files Modified
- `.planning/CODEBASE.md` — Full refresh: 2026-04-16 date, 293 test count (actual), all 8 subpackages listed (`orchestrator`, `memory_client`, `task_runner`, `dataset_builder`, `trainer`, `introspection`, `task_generator`, `evolution`), current tests enumerated, artifact layout updated with `runtime/watchdog.json`, Risk Areas table extended to cover daemon, merge, trainer, orchestrator, task_runner, storage
- `homunculus/autonomy/__init__.py` — Package scaffolding with explicit `__all__` exporting 8 public names (6 dataclasses + `generate_report` + `Watchdog`)
- `homunculus/autonomy/models.py` — 6 dataclasses per spec §4: `AutonomyReport` (frozen, 16 fields), `WatchdogSnapshot` (mutable, thresholds as `ClassVar`), `GateResult` + `PreflightResult` + `CriterionResult` + `AcceptanceVerdict` (frozen); all with `to_dict`; `WatchdogSnapshot` also gets `from_dict` with defensive coercion
- `homunculus/autonomy/reporter.py` — `generate_report(runtime_dir, traces_dir, models_dir, *, since=None) -> AutonomyReport`; streams `episodes.jsonl`, `task_history.jsonl` (fallback to `task_queue.jsonl`), `registry.json`, `lineage.jsonl`, `introspection.jsonl`, `watchdog.json`; graceful-missing everywhere (returns zero-valued report); narrow exceptions (`FileNotFoundError`, `OSError`, `json.JSONDecodeError`, `TypeError`, `ValueError`)
- `homunculus/autonomy/watchdog.py` — `Watchdog` class with atomic persist (`os.replace` on `<state_path>.tmp`), corrupt-JSON recovery (log warning + fresh snapshot), flag derivation per spec §5 (`cycle_failure:{N}`, `merge_failure:{N}`, `repeat_revert:{task_id}`), tick accepts either `DaemonCycleResult`-like object or `dict`
- `homunculus/daemon.py` — Additive integration: `Watchdog` constructed in `__init__` pointing at `runtime_dir / "watchdog.json"`; new `_finalize_cycle(outcome)` helper called after `save_state(state)` in `run_continuous`; new `_read_merge_failure_count()` helper reads (does not mutate) the evolution counter via a fresh `TrainingManager`. No existing method signature changed.

## Verification
| Command | Result | Pass? |
|---------|--------|-------|
| `python -m unittest tests.test_daemon -v` | 38 passed, 0 failed | Yes |
| `python -m unittest discover -v 2>&1 \| tail -5` | 293 tests passed | Yes |
| `python -c "from homunculus.autonomy import AutonomyReport, generate_report, Watchdog, WatchdogSnapshot, PreflightResult, AcceptanceVerdict, CriterionResult, GateResult; print('ok')"` | `ok` | Yes |
| `python -c "from dataclasses import fields; from homunculus.autonomy.models import AutonomyReport; print(len(fields(AutonomyReport)))"` | `16` (matches spec §4 exactly) | Yes |
| `generate_report(Path('/nonexistent'), ...)` on three missing dirs | Returns zero-valued `AutonomyReport` with empty `watchdog_flags`, no exception | Yes |
| Watchdog cycle_failure accumulation (3 consecutive `{'status':'failed'}`) | `active_flags() == ['cycle_failure:3']` after save | Yes |
| Watchdog corrupt-JSON recovery (`not-json` → `load()`) | Fresh snapshot returned, warning logged, no exception | Yes |
| `grep -c '293' .planning/CODEBASE.md` | `2` (>= 1) | Yes |
| `grep -E 'introspection/\|task_generator/\|evolution/' .planning/CODEBASE.md` | 3+ matches (all three packages present) | Yes |
| `grep 'Analyzed.*2026-04-16' .planning/CODEBASE.md` | 1 match | Yes |

## Decisions

- **Watchdog tick location**: Placed inside `run_continuous` immediately after `self.save_state(state)` via a new `_finalize_cycle(outcome)` helper. Rationale: `run_continuous` is the only method that executes strictly once per cycle with the `DaemonCycleResult` in scope. `run_once` has three exit points (idle / executed / error) — instrumenting it would have required three call sites or an additional wrapping layer. `_finalize_cycle` is purely additive; `run_once` signature and behavior unchanged. Tests that call `run_once` directly do not exercise the watchdog (by design — the plan allows "watchdog should no-op" for such fixtures, and since those fixtures don't run `run_continuous`, no persistence occurs).
- **Merge-failure mirroring**: Built a fresh `TrainingManager` inside `_read_merge_failure_count` rather than caching one, matching the existing pattern in `_check_evolution` / `_process_merge_result`. Any exception (including `TrainingManager` constructor refusing a MagicMock in tests) returns 0, so the watchdog aggregation never crashes a cycle.
- **Episode success semantics**: In `reporter.py`, `_is_success` treats only `outcome == "accepted"` as success. This matches `EpisodeRecord.outcome` vocabulary (`accepted`, `reverted`, `blocked`, `error`). For the task-history counters (`self_directed_tasks_completed`, `suggestion_tasks_completed`), the reporter accepts both `outcome == "success"` (SC2 literal in spec) and `outcome == "accepted"` (the queue-entry mirror vocabulary) to handle the known Phase 3 vocabulary drift without requiring a schema migration.
- **Watchdog `dict` flag derivation mirrored in reporter**: `reporter._derive_flags` intentionally re-implements the watchdog's flag logic by reading `WatchdogSnapshot.FAILURE_THRESHOLD_*` class-vars. This keeps `reporter.py` import-cycle-free and makes the reporter strictly read-only: it never needs to construct a `Watchdog` instance. Thresholds remain single-source-of-truth on `WatchdogSnapshot`.
- **Dataclass count**: Spec §4 lists 16 `AutonomyReport` fields; the plan's verify hint suggested "14 (or exact spec count)". Chose exact spec count (16) — fidelity to the data contract over fidelity to the hint.

## Pre-existing Issues Encountered

- **Plan test count drift**: Plan header and verify block reference "286 tests", but actual repo state is **293 tests** (delta +7 since the plan was written — the `fix/spec-alignment` merge at `360ff9d` brought the suite to 286, and subsequent commits `763e6e9`, `06978c8`, `d5b75be`, `c74c475` added additional tests). Used the correct 293 in `CODEBASE.md` — reality over stale plan. Flagging so downstream plans (05-02, 05-03) update their baseline assumptions.
- **`.planning/specs/` directory was previously untracked**: Listed in `git status` as `??`. Spec file `05-full-autonomy-spec.md` is present and read-only for this plan; no change made.
- **Docstring attribute references `datetime`/`timedelta`**: `AutonomyReport.to_dict` serializes both to JSON-native forms (ISO string and seconds). Flagged for 05-02's CLI layer so the `--json` flag doesn't need custom encoder plumbing.
- **`DaemonState.started_at` is `str` not `datetime`**: The reporter parses via `datetime.fromisoformat`. Works today because the daemon writes `utc_now()` output, but a schema fix in `models.py` to make it a real `datetime` would eliminate the parse step. Out of scope for 05-01; noted for the Phase 5.1 follow-up register.

## Auto-Remediation (if any)

- None required. All three verify blocks passed on first execution after implementation. No tests were broken; no code was retried.
