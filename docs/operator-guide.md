# Operator Guide

This guide is for the person running `homunculus` against a real repository.

## Core operating model

The source repo is treated as protected state.

`homunculus` does not edit it directly during `run-episode`. Instead it:

1. checks that the source repo is clean
2. creates a linked detached worktree under `runtime/worktrees/<episode_id>`
3. applies the teacher patch there
4. runs verification there
5. stores the resulting patch under `traces/patches/<episode_id>.patch`
6. removes the worktree

Only `apply-episode` touches the source repo.

## Typical daily workflow

### 1. Check readiness

```powershell
python -m homunculus.cli doctor --config homunculus.toml
```

If this fails because the workspace is dirty, stop and clean up the repo first.

### 2. Run an episode

```powershell
python -m homunculus.cli run-episode --config homunculus.toml --workspace self --task-id parser-fix --prompt "Fix the failing parser tests"
```

The command returns an episode JSON record. Important fields:

- `episode_id`: unique ID for this attempt
- `task_id`: logical task key
- `attempt_index`: retry number for the task
- `outcome`: `accepted`, `reverted`, `blocked`, or `error`
- `patch_path`: stored patch artifact
- `failure_stage`: where the run failed if blocked or errored

### 3. Inspect the results

Look at:

- `traces/events.jsonl`
- `traces/episodes.jsonl`
- `traces/patches/<episode_id>.patch`

Interpret outcomes this way:

- `accepted`: verification passed inside the isolated worktree
- `reverted`: verification failed inside the isolated worktree
- `blocked`: preflight or guardrails stopped execution
- `error`: runtime failure occurred and was persisted

### 4. Apply an accepted patch

Only do this after review.

```powershell
python -m homunculus.cli apply-episode --config homunculus.toml --episode-id <episode-id>
```

This command:

- re-checks that the source repo is clean
- applies the stored patch artifact to the source repo
- re-runs verification commands in the source repo
- resets the repo back to `HEAD` if verification fails

If the repo is dirty when you run `apply-episode`, the command stops before doing anything.

## Understanding artifacts

### `traces/events.jsonl`

Append-only lifecycle log. Typical event types:

- `assess`
- `preflight`
- `preflight_blocked`
- `recall`
- `plan`
- `execute`
- `reflect`
- `curate`
- `episode_failed`
- `episode_completed`

### `traces/episodes.jsonl`

One normalized record per attempt. This is the durable audit log.

### `traces/patches/`

Canonical patch artifacts keyed by `episode_id`.

### `datasets/sft/*.jsonl`

Append-only curated SFT samples split into `train`, `valid`, and `test`.

### `datasets/dpo/*.jsonl`

Append-only DPO preference pairs. Present now as scaffolding; not part of live launch.

### `datasets/snapshots/sft/<snapshot_id>/`

Immutable materialized training input for one SFT run:

- `train.jsonl`
- `valid.jsonl`
- `test.jsonl`
- `snapshot.json`

### `models/registry.json`

Registry of candidates, active candidate pointer, and promotion history.

## Running SFT

### Simulate first

```powershell
python -m homunculus.cli train-sft --config homunculus.toml --simulate
```

Simulation still:

- materializes the snapshot
- creates a candidate record
- writes a simulated adapter artifact

### Real training

```powershell
python -m homunculus.cli train-sft --config homunculus.toml
```

Preconditions:

- seed SFT data exists
- valid and test splits are non-empty
- approved self-generated train samples exist
- `mlx_lm` is installed

The command records:

- `dataset_snapshot`
- `snapshot_path`
- `training_command`
- `sample_counts`
- `self_generated_ratio`

## Evaluating and promoting a candidate

### Prepare a metrics file

Example `metrics.json`:

```json
{
  "compile_pass_rate": 1.0,
  "task_success_rate": 0.72,
  "average_retries_to_success": 0.9,
  "regression_count": 0,
  "memory_usefulness_score": 0.58,
  "tool_misuse_rate": 0.0
}
```

### Record evaluation

```powershell
python -m homunculus.cli evaluate-candidate --config homunculus.toml --candidate-id <candidate-id> --metrics-file metrics.json
```

This does not activate the candidate.

### Promote

```powershell
python -m homunculus.cli promote-candidate --config homunculus.toml --candidate-id <candidate-id> --human-approved
```

Promotion will fail if:

- human approval is required and not supplied
- the candidate was not evaluated
- canary regressions are non-zero
- task success delta is too small
- tool misuse regresses
- retry count regresses
- compile pass rate regresses
- the snapshot path recorded in the manifest does not exist

## Failure handling

If an episode throws during memory recall, teacher generation, patch application, verification, reflection, or curation, the runtime still persists:

- an `error` episode record
- the failure stage
- the exception type
- the exception message
- an `episode_failed` event

That means you should debug by inspecting the persisted traces first, not by trying to reproduce from memory.

## Recommended operating discipline

- Keep one human responsible for approving `apply-episode`.
- Keep one human responsible for approving `promote-candidate`.
- Never let the agent run against a repo with uncommitted work.
- Keep verification commands stronger than the patch generator.
- Treat `doctor` failures as launch blockers, not as warnings.
- Keep DPO disabled until the SFT path has already proven stable.

## Missing operator features

Current gaps you should be aware of:

- no CLI command for rollback yet
- no CLI command for browsing episodes or registry in a friendly format
- no built-in metrics generator for `evaluate-candidate`
- no bundled seed-data generator

Those are reasonable next additions, but they are not required to operate the current scaffold safely.
