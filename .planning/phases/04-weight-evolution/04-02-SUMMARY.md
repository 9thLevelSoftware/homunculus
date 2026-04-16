# Plan 04-02 Summary: Merge Pipeline

**Status**: Complete
**Wave**: 2
**Agent**: engineering-ai-engineer

## What Changed

### Files Created
- `homunculus/evolution/__init__.py` — Package init with exports for MergeManager, MergeResult, detect_backend
- `homunculus/evolution/merge.py` — Complete merge pipeline implementation:
  - `detect_backend()` — Hardware detection (returns "mergekit" for CUDA/CPU, "mlx" for Apple Silicon)
  - `MergeResult` dataclass — Result container for merge operations
  - `MergeManager` class — Full merge orchestration:
    - `should_merge()` — Checks if threshold of promoted LoRAs is reached
    - `get_merge_candidates()` — Returns LoRAs promoted since last merge
    - `merge()` — Executes merge with appropriate backend
    - `_merge_with_mergekit()` — mergekit backend (CUDA/CPU)
    - `_merge_with_mlx()` — MLX backend (Apple Silicon)
    - `_generate_mergekit_config()` — YAML config generator for linear/TIES/DARE methods
    - `_load_lora_weights()` — safetensors LoRA weight loader
    - `_apply_lora_to_weights()` — LoRA weight application logic

### Files Modified
- `tests/test_evolution.py` — Added MergeManagerTests class with 16 new tests

## Verification

| Command | Result | Pass? |
|---------|--------|-------|
| `from homunculus.evolution import MergeManager` | OK | Yes |
| `detect_backend()` | mergekit | Yes |
| `python -m unittest tests.test_evolution.MergeManagerTests -v` | 16 tests OK | Yes |
| `python -m unittest discover` | 216 tests OK | Yes |

## Decisions Made
- Backend detection returns "mergekit" on Windows with CUDA, "mlx" on Apple Silicon
- Graceful error handling when mergekit/pyyaml/mlx not installed
- Supports linear, TIES, and DARE merge methods
- Uses existing ArtifactStore methods from Plan 04-01

## Requirements Covered
- WE-1: LoRA merge pipeline (complete)
- WE-4 (partial): Auto-trigger based on promoted LoRA count

## Ready For
Plan 04-04 (Validation + Integration) can now use MergeManager to trigger merges.
