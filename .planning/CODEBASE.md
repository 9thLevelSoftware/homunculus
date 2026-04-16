# Homunculus Codebase Map

**Analyzed**: 2026-04-16

## Overview

| Metric | Value |
|--------|-------|
| Language | Python 3.11+ |
| Files | ~40 Python files |
| Packages | 9 packages (`homunculus` + 8 subpackages) |
| Tests | 293 tests (all passing) |
| Build | setuptools/pyproject.toml |

## Architecture

```
homunculus/                     # Main package
  __init__.py                   # Package marker
  __main__.py                   # Module entry point
  cli.py                        # CLI commands (init-artifacts, run-episode, apply-episode, train-sft, doctor)
  config.py                     # TOML config parsing into typed dataclasses
  daemon.py                     # Continuous daemon loop (Phase 1+), --once mode, background merge worker
  models.py                     # Core dataclasses (EpisodeRecord, TaskRequest, GeneratedTask, DaemonState,
                                # AdapterManifest, MergeManifest, LineageRecord, IntrospectionResult,
                                # TaskQueueEntry, CommitResult, …)
  policy.py                     # Guardrail pattern matching (block/warn rules)
  runtime.py                    # build_runtime helper (wires config → store → orchestrator)
  storage.py                    # Artifact persistence (events, episodes, patches, registry, task queue,
                                # merges, lineage, introspection) with atomic writes
  suggestions.py                # Markdown task suggestion parser + resonance scoring

  orchestrator/                 # Episode coordination
    loop.py                     # Episode lifecycle: assess → preflight → recall → plan → execute → reflect → curate
    teacher.py                  # OpenAI-compatible teacher client
    student.py                  # Local mlx-lm subprocess wrapper

  memory_client/                # Memory retrieval
    base.py                     # MemoryContract interface
    engram.py                   # Engram HTTP client
    in_memory.py                # Test double

  task_runner/                  # Execution isolation
    runner.py                   # Git worktree isolation, patch application, verification, auto-commit

  dataset_builder/              # Training data curation
    builder.py                  # SFT/DPO sample curation and snapshot materialization

  trainer/                      # Model training
    manager.py                  # Training orchestration, candidate evaluation, promotion gates,
                                # run_merge, consecutive-merge-failure tracking

  introspection/                # Phase 2 — self-analysis modes
    base.py                     # IntrospectionMode protocol + IntrospectionContext
    scheduler.py                # Rotating mode scheduler (metrics:1, critique:3, coverage:5, comparative:3)
    metrics.py                  # Quantitative performance metrics mode
    critique.py                 # LLM-based episode pattern analysis mode
    coverage.py                 # pytest-cov, TODO scanning, test-gap mode
    comparative.py              # Winner vs loser patch comparison mode

  task_generator/               # Phase 3 — weakness → task synthesis
    generator.py                # TaskGenerator: introspection-driven GeneratedTask creation,
                                # merge-failure investigation tasks
    prioritizer.py              # TaskPrioritizer: resonance + priority scoring

  evolution/                    # Phase 4 — LoRA merge + lineage + validation
    merge.py                    # MergeManager: MLX α/r-scaled merge or mergekit (PEFT-baked)
    validation.py               # MergeValidator: post-merge load / canary / coherence, fails closed
    lineage.py                  # LineageTracker: full model history (every LoRA + merge registered)

tests/                          # Unit + integration tests (293 total)
  test_auto_commit.py           # Auto-commit to source
  test_config_evolution.py      # Evolution config parsing
  test_daemon.py                # Daemon cycle execution, merge worker, queue restart safety
  test_dataset_builder.py       # Dataset curation
  test_evolution.py             # Merge / validation / lineage / run_merge integration
  test_introspection.py         # Mode dispatch + scheduler
  test_orchestrator.py          # Episode lifecycle
  test_packaging.py             # Install-from-source smoke
  test_prioritizer.py           # Task prioritization
  test_suggestions.py           # Markdown parser + resonance scoring
  test_task_generator.py        # Introspection → task synthesis
  test_task_queue.py            # Queue persistence + archival
  test_task_runner.py           # Worktree isolation
  test_trainer.py               # Training / promotion / merge triggers
```

## Data Flow

```
                            ┌──────────────────────────────────────────────────────────────────────────────────┐
                            │                            Episode Lifecycle                                     │
                            │                                                                                  │
  ┌─────────┐   ┌────────┐  │  ┌──────┐   ┌──────────┐   ┌──────┐   ┌────┐   ┌───────┐   ┌───────┐   ┌──────┐ │
  │ Suggest │──▶│ Daemon │──▶──│assess│──▶│preflight │──▶│recall│──▶│plan│──▶│execute│──▶│reflect│──▶│curate│──▶─┐
  └─────────┘   └────────┘  │  └──────┘   └──────────┘   └──────┘   └────┘   └───────┘   └───────┘   └──────┘ │  │
        ▲            │      │                              │           │                                       │  │
        │            │      └──────────────────────────────│───────────│───────────────────────────────────────┘  │
        │            │                                      │           │                                          │
  ┌───────────┐      │                                      │           │                                          │
  │Introspec- │◀─────┤                                      │           │                                          │
  │tion Modes │      │                                      │           │                                          │
  └───────────┘      │                                      │           │                                          │
  (metrics/crit/      │                                      │           │                                          │
   coverage/comp)    ▼                                      │           │                                          │
  ┌────────────────────────────────┐                        │           │                                          │
  │ runtime/task_queue.jsonl       │                        │           │                                          │
  │ runtime/task_history.jsonl     │                        │           │                                          │
  │ runtime/daemon_state.json      │                        │           │                                          │
  │ runtime/watchdog.json (new)    │                        │           │                                          │
  └────────────────────────────────┘                        │           │                                          │
                                                            │           │                                          │
  ┌────────────┐                                            │           │                                          │
  │   Engram   │◀───────────────────────────────────────────┘           │                                          │
  │  (Memory)  │                                                        │                                          │
  └────────────┘                                                        │                                          │
                                                                        │                                          │
  ┌────────────┐                                                        │                                          │
  │  Teacher   │◀───────────────────────────────────────────────────────┘                                          │
  │ (OpenAI)   │                                                                                                   │
  └────────────┘                                                                                                   │
                                                                                                                   │
  ┌──────────────────────────────────────────────────────────────────────────────────────────────────────────────┘
  │
  │    ┌──────────┐           ┌──────────┐           ┌──────────────┐           ┌────────────┐          ┌───────────┐
  └───▶│ Dataset  │──────────▶│ Snapshot │──────────▶│   Trainer    │──────────▶│  Promoted  │─────────▶│ Evolution │
       │ Builder  │           │ (train/  │           │ (run_sft,    │           │  Candidate │          │   Merge   │
       │ (SFT/DPO)│           │  valid)  │           │  evaluate,   │           │  (active)  │          │ + Lineage │
       └──────────┘           └──────────┘           │  promote,    │           └────────────┘          │  + Valid. │
                                                     │  run_merge)  │                                   └───────────┘
                                                     └──────────────┘                                          │
                                                                                                               ▼
                                                                                               ┌─────────────────────────┐
                                                                                               │ models/registry.json    │
                                                                                               │ models/adapters/…       │
                                                                                               │ traces/lineage.jsonl    │
                                                                                               │ traces/merges.jsonl     │
                                                                                               └─────────────────────────┘
```

## Key Dataclasses

| Class | Location | Purpose |
|-------|----------|---------|
| `TaskRequest` | models.py | Input task specification |
| `GeneratedTask` | models.py | Daemon-generated task with priority |
| `TaskQueueEntry` | models.py | Persisted queue entry with attempts/status/outcome |
| `EpisodeRecord` | models.py | Complete episode outcome |
| `AdapterManifest` | models.py | Training candidate metadata |
| `MergeManifest` | models.py | Merge outcome metadata (status: pending/complete/merged/validated/failed) |
| `LineageRecord` | models.py | Model history node (base/lora/merged, generation counter) |
| `IntrospectionResult` | models.py | Output of an introspection mode |
| `DaemonState` | models.py | Persisted cycle counter + started_at |
| `CommitResult` | models.py | Auto-commit result |
| `HomunculusConfig` | config.py | Parsed TOML configuration |

## Safety Boundaries (Intentional Constraints)

- Source workspace must be clean before any episode
- `run-episode` never mutates source repo (worktree isolation)
- Accepted patches auto-commit to source when `[daemon].auto_commit_on_accept = true`
- Training only from immutable materialized snapshots
- Promotion is fully automated (no `require_human_approval`)
- Merges run on a background thread with single-flight guard (never block cycle)
- Watchdog is **advisory only** — never stops the daemon; surfaces flags for operator review

## Configuration

- `homunculus.toml` / `homunculus.example.toml` - Main config
- Key sections: `[teacher]`, `[student]`, `[memory]`, `[workspaces]`, `[guardrails]`, `[daemon]`, `[evolution]`, `[introspection]`, `[thresholds]`, `[promotion]`

## Artifact Layout (runtime-relative)

```
traces/
  events.jsonl           # Append-only lifecycle events
  episodes.jsonl         # Terminal episode records
  introspection.jsonl    # Introspection results (Phase 2)
  lineage.jsonl          # Model history (Phase 4)
  merges.jsonl           # Merge manifests (Phase 4)
  patches/<episode_id>.patch

datasets/
  seed/sft_seed.jsonl
  sft/{train,valid,test}.jsonl
  dpo/{train,valid}.jsonl
  snapshots/sft/<snapshot_id>/

models/
  adapters/<candidate_id>/
  registry.json          # Candidate manifests, active pointer

runtime/
  worktrees/<episode_id>/ # Temporary, cleaned after episode
  daemon_state.json       # Cycle counter, started_at (atomic)
  daemon.pid              # Single-instance lock
  task_queue.jsonl        # Pending/in-flight task entries
  task_history.jsonl      # Archived terminal entries
  watchdog.json           # Phase 5 — failure-signal tracker (atomic)
```

## Risk Areas

| Area | Risk | Notes |
|------|------|-------|
| `homunculus/daemon.py` | Cycle loop complexity | Orchestrates introspection, queue, episodes, evolution, archival; background merge thread + single-flight guard. Integration point for Phase 5 watchdog. |
| `homunculus/evolution/merge.py` | MLX math + mergekit shell-out | α/r LoRA scaling; PEFT key-prefix stripping (`base_model.model.`) must not regress into a silent no-op. Mergekit path requires PEFT-baked checkpoints via `_bake_lora_into_base`. |
| `homunculus/trainer/manager.py` | Promotion + merge triggers | `should_merge`, `promote_candidate`, `run_merge`, consecutive-merge-failure counter; LoRA → merge → validation → lineage all wired here. |
| `homunculus/orchestrator/loop.py` | Episode lifecycle | 300+ lines, many exception paths; preflight-clean enforcement + worktree isolation contract lives here. |
| `homunculus/task_runner/runner.py` | Git operations | External process calls, worktree cleanup, patch application, commit_to_source. |
| `homunculus/storage.py` | Atomic writes | `update_merge` uses temp-file + `os.replace` under a class-level lock; `daemon_state.json` persistence uses the same pattern (see `daemon.save_state`). |

## Phase Progression

| Phase | Status | Summary |
|-------|--------|---------|
| 0 Autonomous Bootstrap | Complete | Daemon scaffolding, `--once`, commit-to-source, removal of human-approval gate |
| 1 Daemon Mode | Complete | Continuous loop, signal handling, atomic state persistence, single-instance lock |
| 2 Introspection | Complete | 4 modes + rotating scheduler, results persisted to `introspection.jsonl` |
| 3 Task Generation | Complete | Weakness → task synthesis, resonance scoring, queue restart-safety |
| 4 Weight Evolution | Complete | LoRA merge (MLX + mergekit), post-merge validation, lineage tracking |
| 5 Full Autonomy | In progress | Instrumentation (`autonomy/` package), soak protocol, acceptance predicates |
