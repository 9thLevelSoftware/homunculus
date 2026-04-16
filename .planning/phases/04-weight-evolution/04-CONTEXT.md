# Phase 4: Weight Evolution — Context

## Phase Goal

Enable continuous model improvement — agent trains and merges its own weights.

## Requirements

| ID | Requirement | Description |
|----|-------------|-------------|
| WE-1 | LoRA merge pipeline | Merge accumulated LoRAs into base model (mergekit/MLX) |
| WE-2 | Lineage tracking | Full history of base generations, LoRAs merged, episodes incorporated |
| WE-3 | Merge validation | Model loads, generates coherent output, passes canary suite |
| WE-4 | Auto-trigger | Train after N samples, merge after N LoRAs |

## Success Criteria

- [ ] `evolution/merge.py` successfully merges LoRA stack to base
- [ ] `evolution/lineage.py` tracks full model history
- [ ] `evolution/validation.py` catches bad merges before adoption
- [ ] Merge failure generates introspection task after 3 consecutive failures
- [ ] Tests cover merge success, merge failure, and rollback scenarios

## Existing Assets

### Files to Create
- `homunculus/evolution/__init__.py`
- `homunculus/evolution/merge.py`
- `homunculus/evolution/lineage.py`
- `homunculus/evolution/validation.py`
- `tests/test_evolution.py`

### Files to Modify
- `homunculus/trainer/manager.py` — Integrate merge triggers
- `homunculus/config.py` — Add `EvolutionSettings` dataclass

### Relevant Existing Code

**TrainingManager** (`trainer/manager.py`):
- `should_train_sft()` — trigger based on samples/days
- `run_sft()` — training execution
- `evaluate_candidate()` — candidate evaluation
- Already creates `AdapterManifest` with basic `lineage` field

**AdapterManifest** (`models.py`):
- `lineage: list[str]` — basic ancestry (snapshot IDs)
- `base_model: str` — reference to base model
- `status: str` — training status tracking

**Registry** (`storage.py` → `models/registry.json`):
- `active_candidate_id` — current promoted model
- `candidates` — list of all trained adapters
- `history` — previous active candidates

**Config** (`config.py`):
- Already has `[evolution]` stub section (unused)
- `ThresholdSettings` — training triggers
- `PromotionSettings` — candidate gates

## Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Merge backend | mergekit (primary) + MLX fallback | mergekit is the standard LoRA merge tool; MLX for Mac compatibility |
| Lineage storage | JSONL append-log + registry.json | Consistent with existing event sourcing pattern |
| Validation stages | 3-stage (load, canary, coherence) | Catches different failure modes at appropriate cost |
| Merge trigger | After N promoted LoRAs | Align with training pattern (after N samples) |

## Plan Structure

| Plan | Wave | Title | Agent | Depends On |
|------|------|-------|-------|------------|
| 04-01 | 1 | Infrastructure (Config, Models, Storage) | Senior Developer | — |
| 04-02 | 2 | Merge Pipeline | AI Engineer | 04-01 |
| 04-03 | 2 | Lineage Tracking | Senior Developer | 04-01 |
| 04-04 | 3 | Validation + Integration | AI Engineer | 04-02, 04-03 |

## Hardware Context

From PROJECT.md:
- **Primary**: RTX 5070 (12GB), 64GB RAM, i9-12900KS — QLoRA training, episode execution
- **Backup**: Mac Mini M4, 24GB unified — MLX inference, backup training

Merge operations should:
- Detect available hardware (CUDA vs MPS)
- Use appropriate backend (mergekit for CUDA, MLX merge for MPS)
- Handle memory constraints (12GB VRAM limit on primary)
