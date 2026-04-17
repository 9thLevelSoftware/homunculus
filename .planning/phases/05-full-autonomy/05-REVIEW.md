# Phase 5: Full Autonomy — Review Summary

## Result: PASSED (scoped to landed tooling)

**Review date**: 2026-04-16
**Cycles used**: 3 of 3 max
**Scope**: Phase 5 build (05-01, 05-02, 05-03 kickoff suite) + cycle-1 + cycle-2 remediations
**Out of scope**: Post-soak acceptance (05-03-2) and sign-off (05-03-3) — those require ≥7-day wall-clock soak execution on the target PC

Phase 5 is marked **review-passed for landed work**. Overall phase completion remains **pending soak execution**. The `/legion:review` outcome does NOT mark Phase 5 100% complete in ROADMAP.md — that happens only after `autonomy-accept` produces `overall=PASS` in a second legion session after the 7-day soak elapses on target PC.

## Review Panel (Dynamic)

| Agent | Division | Rubric Focus | Final Verdict |
|-------|----------|--------------|---------------|
| testing-reality-checker | Testing | Production Readiness (gates, fantasy detection) | PASS (cycle 2) |
| engineering-senior-developer | Engineering | Code Quality & Correctness (6-dim rubric) | APPROVE_WITH_COMMENTS (cycle 2) |
| support-infrastructure-maintainer | Infrastructure | Ops Reliability (Windows, Scheduler, Ollama, unattended survival) | PASS (cycle 3) |
| testing-evidence-collector | Testing | Evidence & Verification Gaps (defaults to 3-5 issues) | PASS (cycle 2) |

Panel diversity: 2 Testing + 1 Engineering + 1 Infrastructure. At least one Testing division agent (≥ constraint: 1).

## Findings Summary

| Severity | Cycle 1 | Cycle 2 | Cycle 3 | Resolved | Outstanding |
|----------|---------|---------|---------|----------|-------------|
| BLOCKER | 5 | 1 (new) | 0 | 6 | 0 |
| WARNING | 9 | 1 (new) | 0 | 10 | 0 |
| SUGGESTION | 6 | 5 (new) | 1 (new) | 6 deferred | 6 (Phase 6 follow-ups) |

All 5 cycle-1 BLOCKERs + 9 WARNINGs fixed in commit `e8417b5`.
1 cycle-2 BLOCKER + 1 WARNING fixed in commit `072c9ac`.
Cycle-3 spotted 1 non-blocking SUGGESTION (orphaned-daemon on watchdog-registration failure) — deferred.

## Findings Detail (Cycle 1)

| # | File | Severity | Issue | Fix Commit | Status |
|---|------|----------|-------|------------|--------|
| c1-1 | `scripts/phase5/bootstrap.ps1` | BLOCKER | `$MinSuccessful` did not gate exit code | `e8417b5` | Fixed |
| c1-2 | `scripts/phase5/stop-soak.ps1` | BLOCKER | `Stop-Process` does not trigger SIGINT | `e8417b5` | Fixed — stop-file pattern |
| c1-3 | `scripts/phase5/start-soak.ps1` | BLOCKER | ScheduledTask no `-Principal`/`-Settings` | `e8417b5` | Fixed — S4U + WakeToRun |
| c1-4 | new | BLOCKER | No Ollama watchdog | `e8417b5` | Fixed — new `ollama-watchdog.ps1` |
| c1-5 | `tests/test_autonomy.py` | BLOCKER | SC6 never exercised real git-log path | `e8417b5` | Fixed — 3 real-git tests |
| c1-6 | `scripts/phase5/start-soak.ps1` | WARNING | `-Force` bypassed gates silently | `e8417b5` | Fixed — `-SkipPrecheck`/`-SkipPreflight` + audit |
| c1-7 | `scripts/phase5/start-soak.ps1` | WARNING | 3s sleep missed delayed crash | `e8417b5` | Fixed — 60s stability window |
| c1-8 | `homunculus/autonomy/reporter.py` | WARNING | Dual success-def drift | `e8417b5` | Fixed — `EPISODE_SUCCESS_STATES` + `TASK_HISTORY_SUCCESS_STATES` constants |
| c1-9 | `homunculus/autonomy/reporter.py` | WARNING | `_count_candidates` substring `"merge" in status` | `e8417b5` | Fixed — `MERGED_CANDIDATE_STATES` allowlist |
| c1-10 | `homunculus/autonomy/precheck.py` | WARNING | Fractional `projected_loras_merged` | `e8417b5` | Fixed — `math.floor` on final ratio; field is `int` |
| c1-11 | daemon logs | WARNING | No log rotation | `e8417b5` | Fixed — disk-pressure check + README guidance |
| c1-12 | `scripts/phase5/daily-observe.ps1` | WARNING | Env var not visible in scheduled-task session | `e8417b5` | Fixed — User-scope fallback |
| c1-13 | `tests/test_autonomy.py` | WARNING | "property-based" watchdog test was deterministic | `e8417b5` | Fixed — renamed + added real concurrent-save test |
| c1-14 | `tests/test_autonomy.py` | WARNING | Daemon-watchdog integration didn't assert reporter surfaces flag | `e8417b5` | Fixed — strengthened assertion + reset-path test |

## Findings Detail (Cycle 2)

| # | File | Severity | Issue | Fix Commit | Status |
|---|------|----------|-------|------------|--------|
| c2-1 | `scripts/phase5/start-soak.ps1` + `stop-soak.ps1` | BLOCKER | Stop-file path divergence: relative default vs config-resolved absolute | `072c9ac` | Fixed — `runtime_dir` resolved via Python one-liner against config; persisted into `day-00-process.json`; stop-soak reads from process-file (3-tier fallback) |
| c2-2 | `scripts/phase5/start-soak.ps1` | WARNING | schtasks `/TR` fallback quoting broken | `072c9ac` | Fixed — broken advice removed; fail-closed with pointer to `SCHEDULING.md` |
| c2-3 | `scripts/phase5/start-soak.ps1` | SUGGESTION | Watchdog trigger overlap race | `072c9ac` | Fixed — `-MultipleInstances IgnoreNew` on watchdog settings only |
| c2-4 | `scripts/phase5/stop-soak.ps1` | SUGGESTION | Final snapshot after Force-kill showed misleading ABORT_RECOMMENDED | `072c9ac` | Fixed — `$gracefulExit` tracker; `final-snapshot.json` written directly |
| c2-5 | `homunculus/autonomy/precheck.py` | SUGGESTION | Docstring said "all rounded to 4 dp" but `_merged_soak` is `int` | `072c9ac` | Fixed — docstring clarifies integer floor |
| c2-6 | `homunculus/daemon.py` | SUGGESTION | `_consume_stop_file` lacked clarification on cross-crash persistence | `072c9ac` | Fixed — docstring now explains fail-safe intent |

## Deferred (Phase 6 or later)

These findings were SUGGESTION-level and acceptable at PASS time. Recorded for future cleanup.

| # | Source | Issue |
|---|--------|-------|
| d-1 | reality-checker c2 | `gates-bypassed.json` audit file is informational only; acceptance.py does not cite-through. Banner claims "acceptance may cite" — future version could integrate into SC evaluation |
| d-2 | senior-dev c2 | `_consume_stop_file` persists stop-file across crash (fail-safe, documented) — no action required |
| d-3 | senior-dev c1 | Existing reporter trend-window is 50 episodes; SC5 has implicit 100-episode minimum documented in SOAK-PROTOCOL §9 |
| d-4 | senior-dev c1 | `_gate_teacher_reachable` passes non-404 4xx codes silently — fail-closed on 404/401/403 suffices |
| d-5 | infra-maintainer c3 | Watchdog-registration failure after daily-observe succeeds leaves orphaned daemon + partial task state. Operator recovery via `stop-soak.ps1` is documented but not auto-executed |
| d-6 | evidence-collector c1 | Some negative-case acceptance tests use `_fixture_report(...)` helper rather than real git — SC6 tests now cover the real path, others remain fixture-based |

## Test Suite

| Metric | Cycle 1 Start | Cycle 1 End | Cycle 2 End | Final |
|--------|---------------|-------------|-------------|-------|
| Total tests | 316 | 326 | 326 | **326** |
| Failures | 0 | 0 | 0 | 0 |
| Skips | 4 (git-guards) | 4 | 4 | 4 |
| Time | ~15s | ~18s | ~16s | ~16s |

New tests added in cycle 1:
- `test_watchdog_persists_atomically_deterministic` (renamed)
- `test_watchdog_concurrent_save_tolerates_race` (real 2-thread)
- `test_watchdog_flag_clears_after_successful_cycle`
- `test_sc6_classifies_agent_commits_as_passed` (real git)
- `test_sc6_classifies_foreign_commits_as_failed` (real git)
- `test_sc6_evidence_includes_offending_shas`
- `test_preflight_teacher_reachable_fails_on_404`
- `test_report_trend_negative_when_quality_degrades`
- `test_check_metrics_stable_coverage_trend_none_branch`
- `test_reporter_keeps_records_missing_timestamp`
- `StopFileTests.test_daemon_respects_stop_file` (in `tests/test_daemon.py`)

## Scripts Modified

All 7 PowerShell scripts in `scripts/phase5/` pass `[Parser]::ParseFile` with 0 errors:
- `setup.ps1` (unchanged cycle 1+2)
- `bootstrap.ps1` (cycle 1 exit-code fix)
- `precheck.ps1` (unchanged since pre-review)
- `start-soak.ps1` (cycle 1 + cycle 2 fixes)
- `stop-soak.ps1` (cycle 1 + cycle 2 fixes)
- `daily-observe.ps1` (cycle 1 fixes)
- `ollama-watchdog.ps1` (new in cycle 1)

## Next Action

Phase 5 review complete — **tooling approved for target-PC deployment**.

Post-soak closeout (after ≥7-day wall-clock soak on target PC):
1. Operator runs `scripts/phase5/stop-soak.ps1`
2. Operator runs `python -m homunculus.cli autonomy-accept ...`
3. Operator opens a new legion session and runs `/legion:build --phase 5` (or directly dispatches QA Verification Specialist for 05-03-2 + 05-03-3)
4. Session 2 produces `05-ACCEPTANCE.md` + updates ROADMAP/STATE to mark Phase 5 100% complete if `overall == PASS`

## Commit Trail

| Commit | Purpose |
|--------|---------|
| `a5502e7` | Plan 05-01 landing (autonomy package + watchdog) |
| `c27353d` | Plan 05-02 landing (preflight + acceptance + CLI + 15 tests) |
| `63ef4d5` | Plan 05-03 partial (SOAK-PROTOCOL.md + throughput gate refusal) |
| `4107684` | Evolution thresholds lowered (50→10, 5→2) for soak feasibility |
| `311c1be` | Portable soak kickoff suite (7 PS scripts + seed tasks + README) |
| `b4711b7` | `autonomy-precheck` CLI subcommand + 8 tests |
| `e3d16bc` | start-soak.ps1 output visibility fix |
| `e8417b5` | Review cycle 1 fixes (14 items across Python + PS) |
| `072c9ac` | Review cycle 2 fixes (4 items across PS + 2 Python doc nits) |
