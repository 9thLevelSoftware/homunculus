# Architecture and Artifacts

This document describes the runtime design the current code implements.

## System Components

- **CLI:** `homunculus/cli.py` exposes artifact initialization, episodes,
  training, evaluation, promotion, autonomy checks, reports, acceptance, and
  `harness-check`.
- **Daemon:** `homunculus/daemon.py` runs continuous cycles, dispatches tasks,
  archives terminal work, updates watchdog state, and triggers evolution.
- **Orchestrator:** `homunculus/orchestrator/loop.py` owns the episode lifecycle:
  `assess -> preflight -> recall -> plan -> execute -> reflect -> curate`.
- **Task runner:** `homunculus/task_runner/runner.py` enforces clean workspaces,
  linked worktree execution, verification, source patch application, and commits.
- **Memory:** `homunculus/memory_client/` provides Engram-compatible recall and
  outcome persistence.
- **Datasets:** `homunculus/dataset_builder/` curates accepted episodes into
  append-only SFT/DPO stores and materializes immutable SFT snapshots.
- **Training:** `homunculus/trainer/manager.py` runs SFT, records evaluation,
  promotes eligible candidates, and orchestrates merge/validation.
- **Evolution:** `homunculus/evolution/` handles LoRA merge, post-merge
  validation, rollback support, and lineage.
- **Autonomy:** `homunculus/autonomy/` provides precheck, preflight, reporting,
  acceptance predicates, and watchdog instrumentation.
- **Harness:** `homunculus/harness.py` validates docs/config/CI alignment for
  agent-first operation.

## Episode Data Flow

1. `run-episode` or the daemon creates a unique `episode_id`.
2. The runtime writes an initial patch artifact.
3. The source workspace must be a clean git repo.
4. Engram recall returns relevant memories.
5. The local student produces a hint.
6. The teacher returns JSON with `plan`, `candidate_patch`, and `rationale`.
7. Guardrails inspect prompt, patch, and recalled warnings/failures.
8. The task runner creates `runtime/worktrees/<episode_id>`.
9. The patch and verification commands run in that linked worktree.
10. The canonical diff is written to `traces/patches/<episode_id>.patch`.
11. If verification passes and `daemon.auto_commit_on_accept = true`, the patch
    is applied to the source workspace and committed with `Episode-ID` and
    `Task-ID` footers.
12. Reflect and curate steps persist memory and training data.
13. The final `EpisodeRecord` is appended to `traces/episodes.jsonl`.

## Training And Evolution Flow

1. Accepted episodes become SFT samples under `datasets/sft/*.jsonl`.
2. Snapshot composition combines seed and self-generated samples under
   `datasets/snapshots/sft/<snapshot_id>/`.
3. `train-sft` records a candidate manifest in `models/registry.json`.
4. `evaluate-candidate` stores metrics and eligibility.
5. `promote-candidate` activates an eligible candidate without a human approval
   flag; metric gates are the guard.
6. The daemon checks whether enough promoted LoRAs exist to merge.
7. Merge validation must pass before lineage advances the base generation.

## Core Persisted Types

- `EpisodeRecord` - complete attempt outcome, verification, patch, and commit
  metadata.
- `SFTSample` and `PreferencePair` - curated training records with episode
  provenance.
- `DatasetSnapshot` - immutable SFT snapshot metadata and selected episodes.
- `AdapterManifest` - candidate adapter, metrics, status, lineage, and training
  command.
- `MergeManifest` and `LineageRecord` - model merge evidence and generation
  history.
- `TaskQueueEntry` - restart-safe daemon queue entry with status and outcome.
- `AutonomyReport` and `AcceptanceVerdict` - soak evidence and pass/fail
  criteria.

## Artifact Layout

```text
traces/
  events.jsonl
  episodes.jsonl
  introspection.jsonl
  lineage.jsonl
  merges.jsonl
  patches/<episode_id>.patch

datasets/
  seed/{sft_seed,dpo_seed}.jsonl
  sft/{train,valid,test}.jsonl
  dpo/{train,valid}.jsonl
  snapshots/sft/<snapshot_id>/

models/
  adapters/<candidate_id>/
  registry.json

runtime/
  worktrees/<episode_id>/
  daemon_state.json
  daemon.pid
  task_queue.jsonl
  task_history.jsonl
  watchdog.json
```

## Safety Boundaries

- Episode execution starts only from a clean source workspace.
- Candidate patches are verified in isolated linked worktrees before source
  mutation.
- Source commits happen only for accepted episodes when auto-commit is enabled.
- Training reads immutable snapshots, not mutable live dataset tails.
- Promotion is automated but still gated by metrics and snapshot existence.
- Merge advancement requires validation; failed merges produce investigation
  signals.
- Watchdog failures are advisory and must not crash the daemon.

## Known Limitations

- GitHub PR publishing is not first-class because this checkout has no remote;
  CI is present for future remote use.
- `doctor` checks reachability and availability, not semantic correctness of
  external teacher or Engram services.
- Runtime warning noise from watchdog persistence tests should be reduced.
- Structural import-boundary checks are not yet enforced beyond packaging and
  harness docs/config checks.
