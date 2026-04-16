# Phase 4: Weight Evolution — Review Summary

## Result: PASSED (after spec-fix branch)

- **Cycles**: 1 review cycle + 1 spec-fix branch (this PR)
- **Reviewers**: testing-reality-checker, testing-qa-verification-specialist, engineering-ai-engineer
- **Date**: 2026-04-16
- **Fix branch**: `fix/spec-alignment` (22 commits, `2b7128e..339dced`)
- **Plan**: `docs/superpowers/plans/2026-04-16-spec-alignment-and-merge-correctness.md`

## Findings Summary

| Severity   | Found | Resolved | Deferred |
|------------|-------|----------|----------|
| BLOCKER    | 5     | 5        | 0        |
| WARNING    | 16    | 16       | 0        |
| SUGGESTION | 4     | 4        | 0        |

The initial audit + `/legion:review` cycle 1 surfaced 5 BLOCKERs (merge
pipeline broken despite passing tests) and 16 WARNINGs (spec drift,
validation false-positives, state-file brittleness, silent storage
races). The spec-fix branch turned each finding into a concrete task
(Tasks 1–22) with code, tests, and a commit.

## Findings Detail

| Finding | Severity | Fix | Commit |
|---------|----------|-----|--------|
| `pyproject.toml` missing `evolution`, `introspection`, `task_generator` subpackages → `pip install -e .` ships a broken package | BLOCKER | Task 1 | `2b7128e` |
| `.pyc` files tracked, `traces/` not gitignored | WARNING | Task 2 | `54a4aa1` |
| `homunculus.example.toml` missing `daemon.target_workspace` | WARNING | Task 3 | `0a1b0c0` |
| `[evolution]` config keys silently dropped — TOML `auto_merge_after_loras` / `merge_method` never reached code | WARNING | Task 4 | `0dd7f0c` |
| `MergeValidator` returned `passed=True` when no inference backend was installed (silent pass on garbage weights) | WARNING | Task 5 | `622f238` |
| Coherence stage: prompt-slicing off-by-one, `_is_repetitive` over-counts, MLX exceptions swallowed, non-deterministic sampling, model-cache leak | WARNING | Task 6 | `2056b91` |
| `evolution_state.json`: non-atomic write, non-int value crashed trainer with `TypeError` | WARNING | Task 7 | `1068c38` |
| Daemon single-instance lock: TOCTOU race, Windows pid-liveness wrong, ownership not enforced on release | WARNING | Task 8 | `0d4898a`, `1808454` |
| `daemon.run_once` archived suggestions only on the success path — rejected/reverted tasks leaked back into the queue | WARNING | Task 9 | `ebeebfd` |
| `lineage.py`: `register_merge` only walked the first source LoRA's parents / episodes, truncating ancestry on multi-LoRA merges | WARNING | Task 10 | `63c28fe` |
| `MergeManager.merge` accepted LoRA stacks with mismatched `base_model` values — nonsensical merges passed validation | WARNING | Task 11 | `4904a53` |
| Cluster: `comparative.py` typo-types, `coverage.py` hardcoded path, `merge.py` `NameError` on `config_path`, 5 unused imports, daemon `append_to_queue` exceptions uncaught, `_generate_transformers` cleanup | WARNING | Task 12 | `471a0a9`, `624ff6a` |
| Phase 2's `IntrospectionScheduler` existed but was never called by the daemon (integration gap) | BLOCKER | Task 13 | `99d2483` |
| No E2E test for the introspection → task → episode pipeline | WARNING | Task 14 | `c93ecca` |
| **`register_lora` implemented but never called** — every promoted candidate created lineage-less records, violating Phase 4's "full model history" criterion | BLOCKER | Task 15 | `7d071ed` |
| `commit_to_source` existed with tests but orchestrator never invoked it on accepted episodes — nothing ever committed to the target workspace | BLOCKER | Task 16 | `ec71809` |
| Daemon's `task_queue.jsonl` was written but never read — queue bypassed, no restart safety despite Phase 3 claiming it | BLOCKER | Task 17 | `39e7909` |
| **MLX merge math wrong**: no α/r scaling, PEFT key prefixes not stripped (`base_model.model.` → zero matches → silent no-op), `from mlx_lm import save` (nonexistent) | BLOCKER | Task 18 | `fedef42` |
| **mergekit YAML referenced raw LoRA adapter paths** — mergekit expects full checkpoints, merges silently no-op'd | BLOCKER | Task 19 | `a7557d8` |
| Tests 100 % mocked merge backends (`patch.object(mgr, "_merge_with_mergekit")`) — argv / YAML / stderr paths never exercised | WARNING | Task 20 | `c7bd789` |
| No integration tests for `TrainingManager.run_merge` or `Daemon._check_evolution` despite Phase 4 claiming "tests cover merge success, failure, and rollback" | WARNING | Task 21 | `339dced` |
| No `04-REVIEW.md` | WARNING | Task 22 | this commit |
| `storage.update_merge` read-modify-write race | WARNING | Task 24 (atomic temp+replace already present; added class-level threading.Lock around the read-modify-write window) | `635abe1` |
| Daemon performs synchronous blocking merge on the main cycle thread | WARNING | Task 24 (merge runs on a daemon Thread with single-flight guard; result processed on the next cycle) | `c74c475` |
| `manifest.status == "complete"` briefly before validation runs (observable via concurrent read) | SUGGESTION | Task 24 (lifecycle now `merging → merged → validated\|failed`; "complete" is no longer set at runtime; defense-in-depth downgrade in `run_merge`) | `d5b75be` |
| Lineage cache O(N²) on sequential merges | SUGGESTION | Task 24 (incremental cache update via `_cache_record`; `get_current_generation` also routed through cache) | `06978c8` |

## Reviewer Verdicts (final)

- **Reality Checker — PASS**: installer fixed (Task 1), config drift
  resolved (Tasks 3, 4), validation fails closed without a backend
  (Task 5), coherence stage hardened (Task 6). No silent passes
  on garbage data paths remain.
- **QA Verification — PASS**: state file atomic + resilient (Task 7),
  lock is race-free and ownership-aware (Task 8), integration tests
  added for `run_merge` and `_check_evolution` (Task 21), subprocess-
  level mocks expose argv / YAML correctness (Task 20), E2E test for
  the introspection pipeline (Task 14). 286 tests pass; no fantasy
  approvals remain.
- **AI Engineer — PASS**: MLX merge math correct — α/r scaling
  applied, `base_model.model.` prefix stripped, zero-delta detection
  raises instead of silently succeeding (Task 18). Mergekit uses
  baked full checkpoints via PEFT `merge_and_unload` instead of raw
  adapter paths (Task 19). `register_lora` wired into
  `promote_candidate` (Task 15). `register_merge` aggregates across
  every source LoRA (Task 10). Mixed-base stacks rejected (Task 11).

## Suggestions (resolved)

All four items previously deferred from the spec-fix branch were
addressed in Task 24, each with TDD (failing test → fix → green) and
its own commit:

- **`storage.update_merge` read-modify-write race** (`635abe1`) —
  added a class-level `threading.Lock` guarding the load → mutate →
  atomic-replace sequence. New tests cover both crash safety
  (simulated `os.replace` failure) and concurrent-writer correctness
  (deterministic race exposed via slowed `load_merges`).
- **Daemon synchronous blocking merge** (`c74c475`) — merges now run
  on a `threading.Thread(daemon=True)` with a single-flight guard;
  the next cycle observes completion and processes the result. New
  tests assert `_check_evolution` returns in <1s while a 3s stub
  merge runs in the background, and that the subsequent cycle
  processes the completed result.
- **Manifest "complete" status before validation** (`d5b75be`) —
  lifecycle now `pending → merging → merged → validated | failed`.
  `MergeManager.merge()` sets `merged` on backend success;
  `TrainingManager.run_merge()` owns the `validated|failed`
  transition. Defense in depth: `run_merge` downgrades any legacy
  `complete` it observes back to `merged` before validating. New
  tests capture every persisted status across success and failure
  paths and assert `complete` never appears.
- **Lineage cache O(N²) on sequential merges** (`06978c8`) — replaced
  post-append `_invalidate_cache()` calls with `_cache_record(record)`
  for incremental updates; routed `get_current_generation` through
  the cache. New test asserts ≤1 cold lineage load when registering
  a base + 10 LoRAs (was 10+).

## Test Suite Status

- **Before spec-fix branch**: 230 tests, ~57 % green, merge pipeline
  passing tests but broken end-to-end.
- **After spec-fix branch**: 286 tests, all green.
- **After Task 24**: 293 tests, all green (skipped=10).
- **New tests**: 56 across Tasks 1–22, plus 7 across Task 24
  (2 storage atomicity/concurrency, 2 daemon async-merge,
  2 manifest-lifecycle, 1 lineage-cache).

## Lessons Carried Forward

1. **"Tests pass" ≠ "works"**. Phase 4's unit tests green-lit a merge
   pipeline whose MLX path silently zero-deltaed and whose mergekit
   path fed raw adapter dirs to a tool that expects checkpoints. Both
   paths were behind method-level `patch.object` mocks that never
   exercised argv or YAML. Tasks 20–21 replace those with subprocess-
   level mocks plus integration tests.
2. **Integration gaps hide behind plan checklists**. Three components
   (`IntrospectionScheduler`, `register_lora`, `commit_to_source`) were
   implemented and tested in isolation but never wired into the daemon
   / orchestrator / promotion path. The phase's success criteria were
   satisfied on paper while the whole was non-functional. Tasks 13,
   15, 16 close these gaps with E2E tests.
3. **Spec drift is silent**. `auto_merge_after_loras` vs
   `merge_after_loras`, `max_merge_attempts` vs `merge_failure_limit` —
   TOML keys parsed to nothing but never raised. Task 4 normalizes the
   names and Task 3 documents them.

## Phase 4 Definitive Status

All phase success criteria met, and now actually exercised end-to-end:

| Criterion | Status |
|-----------|--------|
| `evolution/merge.py` merges LoRA stack to base | Pass (MLX α/r math correct; mergekit uses baked checkpoints) |
| `evolution/lineage.py` tracks full model history | Pass (register_lora wired; register_merge walks all sources) |
| `evolution/validation.py` catches bad merges | Pass (fails closed without backend; coherence hardened) |
| Merge failure generates introspection task after N failures | Pass (integration test in Task 21) |
| Tests cover merge success, failure, and rollback | Pass (286 tests; subprocess + integration coverage) |
| Daemon queue is restart-safe | Pass (Task 17 wiring + 3 restart-safety tests) |
| Commits land in target workspace | Pass (Task 16 orchestrator wiring) |
| Install from source works (`pip install -e .`) | Pass (Task 1 subpackage discovery) |
