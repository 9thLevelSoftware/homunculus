# Technical Architecture Reference

## Executive Overview

`homunculus` is a Python 3.11 teacher-student self-improving coding-agent scaffold. It runs coding tasks against this repository, verifies generated patches in isolated git worktrees, commits accepted changes when configured, curates successful episodes into training datasets, trains/promotes local LoRA adapters, and can orchestrate Linear issues through a Python Symphony layer. The README describes this repo as a standalone Python scaffold that runs tasks against its own repo, verifies patches in worktrees, commits accepted changes, curates training data, evolves local LoRA adapters, and includes Linear/Symphony orchestration (`README.md:3-9`). The architecture doc enumerates the same runtime components (`docs/architecture.md:5-30`).

The repository is intentionally also the harness: docs, configuration, checks, traces, scripts, and tests are local and inspectable by future agent runs (`README.md:11-22`; `docs/harness-engineering.md:18-29`). Current docs in `docs/` are the source of truth; `.planning/` and `docs/superpowers/` are historical unless current docs link them as active guidance (`docs/index.md:3-4`, `docs/index.md:22-32`).

## High-Level Architecture

The system has five cooperating planes:

1. **Episode execution plane** - `EpisodeOrchestrator` owns `assess -> preflight -> recall -> plan -> execute -> reflect -> curate` (`docs/architecture.md:15-18`; `README.md:77-91`). `TaskRunner` enforces clean git state, linked worktree execution, patch application, verification, and source commits (`homunculus/task_runner/runner.py:26-70`, `homunculus/task_runner/runner.py:115-188`).
2. **Memory and learning plane** - `MemoryContract` abstracts recall/persistence (`homunculus/memory_client/base.py:8-19`), `EngramMemoryClient` implements the HTTP client (`homunculus/memory_client/engram.py:12-63`), and `DatasetBuilder` curates accepted episodes into SFT/DPO data (`homunculus/dataset_builder/builder.py:17-32`, `homunculus/dataset_builder/builder.py:34-90`).
3. **Training and evolution plane** - `TrainingManager` creates snapshots, trains/evaluates/promotes adapters, and orchestrates merges (`homunculus/trainer/manager.py:25-65`, `homunculus/trainer/manager.py:147-185`, `homunculus/trainer/manager.py:275-348`). `homunculus/evolution/` handles merge backends, validation, and lineage (`homunculus/evolution/merge.py:61-179`, `homunculus/evolution/validation.py:45-89`, `homunculus/evolution/lineage.py:11-68`).
4. **Autonomy/daemon plane** - `Daemon` reads queues, suggestions, and introspection output, dispatches episodes, archives terminal tasks, updates watchdog state, and checks evolution (`homunculus/daemon.py:61-79`, `homunculus/daemon.py:365-450`, `homunculus/daemon.py:452-556`, `homunculus/daemon.py:558-604`).
5. **Symphony/Linear orchestration plane** - `WORKFLOW.md` defines Linear polling, workspace roots, runner settings, merge gates, and branch policy (`WORKFLOW.md:1-56`). `SymphonyOrchestrator` polls Linear, creates per-issue worktrees, runs agents, gates branches, fast-forwards source when configured, and records run state (`homunculus/symphony/orchestrator.py:22-73`, `homunculus/symphony/orchestrator.py:112-167`).

## Summarized Directory Tree

```text
homunculus/
  cli.py                  # CLI subcommands and command routing
  daemon.py               # continuous autonomous loop
  runtime.py              # shared runtime object construction
  config.py               # TOML-backed dataclass config
  models.py               # core persisted/domain dataclasses
  storage.py              # artifact and registry persistence
  policy.py               # guardrail engine
  harness.py              # repository harness checks
  suggestions.py          # markdown suggestion ingestion

  orchestrator/           # episode lifecycle, teacher, student
  task_runner/            # git worktrees, patch application, verification
  memory_client/          # memory protocol + Engram/in-memory clients
  dataset_builder/        # SFT/DPO samples and snapshots
  trainer/                # SFT, evaluation, promotion, merge orchestration
  evolution/              # LoRA merge, validation, lineage
  introspection/          # metrics, critique, coverage, comparative modes
  task_generator/         # introspection findings -> tasks + prioritization
  autonomy/               # reports, preflight, precheck, acceptance, watchdog
  symphony/               # Linear/Symphony orchestration

docs/                     # current source-of-truth docs
scripts/phase5/           # soak/bootstrap/operator PowerShell scripts
tests/                    # unit tests by subsystem
runtime/ traces/ datasets/ models/ suggestions/  # runtime/artifact surfaces
.planning/ docs/superpowers/                       # historical planning/audit
```

The artifact layout is documented in `docs/architecture.md:77-109`. Generated runtime directories are gitignored (`.gitignore:17-20`).

## Entry Points

- Console script: `homunculus = "homunculus.cli:main"` (`pyproject.toml:11-13`).
- Module entry point: `python -m homunculus` delegates to `homunculus.cli.main` (`homunculus/__main__.py:1-5`).
- CLI commands include `init-artifacts`, `harness-check`, `run-episode`, `apply-episode`, `train-sft`, `evaluate-candidate`, `promote-candidate`, `doctor`, autonomy commands, and Symphony commands (`homunculus/cli.py:27-320`, `homunculus/cli.py:323-441`).
- Daemon entry point: `python -m homunculus.daemon --config homunculus.toml [--once|--dry-run]` (`homunculus/daemon.py:796-841`).

## Configuration System

Configuration is TOML-backed and parsed into dataclasses by `load_config()` (`homunculus/config.py:246-353`). Major sections map to `TeacherSettings`, `StudentSettings`, `MemorySettings`, thresholds, promotion, paths, daemon, introspection, evolution, guardrails, workspaces, and canary commands (`homunculus/config.py:25-193`).

`homunculus.example.toml` documents the expected shape (`homunculus.example.toml:1-111`). The local `homunculus.toml` currently points the teacher to an Ollama/OpenAI-compatible endpoint at `http://127.0.0.1:11434/v1` with model `qwen2.5-coder:14b-instruct-q4_K_M` (`homunculus.toml:1-13`).

`WORKFLOW.md` separately configures Symphony/Linear orchestration: tracker, polling, workspace root, hooks, Codex command, Homunculus merge policy, and issue prompt template (`WORKFLOW.md:1-70`).

## Core Data Models and Interfaces

`homunculus/models.py` centralizes persisted/domain records, including `TaskRequest`, `TeacherResponse`, `StudentResponse`, `TaskExecutionResult`, `EpisodeRecord`, `SFTSample`, `PreferencePair`, `EvaluationMetrics`, `AdapterManifest`, `DatasetSnapshot`, `MergeManifest`, `LineageRecord`, `IntrospectionResult`, and `TaskQueueEntry` (`homunculus/models.py:54-394`).

Protocol-style interfaces include:

- `MemoryContract` (`homunculus/memory_client/base.py:8-19`).
- `IntrospectionMode` (`homunculus/introspection/base.py:13-24`).
- `AgentRunner` (`homunculus/symphony/runner.py:21-30`).
- `IssueTracker` (`homunculus/symphony/tracker.py:14-25`).

## Runtime Construction and Dependency Graph

`build_runtime()` is the composition root. It loads config, creates `ArtifactStore`, `DatasetBuilder`, `EngramMemoryClient`, `OpenAICompatibleTeacher`, `LocalStudentRunner`, `TaskRunner`, `GuardrailEngine`, `TrainingManager`, and `EpisodeOrchestrator` (`homunculus/runtime.py:16-35`).

```text
CLI / Daemon
  -> build_runtime()
     -> load_config()
     -> ArtifactStore
     -> DatasetBuilder
     -> EngramMemoryClient
     -> OpenAICompatibleTeacher
     -> LocalStudentRunner
     -> TaskRunner
     -> GuardrailEngine
     -> TrainingManager
     -> EpisodeOrchestrator
```

The orchestrator dependencies are explicit in `EpisodeOrchestrator.__init__` (`homunculus/orchestrator/loop.py:17-37`). The daemon adds `SuggestionReader`, `TaskGenerator`, `TaskPrioritizer`, `IntrospectionScheduler`, `Watchdog`, and evolution checks (`homunculus/daemon.py:13-18`, `homunculus/daemon.py:73-111`, `homunculus/daemon.py:558-604`). Symphony composes `IssueTracker`, `WorkspaceManager`, `AgentRunner`, and `MergeGate` (`homunculus/symphony/orchestrator.py:22-37`, `homunculus/symphony/runner.py:180-184`).

## Episode Data Flow

The documented episode flow is in `docs/architecture.md:32-49` and is implemented in `EpisodeOrchestrator.run_episode()` (`homunculus/orchestrator/loop.py:38-261`):

1. Create `episode_id`.
2. Write initial patch artifact.
3. Require clean git source workspace.
4. Recall Engram memories.
5. Run local student for a hint.
6. Ask teacher for JSON containing `plan`, `candidate_patch`, and `rationale`.
7. Evaluate guardrails.
8. Create linked worktree under `runtime/worktrees/<episode_id>`.
9. Apply patch and run verification.
10. Write canonical patch to `traces/patches/<episode_id>.patch`.
11. If accepted and auto-commit is enabled, apply to source and commit.
12. Reflect to memory and curate datasets.
13. Append terminal `EpisodeRecord`.

Guardrails inspect prompt, patch, and relevant memory (`homunculus/orchestrator/loop.py:90-103`; `homunculus/policy.py:7-35`). Patch execution delegates to `TaskRunner.execute_patch()` (`homunculus/orchestrator/loop.py:105-124`; `homunculus/task_runner/runner.py:115-136`). Auto-commit applies verified canonical patch to source and commits with episode/task metadata (`homunculus/orchestrator/loop.py:126-137`, `homunculus/orchestrator/loop.py:310-359`; `homunculus/task_runner/runner.py:162-188`).

## Training and Evolution Flow

Accepted episodes become SFT/DPO records. `build_sft_sample()` accepts only approved, verified, accepted episodes (`homunculus/dataset_builder/builder.py:34-59`). `build_preference_pair()` pairs an accepted episode with a prior failed attempt for the same task and prompt (`homunculus/dataset_builder/builder.py:61-90`). Snapshots combine seed and self-generated samples while enforcing split and self-generated-ratio constraints (`homunculus/dataset_builder/builder.py:123-153`).

Training flow: materialize SFT snapshot, create adapter directory, run training or simulation, register/update `AdapterManifest`, evaluate metrics, promote if gates pass, and register LoRA lineage (`homunculus/trainer/manager.py:65-141`, `homunculus/trainer/manager.py:147-185`).

Evolution flow: `MergeManager.should_merge()` checks promoted LoRAs since the last merge (`homunculus/evolution/merge.py:75-96`); `MergeManager.merge()` writes `MergeManifest` and runs the selected backend (`homunculus/evolution/merge.py:113-179`); `TrainingManager.run_merge()` validates and advances lineage (`homunculus/trainer/manager.py:281-348`); `MergeValidator` runs load, canary, and coherence stages (`homunculus/evolution/validation.py:45-89`).

## Daemon and Autonomous Task Flow

The daemon loop runs introspection, loads persisted queue entries, generates fresh tasks, reads suggestions, persists fresh tasks, prioritizes/de-duplicates work, executes episodes, archives terminal tasks, checks evolution, and updates watchdog/cycle state. Key implementation areas:

- Introspection execution and persistence (`homunculus/daemon.py:274-304`).
- Pending task assembly (`homunculus/daemon.py:365-450`).
- One-cycle execution (`homunculus/daemon.py:452-556`).
- Queue persistence/archive in `ArtifactStore` (`homunculus/storage.py:301-430`).
- Advisory watchdog (`homunculus/autonomy/watchdog.py:1-14`, `homunculus/autonomy/watchdog.py:179-202`).

## Introspection and Task Generation

`IntrospectionScheduler` schedules modes by cycle number and runs due modes (`homunculus/introspection/scheduler.py:44-175`). The registry includes `metrics`, `critique`, `coverage`, and `comparative` (`homunculus/introspection/__init__.py:11-36`).

Responsibilities:

- `MetricsMode`: success/revert/error/blocked rates, retry stats, failure concentration (`homunculus/introspection/metrics.py:19-105`).
- `CritiqueMode`: teacher-model analysis of episode summaries (`homunculus/introspection/critique.py:43-99`).
- `CoverageMode`: optional pytest/coverage analysis, TODO scan, test gap detection (`homunculus/introspection/coverage.py:18-63`).
- `ComparativeMode`: winner/loser patch comparison by `comparison_group` (`homunculus/introspection/comparative.py:11-98`).

`TaskGenerator` converts findings into `GeneratedTask` objects (`homunculus/task_generator/generator.py:16-87`, `homunculus/task_generator/generator.py:201-898`). `TaskPrioritizer` scores by alignment, complexity, and freshness (`homunculus/task_generator/prioritizer.py:36-86`).

## Symphony / Linear Architecture

Symphony is a Python implementation that turns Linear issues into isolated persistent runs (`docs/symphony-autonomy.md:1-6`). Runtime state lives under `runtime/symphony_state.json`, `runtime/symphony_runs.jsonl`, and `runtime/symphony_logs/*.jsonl` (`docs/symphony-autonomy.md:25-36`). Branch names are deterministic: `codex/<issue-key>-<title-slug>` (`docs/symphony-autonomy.md:38-49`; `homunculus/symphony/workspace.py:23-25`).

Control flow:

1. `LinearTracker.fetch_candidate_issues()` queries active labeled Linear issues (`homunculus/symphony/tracker.py:40-45`, `homunculus/symphony/tracker.py:99-162`).
2. `SymphonyOrchestrator.run_once()` reconciles state, selects eligible issues, claims them, executes each, and updates retry/completion state (`homunculus/symphony/orchestrator.py:40-73`).
3. `WorkspaceManager.ensure_workspace()` creates or reuses a git worktree branch (`homunculus/symphony/workspace.py:32-89`).
4. `HomunculusEpisodeRunner` routes the issue through the episode loop in the issue worktree (`homunculus/symphony/runner.py:33-85`).
5. `MergeGate.run_gates()` executes configured gates and `merge_branch()` fast-forwards source when clean and configured (`homunculus/symphony/merge_gate.py:13-52`).

## Persistence and Artifact Model

`ArtifactStore` owns append-only trace, dataset, registry, merge, lineage, and queue persistence (`homunculus/storage.py:13-26`). `ensure_layout()` creates trace, dataset, model, and runtime directories/files (`homunculus/storage.py:26-59`).

Important records:

- `traces/events.jsonl`
- `traces/episodes.jsonl`
- `traces/introspection.jsonl`
- `traces/merges.jsonl`
- `traces/lineage.jsonl`
- `traces/patches/<episode_id>.patch`
- `models/registry.json`
- `runtime/task_queue.jsonl`
- `runtime/task_history.jsonl`

The documented artifact layout mirrors this (`docs/architecture.md:77-109`).

## Safety Boundaries

Primary invariants:

- Source workspace must be clean before episode execution.
- Candidate patches are verified in linked worktrees before source mutation.
- Source commits happen only for accepted episodes when auto-commit is enabled.
- Symphony work lands on `codex/<issue>` branches and reaches source only through merge gates.
- Training reads immutable snapshots, not mutable live dataset tails.
- Promotion/merge are automated but metric- and validation-gated.
- Watchdog failures are advisory and must not crash the daemon.

Citations: `docs/architecture.md:111-123`, `docs/operator-guide.md:5-18`. `TaskRunner.require_clean_workspace()` enforces git repo and clean status (`homunculus/task_runner/runner.py:63-70`). Worktree isolation is implemented through `git worktree add --detach` and cleanup/prune (`homunculus/task_runner/runner.py:199-214`).

## External Dependencies and Integrations

`pyproject.toml` declares no mandatory third-party runtime dependencies beyond setuptools packaging (`pyproject.toml:1-20`). Operationally, the system expects Python 3.11+, Git, and often PowerShell 7+ for Windows soak scripts (`scripts/phase5/README.md:11-18`).

Optional/production integrations include `mlx-lm`, OpenAI-compatible teacher endpoints, Engram-compatible memory, Ollama, Linear GraphQL, Codex app-server smoke, PyYAML, mergekit, PEFT/transformers/torch, safetensors, pytest/coverage, and optional psutil (`docs/setup-and-configuration.md:14-20`; `homunculus/symphony/tracker.py:28-80`; `homunculus/evolution/merge.py:202-306`; `homunculus/evolution/validation.py:131-347`; `homunculus/introspection/coverage.py:80-87`; `homunculus/daemon.py:36-58`).

## Build, Test, and Deployment Workflow

Install locally:

```powershell
python -m venv .venv
.venv/Scripts/Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e .
```

Citations: `docs/setup-and-configuration.md:5-12`, `README.md:35-42`.

Baseline validation:

```powershell
python -m homunculus.cli harness-check --strict
python -m unittest discover -q
python -m homunculus.cli doctor --config homunculus.toml
python -m homunculus.cli autonomy-preflight --config homunculus.toml
```

Citations: `docs/operator-guide.md:20-33`, `docs/setup-and-configuration.md:194-206`.

GitHub Actions installs the package, runs `harness-check --strict`, then runs unit tests (`.github/workflows/harness.yml:1-24`).

Episode/daemon commands are documented in `README.md:51-75` and `docs/operator-guide.md:35-57`. Symphony commands are documented in `docs/operator-guide.md:58-80`. VM/soak deployment is documented in `docs/vm-runbook.md:1-78` and `scripts/phase5/README.md:22-63`.

## Testing Strategy

Tests cover orchestrator, task runner, dataset builder, trainer, evolution, daemon, autonomy, Symphony, harness, packaging, policy, suggestions, task generator, prioritizer, and task queue. Packaging tests verify required subpackages and gitignore hygiene (`tests/test_packaging.py:9-19`, `tests/test_packaging.py:45-62`). Harness tests assert the current repository passes strict harness checks and CLI JSON output works (`tests/test_harness.py:13-40`).

## Known Limitations

Current documented limitations include no first-class GitHub PR publishing in this checkout, `doctor` checking reachability rather than semantic correctness of external services, watchdog warning noise, and no structural import-boundary checks beyond packaging/harness checks (`docs/architecture.md:125-133`).
