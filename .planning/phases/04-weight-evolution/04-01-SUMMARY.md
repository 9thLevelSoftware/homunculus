# Plan 04-01 Summary: Infrastructure (Config, Models, Storage)

**Status**: Complete
**Wave**: 1
**Agent**: engineering-senior-developer

## What Changed

### Files Modified
- `homunculus/config.py` — Added `EvolutionSettings` dataclass with merge_after_loras, max_merge_attempts, validation_timeout_seconds, coherence_prompt, coherence_min_tokens, merge_backend fields. Added `evolution: EvolutionSettings` field to `HomunculusConfig`. Added TOML parsing for `[evolution]` section with graceful defaults.
- `homunculus/models.py` — Added `MergeManifest` dataclass (merge_id, source_loras, target_base, merge_method, merge_params, status, created_at, completed_at, output_path, validation_results, error_message with to_dict/from_dict). Added `LineageRecord` dataclass (record_id, record_type, model_id, parent_ids, episode_ids, merge_id, generation, created_at, metadata with to_dict/from_dict).
- `homunculus/storage.py` — Added imports for MergeManifest and LineageRecord. Added merge persistence methods (merges_path, append_merge, load_merges, get_merge, update_merge with atomic write). Added lineage persistence methods (lineage_path, append_lineage, load_lineage, get_lineage_record, get_lineage_by_generation).

### Files Created
- `tests/test_evolution.py` — Comprehensive tests for EvolutionSettings defaults, MergeManifest serialization, LineageRecord serialization, and all storage methods for merges and lineage.

## Verification

| Command | Result | Pass? |
|---------|--------|-------|
| `python -c "from homunculus.config import EvolutionSettings; ..."` | `merge_after_loras=3` | Yes |
| `python -c "from homunculus.models import MergeManifest; ..."` | Full dict with all fields | Yes |
| `python -c "from homunculus.models import LineageRecord; ..."` | `generation=0, parents=['base', 'lora1']` | Yes |
| `python -c "from homunculus.storage import ArtifactStore; ..."` | `traces\merges.jsonl` | Yes |
| `python -m unittest tests.test_evolution -v` | 13 tests OK | Yes |
| `python -m unittest discover -v` | 190 tests OK | Yes |

## Decisions Made
- Used atomic writes (temp file + os.replace) for `update_merge()` following the existing `update_queue_entry()` pattern
- Graceful defaults for all EvolutionSettings fields to avoid breaking existing configs
- LineageRecord uses generation=0 for base models, incrementing with each merge

## Requirements Covered
- WE-1 (partial): Infrastructure for LoRA merge pipeline
- WE-2 (partial): Infrastructure for lineage tracking

## Ready For
Plans 04-02 (Merge Pipeline) and 04-03 (Lineage Tracking) can now build on this foundation.
