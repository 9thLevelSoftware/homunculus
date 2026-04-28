# Homunculus E2E Audit — Master Gap Report

**Date:** 2026-04-16
**Scope:** Comprehensive (module sweep + runtime trace + test-quality + schema consistency + plan cross-ref)
**Method:** 5 parallel read-only audit agents, each owning one bucket. No code changes performed.

---

## Headline

- **326/326 tests pass. 0 import errors. 0 schema drift.** HEAD is runnable.
- **21 of 22** baseline-plan tasks are **resolved at HEAD**. Task 22 (docs/REVIEW.md) is a process gap only.
- **4 new BLOCKERs** discovered beyond the baseline plan. All in orchestrator/autonomy-reporter/preflight surface.
- **6 new SILENT-DROPs**. Mostly config/retry/offline-fallback holes.
- **~10 POLISH items**.

---

## New BLOCKERs (not covered by existing plan)

| # | Bucket | File:Line | Defect (class) | One-liner |
|---|--------|-----------|----------------|-----------|
| B1 | orchestrator | `task_runner/runner.py:178` | a/g | `commit_to_source` uses `git add -A` → any dirty file in source repo gets swept into auto-commit alongside patch. No allowlist, no patch-derived staging. |
| B2 | orchestrator | `orchestrator/loop.py:250` | f | Episodes with `outcome=="error"` persist to `episodes.jsonl` but **skip the `episode_completed` event** → downstream event-based metrics undercount errors. |
| B3 | autonomy-reporter | `autonomy/reporter.py` `_count_self_directed` | a | Matches `source in {"generated","resonance"}` but producers emit `"introspection"` / `"user"`. Every soak reports `self_directed_tasks_completed=0`. **SC2 always fails.** |
| B4 | autonomy-preflight | `autonomy/preflight.py:_gate_task_queue_ready` | c | Fallback is tautological pass (`TaskGenerator(store=None)` cannot raise). **Empty-queue soaks pass preflight and idle for 7 days.** |

---

## New SILENT-DROPs

| # | Bucket | File | Defect | One-liner |
|---|--------|------|--------|-----------|
| S1 | orchestrator/teacher | `orchestrator/teacher.py` + `config.py` | b | No retry/backoff on 429/5xx/URLError. `TeacherSettings` has no retry field — user cannot express intent. Transient = hard error. |
| S2 | memory_client | `memory_client/engram.py` | c | No offline fallback. Engram outage = every in-flight episode errors at `failure_stage="recall"`. Tests cement this. |
| S3 | config | `homunculus.example.toml [evolution]` | b/h | Ships only 5 of 11 supported keys. Missing: `enabled`, `max_merge_attempts`, `validation_timeout_seconds`, `coherence_prompt`, `coherence_min_tokens`, `merge_backend`. Operators blind to defaults. |
| S4 | config | `config.py _validate_interval` | b | Coerces 0 / negative introspection intervals to defaults without warning. User intent flipped silently. |
| S5 | config | `config.py _warn_on_unknown_keys` | b | Unknown-key guard scoped only to `[evolution]`. Typos in `[teacher]`/`[student]`/`[daemon]`/`[memory]` silently ignored. |
| S6 | autonomy-accept | `cli.py autonomy-accept --soak-log` | h | Orphan CLI flag; argument parsed but never read. |

---

## POLISH (ordered by blast radius)

1. `evolution/merge.py:280` — coherence token count uses `split() * 1.3` instead of real tokenizer. Band-approx only.
2. `evolution/merge.py:482` — MLX merge averages deltas by `1/len(loras)`; mergekit backend has weighted. Not wrong, but divergent behavior between backends.
3. `storage.py:144-169` — `save_registry` non-atomic. Asymmetric with atomic `update_merge`. Latent race even under single-threaded daemon on SIGTERM.
4. `evolution/merge.py:388-441` — legacy `_generate_mergekit_config` dead code still covered by 4 tests, inflating coverage signal.
5. `evolution/merge.py:310-312` — `_bake_lora_into_base` cache-hit path untested; could return stale baked checkpoint.
6. `autonomy/watchdog.py` — `record_task_revert` defined but never called from `Daemon.run_once` → `repeat_revert:*` flags cannot fire.
7. `policy.py` — guardrail regex not compiled at `load_config`. Bad patterns crash first episode instead of at launch.
8. `dataset_builder/builder.py:156` — `_build_snapshot` double-composes payloads (re-invokes `_compose_snapshot_payloads`, discards metadata arg). Redundant disk reads; RuntimeErrors can fire twice.
9. `dataset_builder/builder.py _load_seed_payloads` — missing seed file surfaces as "no self-generated samples" (wrong downstream error).
10. `orchestrator/loop.py` — `_auto_commit` failure not recorded on episode record (logs only).
11. `orchestrator/student.py` — crashes if `mlx-lm` binary missing. No graceful degrade to a clear config-error.
12. `orchestrator/student.py` — `subprocess.TimeoutExpired` not caught in one branch.
13. orchestrator — empty-patch episodes (`plan.candidate_patch == ""`) silently accepted by verification if tests still pass.
14. `memory_client/base.py MemoryContract` — not `@runtime_checkable`, so `isinstance` checks at runtime fail soft.

---

## Baseline Plan Cross-Reference

| Status | Count | Notes |
|--------|-------|-------|
| Resolved at HEAD | 21/22 | Tasks 1–21 all verified with file:line or test evidence across 4 buckets. |
| Open (process-only) | 1/22 | Task 22: `.planning/phases/04-weight-evolution/04-REVIEW.md` missing. Zero code impact; STATE.md already advanced to Phase 5. |
| Extended | Task 14 | Integration test "introspection → task_generator → daemon executes" — scheduler wired, producers wired, but **the wired E2E test asserting loop closure is not landed**. Plan claimed Task 13 done; Task 14 (the actual closure verification) is the gap. |

---

## Flows Traced — Status

| Flow | Status |
|------|--------|
| Episode happy path (assess→…→curate) | PASS |
| Episode error path | **B2** — event-log undercount |
| Auto-commit on accepted | **B1** — over-broad `git add -A` |
| Memory offline fallback | **S2** — absent |
| Teacher transient-error retry | **S1** — absent |
| Daemon cycle (introspection→tasks→execute→evolution) | PASS |
| Lock / corrupt PID / stale PID | PASS (plan Task 8) |
| Task queue restart safety | PASS (plan Task 17) |
| Suggestion archival on all terminal outcomes | PASS (plan Task 9) |
| Autonomy preflight gating | **B4** — queue-ready gate tautological |
| Autonomy reporter SC2 | **B3** — source-name mismatch |
| Merge (MLX + mergekit-via-PEFT) happy path | PASS |
| Validation fail-closed without backend | PASS (plan Task 5) |
| Lineage parent aggregation from all source LoRAs | PASS (plan Task 10) |
| `register_lora` wired from `promote_candidate` | PASS (plan Task 15) |
| Introspection rotating scheduler | PASS (plan Task 13) |
| Introspection → task_generator → daemon (closed loop) | **GAP** — Task 14 test absent |
| Dataset snapshot build | PASS (with double-compose polish) |
| CLI subcommands (init-artifacts / doctor / run-episode / apply-episode / train-sft / autonomy-precheck / autonomy-accept) | PASS (S6 flag orphan) |
| Config round-trip | PASS (S3/S4/S5 silent-drop surface) |

---

## Test Quality

- 15 test files. 11 with zero mock tokens exercise real `ArtifactStore`, `TaskRunner`, `OrchestratorLoop`, real git worktrees.
- `test_evolution.py` — 72 mock tokens, but all at subprocess boundary per Task 20. Not an SUT-mocking defect.
- **No class-(e) defects** (SUT-mocking) detected.
- Coverage gap: `_bake_lora_into_base` cache-hit path, `_validate_canary` with empty command list, empty-patch episode acceptance.

---

## Schema Drift

**None.** `EpisodeRecord`, `AdapterManifest`, `MergeManifest`, `LineageRecord`, `TaskQueueEntry`, `SFTSample`, `DPOSample` all round-trip cleanly vs. their JSONL / JSON on-disk representations. `MergeManifest.status` docstring tolerates legacy `"complete"` — correct handling.

---

## Recommended Next Wave

Close B1–B4 + S1–S6 (10 items) before more Phase 5 soak work; these contaminate the soak acceptance signal:
- B3 alone makes every SC2 metric zero
- B4 makes preflight a no-op when queue empty
- B1 makes auto-commit unsafe in any multi-file edit session
- S2+S1 make the system brittle against common transient failures

**Suggested grouping for next plan:** `autonomy-signal-fidelity` (B3+B4+B6 watchdog wiring) → `orchestrator-safety` (B1+B2+S1+S2) → `config-hygiene-v2` (S3+S4+S5+S6).

---

## Sub-audit files

- `01-orchestrator-taskrunner-teacher-student-memory.md`
- `02-daemon-autonomy-taskgen-suggestions-policy.md`
- `03-evolution-trainer.md`
- `04-introspection-dataset-storage-config-cli.md`
- `05-runtime-tests-schema-crossref.md`
