# Audit: Evolution / Trainer (merge, validation, lineage, run_merge)
Date: 2026-04-16

## Summary

The evolution and trainer bucket is in **much better shape than the
spec-alignment plan implies**. Every one of the ten plan tasks that
targets this bucket (Tasks 4, 5, 6, 10, 11, 15, 18, 19, 20, 21) is
**fully resolved at HEAD**. The original silent-drop, fail-open, and
mock-shaped defects the plan was written against have all been
remediated:

- `MergeManager.merge` now validates target_base consistency, rejects
  empty and mixed-base stacks (`merge.py:133-141`).
- `_apply_lora_to_weights` applies the correct `(alpha/r) * scale * (B@A)`
  delta, strips PEFT's `base_model.model.` prefix, and **raises
  `RuntimeError` when zero deltas land** (`merge.py:584-628`).
- `_merge_with_mergekit` bakes each LoRA via `PeftModel.merge_and_unload`
  into a full checkpoint before calling `mergekit-yaml`, and the YAML
  generator consumes baked paths (`merge.py:218-234`, `284-328`,
  `329-386`).
- `_validate_coherence` fails closed with `"backend_unavailable"` when
  neither MLX nor transformers is importable (`validation.py:254-263`).
- `_generate_transformers` slices prompt tokens, uses `do_sample=False`,
  and frees CUDA memory in `finally` (`validation.py:313-352`).
- `register_merge` aggregates parents from **every** source LoRA with a
  set (no more `break`) and always links the target base
  (`lineage.py:150-194`).
- `TrainingManager.promote_candidate` calls `lineage_tracker.register_lora`
  after activation, swallowing lineage failures as observability
  (`trainer/manager.py:167-179`).
- `TrainingManager.run_merge` executes merge → validate →
  `status="validated"` → `register_merge` → reset failure counter, with
  failure paths incrementing the counter and persisting `status="failed"`
  (`trainer/manager.py:281-348`).
- Test mocks are now subprocess-level in `tests/test_evolution.py`
  (six call sites patch `homunculus.evolution.merge.subprocess.run`,
  zero sites use `patch.object(..., "_merge_with_(mergekit|mlx)")`).

Given that, the inventory here is deliberately short. I recorded a small
number of **new findings** that remain, none of them blockers: the
coherence stage uses a word-count proxy for tokens (works but doesn't
honor true tokenization), the MLX merge averages deltas by
`1/len(loras)` which silently weakens single-LoRA merges that arrive as
stacks, and the registry / `save_registry` write is non-atomic (a small
race hazard relative to the atomic `update_merge`). Each is a POLISH
finding and documented below with exact line references.

## Cross-reference: Existing Plan Tasks

| Task | Title | Status | Evidence |
|------|-------|--------|----------|
| 4 | Reconcile `[evolution]` config — TOML wins, rename fields, warn on unknown keys | **resolved** | `config.py:9-21` defines `_warn_on_unknown_keys` helper; `config.py:131-142` has `EvolutionSettings` with all 11 documented fields; `config.py:269-295` consumes every key with defaults + the `merge_after_loras` back-compat alias + `_warn_on_unknown_keys` gate; `tests/test_config_evolution.py:88-118` covers both full-keyset and back-compat-alias paths. |
| 5 | `_validate_coherence` fail closed without backend | **resolved** | `validation.py:254-263` returns `ValidationResult(stage="coherence", passed=False, message="backend_unavailable: install mlx_lm or transformers to enable evolution")` when the transformers import raises `ImportError`; `tests/test_evolution.py:1403-1468` (`CoherenceFailClosedTests`) force-blocks both imports via `builtins.__import__` monkey-patch and asserts `backend_unavailable` in the message. |
| 6 | Fix `_generate_transformers` — slice prompt, deterministic decode, free CUDA memory | **resolved** | `validation.py:313-352` uses `do_sample=False`, slices `output_ids[0][inputs.input_ids.shape[1]:]`, wraps work in `try/finally` with `del model` and `torch.cuda.empty_cache()`. `_is_repetitive` (`validation.py:354-383`) now uses 4-gram dominance >15% with ≥2-occurrence guard — no early-return for short outputs. `tests/test_evolution.py:1471-1599` covers token-slicing and short-repetitive detection. |
| 10 | Lineage `register_merge` aggregates parents from ALL source LoRAs | **resolved** | `lineage.py:150-194` uses `parent_set: set[str]` and iterates **every** `lora_id` without `break`, aggregates `episode_set`, incorporates `cached.parent_ids` (grandparent edges), and always adds `base-<target_base>` when present. `tests/test_evolution.py:1659-1760` has two regression tests (aggregates-all-sources and dedups-shared-base). |
| 11 | Validate `target_base` consistency in `MergeManager.merge` | **resolved** | `merge.py:133-141` computes `bases = {lora.base_model for lora in loras if lora.base_model}`, raises on `len(bases) > 1` and on empty set. `tests/test_evolution.py:1762-1841` covers mixed-base (ValueError), no-base (ValueError), and homogeneous (proceeds). |
| 15 | Call `register_lora` from `TrainingManager.promote_candidate` | **resolved** | `trainer/manager.py:167-179` calls `self.lineage_tracker.register_lora(candidate, episode_ids=list(candidate.contributing_episode_ids or []))` inside a `try/except Exception` that logs-not-raises — lineage failures don't break promotion. `tests/test_trainer.py:83-203` has end-to-end coverage that round-trips through `run_sft(simulate=True)` → `promote_candidate` → `store.load_lineage()` against a real file store (not a mock), asserting exactly one record and matching episode_ids. The rejection-path test confirms no lineage record on failed gates. |
| 18 | Fix MLX merge — real key resolution, alpha/r scaling, no-op detection | **resolved** | `merge.py:584-633` implements correct PEFT key stripping (`base_model.model.` prefix, `.lora_A.weight` → `.weight`), `lora_scale = (alpha / rank) * scale`, `applied == 0` raises `RuntimeError("zero deltas applied — LoRA/base key mismatch. ...")`. `_read_lora_config` (`merge.py:529-553`) reads `lora_alpha` + `r` from `adapter_config.json` with warning-logged fallback defaults. `tests/test_evolution.py:1843-1962` covers all three properties. |
| 19 | Fix mergekit YAML — bake via PEFT, then linear-merge | **resolved** | `merge.py:181-283` (`_merge_with_mergekit`) now calls `self._bake_lora_into_base(lora)` per adapter, collects `baked_paths: list[str]`, and feeds **those** into `_generate_mergekit_config_for_baked` (`merge.py:329-386`) which emits `{"model": baked_path, ...}` entries — no raw `adapter_path` reaches mergekit. `_bake_lora_into_base` (`merge.py:284-327`) uses `PeftModel.from_pretrained(...).merge_and_unload()` and caches to `<models_dir>/baked/<candidate_id>`. A single bake failure aborts the merge with a descriptive error. `tests/test_evolution.py:1964-2080` asserts the YAML references baked checkpoints and that mergekit stderr propagates. Legacy `_generate_mergekit_config` (`merge.py:388-441`) is explicitly retained as "legacy, don't use" for pre-Task-19 tests but is not reachable from production flow. |
| 20 | Subprocess-level mocks replace method-level ones | **resolved** | `tests/test_evolution.py` contains **zero** `patch.object(mgr, "_merge_with_(mergekit\|mlx)")` call sites and **six** `patch("homunculus.evolution.merge.subprocess.run", ...)` sites (lines 590, 634, 676, 1833, 2038, 2073). The stderr-propagation test exists at line 2055 (`test_mergekit_nonzero_exit_propagates_stderr`). |
| 21 | Integration tests for `TrainingManager.run_merge` and `daemon._check_evolution` | **resolved** | `tests/test_evolution.py:2082-2387` has `RunMergeIntegrationTests` with seven scenarios (no-candidates short-circuit, success → register_merge + counter reset, merge failure → increment, validation failure → increment + `status="failed"`, missing manifest → failure, `status="complete"` rejection, legacy-complete correction). `tests/test_daemon.py:1211-1493` has `CheckEvolutionIntegrationTests` with six scenarios (no-merge, success-emits-completion, failure-enqueues-task-at-threshold, counter-not-reset-on-enqueue-failure, does-not-block-cycle, subsequent-cycle-processes-completion). All subprocess-level or component-mock style. |

**Net take on plan vs. HEAD in this bucket:** All ten plan tasks that
touch this bucket are complete. Recommend marking Tasks 4, 5, 6, 10, 11,
15, 18, 19, 20, 21 done in the plan header without further code changes.

## New Findings (not in existing plan)

### POLISH (c) — Coherence token count uses word-count proxy, not real tokenization

- **File:** `homunculus/evolution/validation.py:280`
- **Symptom:** `approx_tokens = len(output.split()) * 1.3` is compared to
  `coherence_min_tokens`. For a tokenizer whose BPE ratio diverges from
  1.3 (CJK, code with lots of identifiers), this floor is wrong in
  either direction — underestimating trips the threshold on legitimate
  code output; overestimating lets truly short generations slip through.
- **Impact:** Fail-closed correctness is preserved because
  `_generate_transformers` has already sliced the prompt and the
  repetitive-output check runs. The error is in the band between
  "passing" and "failing" — a real model is unlikely to land there.
- **Fix sketch:** `tokenizer = AutoTokenizer.from_pretrained(model_path)`
  in `_validate_coherence` and count real output tokens from the
  tokenized `output`. Slightly more expensive (one tokenizer load) but
  removes the proxy. Alternatively, have `_generate_transformers`
  return both the decoded text and the token count, dodging a second
  tokenizer instantiation.

### POLISH (c) — MLX merge averages single-LoRA deltas by `1/len(loras)`, weakening merges that happen to have one candidate

- **File:** `homunculus/evolution/merge.py:482`
- **Symptom:** `_merge_with_mlx` passes `scale=1.0 / len(loras)` to
  `_apply_lora_to_weights` uniformly. For a single-LoRA stack
  (`len(loras) == 1`), scale is 1.0 — correct. For two LoRAs, scale
  becomes 0.5, which halves each delta before `(alpha/r) * (B @ A)`.
  This is a **deliberate average** and matches the mergekit `"linear"`
  semantics (`_generate_mergekit_config_for_baked` emits
  `weight = 1.0 / len(baked_paths)`), but the dampening is invisible to
  the caller and interacts with the `alpha/r` scaling. A `linear` merge
  of two half-strength LoRAs is typically fine; but a user who expected
  "compose both LoRAs at full strength" (a `ties`-like semantic) gets
  unexpected under-performance and no hint.
- **Impact:** Not a correctness bug (the averaging is documented in the
  docstring at `merge.py:482`), but a surprise for anyone reading the
  merge call site. The mergekit backend can at least express different
  weights via YAML; the MLX path cannot.
- **Fix sketch:** Accept a `method: str = "linear"` arg in
  `_merge_with_mlx` (the public `merge()` already threads it through
  `MergeResult`) and branch scaling: `linear` → `1/n`, `ties` → `1.0`
  with optional `density` sparsification. This is a larger change and
  unnecessary until the merge backend matters in production, hence
  POLISH.

### POLISH (g) — `save_registry` is non-atomic; concurrent promoters can lose the `active_candidate_id`

- **File:** `homunculus/storage.py:144-169`
- **Symptom:** `save_registry` does a bare
  `path.write_text(json.dumps(registry, indent=2))` — no temp-file +
  `os.replace`, no in-process lock. Contrast with `update_merge`
  (`storage.py:235-261`) which holds
  `type(self)._merge_update_lock` and writes via
  `tempfile.mkstemp` + `os.replace`. `set_active_candidate` is the
  worst call site: read registry, mutate `history` + `active_candidate_id`,
  write whole dict. Two promoters landing within the same cycle —
  unlikely under the single-threaded daemon today but possible if a
  user ever calls `apply-episode` concurrently — race-window-clobber
  each other's `history` append.
- **Impact:** The current daemon is single-threaded for training /
  promotion, so this is **latent** rather than active. Nothing here is
  broken today; the asymmetry (merge-manifest writes are atomic,
  registry writes are not) just invites future bugs. Not a blocker.
- **Fix sketch:** Wrap `save_registry` in the same lock + temp-file
  idiom used by `update_merge`. Five lines of code.

### POLISH (d) — Legacy `_generate_mergekit_config` kept as dead code in production flow

- **File:** `homunculus/evolution/merge.py:388-441`
- **Symptom:** The old raw-adapter-path YAML generator remains in the
  file, explicitly marked "Legacy config generator kept for
  backward-compatible tests" in its docstring. Production flow calls
  `_generate_mergekit_config_for_baked`. The legacy function is still
  exercised by pre-Task-19 tests that call it directly (confirmed
  search: `test_generate_mergekit_config_linear` etc. at
  `tests/test_evolution.py:687, 731, 763, 794`).
- **Impact:** No correctness impact — the legacy generator is
  unreachable from production `merge()`. But the tests at 687-825 are
  now testing a dead code path, inflating the coverage number without
  protecting anything that can be called at runtime. This is
  **mock-shaped test** territory (class e) because the tests assert on
  output that never feeds a live subprocess.
- **Fix sketch:** Either (a) delete `_generate_mergekit_config` and the
  four tests, or (b) gate it behind `@pytest.mark.skip` with a deprecation
  comment. If neither, leave a TODO referencing this audit.

## Flows Traced

### Merge happy path (MLX backend)

1. `daemon._check_evolution` (daemon.py:546) asks
   `trainer.should_merge()`; on yes, spawns `_run_merge_in_thread`
   (daemon.py:585-591) on a daemon thread. The cycle returns
   immediately (single-flight, does not block).
2. `TrainingManager.run_merge` (trainer/manager.py:281-348):
   - Calls `merge_manager.get_merge_candidates()` — empty list
     short-circuits to `"No candidates"` failure **without** touching
     the consecutive-failure counter (trainer/manager.py:300-302).
   - Calls `merge_manager.merge(candidates)`.
3. `MergeManager.merge` (merge.py:113-180):
   - Rejects empty list, mixed-base list, no-base list.
   - Appends manifest with `status="merging"` via `store.append_merge`.
   - Dispatches to `_merge_with_mlx` or `_merge_with_mergekit`.
4. `_merge_with_mlx` (merge.py:443-511):
   - `load(loras[0].base_model)` pulls base weights.
   - For each LoRA: `_load_lora_weights` +
     `_read_lora_config(alpha, rank)` + `_apply_lora_to_weights(...,
     scale=1.0/len(loras), alpha, rank)`.
   - `_apply_lora_to_weights` raises if zero deltas applied
     (merge.py:621).
   - `base_model.update(base_weights)` → `save_weights` → copy
     tokenizer + `config.json`.
5. Back in `merge()` (line 164): `manifest.status = "merged"` (not
   "complete" — docstring at line 160 documents why), `output_path`
   set, `completed_at = utc_now()`, `store.update_merge(manifest)`.
6. Back in `run_merge`: defensive re-flip of `"complete"` →
   `"merged"` (line 320), then `merge_validator.validate(manifest)`.
7. On pass: `manifest.status = "validated"`,
   `manifest.validation_results = validation.to_dict()`,
   `store.update_merge` (atomic). Then
   `lineage_tracker.register_merge(manifest, f"{target_base}-gen{gen+1}")`,
   counter reset to 0 (line 347). Returns the original `MergeResult`
   (not a new one — `merge_manifest` was already set by `merge()`).

### Merge failure rollback

- Path 1: `MergeManager.merge` raises → bare `except Exception`
  (merge.py:175-179) sets `status="failed"`, `error_message=str(e)`,
  updates store, returns `success=False, merge_manifest=manifest`.
- Path 2: Backend returns `success=False` → `manifest.status="failed"`
  + `error_message` set → `store.update_merge` (merge.py:167-169).
  **Note:** `completed_at` is NOT set on failure. That asymmetry is
  intentional (failed merges weren't "completed") but worth noting.
- Path 3: `run_merge` sees `result.success == False`: increments the
  consecutive-failure counter (trainer/manager.py:308) and returns the
  result **without** calling the validator. Counter persistence to
  `runtime_dir/merge_failures.txt` (see `_set_consecutive_merge_failures`
  at trainer/manager.py:260-274) is atomic via temp-file + os.replace.
- Path 4: `run_merge` sees no `merge_manifest` after success (contract
  violation): treated as failure (trainer/manager.py:313-315).
- Path 5 (background): `_run_merge_in_thread` catches **any** unhandled
  exception (daemon.py:602-608) and stashes a synthetic
  `MergeResult(success=False, error_message="merge worker crashed: ...")`
  so the next cycle's `_process_merge_result` still observes it.

### Validation gating

`MergeValidator.validate` (validation.py:57-89) runs stages
sequentially and **short-circuits on the first failure**:

- Stage 1 load: file-existence (config.json, ≥1 weight file) **plus** a
  real `safetensors.safe_open` key listing on each `.safetensors` file.
  `safetensors` missing → `ImportError` caught and deep check skipped
  (validation.py:147-149) — this is the one "skip on missing dep" that
  remains, but the earlier `config.json` / weight-file checks still run,
  so the stage isn't purely file-existence.
- Stage 2 canary: if no `canary_commands` configured → passes with
  message "No canary commands configured (skipped)". If configured,
  runs each via `subprocess.run(shell=True, ..., timeout=...)` and
  collects non-zero + timeout + generic-exception failures. One canary
  failure → stage fails.
- Stage 3 coherence: platform-dispatches to
  `_generate_mlx` (Darwin) → fallback to `_generate_transformers`
  (any platform). **Both backends missing** → stage fails with
  `"backend_unavailable"`. Non-empty output + ≥min_tokens +
  non-repetitive → pass.

`FullValidationResult.passed` is the boolean-AND of all stages reached
(short-circuit on failure means it's really "all reached stages
passed"), returned to `run_merge`. The plan's worry about silent
AND-combine was pre-fix; HEAD is correctly gating.

### Lineage recording

- Base model: `ensure_base_registered(model_id)` (lineage.py:88-94) is
  idempotent — called at the top of `register_lora`. Writes a
  `record_id=f"base-{model_id}"` with `generation=0` if absent.
- LoRA: `register_lora(candidate, episode_ids)` (lineage.py:96-129).
  Invoked from `TrainingManager.promote_candidate` inside a
  `try/except` — lineage is observability, failures log but don't
  crash promotion. `generation = base_record.generation` (LoRAs share
  their base's generation — distinct from merges, which increment).
- Merge: `register_merge(manifest, output_model_id)` (lineage.py:131-198).
  Iterates **every** `lora_id` in `source_loras`, aggregates
  `parent_set` (LoRA itself + its parents — i.e., the base model
  edges) and `episode_set` (LoRA's training episodes). Unregistered
  LoRAs are still linked as edges but can't contribute episodes.
  Always adds `base-{target_base}` as a parent to cover single-base
  merges whose target isn't any source's own base.
  `generation = max_generation + 1`. Sorted for stable JSON.

### Promotion gate

`TrainingManager.promote_candidate` (trainer/manager.py:156-185):
- Requires `candidate.metrics` — raises if empty (line 158).
- Reads `EvaluationMetrics.from_dict(candidate.metrics)`.
- `_promotion_gates(candidate, metrics)` (line 202-237) returns
  `(allowed: bool, reasons: list[str])`. Out of bucket to audit the
  gate math, but it is invoked unconditionally, so no
  `require_human_approval` escape hatch exists.
- On pass: `status="promoted"`, `evaluation_status="eligible"`,
  `promotion_reason="passed promotion gates"`,
  `store.update_candidate` + `store.set_active_candidate`, then the
  (try/except'd) `lineage_tracker.register_lora`. Return the candidate.
- On fail: `status="rejected"`, `evaluation_status="ineligible"`,
  `promotion_reason = "; ".join(reasons)`,
  `store.update_candidate` (persists the rejection),
  `raise RuntimeError(reasons)`. **No** lineage record on rejection —
  confirmed by `test_promote_failure_does_not_register_lineage`
  (tests/test_trainer.py:169-203).

## Test Coverage Gaps

The test suite in this bucket is **unusually thorough post-fix**. Gaps
that remain are small:

1. **Real tokenizer for coherence token count.** The only coherence
   test that exercises `_generate_transformers` is the token-slicing
   one (`CoherenceTokenSlicingTests`), which mocks `AutoModelForCausalLM`
   + `AutoTokenizer`. No test verifies that a **real** tokenizer's
   byte-pair-encoded output count matches the `len(output.split()) * 1.3`
   proxy. Because this is a POLISH item, not a correctness gap.
2. **`_merge_with_mlx` alpha/r scaling end-to-end.**
   `test_apply_lora_scales_by_alpha_over_rank` (tests/test_evolution.py:1939)
   tests `_apply_lora_to_weights` directly with numpy arrays — good.
   But `_merge_with_mlx` feeding alpha/r from `_read_lora_config`
   through to `_apply_lora_to_weights` is only covered by the pieces
   separately. A single integration test (mock `load`, mock
   `save_weights`, feed a fake PEFT `adapter.safetensors` + `adapter_config.json`,
   assert resulting weights include `(alpha/r)` scaling) would close
   the loop. Not a correctness gap today because each piece is
   individually correct and the seam is mechanical.
3. **Registry concurrent-write race.** No test equivalent to
   `test_update_merge_concurrent_writers_dont_lose_appends` exists for
   `save_registry` / `set_active_candidate`. If POLISH-g above is
   taken, add a matching test.
4. **`_bake_lora_into_base` cache hit path.** The bake method caches
   by `candidate_id` at `<models_dir>/baked/<candidate_id>`
   (merge.py:309-312). The cache-miss (fresh bake) path is not
   directly tested because it requires PEFT + transformers + torch +
   a real base model on disk. The cache-hit path (early return on
   `config.json` present) is also untested and could silently return a
   stale checkpoint if the LoRA adapter was retrained without changing
   its `candidate_id`. A regression test could `touch` a dummy
   `baked/<id>/config.json` and assert early-return without invoking
   AutoModelForCausalLM — straightforward to add.
5. **`legacy _generate_mergekit_config` is tested but unused in
   production.** See POLISH (d) above. The four tests at lines
   687-825 give coverage credit for a dead code path. Clean up when
   the legacy function is removed.

None of the above rises to BLOCKER or SILENT-DROP. The bucket is in
ship shape relative to the plan.
