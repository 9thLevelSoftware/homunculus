# Audit Bucket 2 — Daemon / Autonomy / Task-Gen / Suggestions / Policy

**Date:** 2026-04-16
**Scope:** `homunculus/daemon.py`, `homunculus/autonomy/*`, `homunculus/task_generator/*`, `homunculus/suggestions.py`, `homunculus/policy.py`, and matching tests
**Commit at audit time:** HEAD = `54c7ddb` (phase 5 review passed, scoped to landed tooling)
**Audit mode:** READ-ONLY — no code modified

---

## Summary

Most of the "mechanical defects" the existing plan (`2026-04-16-spec-alignment-and-merge-correctness.md`) called out in this bucket (Tasks 7, 8, 9, 12, 13, 17) are **already resolved in HEAD** — the fixes landed with defensive parsing, atomic writes, lock-corruption guards, broadened archival outcomes, wired introspection scheduling, and task-queue restart safety. Tests exist for each.

However, the audit surfaced **three new defects** the existing plan does not cover, one of them a soak-acceptance **BLOCKER**:

1. **BLOCKER — SC2 source-value vocabulary mismatch** between the autonomy reporter and the actual task producers. `SuggestionReader` emits `source="user"`, `TaskGenerator` emits `source="introspection"`, but `reporter._count_self_directed` matches `source in {"generated", "resonance"}` and `_count_suggestion_tasks` matches `source == "suggestion"`. The result is that `self_directed_tasks_completed` and `suggestion_tasks_completed` are structurally **zero** under real operation, so SC2 (≥10 self-directed tasks) will fail the acceptance verdict regardless of real progress.
2. **SILENT-DROP — `task_queue_ready` preflight gate passes on empty environment.** The gate's "can the generator be constructed?" fallback always succeeds (plain `TaskGenerator(store=None)` never raises). A soak started with zero queued suggestions, zero prior introspection, and zero seed material passes preflight and then idles for 7 days, failing SC2 structurally.
3. **POLISH / unwired — `Watchdog.record_task_revert()` is defined but never called.** Repeated-revert flagging (spec §5 `repeat_revert:{task_id}`) is wired into `active_flags()` but no call site in `Daemon.run_once` / `_finalize_cycle` feeds it. The method and threshold exist for a signal that is never emitted.

Two lesser fail-open surfaces were also flagged (policy regex not validated at config load; `suggestions.archive` trusts its `filename` argument). Plus one orphaned CLI arg (`autonomy-accept --soak-log`). Details below.

**Net verdict on this bucket:** landed Phase 5 work is solid; the severity-1 risk is the reporter vocabulary mismatch, which silently invalidates the entire Phase 5 acceptance loop.

---

## Cross-reference: Existing Plan Tasks Touching This Bucket

| Plan Task | Subject | Status in HEAD | Evidence |
|---|---|---|---|
| **Task 7** | Defensive state parsing in `_get_consecutive_merge_failures` + atomic writes | **RESOLVED** | `homunculus/trainer/manager.py`: `_get_consecutive_merge_failures` catches `JSONDecodeError`/`OSError`, rejects non-int/bool/negative values, defaults to 0; `_set_consecutive_merge_failures` uses temp file + `os.replace`. Same hardening echoed in `Daemon.save_state` (daemon.py:179-188), `Watchdog.save` (watchdog.py:91-118). |
| **Task 8** | Lock-file race fix in `daemon.py` | **RESOLVED** | `Daemon.acquire_lock` (daemon.py:190-224) refuses to overwrite a corrupt PID file (ValueError → return False, logged), distinguishes "vanished mid-read" (FileNotFoundError → proceed) from "unreadable" (OSError → refuse), verifies ownership in `release_lock` (daemon.py:226-245). `tests/test_daemon.py::LockSafetyTests` (lines 529-614) cover corrupt content, vanishing file, and non-owner release. |
| **Task 9** | Suggestion archival on blocked/error outcomes | **RESOLVED** | `Daemon.run_once` (daemon.py:504-513) archives on `outcome in {"accepted","reverted","blocked","error"}`. Archive failures are logged and swallowed (won't crash cycle). `tests/test_daemon.py::SuggestionArchivalTests` (lines 616-760) cover all four outcomes + archive-failure-tolerance. |
| **Task 12** | Misc small fixes — `append_to_queue` try/except for merge-failure enqueue | **RESOLVED** | `Daemon._process_merge_result` (daemon.py:656-671) wraps `append_to_queue` in try/except; `trainer.reset_merge_failure_count()` only runs when enqueue succeeds. `tests/test_daemon.py::test_failure_counter_not_reset_when_enqueue_fails` (line 1354) locks the contract. (Other Task-12 items — `comparative.py` types, `coverage.py` path, `lineage.py` imports — are outside this bucket.) |
| **Task 13** | Wire `IntrospectionScheduler` into `Daemon` | **RESOLVED** | `Daemon.__init__` (daemon.py:97-103) constructs the scheduler iff `introspection.enabled AND store is not None`. `Daemon._run_introspection` (daemon.py:274-303) runs due modes each cycle and persists to `store.append_introspection_result`, failures logged-and-swallowed (opportunistic). `Daemon.run_once` (daemon.py:456) calls it **before** `get_pending_tasks` so fresh signals feed generation. `tests/test_daemon.py::DaemonIntrospectionIntegrationTests` (762-875) and `DaemonE2EIntrospectionToTaskTests` (876-1045) cover it end-to-end. |
| **Task 17** | Wire daemon to use task queue for restart safety | **RESOLVED** | `Daemon.get_pending_tasks` (daemon.py:365-450) loads `status="pending"` from queue first, generates fresh tasks, persists each fresh task via `store.append_to_queue`, deduplicates, returns prioritized list. `run_once` marks in-progress / completed / failed transitions, `_archive_queue_safely` sweeps at end. `tests/test_daemon.py::TaskQueuePersistenceTests` (1046-1210) cover persistence + restart pick-up. |

All six plan tasks in this bucket look **resolved and tested**. Nothing superseded, nothing never-accurate.

---

## New Findings (not in existing plan)

### Finding N-01 — [BLOCKER, class (b) Silent-drop config key / (h) Orphaned field]
**SC2 vocabulary mismatch: reporter classifies no real task as self-directed or suggestion**

The reporter's source-classification buckets (`homunculus/autonomy/reporter.py:233-278`) pair:

```python
_count_self_directed:   source in {"generated", "resonance"}
_count_suggestion_tasks: source == "suggestion"
```

against a soak-run population where **every actual task** has one of two values:

- `TaskGenerator` emits `source="introspection"` — 14 call sites (`homunculus/task_generator/generator.py:257, 269, 281, 294, 361, 382, 458, 474, 492, 506, 573, 585, 597, 887`).
- `SuggestionReader` emits `source="user"` (`homunculus/suggestions.py:98`).

Nothing in the codebase ever sets `source` to `"generated"`, `"resonance"`, or `"suggestion"`. Consequence:

- `AutonomyReport.self_directed_tasks_completed` is structurally **0** for a real soak.
- `AutonomyReport.suggestion_tasks_completed` is structurally **0** for a real soak.
- `acceptance._check_self_directed_tasks` (acceptance.py:122) requires `>= MIN_SELF_DIRECTED_TASKS` (10) → SC2 **always fails**.
- Overall `AcceptanceVerdict.overall == "FAIL"` regardless of agent performance (acceptance.py:91 — `all(c.passed …)`).

This is the defining "spec drift" in the bucket: the spec §2 vocabulary (`generated` / `resonance` / `suggestion`) never reached the producers. Either the producers must adopt the spec vocabulary, or the reporter must translate `{"introspection"} → self_directed`, `{"user"} → suggestion`.

Severity **BLOCKER** — Phase 5 acceptance cannot pass with this mismatch in place.

**Not caught by tests:** `tests/test_autonomy.py::AcceptanceTests::test_acceptance_all_criteria_met` (line 735) constructs an `AutonomyReport` with `self_directed_tasks_completed=10` **directly**, bypassing the reporter's counting code. No end-to-end test wires a queue through `archive_completed_tasks` → `generate_report` → `validate_acceptance`.

---

### Finding N-02 — [BLOCKER, class (c) Fail-open validator]
**`task_queue_ready` preflight gate passes with zero real work available**

`homunculus/autonomy/preflight.py:239-290` — `_gate_task_queue_ready`:

```python
if pending > 0:
    return PASS
# Dry-run: can the generator be constructed?
try:
    TaskGenerator(store=None)
except Exception:
    return FAIL
return PASS  # "Queue empty; generator available to synthesize work."
```

`TaskGenerator.__init__` is a two-line assignment (`generator.py:42-49`); it cannot raise. Therefore the second branch is a **tautological pass** — every environment that has the `homunculus` package installed satisfies this gate, including one with:

- zero queued tasks,
- zero markdown suggestions in `suggestions_dir`,
- zero prior introspection results in `traces/introspection.jsonl` (which is what the generator actually consumes).

Starting a 7-day soak in that state would yield all-idle cycles, zero episodes, and then SC1 would scrape through on uptime, while SC2/SC3/SC5 all fail. The preflight gate was supposed to prevent exactly this.

**Fix direction:** the gate must assert either `pending > 0` OR `(suggestions_dir has pending files)` OR `(recent introspection findings can drive a dry-run generation that yields ≥1 task)`. Failing that, it is a rubber stamp.

Severity **BLOCKER** for "Phase 5 preflight is reliable before burning a 7-day run".

---

### Finding N-03 — [POLISH / SILENT-DROP, class (a) Unwired integration / (h) orphaned field]
**`Watchdog.record_task_revert()` never called; `repeat_revert:{task_id}` flags cannot fire**

Signals defined in spec §5 and implemented in `homunculus/autonomy/watchdog.py`:

- `cycle_failure:{N}` — fed by `Daemon._finalize_cycle → watchdog.tick(outcome)` (wired; see `tests/test_autonomy.py::DaemonWatchdogIntegrationTests`).
- `merge_failure:{N}` — fed by `Daemon._finalize_cycle → watchdog.merge_failures(…)` (wired).
- `repeat_revert:{task_id}` — fed by `watchdog.record_task_revert(task_id)` — **no call sites** anywhere in the tree (only its own definition at watchdog.py:144 shows up in a repo-wide grep).

Consequence: the daemon can revert the same task five times in a row, and the report will never surface a `repeat_revert:*` flag. The dataclass field `repeated_task_reverts` stays at `{}` forever. The `FAILURE_THRESHOLD_TASK_REVERT = 3` class constant is dead.

This is both "unwired integration" (hook exists, call site missing) and "orphaned dataclass field" (the dict never receives a mutation). Low-impact because the watchdog is advisory, but the spec §5 surface is incomplete.

**Fix direction:** in `Daemon.run_once`, when `outcome == "reverted"`, call `self._watchdog.record_task_revert(task.task_id)` before the existing archive/queue-update path. Add a test that simulates three reverts of the same task and asserts `repeat_revert:task-xxx` shows up in `_watchdog.active_flags()`.

Severity **POLISH** (advisory signal missing, not a blocker for acceptance).

---

### Finding N-04 — [POLISH, class (c) Fail-open validator]
**Guardrail regex patterns are not compiled / validated at `load_config` time**

`homunculus/config.py:_parse_rules` (one-liner) constructs `PatternRule(pattern=..., message=...)` directly from the TOML — it never calls `re.compile(pattern)` to validate syntax. The first invalid pattern silently lives in the config until the **first** orchestrator episode calls `GuardrailEngine.evaluate` (`homunculus/policy.py:16-26`), at which point `re.search` raises `re.error` on that specific rule and the whole cycle fails at the `preflight` stage of the orchestrator loop (`homunculus/orchestrator/loop.py:91`).

This is fail-open in the configuration layer (config loads fine, deployment proceeds) but fail-loud-and-noisy at runtime. Preferred: validate regex syntax in `_parse_rules` so a broken config cannot launch.

Severity **POLISH**. A related question: `[guardrails]` with no `block_patterns` / `warn_patterns` is a legal config that lets every prompt through. That is by design (guardrails opt-in), but the empty-by-default behavior deserves a note in `homunculus.example.toml` — not a code defect.

---

### Finding N-05 — [POLISH, class (h) Orphaned CLI arg]
**`autonomy-accept --soak-log` is declared but never consumed**

`homunculus/cli.py:342-346`:

```python
accept_parser.add_argument(
    "--soak-log",
    default=None,
    help="Path to soak-log directory (reserved; report is regenerated live).",
)
```

`cmd_autonomy_accept` (cli.py:231-256) never reads `args.soak_log`. The help text admits it's "reserved". That's harmless but user-confusing — an operator script passing `--soak-log` will silently have no effect. Either remove the arg or wire it (e.g., use it to compute `since` for `generate_report`, so SC1 uptime reflects the actual soak start rather than whenever the daemon happened to persist `daemon_state.started_at`).

Severity **POLISH**.

---

### Finding N-06 — [POLISH, defensive]
**`SuggestionReader.archive` trusts its `filename` argument**

`homunculus/suggestions.py:58-67` — `archive(filename, outcome)` builds `source = self.suggestions_dir / filename` with no `Path(filename).name` normalization. Today every call site passes `md_file.name` (which is already unpathed), so the risk is purely hypothetical. Still, a future caller that passes a full path from arbitrary metadata (e.g. `record.patch_path`) could rename a file outside `suggestions_dir`. Preferred: `source = self.suggestions_dir / Path(filename).name` and reject absolute paths.

Severity **POLISH**.

---

## Flows Traced

### Daemon cycle — `run_once()` in `homunculus/daemon.py:452-544`

1. `_run_introspection()` (daemon.py:274-303) → `IntrospectionScheduler.run_due_modes(cycle_number=state.cycles_completed)` → `store.append_introspection_result(result)` for each. Guarded by `introspection_scheduler is not None AND store is not None`. All exceptions are caught and logged; scheduler failures never crash the cycle. **Task 13 resolved.**
2. `get_pending_tasks()` (daemon.py:365-450) — four steps in order:
   - Load `store.load_queue()` to surface pending tasks from previous cycles (restart-safe). `queued_ids` tracks what's already queued.
   - Call `task_generator.generate_from_introspection(results, max_tasks=3)` for fresh introspection-derived tasks (guarded by `task_generator AND introspection_results`).
   - Call `suggestion_reader.read_pending_with_resonance(...)` if introspection exists, else `read_pending()` without resonance boost.
   - Persist every fresh task not already in queued_ids via `store.append_to_queue(entry)`. Persistence failures logged but not fatal — execution proceeds.
   - Combine `carry_over + fresh_tasks`, return `prioritizer.prioritize(all_tasks, introspection_results)`.
3. For each prioritized task up to `max_episodes_per_cycle`:
   - `_mark_queue_status(task_id, status="in_progress", increment_attempts=True)` **before** execution (so a crash mid-episode leaves an honest "in_progress" record).
   - Call `orchestrator.run_episode(task.to_task_request(target_workspace))`.
   - On success: `_mark_queue_status(..., status="completed", outcome=outcome)`. If `outcome in {"accepted","reverted","blocked","error"} AND task.context["filename"]`, call `suggestion_reader.archive(filename, outcome)`. **Task 9 resolved.**
   - On exception: `_mark_queue_status(..., status="failed", last_error=str(e))`, sweep archive, return `DaemonCycleResult(status="error", ...)`.
4. After tasks: `_check_evolution()` (non-blocking background merge thread — single-flight guard; see daemon.py:546-591 and `_process_merge_result` at 610-671 for the deferred-result pattern).
5. `_archive_queue_safely()` sweeps `status in {"completed","failed"}` into `task_history.jsonl`.
6. (In `run_continuous`) `_finalize_cycle(result)` → `_watchdog.tick(result)` → `_watchdog.merge_failures(_read_merge_failure_count())` → `_watchdog.save()`.

**Note:** `_watchdog.record_task_revert(task_id)` is **not** called in step 3, so Finding N-03 fires.

### Autonomy precheck / preflight → daemon

`cli.py:cmd_autonomy_precheck` returns exit 2 on verdict != PASS; `cmd_autonomy_preflight` returns exit 1 on `passed == False`. These are **advisory CLI commands** — no automatic gating is applied to `Daemon.run_continuous` or `run_once`; it is the **operator's responsibility** to run preflight before launching soak, per `scripts/phase5/start-soak.ps1` (not in this bucket but referenced by the plan). This split is by design — the daemon does not re-run preflight on restart. The concerning surface is Finding N-02: a failing environment can still get a PASS from the gate.

### Task_generator → queue

- `TaskGenerator.generate_from_introspection` produces tasks with fresh UUIDs (`_make_task_id` at generator.py:193) so no dedup-by-id at generation time. Dedup is handled downstream in `TaskPrioritizer._deduplicate` via 100-char prompt-prefix key (prioritizer.py:196-221).
- `TaskGenerator.generate_merge_failure_task` (generator.py:847-898) produces a task with a single `merge-fix-{uuid}` ID and no cross-cycle dedup. That means repeated merge failures within a single cycle could in principle enqueue multiple failure-investigation tasks, but `_check_evolution` only enqueues once per cycle (after the background merge finishes), so in practice the dedup isn't needed.
- All enqueue paths go through `store.append_to_queue` (storage.py:301). The prioritizer is consulted on **every** `get_pending_tasks` call — so queue persistence and ranking are both wired.

### Suggestion ingest

`SuggestionReader.read_pending` (suggestions.py:41-56) globs `*.md` under `suggestions_dir`, skips `.`-prefixed files, calls `_parse_suggestion` which requires a `## What` section (returns None otherwise — silent skip). Emits `GeneratedTask(source="user", context={"filename": md_file.name}, …)`. The `filename` in context is consumed by `Daemon.run_once` for the archive step. Resonance boost via `_extract_keywords` operates over 10 keyword buckets; resonance score `= Jaccard similarity * exponential-weight-decay over recent results`.

**Poison-input handling:** no file-size guard. A giant suggestion file will be fully loaded into memory by `md_file.read_text`. Not a security hole (suggestions are trusted operator input) but a potential DoS. Also no regex safeguards on the `Priority:` field — `.strip().upper()` handles stray whitespace, but `_extract_section` is regex-based and could miss `## What` that uses tab-prefixed heading. Low risk.

---

## Test Coverage Gaps

| Gap | Missing Assertion | Proposed Test |
|---|---|---|
| **G-01** (Finding N-01) | No end-to-end test that exercises the real task source strings and the reporter's counts in the same run. | Add to `tests/test_autonomy.py`: build a real queue via `store.append_to_queue`, mark some `completed`+`outcome="accepted"` with both `source="introspection"` and `source="user"`, archive to `task_history.jsonl`, then call `generate_report` and assert `self_directed_tasks_completed > 0`. This will **FAIL** today, proving the vocabulary mismatch. |
| **G-02** (Finding N-02) | `test_preflight_task_queue_ready` does not exist. `PreflightTests` only covers config/worktree/tests/teacher gates. | Add: empty `task_queue.jsonl`, empty `suggestions_dir`, empty `traces/introspection.jsonl` → expect `run_preflight(settings).gates["task_queue_ready"].passed == False`. This will **FAIL** today. |
| **G-03** (Finding N-03) | No test exercises `Watchdog.record_task_revert` from inside `Daemon.run_once`. Only `test_autonomy.WatchdogTests::test_watchdog_increments_and_resets_on_success` exercises `tick()` directly. | Add: stub orchestrator to return `outcome="reverted"` three times for the same task_id, call `daemon.run_once()` three cycles, assert `daemon._watchdog.active_flags()` contains `repeat_revert:task-xxx`. Will **FAIL** until the call site is added. |
| **G-04** (Finding N-04) | No test that `load_config` rejects a config with an invalid regex in `[guardrails] block_patterns`. | Add: write a config with `block_patterns = [{ pattern = "[unclosed", message = "x" }]`, assert `load_config(path)` raises (or at minimum `GuardrailEngine.evaluate` on any input raises `re.error` — which today it does, but not at load time). |
| **G-05** | Reporter's `_count_self_directed` uses `outcome == "success"` or `"accepted"` via `TASK_HISTORY_SUCCESS_STATES`. No test asserts that the daemon's on-disk queue entry carries `outcome="accepted"` (not `outcome="success"`) after a real accepted episode. Could mask if outcome serialization ever drifts. | Add: real store, run one episode with a real orchestrator stub that returns `outcome="accepted"`, load `task_history.jsonl`, assert the archived entry's `outcome` equals `"accepted"` (the EpisodeRecord literal). |
| **G-06** | `test_archive_failure_does_not_crash_cycle` (test_daemon.py:746) covers the exception path, but no test covers the **silent** branch where `task.context["filename"]` is empty (i.e., introspection-generated tasks have no filename). Could regress: a future change could unconditionally archive by task_id, which would break for introspection tasks. | Add: run an introspection-sourced task with empty context to accepted outcome, verify `suggestion_reader.archive` is **not** called. Locks the existing (correct) behavior. |
| **G-07** (Finding N-05) | No test that passing `--soak-log` to `homunculus autonomy-accept` has any observable effect on the generated report. | Either remove the arg (and its help text) or wire it; then test. |

---

## Flows and Behaviors Verified as Correct (No Finding)

For completeness — these were inspected and are behaving as spec requires:

- **Atomic persistence** (`Daemon.save_state`, `Watchdog.save`, `trainer._set_consecutive_merge_failures`) — all use temp-file + `os.replace`.
- **Corrupt-state recovery** (`Daemon.load_state`, `Watchdog.load`, `trainer._get_consecutive_merge_failures`) — all default to fresh zero-valued state with a WARNING log, never crash the daemon.
- **Stop-file mechanism** (`Daemon._check_stop_file` / `_consume_stop_file` — daemon.py:133-167) — idempotent, OS-tolerant, survives crash.
- **Background merge single-flight** (`_check_evolution` + `_run_merge_in_thread` + `_process_merge_result`) — next cycle always processes the prior result, never loses a merge outcome even on crash.
- **Reporter graceful-missing contract** (reporter.py:73) — every "missing file" path returns zero-valued fields, not exceptions.
- **Acceptance predicates fail closed** — SC4 re-runs tests, SC6 treats git errors/timeouts as `passed=False`, SC5 fails when trend is `None`.
- **Prioritizer dedup / FIFO tiebreaker** (prioritizer.py:196-221) — 100-char prefix key (lowercased, stripped), higher-priority duplicate wins, ties broken by `created_at` ascending.
- **Policy (`GuardrailEngine.evaluate`)** — wired into `orchestrator/loop.py:91`; a regex match on `block_patterns` produces `outcome="blocked"` → `review_status="rejected"` → daemon archives the suggestion.
- **Task queue restart safety end-to-end** — verified via `tests/test_daemon.py::TaskQueuePersistenceTests` (three cases: persistence, archival, pick-up on restart).

---

## Severity-Ranked Remediation List

| Rank | Finding | Severity | Why |
|---|---|---|---|
| 1 | **N-01** SC2 vocabulary mismatch | BLOCKER | Silently invalidates Phase 5 acceptance verdict on every real soak. |
| 2 | **N-02** `task_queue_ready` fail-open | BLOCKER | Allows a guaranteed-idle soak to pass preflight and burn 7 days. |
| 3 | **N-03** `record_task_revert` unwired | POLISH | Advisory signal missing; no functional impact on soak pass/fail. |
| 4 | **N-04** Policy regex not validated at config load | POLISH | First-episode crash instead of launch-time rejection. |
| 5 | **N-05** `--soak-log` orphan arg | POLISH | Cosmetic; operator UX. |
| 6 | **N-06** `archive()` filename not normalized | POLISH | Defensive hardening, no current risk. |

Tests gaps G-01 through G-03 should land alongside the fixes for N-01, N-02, and N-03 respectively, using the TDD pattern the existing plan already documents ("write the failing test first, verify it fails, then fix").
