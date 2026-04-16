# Plan 04-04 Summary: Validation + Integration

**Status**: Complete
**Wave**: 3
**Agent**: engineering-ai-engineer

## What Changed

### Files Created
- `homunculus/evolution/validation.py` — MergeValidator class with 3-stage validation pipeline:
  - `_validate_load()` — Check model files exist and parse correctly
  - `_validate_canary()` — Run configured canary commands (or skip gracefully)
  - `_validate_coherence()` — Generate output and check for degeneracy (platform-aware, skips gracefully)
  - `_is_repetitive()` — Detect degenerate repetitive output
  - `ValidationResult` and `FullValidationResult` dataclasses

### Files Modified
- `homunculus/evolution/__init__.py` — Added exports for MergeValidator, ValidationResult, FullValidationResult
- `homunculus/trainer/manager.py` — Added:
  - Lazy-loaded evolution properties (merge_manager, lineage_tracker, merge_validator)
  - `_get_consecutive_merge_failures()` / `_set_consecutive_merge_failures()` — Persistent state
  - `should_merge()` — Respects evolution.enabled config
  - `run_merge()` — Full merge + validation + lineage registration
  - `should_generate_merge_failure_task()` — Check if introspection task needed
  - `reset_merge_failure_count()` — Reset failure counter on success
- `homunculus/task_generator/generator.py` — Added `generate_merge_failure_task()` for introspection tasks
- `homunculus/daemon.py` — Added `_check_evolution()` method called at end of `run_once()`
- `tests/test_evolution.py` — Added ValidationTests (7), TrainingManagerEvolutionTests (3), IntegrationTests (2), TaskGeneratorMergeTests (2)

## Verification

| Command | Result | Pass? |
|---------|--------|-------|
| `from homunculus.evolution.validation import MergeValidator` | OK | Yes |
| `TrainingManager.should_merge` exists | True | Yes |
| `TrainingManager.run_merge` exists | True | Yes |
| `TaskGenerator.generate_merge_failure_task` exists | True | Yes |
| `Daemon._check_evolution` exists | True | Yes |
| `python -m unittest tests.test_evolution -v` | 53 tests OK | Yes |
| `python -m unittest discover -v` | 230 tests OK | Yes |

## Decisions Made
- Used lazy imports (TYPE_CHECKING pattern) to avoid circular dependencies
- Validation gracefully skips stages when backends unavailable (coherence on Windows)
- Merge failure count persisted to `runtime/evolution_state.json` for restart safety
- Daemon appends events for merge start/complete/fail for audit trail
- Failure task generated after 3 consecutive failures (configurable via max_merge_attempts)

## Requirements Covered
- WE-3: Merge validation (complete)
- WE-4: Auto-trigger and failure recovery (complete)

## Phase 4 Complete
The weight evolution system is now fully integrated:
- LoRA merges trigger automatically after N promoted adapters
- Merges are validated in 3 stages before adoption
- Failed merges generate introspection tasks for self-improvement
- Full lineage tracking maintains model genealogy
