# Architecture and Artifacts

This document describes the runtime design that the current code actually implements.

## System components

### Orchestrator

The orchestrator owns the episode lifecycle:

`assess -> preflight -> recall -> plan -> execute -> reflect -> curate`

It is also responsible for:

- allocating a unique `episode_id`
- incrementing `attempt_index` for a logical `task_id`
- persisting a terminal `EpisodeRecord` for every attempt
- persisting `episode_failed` events when runtime exceptions occur

### Teacher

The teacher is any OpenAI-compatible chat endpoint that returns structured JSON. The runtime accepts content in either of these forms:

- plain string JSON
- list-of-text-parts content that can be flattened into a string

The parsed JSON must contain:

- `plan`
- `candidate_patch`
- `rationale`

### Student

The student is a local subprocess wrapper around `mlx_lm.generate`. It is advisory in the current implementation: the orchestrator sends the student output to the teacher as a hint.

### Memory

The memory client is Engram-compatible over HTTP. The orchestrator uses it at four points:

- recall before planning
- warning persistence when blocked at preflight
- outcome recording after execution
- failure/growth persistence on bad runs

### Task runner

The task runner enforces the source-repo safety model:

- source repo must be clean
- each episode executes in a linked detached worktree
- verification runs in that worktree
- the worktree is removed afterward
- patch application to the source repo is a separate explicit step

### Dataset builder

The dataset builder has two responsibilities:

- append verified accepted episodes to the append-only SFT store
- derive winner/loser DPO pairs from accepted versus failed attempts

For training, it materializes immutable SFT snapshots under `datasets/snapshots/sft/`.

### Training manager

The training manager:

- decides when SFT should run
- materializes an immutable SFT snapshot
- launches `mlx_lm.lora`
- records candidate manifests with snapshot lineage
- records evaluation separately from promotion
- enforces promotion gates before activation

## Runtime data flow

### Episode execution flow

1. `run-episode` creates a unique `episode_id`
2. the runtime writes an initial empty patch artifact
3. workspace preflight ensures git exists and the repo is clean
4. memory recall pulls relevant context from Engram
5. student inference runs locally
6. teacher generation returns a plan and candidate patch
7. guardrails inspect the prompt, patch, and recalled warnings/failures
8. if allowed, the task runner creates `runtime/worktrees/<episode_id>`
9. patch application and verification happen in the linked worktree
10. the canonical diff is written back to `traces/patches/<episode_id>.patch`
11. reflect/curate steps run
12. the final episode record is appended to `traces/episodes.jsonl`

### Training flow

1. curated SFT samples accumulate in `datasets/sft/*.jsonl`
2. seed SFT data is read from `datasets/seed/sft_seed.jsonl`
3. snapshot composition enforces the self-generated ratio
4. a materialized snapshot is written to `datasets/snapshots/sft/<snapshot_id>/`
5. the trainer points `mlx_lm.lora` at that snapshot directory
6. a candidate manifest is registered in `models/registry.json`
7. evaluation writes metrics into the candidate manifest
8. promotion updates the active candidate pointer only after gates pass

## Core persisted types

### EpisodeRecord

Important fields:

- `episode_id`: unique per-attempt identifier
- `task_id`: logical task key
- `attempt_index`: retry number for that task
- `outcome`: `accepted|reverted|blocked|error`
- `patch_path`: canonical patch artifact path
- `failure_stage`: stage where the run failed, if applicable
- `error_type`
- `error_message`

### SFTSample

Stored in chat-message format and keyed by `episode_id`, not by `task_id`.

### PreferencePair

Stores winner and loser `episode_id` values so retries do not collapse into one provenance record.

### DatasetSnapshot

Contains:

- `snapshot_id`
- `snapshot_path`
- split counts
- selected episode IDs per split
- self-generated ratio
- config hash

### AdapterManifest

Contains:

- `candidate_id`
- `dataset_snapshot`
- `snapshot_path`
- `training_command`
- `sample_counts`
- `self_generated_ratio`
- `evaluation_status`
- `promotion_reason`

## Artifact layout

```text
traces/
  events.jsonl
  episodes.jsonl
  patches/
    <episode_id>.patch

datasets/
  seed/
    sft_seed.jsonl
    dpo_seed.jsonl
  sft/
    train.jsonl
    valid.jsonl
    test.jsonl
  dpo/
    train.jsonl
    valid.jsonl
  snapshots/
    sft/
      <snapshot_id>/
        train.jsonl
        valid.jsonl
        test.jsonl
        snapshot.json

models/
  adapters/
    <candidate_id>/
  registry.json

runtime/
  worktrees/
    <episode_id>/
```

## Safety boundaries

The current implementation intentionally enforces these boundaries:

- no operation on a dirty source repo
- no source mutation during `run-episode`
- no auto-application of accepted patches
- no auto-promotion of evaluated candidates
- no live DPO use at launch

Those constraints are not incidental. They are the main reason the scaffold is safe enough to iterate on.

## Known limitations

- canary commands exist in config, but there is no first-class CLI runner for them yet
- rollback exists in code but not as a CLI command
- teacher/provider compatibility is still only as good as the endpoint’s adherence to OpenAI-style response shapes
- `doctor` validates availability and reachability, not semantic correctness of your external services

## Recommended next steps after documentation

If you want to keep hardening the system, the next high-value additions are:

1. a rollback CLI
2. a friendly `list-episodes` or `show-episode` CLI
3. a formal canary evaluation runner
4. a friendlier registry inspector
5. richer Engram integration tests against a real service instance
