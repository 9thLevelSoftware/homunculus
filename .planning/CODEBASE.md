# Homunculus Codebase Map

## Overview

| Metric | Value |
|--------|-------|
| Language | Python 3.11+ |
| Files | 30 Python files |
| Packages | 6 packages |
| Tests | 19 tests (all passing) |
| Build | setuptools/pyproject.toml |

## Architecture

```
homunculus/                     # Main package
  __init__.py                   # Package marker
  __main__.py                   # Module entry point
  cli.py                        # CLI commands (init-artifacts, run-episode, apply-episode, train-sft, doctor)
  config.py                     # TOML config parsing into typed dataclasses
  daemon.py                     # Daemon entry point (Phase 0 stub, Phase 1 target)
  models.py                     # Core dataclasses (EpisodeRecord, TaskRequest, GeneratedTask, etc.)
  policy.py                     # Guardrail pattern matching (block/warn rules)
  storage.py                    # Artifact persistence (events, episodes, patches, registry)
  suggestions.py                # Markdown task suggestion parser

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
    manager.py                  # Training orchestration, candidate evaluation, promotion gates

tests/                          # Unit tests (19 total)
  test_auto_commit.py           # Auto-commit to source
  test_daemon.py                # Daemon cycle execution
  test_dataset_builder.py       # Dataset curation
  test_orchestrator.py          # Episode lifecycle
  test_suggestions.py           # Markdown parser
  test_task_runner.py           # Worktree isolation
  test_trainer.py               # Training/promotion
```

## Data Flow

```
                            ┌──────────────────────────────────────────────────────────────────────────────────┐
                            │                            Episode Lifecycle                                     │
                            │                                                                                  │
  ┌─────────┐   ┌────────┐  │  ┌──────┐   ┌──────────┐   ┌──────┐   ┌────┐   ┌───────┐   ┌───────┐   ┌──────┐ │
  │ Suggest │──▶│ Daemon │──▶──│assess│──▶│preflight │──▶│recall│──▶│plan│──▶│execute│──▶│reflect│──▶│curate│──▶─┐
  └─────────┘   └────────┘  │  └──────┘   └──────────┘   └──────┘   └────┘   └───────┘   └───────┘   └──────┘ │  │
                            │                                          │         │                            │  │
                            └────────────────────────────────────────│─────────│────────────────────────────────┘  │
                                                                       │         │                                 │
                          ┌────────────┐                               │         │                                 │
                          │   Engram   │◀──────────────────────────────│─────────│                                 │
                          │  (Memory)  │                               │         │                                 │
                          └────────────┘                               │         │                                 │
                                                                       │         │                                 │
                          ┌────────────┐                               │         │                                 │
                          │  Teacher   │◀──────────────────────────────┘         │                                 │
                          │ (OpenAI)   │                                         │                                 │
                          └────────────┘                                         │                                 │
                                                                                 │                                 │
                          ┌────────────┐                                         │                                 │
                          │ Worktree   │◀────────────────────────────────────────┘                                 │
                          │ (isolated) │                                                                           │
                          └────────────┘                                                                           │
                                                                                                                   │
  ┌──────────────────────────────────────────────────────────────────────────────────────────────────────────────┘
  │
  │    ┌──────────┐           ┌──────────┐           ┌──────────────┐           ┌────────────┐
  └───▶│ Dataset  │──────────▶│ Snapshot │──────────▶│   Trainer    │──────────▶│  Promoted  │
       │ Builder  │           │ (train/  │           │ (run_sft,    │           │  Candidate │
       │ (SFT/DPO)│           │  valid)  │           │  evaluate,   │           │  (active)  │
       └──────────┘           └──────────┘           │  promote)    │           └────────────┘
                                                     └──────────────┘
```

## Key Dataclasses

| Class | Location | Purpose |
|-------|----------|---------|
| `TaskRequest` | models.py | Input task specification |
| `GeneratedTask` | models.py | Daemon-generated task with priority |
| `EpisodeRecord` | models.py | Complete episode outcome |
| `AdapterManifest` | models.py | Training candidate metadata |
| `CommitResult` | models.py | Auto-commit result |
| `HomunculusConfig` | config.py | Parsed TOML configuration |

## Safety Boundaries (Intentional Constraints)

- Source workspace must be clean before any episode
- `run-episode` never mutates source repo (worktree isolation)
- Accepted patches stay as artifacts until explicit `apply-episode`
- Training only from immutable materialized snapshots
- Promotion gates based on metrics (no more human approval - removed in Phase 0)

## Configuration

- `homunculus.toml` / `homunculus.example.toml` - Main config
- Key sections: `[teacher]`, `[student]`, `[memory]`, `[workspaces]`, `[guardrails]`, `[daemon]`, `[evolution]`

## Risk Areas

| Area | Risk | Notes |
|------|------|-------|
| daemon.py:52-70 | Incomplete | Continuous mode stub - Phase 1 target |
| config.py | Missing DaemonSettings | Config section exists but no typed dataclass yet |
| orchestrator/loop.py | Complex | 300 lines, many exception paths |
| task_runner/runner.py | Git operations | External process calls, cleanup needed |

## Phase 0 Changes (Complete)

1. Removed `require_human_approval` from PromotionSettings
2. Removed `human_approved` parameter from `promote_candidate()`
3. Added `CommitResult` dataclass and `commit_to_source()` method
4. Added `GeneratedTask` dataclass
5. Added `SuggestionReader` class for markdown parsing
6. Added `Daemon` class with `--once` mode
7. Added `[daemon]` and `[evolution]` config sections

## Phase 1 Target (Next)

File: `suggestions/phase1-daemon-mode.md`
- Continuous daemon mode with configurable interval
- SIGTERM/SIGINT handling
- State persistence to `runtime/daemon_state.json`
- Multi-episode cycles
