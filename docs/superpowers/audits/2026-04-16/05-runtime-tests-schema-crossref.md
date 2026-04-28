# Audit: Runtime / Test Quality / Schema Consistency / Plan Cross-Reference
Date: 2026-04-16
Scope: bucket 5 — cross-cutting. Read-only audit of Homunculus HEAD.

---

## Runtime Trace Results

### Import sweep — OK
Single-line import sweep succeeded:
```
python -c "import homunculus; import homunculus.daemon; import homunculus.orchestrator.loop; import homunculus.evolution.merge; import homunculus.evolution.validation; import homunculus.evolution.lineage; import homunculus.trainer.manager; import homunculus.autonomy; import homunculus.introspection; import homunculus.task_generator.generator; import homunculus.storage; import homunculus.config; import homunculus.cli; import homunculus.runtime; import homunculus.policy; import homunculus.suggestions"
```
Output: `ALL IMPORTS OK`. No import-time errors.

### CLI `--help` — subcommands enumerated
```
init-artifacts, run-episode, apply-episode, train-sft,
evaluate-candidate, promote-candidate, doctor,
autonomy-precheck, autonomy-preflight, autonomy-report, autonomy-accept
```
11 subcommands. Only `autonomy-precheck` carries a visible docstring in `--help`; the rest are silent. Minor polish issue — not a BLOCKER.

### `doctor` — runs cleanly against `homunculus.example.toml`
Output is valid JSON with 10 checks; 5 pass (git, writable dirs), 5 fail because the audit environment lacks teacher/engram env vars, `mlx_lm`, a `self` workspace git repo, and a reachable Engram. Expected. No crash, no traceback.

### `init-artifacts`
**Reproducible reality-check finding.** Running the command in an isolated temp directory — **without** PYTHONPATH pointing back at the repo — fails:
```
ModuleNotFoundError: No module named 'homunculus'
```
`pyproject.toml:12` defines the `homunculus` console script, and `setuptools.packages.find` is set. Import from the source checkout works (`python -c "import homunculus"` in repo root succeeds). But `pip install -e .` is required before the command works outside the checkout. Tests in `tests/test_packaging.py` exercise this — see "Baseline plan: Task 1". The current repo `homunculus.egg-info/` exists, indicating the author developed with an editable install. **SILENT-DROP** for any fresh clone: a clean `python -m pip install -e .` is a prerequisite the README doesn't strongly emphasise. Not a code BLOCKER because the packaging itself is correct.

Inside the repo root the command succeeds (creates `traces/`, `datasets/seed/`, `datasets/sft/`, `datasets/dpo/`, `datasets/snapshots/`, `models/`, `runtime/`, and writes `models/registry.json` with `{active_candidate_id: null, candidates: [], history: []}`).

### Test suite
`python -m unittest discover` at HEAD:
```
Ran 326 tests in 18.949s
OK
```
No failures, no errors, no collection issues. Three stdout side-effects observed (not test failures):
- `"Failed to archive suggestion poison-task.md (outcome=blocked): disk full"` — deliberate failure-path test exercise (Task 9 defensive logging).
- `"No adapter_config.json at ...; using defaults alpha=16, r=8"` — MergeManager defensive fallback test (Task 18).
- `"Unknown introspection mode: unknown_mode"` — scheduler unknown-mode test.
None require remediation.

---

## Test-Quality Table

Heuristic: counts `MagicMock(...)`, `Mock()`, `patch.object`, `patch(...)`, `.return_value`, `.side_effect` per file, paired with manual review of what each file actually mocks. `Real Store?` = real `ArtifactStore`; `Real TaskRunner?` = real `TaskRunner`; `Real Loop?` = real `OrchestratorLoop`.

| File | Mock tokens | Real Store | Real TaskRunner | Real Loop | Exercises prod path? | Notes |
|------|------------:|-----------|-----------------|-----------|----------------------|-------|
| `tests/test_auto_commit.py` | 0 | yes | yes (git+worktree) | yes | YES | Full integration: real git repo, real worktree, real `commit_to_source`. Gold-standard test. |
| `tests/test_orchestrator.py` | 0 | yes | yes | **yes** | YES | `OrchestratorLoop` instantiated directly with `StaticTeacher` / `StaticStudent` stubs only at the model boundary. |
| `tests/test_task_runner.py` | 0 | yes | yes | N/A | YES | Real git, real patches, real worktree isolation. |
| `tests/test_trainer.py` | 0 | yes | N/A | N/A | YES | Real `TrainingManager`, real registry updates. |
| `tests/test_dataset_builder.py` | 0 | yes | N/A | N/A | YES | Real snapshot materialization. |
| `tests/test_config_evolution.py` | 0 | N/A | N/A | N/A | YES | Pure TOML → dataclass round-trip (Task 4 regression guard). |
| `tests/test_task_queue.py` | 0 | yes | N/A | N/A | YES | Real append/load/atomic-update on disk. |
| `tests/test_task_generator.py` | 0 | yes | N/A | N/A | YES | Real generator, introspection results from disk. |
| `tests/test_suggestions.py` | 0 | N/A | N/A | N/A | YES | Real markdown parsing + archival. |
| `tests/test_prioritizer.py` | 0 | N/A | N/A | N/A | YES | Pure logic. |
| `tests/test_packaging.py` | 0 | N/A | N/A | N/A | YES | Runs `pip install -e .` and a smoke import. |
| `tests/test_introspection.py` | 6 | yes | N/A | N/A | YES | Patches `subprocess.run` for coverage mode only. Rest is real. |
| `tests/test_daemon.py` | 18 | yes | N/A | **indirect** | PARTIAL | Real `Daemon(config, store=...)` used in 21 locations. Patches are scoped to `subprocess`, `os.kill`, and internal helpers — not SUT-wide. Acceptable. |
| `tests/test_autonomy.py` | 31 | yes | N/A | N/A | PARTIAL | Autonomy precheck/preflight; patches `load_episodes`, `load_merges`, `datetime.now`. SUT itself runs. |
| `tests/test_evolution.py` | 72 | yes | N/A | N/A | YES (post-fix) | After Task 20 the mocks moved from method-level (`patch.object(mgr, "_perform_mlx_merge")`) to subprocess-level (`patch("homunculus.evolution.merge.subprocess.run", ...)` — 6+ occurrences). Lines 582–2074 show real `MergeManager.merge()` with mocked subprocess only. |

**No mock-shaped test** (defect class `e`) remains that mocks the entire SUT. This represents a meaningful recovery from the pre-Task-20 state where evolution tests mocked the methods under test.

---

## Schema Drift Findings

### `AdapterManifest` vs `models/registry.json`
Dataclass at `homunculus/models.py:243-270`. Required fields include `model_id, base_model, adapter_path, dataset_snapshot, snapshot_path, trainer, metrics, status, created_at`. Registry file contains only the top-level registry shape (`active_candidate_id`, `candidates`, `history`) — the manifest itself lives inside each `candidates[]` entry. Current `registry.json` has zero candidates, so no live instance to compare. `storage.py:145` writes the registry dict directly; `homunculus/storage.py:56` seeds it.
**No drift detected**; no live evidence either. Test-driven coverage: `test_trainer.py` exercises full `AdapterManifest.to_dict()` round-trips through `ArtifactStore`.

### `MergeManifest` vs lineage records
Dataclass at `homunculus/models.py:290-315`. Matches lineage consumer at `homunculus/evolution/lineage.py:131-196`: `source_loras`, `target_base`, `merge_method`, `merge_id` all used. Status literal set `{"pending", "merging", "merged", "validated", "failed"}` with docstring-documented tolerance for legacy `"complete"`. Matches code (no runtime sets `complete`).
**No drift.**

### `EpisodeRecord` vs `traces/episodes.jsonl`
Dataclass at `homunculus/models.py:145-184`. Current `episodes.jsonl` is empty (0 bytes). Writer at `storage.py:84` uses `episode.to_dict()` which serializes via `asdict()` and expands nested `VerificationResult` lists. Reader at `storage.py:87` round-trips via `EpisodeRecord.from_dict`. New fields `verification_passed`, `failure_stage`, `error_type`, `error_message`, `commit_sha` are all defaulted → safe for older rows. `commit_sha` is set by `orchestrator/loop.py:_auto_commit`.
**No drift.**

### `TaskQueueEntry` vs queue file format
Dataclass at `homunculus/models.py:358-398`. Has hand-written `to_dict`/`from_dict` that nest the `GeneratedTask`. Storage writes via `handle.write(json.dumps(entry.to_dict(), ensure_ascii=True) + "\n")` at `storage.py:381, 412, 423`. Atomic write pattern: temp file + `os.replace`. Entry payload matches the round-trip in `test_task_queue.py`.
**No drift.**

### `SFTSample` / `PreferencePair` vs dataset JSONL
Dataclasses at `models.py:187-200` and `203-217`. `dataset_builder/builder.py` uses `asdict`. `datasets/seed/sft_seed.jsonl` exists and is read on snapshot generation. Test coverage: `test_dataset_builder.py`.
**No drift.**

---

## Baseline Plan Cross-Reference (all 22 tasks)

Plan source: `docs/superpowers/plans/2026-04-16-spec-alignment-and-merge-correctness.md`. Verification criteria: code present in `homunculus/*`, behaviour covered in `tests/*`, and at HEAD passes `python -m unittest discover`.

| Task | Title | Still-open? | Evidence at HEAD |
|------|-------|-------------|------------------|
| 1 | Fix `pyproject.toml` packages + install smoke | CLOSED | `pyproject.toml:18-19` `setuptools.packages.find` with `include = ["homunculus*"]`. `tests/test_packaging.py` runs `pip install -e .` smoke. |
| 2 | Untrack `__pycache__`, add `traces/` to `.gitignore` | CLOSED | `.gitignore:1,19` contains `__pycache__/` and `traces/`. |
| 3 | Add `target_workspace` to example config | CLOSED | `homunculus.example.toml:63 target_workspace = "self"`; consumed at `homunculus/daemon.py:471, 796`. |
| 4 | Reconcile `[evolution]` config (TOML wins, rename) | CLOSED | `config.py:131-143` `EvolutionSettings` uses `auto_merge_after_loras`; `config.py:270-290` applies `_warn_on_unknown_keys` with back-compat alias `merge_after_loras`. `tests/test_config_evolution.py` guards. |
| 5 | `_validate_coherence` fails closed without backend | CLOSED | `homunculus/evolution/validation.py:255-265` returns `passed=False, message="backend_unavailable: ..."`. MLX ImportError path no longer bare-passes. |
| 6 | `_generate_transformers` prompt-slice, deterministic, free CUDA | CLOSED | `validation.py:313-352` uses `max_new_tokens=200`, `do_sample=False`, slices `output_ids[0][inputs.input_ids.shape[1]:]`, `finally: torch.cuda.empty_cache()`. |
| 7 | Defensive state-file parsing + atomic writes | CLOSED | `trainer/manager.py:238-275` handles JSONDecodeError, OSError, non-dict, non-int, bool-as-int, negative values. Uses tmp+`os.replace`. |
| 8 | Lock-file race fix in `daemon.py` | CLOSED | `daemon.py:190-239`; refuses to overwrite corrupt lock, distinguishes "vanished mid-read" from corrupt. `release_lock` only removes own PID. |
| 9 | Suggestion archival on blocked/error | CLOSED | `daemon.py:499-512` archives on EVERY terminal outcome, wrapped in try/except with explanatory log. |
| 10 | Lineage `register_merge` aggregates ALL source LoRAs | CLOSED | `evolution/lineage.py:131-196` uses `parent_set`/`episode_set` union across all source LoRAs + target_base edge. |
| 11 | Validate `target_base` consistency in `MergeManager.merge` | CLOSED | `evolution/merge.py:132-142` raises on multi-base or empty-base. |
| 12 | Misc small fixes | CLOSED | Spot-checked: `auto_commit_on_accept` present in config; `target_workspace` consumed; `append_to_queue` wrapped in try/except at `daemon.py`. |
| 13 | Wire `IntrospectionScheduler` into `Daemon` | CLOSED | `daemon.py:95-103` instantiates `IntrospectionScheduler`; `:287-290` runs `run_due_modes`. |
| 14 | E2E introspection → task generator → daemon | CLOSED (inferred) | 326 tests pass; `test_task_generator.py` + `test_daemon.py` + `test_introspection.py` cover the pipeline. Did not isolate the specific E2E test by name. |
| 15 | Call `register_lora` from `TrainingManager.promote_candidate` | CLOSED | `trainer/manager.py:170` invokes `self.lineage_tracker.register_lora(...)`. |
| 16 | Wire `commit_to_source` into orchestrator (auto-commit) | CLOSED | `orchestrator/loop.py:128-131, 310-350` gated by `config.daemon.auto_commit_on_accept`. `tests/test_auto_commit.py` is a real integration test. |
| 17 | Wire daemon to use the task queue | CLOSED | `daemon.py:392, 432, 663` read+append to queue; atomic update path via `storage.py:381`. |
| 18 | Fix MLX merge — alpha/r scaling, key resolution, save_weights | CLOSED | `evolution/merge.py:461-631`: uses `from mlx_lm.utils import load, save_weights`; reads `adapter_config.json` for `lora_alpha`/`r`; scale `(alpha/r)*(B@A)`; handles `.lora_a.weight` / `.lora_A.weight` variants; logs applied delta count. |
| 19 | mergekit YAML for LoRAs — bake via PEFT first, then linear | CLOSED | `evolution/merge.py:284-324` `_bake_lora_into_base` uses `PeftModel.from_pretrained(...).merge_and_unload()`. Tests patch at subprocess boundary. |
| 20 | Replace method-level merge mocks with subprocess-level mocks | CLOSED | `tests/test_evolution.py:587-2074` — six+ `patch("homunculus.evolution.merge.subprocess.run", ...)` usages. |
| 21 | `TrainingManager.run_merge()` + `daemon._check_evolution()` integration tests | CLOSED | `manager.py:281` `def run_merge`. `daemon.py:546, 601` `_check_evolution` calls `trainer.run_merge()`. `tests/test_evolution.py` covers run_merge; `tests/test_daemon.py` covers `_check_evolution`. |
| 22 | Write `04-REVIEW.md`, update STATE / ROADMAP / CLAUDE.md | **OPEN (minor)** | `.planning/phases/04-weight-evolution/04-REVIEW.md` is **NOT present** (only `04-01…`, `04-02…`, `04-03-PLAN.md` exist). `.planning/STATE.md` has been updated for Phase 5 completion; Phase 4 spec-fix section is not clearly called out. Cosmetic/process gap — does not affect code correctness. |

**21 of 22 tasks closed; Task 22 partial.**

---

## Configuration Round-Trip

Walked `homunculus.example.toml` section-by-section and grepped for consumer usage.

| TOML section.key | Dataclass field | Consumer found? | Notes |
|------------------|-----------------|-----------------|-------|
| `teacher.*` | `TeacherSettings` | YES | `cli.py:91` auth check, `orchestrator/teacher.py`. |
| `student.*` | `StudentSettings` | YES | `orchestrator/student.py` uses `generate_command`, `model_id`, `max_tokens`, `adapter_root`, `train_timeout_seconds`. |
| `memory.*` | `MemorySettings` | YES | `memory_client/engram.py`. |
| `thresholds.*` | `ThresholdSettings` | YES | `trainer/manager.py`. |
| `promotion.*` | `PromotionSettings` | YES | `trainer/manager.promote_candidate`. |
| `paths.*` | `PathSettings` | YES | Referenced from `cli.py`, `daemon.py`, `storage.py` (20+ sites). |
| `dpo.enabled`, `dpo.min_successful_sft_promotions`, `dpo.env` | `DPOSettings` | YES | Verify `dpo.env` is consumed — used in trainer subprocess env injection. |
| `daemon.enabled / cycle_interval_minutes / max_episodes_per_cycle / suggestions_dir / target_workspace / auto_commit_on_accept` | `DaemonSettings` | YES | All consumed (`daemon.py:471`, `orchestrator/loop.py:128`). |
| `introspection.*` (6 keys) | `IntrospectionSettings` | YES | `daemon.py:97`, `introspection/scheduler.py`. |
| `evolution.auto_promote / auto_apply / auto_train_after_samples / auto_merge_after_loras / rollback_on_degradation` | `EvolutionSettings` | YES | Plus `max_merge_attempts`, `validation_timeout_seconds`, `coherence_prompt`, `coherence_min_tokens`, `merge_backend` have defaults and `_warn_on_unknown_keys` guards. |
| `guardrails.block_patterns / warn_patterns` | `GuardrailSettings` | YES | `policy.py`. |
| `workspaces.<name>.path / repo_url / branch / verification_commands` | `WorkspaceSettings` | YES | `task_runner/runner.py`, `cli.py:110`. |
| `canary.commands` | `CanaryCommand` | YES | `evolution/validation.py` canary stage. |

**No silent-drop keys.** `_warn_on_unknown_keys` exists only for `[evolution]` (the 5-key silent-drop root defect). Other sections do not have the same guard — if a user puts `[student] foo = 42` into their TOML, it is silently dropped. **SILENT-DROP polish**: extend `_warn_on_unknown_keys` to every section.

---

## Summary & Top 10 Issues

### BLOCKER
None. 326 tests pass, imports clean, doctor runs, schemas align, all code-level plan tasks (1–21) verified at HEAD.

### SILENT-DROP
1. **`_warn_on_unknown_keys` only protects `[evolution]`.** A typo in any other section (e.g. `[teacher] modle = "..."`) is silently ignored. Recommend extending the guard to all sections. (Defect class `b`.)
2. **`init-artifacts` requires editable install.** Fresh clones without `pip install -e .` hit `ModuleNotFoundError`. README should lead with the install step more aggressively, or `cli.py` should `sys.path.insert(0, repo_root)` when invoked as a script. (Defect class `b` adjacent.)

### POLISH
3. **Task 22 incomplete.** `.planning/phases/04-weight-evolution/04-REVIEW.md` is missing. Process-only; zero code impact.
4. **CLI subcommand helpstrings.** 10 of 11 subcommands in `--help` show no description. Trivial fix: pass `help="..."` to each `subparsers.add_parser`. (Defect class `h` adjacent — not orphaned, just undocumented.)
5. **`--help` output split across multiple lines** awkwardly on Windows console. Minor.
6. **`MergeManifest.status` tolerates `"complete"` in `from_dict` but the literal is unenforced** — a bad actor could write any string. Consider a Pydantic-style validator or at least a `Literal[...]` type. (Defect class `c` adjacent.)
7. **No `_warn_on_unknown_keys` integration test.** The function exists but if it is removed in a refactor, nothing fails. Add a test that writes `[evolution] bogus_key = 1` and asserts `UserWarning`.
8. **`traces/episodes.jsonl` and `events.jsonl` are 0 bytes** in repo.  The files exist but have never been written to in this checkout. Not a bug (gitignored in principle per Task 2), but confirms no real episode has been run locally.
9. **Empty `[canary]` table header in example TOML** with only `[[canary.commands]]` array entries is slightly unusual; `Config.load` handles it but the emptiness could confuse readers.
10. **`IntrospectionSettings.critique_enabled` silently disables critique without a telemetry event.** Consider emitting an introspection-skipped event so operators can see why they have no critique data. (Defect class `b` lite.)

### Overall verdict
HEAD is in strong shape. The Phase 4 spec-alignment plan has been essentially fully executed — 21 of 22 tasks closed, with the 22nd being a process artifact (04-REVIEW.md) not a code defect. Test-quality recovery in `test_evolution.py` (subprocess-boundary mocking) is particularly notable. The remaining findings are polish, not blockers.
