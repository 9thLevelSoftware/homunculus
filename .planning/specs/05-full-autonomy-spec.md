# Phase 5 Spec — Full Autonomy

**Status**: Draft
**Architecture**: Clean (new `homunculus/autonomy/` package)
**Date**: 2026-04-16

## 1. Goal

Hands-off operation. Agent runs ≥1 week unattended, finds its own work, trains its own models, produces evidence that all previous phases are stable in long-running production.

Phase 5 is **terminal** and **observational**. Deliverables are:
1. Instrumentation to measure autonomy health.
2. Execution protocol for a real multi-day soak run.
3. Evidence-based acceptance report gating Phase 5 completion.

## 2. Success Criteria (from ROADMAP)

| # | Criterion | Measurement Source |
|---|-----------|-------------------|
| SC1 | 1+ week unattended operation | `daemon_state.json` (start timestamp, cycles_completed) + commit log (no human commits during run) |
| SC2 | 10+ self-directed tasks completed | `runtime/task_history.jsonl` filtered by `outcome == "success"` and `source in {"generated", "resonance"}` |
| SC3 | ≥1 LoRA trained and merged | `models/registry.json` (lineage generation increment) + `MergeManifest` status |
| SC4 | Test suite passes | `python -m unittest discover` exits 0 at start and end |
| SC5 | Metrics stable or improving | Patch success rate, coverage — trend comparison between soak start and end snapshots |
| SC6 | No human intervention required | Zero manual commits on soak branch (author != "Developer/Homunculus Agent" allowed only for the final acceptance commit) |

## 3. Scope

### In scope

- `homunculus/autonomy/` package:
  - `reporter.py` — aggregates runtime artifacts into `AutonomyReport`
  - `watchdog.py` — tracks consecutive failure signals, persists to `runtime/watchdog.json`
  - `preflight.py` — pre-soak readiness checks (doctor + test suite + queue sanity)
  - `acceptance.py` — per-criterion predicate validation, produces `AcceptanceVerdict`
  - `models.py` — dataclasses: `AutonomyReport`, `WatchdogSnapshot`, `PreflightResult`, `AcceptanceVerdict`, `CriterionResult`
  - `__init__.py` — public API exports
- CLI additions in `homunculus/cli.py`:
  - `autonomy-report` — emit human + JSON report
  - `autonomy-preflight` — run preflight gates
  - `autonomy-accept` — run acceptance predicates against soak data
- Daemon integration: `daemon.py` calls `Watchdog.tick()` each cycle, records signals.
- CODEBASE.md refresh (stale: says 19 tests, repo has 286) — re-run codebase mapping before plan execution starts.
- Soak protocol doc: `.planning/phases/05-full-autonomy/SOAK-PROTOCOL.md`.
- Daily soak logs: `.planning/phases/05-full-autonomy/soak-log/day-NN.md`.
- Acceptance report: `.planning/phases/05-full-autonomy/05-ACCEPTANCE.md`.
- Tests: `tests/test_autonomy.py` covering reporter aggregation, watchdog counters + atomic persistence, preflight gates, acceptance predicates w/ fixtures.

### Out of scope

- New introspection modes (Phase 2 surface frozen).
- New task-generation strategies (Phase 3 surface frozen).
- Merge algorithm changes (Phase 4 surface frozen).
- Remote monitoring / web dashboard.
- Multi-agent / cross-repo operation.
- Alert push (email, Slack) — report is pull-based via CLI.

## 4. Data Contracts

### `AutonomyReport`

```python
@dataclass(frozen=True)
class AutonomyReport:
    generated_at: datetime
    uptime: timedelta                 # from daemon_state.started_at
    cycles_completed: int
    episodes_total: int
    episodes_success: int
    episodes_failed: int
    self_directed_tasks_completed: int   # source in {generated, resonance}, outcome=success
    suggestion_tasks_completed: int
    loras_trained: int
    loras_merged: int
    current_base_generation: int
    patch_success_rate: float           # last 50 episodes
    patch_success_rate_trend: float     # delta vs first 50 episodes of soak
    coverage_percent: float | None      # from last CoverageMode run
    coverage_trend: float | None        # delta vs soak start
    watchdog_flags: list[str]           # active active signals
```

### `WatchdogSnapshot` (persisted to `runtime/watchdog.json`)

```python
@dataclass
class WatchdogSnapshot:
    consecutive_cycle_failures: int = 0
    consecutive_merge_failures: int = 0     # mirrors evolution counter but tracked independently
    repeated_task_reverts: dict[str, int] = field(default_factory=dict)  # task_id -> revert count
    last_updated: datetime | None = None

    FAILURE_THRESHOLD_CYCLE: ClassVar[int] = 3
    FAILURE_THRESHOLD_MERGE: ClassVar[int] = 3
    FAILURE_THRESHOLD_TASK_REVERT: ClassVar[int] = 3
```

### `PreflightResult`

```python
@dataclass(frozen=True)
class PreflightResult:
    passed: bool
    gates: dict[str, GateResult]          # gate_name -> result

@dataclass(frozen=True)
class GateResult:
    name: str
    passed: bool
    detail: str
```

Gates: `config_parses`, `doctor_passes`, `worktrees_clean`, `test_suite_passes`, `task_queue_ready`, `teacher_reachable`, `git_clean`.

### `AcceptanceVerdict`

```python
@dataclass(frozen=True)
class AcceptanceVerdict:
    overall: Literal["PASS", "FAIL"]
    criteria: list[CriterionResult]

@dataclass(frozen=True)
class CriterionResult:
    id: str                    # SC1..SC6
    name: str
    passed: bool
    evidence: str              # human-readable proof
    raw: dict                  # raw metric values for audit
```

## 5. Watchdog Semantics

- Daemon calls `watchdog.tick(cycle_outcome)` at cycle end.
- `cycle_outcome.status == "failed"` → `consecutive_cycle_failures += 1`; `success` resets to 0.
- Merge failures mirrored from evolution counter (read-only aggregation).
- Task-revert tracking: if same `task_id` has been attempted ≥2 times with outcome=reverted, increment map; hitting `FAILURE_THRESHOLD_TASK_REVERT` surfaces as watchdog flag `repeat_revert:{task_id}`.
- Watchdog never stops the daemon. It records flags; `autonomy-report` surfaces them; soak protocol defines human abort conditions.
- Persistence via atomic temp-file + `os.replace()` (same pattern as `daemon_state.json`, `registry.json`).

## 6. Soak Protocol (outline — detailed file written during 05-02)

1. Branch from `master` named `phase-5/soak-YYYYMMDD`.
2. Run `autonomy-preflight` — must return `passed=True`.
3. Capture baseline: `autonomy-report --json > soak-log/day-00-baseline.json`.
4. Start daemon: `python -m homunculus.daemon --config homunculus.toml`.
5. Daily: run `autonomy-report --json > soak-log/day-NN.json` + markdown diff.
6. Abort conditions (human checks, no code changes during run):
   - Watchdog flag `cycle_failure:3+` persistent ≥ 2 cycles → investigate.
   - Disk full / git corruption → abort, diagnose.
   - Test suite regression detected by daily report → abort.
7. After ≥7 full days: run `autonomy-accept` → writes `05-ACCEPTANCE.md`.

## 7. CLI Contracts

```text
python -m homunculus.cli autonomy-preflight --config homunculus.toml [--json]
  exit 0 if all gates pass, 1 otherwise

python -m homunculus.cli autonomy-report --config homunculus.toml [--json] [--since DAY-N]
  stdout: human table or JSON blob

python -m homunculus.cli autonomy-accept --config homunculus.toml \
       --soak-log .planning/phases/05-full-autonomy/soak-log \
       --output .planning/phases/05-full-autonomy/05-ACCEPTANCE.md
  exit 0 only if all 6 criteria PASS
```

## 8. Testing Strategy

- `tests/test_autonomy.py`:
  - `test_report_aggregates_events_and_episodes`
  - `test_report_computes_success_rate_trend`
  - `test_watchdog_increments_and_resets_on_success`
  - `test_watchdog_persists_atomically`
  - `test_watchdog_recovers_corrupted_json` (fails-closed → fresh snapshot with warning)
  - `test_preflight_all_gates_pass`
  - `test_preflight_fails_when_worktree_dirty`
  - `test_preflight_fails_when_tests_fail`
  - `test_acceptance_all_criteria_met`
  - `test_acceptance_fails_when_uptime_insufficient`
  - `test_acceptance_fails_when_tasks_below_threshold`
  - `test_acceptance_no_human_intervention_detection`
- Integration test: daemon cycle invokes watchdog; watchdog flag surfaces in report.

## 9. Dependencies on Prior Phases

- Reads artifacts only. No breaking changes to existing modules.
- Daemon gets **one hook**: `self._watchdog.tick(cycle_outcome)` at end of cycle loop. Additive; backward compatible with existing tests.
- No changes to: orchestrator, task_runner, memory_client, dataset_builder, trainer, evolution.

## 10. Risks

| Risk | Mitigation |
|------|-----------|
| CODEBASE.md drift misleads agents | Refresh CODEBASE.md as Wave 0 task before plan execution |
| 1-week soak finds Phase 4 regression | Watchdog flags + abort conditions; soak produces debug artifacts for re-plan |
| Watchdog counter clashes with evolution merge counter | Watchdog reads evolution counter; doesn't mutate it. Single source of truth preserved |
| Acceptance false positive | All 6 criteria required; `overall=PASS` only if every `CriterionResult.passed` |
| Disk growth during soak (events.jsonl unbounded) | Out of scope — track as Phase 5.1 follow-up if hit |
| Flaky test causes soak abort | Test suite must pass at preflight AND acceptance; interim flakes logged but not aborts |

## 11. Critique Findings (Stage 4)

| Finding | Severity | Resolution |
|---------|----------|-----------|
| "Watchdog never stops daemon" — true hands-off vs safety tension | MEDIUM | Documented: watchdog is advisory. Human abort during soak is explicit protocol step, not automated. Matches yoyo-evolve philosophy (tests = only gate). |
| `patch_success_rate_trend` undefined when <100 episodes | LOW | Return `None` if episode count insufficient; report renders "n/a" |
| SC6 "no human intervention" — how enforced? | MEDIUM | Checked via `git log --author` on soak branch. Only allowed authors: agent commits (pattern match on commit message prefix from `commit_to_source`). |
| SC5 "stable or improving" — ambiguous | LOW | Define: `delta >= -0.02` (≤2% regression tolerated). Recorded in acceptance predicate. |

## 12. Coverage Assessment (Stage 5)

| ROADMAP Success Criterion | Spec Coverage |
|---------------------------|--------------|
| 1+ week unattended | SC1, `Watchdog`, `SOAK-PROTOCOL.md` |
| 10+ self-directed tasks | SC2, `AutonomyReport.self_directed_tasks_completed` |
| 1+ LoRA trained + merged | SC3, reads `registry.json` + `MergeManifest` |
| Test suite passes | SC4, preflight + acceptance gates |
| Metrics stable/improving | SC5, trend fields + tolerance |
| No human intervention | SC6, author filter |

All 6 criteria covered by concrete predicates with defined measurement sources.
