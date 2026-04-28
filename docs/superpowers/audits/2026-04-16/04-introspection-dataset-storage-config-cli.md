# Audit — Introspection, Dataset Builder, Storage, Config, CLI (Bucket 04)

**Date:** 2026-04-16
**Auditor:** read-only audit, HEAD = `master` (tip `54c7ddb`)
**Scope:** `homunculus/introspection/*.py`, `homunculus/dataset_builder/builder.py`, `homunculus/storage.py`, `homunculus/config.py`, `homunculus/cli.py`, `homunculus/runtime.py`, `homunculus/models.py`, `homunculus.example.toml`, `pyproject.toml`, `.gitignore`, and tests `test_introspection.py`, `test_dataset_builder.py`, `test_packaging.py`, `test_config_evolution.py`.

---

## Summary

HEAD is materially in better shape than the plan's cross-reference suggests. Waves 1, 2, 4(bulk), and 5 of the plan are effectively landed for this bucket. The **remaining defects are concentrated in three areas**:

1. **Example TOML `[evolution]` still ships an incomplete key set** — six of the eleven documented settings (`enabled`, `max_merge_attempts`, `validation_timeout_seconds`, `coherence_prompt`, `coherence_min_tokens`, `merge_backend`) never appear in `homunculus.example.toml`. They are accepted by `load_config` via defaults, but operators have no visible surface for them; any typo falls through silently. The `_warn_on_unknown_keys` helper only catches *unknown* keys — it cannot catch keys the operator forgot to add because they weren't documented.
2. **Snapshot materialization double-composes** — `_build_snapshot` calls `_compose_snapshot_payloads` a second time (line 156 of `dataset_builder/builder.py`), discarding the metadata argument it was handed. No correctness failure (composition is deterministic barring clock), but seed/sft files are re-read on every materialize/preview, and RuntimeErrors can fire twice per invocation path.
3. **Seed-file absence masquerades as "no approved self-generated train sample"** — `_load_seed_payloads` returns `[]` when the file is missing (no error), `_allowed_self_generated_count(0)` returns 0, and `_compose_snapshot_payloads` then raises `"Training snapshot requires at least one approved self-generated train sample."` The message points the operator at the wrong gap.

Everything else in-scope is either **resolved** (Tasks 1, 2, 3, 4, 13 have landed code) or is cosmetic (unused imports per Task 12 — see table).

Overall risk profile: **no BLOCKERs in this bucket**, **one SILENT-DROP (evolution example TOML incomplete coverage)**, and a cluster of **POLISH** items.

---

## Cross-reference table — Plan tasks touching this bucket

| Task | Description | Plan-described state | HEAD state | Verdict |
|------|-------------|----------------------|------------|---------|
| 1 | `pyproject.toml` missing `evolution`, `introspection`, `task_generator` subpackages | BROKEN | `[tool.setuptools.packages.find] include = ["homunculus*"]` is set; `tests/test_packaging.py` asserts auto-discovery and importability of all 9 subpackages | **RESOLVED** |
| 2 | Untrack `__pycache__`, add `traces/` + `runtime/` + `models/` to `.gitignore` | BROKEN | `.gitignore` includes `__pycache__/`, `*.pyc`, `traces/`, `runtime/`, `models/`; `GitignoreTests` in `test_packaging.py` locks the contract | **RESOLVED** |
| 3 | `target_workspace` missing from example config | BROKEN | `[daemon].target_workspace = "self"` present in `homunculus.example.toml`; `ExampleConfigCoverageTests.test_daemon_section_includes_target_workspace` enforces it | **RESOLVED** |
| 4 | `[evolution]` silent-drop; dataclass field rename | BROKEN | `EvolutionSettings` has the full 11-field set (`enabled`, `auto_promote`, `auto_apply`, `auto_train_after_samples`, `auto_merge_after_loras`, `rollback_on_degradation`, `max_merge_attempts`, `validation_timeout_seconds`, `coherence_prompt`, `coherence_min_tokens`, `merge_backend`). `load_config` reads all of them, honors `merge_after_loras` back-compat alias, and `_warn_on_unknown_keys` emits `UserWarning` on unknowns. `tests/test_config_evolution.py` has both forward + back-compat tests. `homunculus/evolution/merge.py:95` and `homunculus/autonomy/precheck.py:142` both read `auto_merge_after_loras` (the new name). No stragglers. | **RESOLVED** — but see Finding 1 (example TOML coverage gap). |
| 12 | Misc fixes: comparative types, coverage path hardcode, unused imports | MIXED | `comparative.py:68-69` casts to float (`float(len(grouped))`, `0.0`), **RESOLVED**. `coverage.py:302` uses `self._get_source_dir_name(context)`, **RESOLVED**. Unused imports: `cli.py:10` (the plan said remove `load_config`) — in HEAD, `cli.py:16` imports `load_config` but it is **actually used** by `cmd_doctor` (line 85+), so the plan's advice was stale. `runtime.py` imports `HomunculusConfig` ... wait: current runtime.py does **not** import `HomunculusConfig` (only from `.config import load_config`). `introspection/base.py` imports `from dataclasses import dataclass` — no `field` left. These have all been cleaned up. | **RESOLVED** |
| 13 | Wire `IntrospectionScheduler` into daemon | BROKEN | `daemon.py:97-103` constructs `IntrospectionScheduler(config, store=store)` when enabled + store present; `daemon.py:274-303` implements `_run_introspection()` with exception isolation; `daemon.py:456` calls it first in `run_once`. `scheduler.run_due_modes(cycle_number)` signature matches call site. | **RESOLVED** |
| 22 | Write `04-REVIEW.md`, update STATE / ROADMAP / CLAUDE.md | PENDING | Out-of-bucket for the source code audit. `.planning/phases/04-weight-evolution/04-REVIEW.md` exists (confirmed via grep hits). | **OUT OF BUCKET** (docs/governance). |

---

## New Findings

### BLOCKER — none in this bucket.

### SILENT-DROP

#### F1. `homunculus.example.toml [evolution]` documents only 5 of 11 supported keys.
- **Class:** (b) silent-drop config key
- **File:** `homunculus.example.toml`, the `[evolution]` section
- **Evidence:** Parsed TOML keys are exactly: `auto_promote`, `auto_apply`, `auto_train_after_samples`, `auto_merge_after_loras`, `rollback_on_degradation`. Missing against `EvolutionSettings` defaults (`config.py:131-142`):
  - `enabled` (bool, default True)
  - `max_merge_attempts` (int, default 3)
  - `validation_timeout_seconds` (int, default 300)
  - `coherence_prompt` (str, default Fibonacci prompt)
  - `coherence_min_tokens` (int, default 50)
  - `merge_backend` (str, default "auto")
- **Impact:** An operator tuning merge policy will read `homunculus.example.toml`, see five keys, and mistakenly assume that is the complete surface. A typo like `merge_back_end` will produce `UserWarning: [evolution] config contains unknown keys: ['merge_back_end']` — *but only if the operator is running in a context that surfaces warnings*. CLI subcommands do not print stderr warnings prominently. The `ExampleConfigCoverageTests` asserts `daemon.target_workspace` only; it does **not** verify `[evolution]` coverage. This is the mirror image of the Task 3 fix — Task 3 closed the `daemon` gap but never extended to `[evolution]`.
- **Severity:** SILENT-DROP.
- **Recommended fix:** Append the missing defaults to `[evolution]` in `homunculus.example.toml` and extend `ExampleConfigCoverageTests` to iterate `EvolutionSettings.__dataclass_fields__` and assert each appears.

#### F2. `IntrospectionSettings` interval `0`/negative coerced to default without warning.
- **Class:** (b) silent-drop config key
- **File:** `homunculus/config.py:242-244`
- **Evidence:** `_validate_interval(value, default)` silently substitutes the default when `value < 1`. No warning emitted. The plan does not flag this.
- **Impact:** An operator setting `metrics_interval = 0` (trying to disable) gets `metrics_interval = 1` (every cycle) silently. The opposite of intent. Should either raise or warn.
- **Severity:** SILENT-DROP (minor — unexpected behavior reversal on a single config key).

#### F3. `_load_seed_payloads` returns `[]` when seed file missing, masking error with a downstream misleading message.
- **Class:** (c) fail-open validator (mismatched diagnostic)
- **File:** `homunculus/dataset_builder/builder.py:202-213`
- **Evidence:** `_load_seed_payloads` calls `_load_jsonl(path)`; `_load_jsonl` at line 206-207 returns `[]` when the path does not exist. Downstream `_allowed_self_generated_count(0)` returns 0, so `selected_self_train = []`, then line 133-134 raises `RuntimeError("Training snapshot requires at least one approved self-generated train sample.")` — even when the actual cause is a missing seed file.
- **Impact:** Operator sees a misleading error message. Seed-file absence is a genuine configuration failure that should be caught at snapshot-time with a clear signal.
- **Severity:** SILENT-DROP (error message misdirection, not silent acceptance).

### POLISH

#### F4. `_build_snapshot` re-composes payloads it was handed via metadata.
- **Class:** (minor inefficiency)
- **File:** `homunculus/dataset_builder/builder.py:155-156`
- **Evidence:** `_build_snapshot(self, metadata)` is invoked by both `materialize_sft_snapshot` (line 111-112) and `preview_sft_snapshot` (line 116-118) after `_compose_snapshot_payloads` has already returned `metadata`. Inside `_build_snapshot` at line 156, `_compose_snapshot_payloads` is **called a second time** to get `train/valid/test_payloads`; the first call's payloads are discarded.
- **Impact:** Redundant seed/sft JSONL disk reads per invocation, and any RuntimeError raised by `_compose_snapshot_payloads` (empty valid/test, ratio violation) can fire twice if the caller re-invokes. No correctness bug — but `preview_sft_snapshot → _build_snapshot → _compose_snapshot_payloads → preview_sft_snapshot` is a subtle redundancy that should be eliminated.
- **Severity:** POLISH.

#### F5. `ArtifactStore.append_*` methods do not validate schema or deduplicate.
- **Class:** (f) partial — not terminal-outcome archival, but schema honesty.
- **File:** `homunculus/storage.py:80-101, 192-194, 220-222, 270-272`
- **Evidence:** `append_event`, `append_episode`, `append_sft_sample`, `append_dpo_pair`, `append_introspection_result`, `append_merge`, `append_lineage` are all thin wrappers over `append_jsonl(path, payload)`. There is no:
  - schema validation (a malformed record can be written)
  - dedup (repeat appends with the same id are silently duplicated — e.g., `append_introspection_result` allows the same mode/timestamp to accumulate)
  - atomicity on the append itself (a crashed write leaves a truncated JSONL line that `load_jsonl` does not defend against — it calls `json.loads(line)` and will raise, aborting the whole load)
- **Impact:** A corrupted traces/*.jsonl file breaks all `load_*` methods — but this is only triggered by a true process kill mid-write. `update_merge` and `update_queue_entry` DO use atomic `os.replace` (lines 236-259, 343-423). The append path is deliberately non-atomic. Acceptable for append-only JSONL in practice; called out only for completeness.
- **Severity:** POLISH.

#### F6. `append_merge` allows duplicate `merge_id` records.
- **Class:** (f) partial — persistence honesty
- **File:** `homunculus/storage.py:220-222`
- **Evidence:** `append_merge` unconditionally calls `append_jsonl(self.merges_path(), manifest.to_dict())`. Repeat calls with the same `merge_id` append a duplicate. `get_merge` at line 228-233 presumably returns the first match (not checked), so duplicates are invisible until `load_merges()`.
- **Impact:** Minor. The caller (`MergeManager`) is responsible for single-appending per merge. No test asserts dedup.
- **Severity:** POLISH.

#### F7. `append_jsonl` / `_ensure_file` write with `path.write_text("")` and no directory creation on the first call.
- **Class:** POLISH
- **File:** `homunculus/storage.py:60-68`
- **Evidence:** `_ensure_file(path)` creates the file if missing, but `path.write_text("", encoding="utf-8")` will raise if the parent directory is missing. `ensure_layout()` (line 26-58) creates the needed directory tree; if a caller uses an `ArtifactStore` without calling `ensure_layout` first, appends will crash on path resolution. CLI `cmd_init_artifacts` calls `ensure_layout`. Daemon constructor does **not** call `ensure_layout` explicitly.
- **Impact:** Acceptable because `ArtifactStore.ensure_layout()` is called in CLI `init-artifacts` and implicitly via `Daemon` constructor's first write path. Minor fragility.
- **Severity:** POLISH.

#### F8. `CritiqueMode._parse_teacher_output` / `_extract_json_from_content` swallow four exception classes with bare `pass`.
- **Class:** (d) bare-pass
- **File:** `homunculus/introspection/critique.py:227-228, 259-262, 267-270`
- **Evidence:** Three `except ... : pass` blocks in `_extract_content` and `_parse_json_content`. Each is paired with a fallback path, so they are not hiding unimplemented branches — they are intentional JSON-resilience fallbacks. A log line per swallow would aid debugging when a real teacher returns malformed JSON.
- **Impact:** Diagnostic quality. A malformed teacher response silently falls through to the empty-dict sentinel at line 272-278, and the operator sees an empty critique instead of a parse-error trace.
- **Severity:** POLISH.

#### F9. `CoverageMode._run_coverage` catches generic `Exception` around `json_path.unlink` with bare `pass`.
- **Class:** (d) bare-pass
- **File:** `homunculus/introspection/coverage.py:199-202`
- **Evidence:** Deliberate cleanup suppression; acceptable pattern.
- **Severity:** POLISH (call out, do not fix).

#### F10. `IntrospectionScheduler` skips cycle 0 but accepts `cycle_number < 0`.
- **Class:** (c) fail-open validator
- **File:** `homunculus/introspection/scheduler.py:87-91`
- **Evidence:** `if cycle_number == 0: return ScheduledModes()`. A negative `cycle_number` passes through to `cycle_number % interval`, which in Python is still non-negative (`-3 % 5 == 2`), so it happens to "work," but the semantics are meaningless. No validation.
- **Impact:** Theoretical — production caller is `self.state.cycles_completed` which is `int` starting at 0 and only incremented. Not reachable under current code.
- **Severity:** POLISH.

#### F11. `Daemon._run_introspection` uses `self.state.cycles_completed` — which is 0 on the *first* cycle, so nothing runs.
- **Class:** (a) unwired integration
- **File:** `homunculus/daemon.py:289-292`
- **Evidence:** `run_due_modes(cycle_number=self.state.cycles_completed)` passes the current count. `DaemonState.cycles_completed` starts at 0. On the first invocation of `run_once`, `_run_introspection` is called *before* cycles_completed is incremented. Per scheduler logic (line 87-91), cycle 0 returns empty `ScheduledModes()`. So introspection **skips the first cycle unconditionally**.
- **Impact:** The first daemon cycle never produces introspection. Task 14's end-to-end test is expected to run two cycles to observe the effect (the plan even mentions this at line 1384-1385). But there is no unit test asserting this off-by-one behavior is intentional. It's surprising that the first cycle's introspection findings are lost.
- **Severity:** POLISH (arguably by design; worth documenting).

#### F12. `cli.py` imports `load_config` at line 16 — plan claimed this was unused.
- **Class:** (not a defect — stale plan reference)
- **File:** `homunculus/cli.py:16`
- **Evidence:** `load_config` IS referenced by `cmd_doctor` at line 85 (and elsewhere). The plan's Task 12.e was wrong about this specific import. HEAD is correct.
- **Severity:** POLISH (no fix needed).

#### F13. `DatasetBuilder.can_build_training_snapshot` broken short-circuit on empty preview.
- **Class:** (c) fail-open
- **File:** `homunculus/dataset_builder/builder.py:92-97`
- **Evidence:** Line 93-94: `try: snapshot = self.preview_sft_snapshot()` catches a bare `Exception` (line 95), returns `False`. That's the intended behavior. But line 96 then indexes `snapshot.sample_counts["splits"]["train"]` which will `KeyError` if `sample_counts` has a different shape. No defensive default.
- **Impact:** Minor — shape drift from another code path could crash `can_build_training_snapshot`. The test `test_materialize_snapshot_writes_metadata_and_splits` exercises the happy path only.
- **Severity:** POLISH.

### Dataclass field usage audit (models.py) — orphaned fields?

Walked every field in `models.py` against in-repo references. Nothing found truly orphaned. A few spot-checks:

- `EpisodeRecord.commit_sha` — populated by orchestrator loop per Task 16; **read** by… nothing in this bucket's scope. Visible in the JSON output but not consumed programmatically. Orphan candidate for the orchestrator bucket, not here.
- `DatasetSnapshot.config_hash` — computed (`builder.py:162`), serialized (`storage.write_snapshot`), **not read anywhere**. Currently audit-only metadata. Orphan for trainer bucket.
- `AdapterManifest.promotion_reason`, `AdapterManifest.evaluation_status` — allocated but not all code paths populate them. Trainer bucket.
- `TaskQueueEntry.outcome` and `.last_error` — populated by daemon, no reader in bucket.

None of these rise to "orphaned" in this bucket's code.

---

## Flows Traced

### A. Introspection cycle (daemon → scheduler → modes → storage)

1. `Daemon.__init__` (daemon.py:97-103) builds `IntrospectionScheduler(config, store=store)` iff `config.introspection.enabled and store is not None`.
2. `Daemon.run_once` (line 452-456) calls `self._run_introspection()` FIRST so downstream task generation reads fresh findings.
3. `_run_introspection` (line 274-303) guards on `scheduler is None or store is None`; catches all exceptions from `run_due_modes`; iterates `results or []` with per-result exception handling for `append_introspection_result`.
4. `IntrospectionScheduler.run_due_modes(cycle_number)` (scheduler.py:113-165):
   - Returns `[]` if `store is None`.
   - Calls `get_scheduled_modes(cycle_number)` which gates on `cycle_number == 0` and `settings.enabled`, then `cycle_number % interval == 0` per mode, with `critique_enabled` as an additional gate for critique only.
   - For each scheduled mode: lazy-import `get_introspection_mode`, run with `IntrospectionContext(store, config, cycle_number, window_size)`, per-mode exception isolation.
5. Each mode's `.run(context)` returns an `IntrospectionResult`.
6. Daemon persists via `store.append_introspection_result(result)` → `append_jsonl(traces_dir / "introspection.jsonl", result.to_dict())`.

**Verdict:** Wiring is complete and defensive. Only concern is F11 (cycle 0 skip behavior is surprising but intentional).

### B. Snapshot build (dataset_builder)

1. Caller (typically trainer) calls `DatasetBuilder.materialize_sft_snapshot()`.
2. `_compose_snapshot_payloads` (builder.py:123-153):
   - Reads seed SFT from `config.paths.seed_sft_path` via `_load_seed_payloads`.
   - Reads self-generated train/valid/test from store via `load_sft_samples`.
   - Enforces non-empty valid/test (RuntimeError if empty).
   - Computes allowed self count via `_allowed_self_generated_count(seed_count)`.
   - Enforces min self-count (RuntimeError if zero).
   - Enforces ratio ceiling (RuntimeError if exceeded).
   - Returns `(train, valid, test, metadata)`.
3. `_build_snapshot(metadata)` is called — **re-invokes** `_compose_snapshot_payloads` (F4), computes combined-SHA `snapshot_id`, reads `config.source_path.read_bytes()` for `config_hash`, builds `DatasetSnapshot`.
4. `store.write_snapshot(snapshot, ...)` writes `train.jsonl`, `valid.jsonl`, `test.jsonl`, `snapshot.json` under `datasets_dir/snapshots/sft/<snapshot_id>/`.

**Verdict:** Functionally correct. F3 (seed-missing misdiagnosis), F4 (double compose), and F13 (brittle guard) are the polish items.

### C. Config load with unknown-key warning path

1. `load_config(path)` opens the TOML, parses with `tomllib`.
2. Every section is parsed with explicit `.get()` calls into a dataclass constructor.
3. ONLY `[evolution]` calls `_warn_on_unknown_keys`. Other sections (`[teacher]`, `[student]`, `[memory]`, `[thresholds]`, `[promotion]`, `[paths]`, `[dpo]`, `[daemon]`, `[introspection]`, `[guardrails]`) do NOT warn on unknown keys. `TeacherSettings(**raw["teacher"])` will **raise TypeError** on unknown keys because it uses `**`. So `[teacher]/[student]/[memory]/[thresholds]/[promotion]` all fail-closed on unknowns (good). `[daemon]` uses `**raw.get("daemon", {})` → also fail-closed. `[dpo]` fail-closed. `[paths]` is field-by-field — unknowns silently dropped. `[introspection]` is field-by-field — unknowns silently dropped.
4. The `[evolution]` known-keys set in `_warn_on_unknown_keys` intentionally lists `merge_after_loras` (the back-compat alias) so the warning doesn't fire on legacy configs.

**Verdict:** Inconsistent policy. `[evolution]` warns; `[introspection]` and `[paths]` silently drop. Operators have no signal if they misspell `window_size` → `windows_size`.

### D. CLI `init-artifacts`

```
cli.cmd_init_artifacts
  → runtime.build_runtime(args.config)
    → load_config
    → ArtifactStore(config)
    → DatasetBuilder, EngramMemoryClient, OpenAICompatibleTeacher, LocalStudentRunner, TaskRunner, GuardrailEngine, TrainingManager, EpisodeOrchestrator
  → store.ensure_layout()
  → print status JSON
```
Creates all artifact dirs + placeholder JSONL files. No CLI-level defect.

### E. CLI `doctor`

`cli.cmd_doctor` (line 85-148) uses `load_config` to parse, then iterates through checks: git on PATH, OPENAI_API_KEY env var, ENGRAM_MCP_BEARER_TOKEN env var, seed SFT file exists, student `generate_command[0]` on PATH, workspace paths, etc. Reports via JSON. No defect observed (beyond F2 silent-drop on introspection intervals which doctor won't catch).

### F. CLI `run-episode`

`cli.cmd_run_episode` (line 29-37) unpacks the 7-tuple from `build_runtime`, reads prompt (from `--prompt` or `--prompt-file`), invokes `orchestrator.run_episode(TaskRequest(...))`. Prints episode JSON. No defect in-bucket.

### G. CLI `apply-episode`

`cli.cmd_apply_episode` (line 39-54) loads the episode via `store.get_episode`, raises SystemExit on unknown, reads the patch artifact, calls `task_runner.apply_episode_patch(workspace, patch)`. Prints diff hash and verification. No defect in-bucket.

### H. CLI `train-sft`

`cli.cmd_train_sft` (line 56-62) calls `trainer.train_sft(simulate=args.simulate)`. Prints the manifest JSON. No defect in-bucket.

### I. Additional CLI subcommands

HEAD also has: `evaluate-candidate`, `promote-candidate`, `autonomy-precheck`, `autonomy-preflight`, `autonomy-report`, `autonomy-accept`. All have parser registrations (line 285-349). None are orphaned.

---

## Test Coverage Gaps

| Gap | Severity | Detail |
|-----|----------|--------|
| No test asserts `[evolution]` keys appear in example TOML | SILENT-DROP | `ExampleConfigCoverageTests` only checks `daemon.target_workspace`. Mirror test for evolution section needed. |
| No test asserts `[introspection]` interval validation | POLISH | `_validate_interval` coerces 0/negative silently (F2); no test catches this. |
| No test for seed-file-missing RuntimeError message quality | POLISH | F3 — the "requires at least one approved self-generated train sample" message fires on missing seed, but no test asserts the message is distinguishable from a genuine no-train-sample case. |
| No test for `_build_snapshot` double-compose (F4) | POLISH | Could be caught by a mock that counts `_compose_snapshot_payloads` invocations. |
| No test that `IntrospectionScheduler.get_scheduled_modes(-1)` is rejected | POLISH | F10. |
| No test for `append_merge` duplicate `merge_id` rejection | POLISH | F6. If a dedup check is ever added, a test should lock the contract. |
| No test that truncated / malformed `traces/*.jsonl` is survivable on load | POLISH | F5 — no defense against partially-written JSONL line. |
| No E2E test from introspection → task generation → episode execution (Task 14) | **GAP** | Plan Task 14 is unlanded. Without this test, the "loop closure" claim for Phase 2→3 integration is asserted but not verified end-to-end. |

---

## Appendix — Verified HEAD facts

- `config.py:131-142` — `EvolutionSettings` has all 11 documented fields.
- `config.py:270-295` — `load_config` reads every field, honors `merge_after_loras` alias, warns on unknowns.
- `daemon.py:97-103, 274-303, 456` — `IntrospectionScheduler` wiring is complete.
- `scheduler.py:44-165` — `run_due_modes(cycle_number)` exists with correct signature.
- `comparative.py:68-69` — float casts present.
- `coverage.py:302, 383-...` — `_get_source_dir_name(context)` is used and defined.
- `builder.py:155-156` — `_build_snapshot` double-composes (F4).
- `builder.py:206-207` — `_load_jsonl` returns `[]` on missing file (F3 contributor).
- `storage.py:192-211` — `append_introspection_result` + `load_introspection_results` exist.
- `storage.py:236-259` — `update_merge` uses atomic temp+replace.
- `storage.py:343-423` — queue updates use atomic temp+replace.
- `pyproject.toml:19-21` — find directive active.
- `.gitignore` — covers `__pycache__/`, `*.pyc`, `traces/`, `runtime/`, `models/`.
- `homunculus.example.toml [evolution]` — 5 of 11 keys present (F1).
- `homunculus.example.toml [daemon]` — `target_workspace = "self"` present, `auto_commit_on_accept = true` present.
- `cli.py` — 11 subcommands, all registered: init-artifacts, run-episode, apply-episode, train-sft, evaluate-candidate, promote-candidate, doctor, autonomy-precheck, autonomy-preflight, autonomy-report, autonomy-accept.
