# AGENTS.md

Short map for agents working in this repository. Keep this file concise; the
source of truth lives in `docs/`.

## What This Is

`homunculus` is a teacher-student self-improving coding agent scaffold. It runs
episodes against its own repository, verifies candidate patches in isolated git
worktrees, auto-commits accepted changes when configured, and curates successful
episodes into training data.

## Read First

- `docs/index.md` - documentation map and freshness rules
- `docs/harness-engineering.md` - repo-local harness standard
- `docs/architecture.md` - implemented runtime architecture and data flow
- `docs/operator-guide.md` - day-to-day commands and autonomous runbooks
- `docs/setup-and-configuration.md` - config reference and launch checklist
- `docs/symphony-autonomy.md` - Linear/Symphony orchestration contract
- `docs/vm-runbook.md` - Ubuntu GPU VM and local model runbook
- `docs/quality-score.md` - current quality grades and cleanup targets

Historical planning artifacts live in `.planning/`. Treat them as audit history
unless a current doc links to a specific file as active guidance.

## Core Commands

```powershell
python -m pip install -e .
python -m unittest discover -q
python -m homunculus.cli harness-check --strict
python -m homunculus.cli doctor --config homunculus.toml
python -m homunculus.cli autonomy-preflight --config homunculus.toml
python -m homunculus.daemon --config homunculus.toml --once
python -m homunculus.daemon --config homunculus.toml
python -m homunculus.cli autonomy-report --config homunculus.toml --json
python -m homunculus.cli autonomy-accept --config homunculus.toml --soak-branch <branch> --output <path>
python -m homunculus.cli symphony-check --workflow WORKFLOW.md
python -m homunculus.cli symphony-run --workflow WORKFLOW.md --once
```

## Operating Model

- Autonomous defaults are intentional: accepted episodes auto-commit, candidate
  promotion is automated after metric gates pass, and evolution can merge LoRAs.
- The source workspace must be clean before episode execution.
- Patches are first applied and verified in linked worktrees under `runtime/`.
- The test suite and configured verification commands are the primary merge gate.
- Runtime artifacts live under `traces/`, `datasets/`, `models/`, and `runtime/`;
  avoid hand-editing them unless a recovery doc says to.
- Use `apply-episode` only as a manual recovery path when auto-commit is disabled
  or a verified patch artifact must be replayed.

## Architecture Map

- `homunculus/orchestrator/loop.py` - episode lifecycle
- `homunculus/task_runner/runner.py` - git worktrees, patch application, verification, commits
- `homunculus/daemon.py` - continuous loop, task dispatch, introspection, evolution hook
- `homunculus/introspection/` - metrics, critique, coverage, comparative analysis
- `homunculus/task_generator/` - introspection and suggestion driven task creation
- `homunculus/trainer/manager.py` - SFT, evaluation, promotion, merge orchestration
- `homunculus/evolution/` - LoRA merge, validation, lineage
- `homunculus/autonomy/` - preflight, precheck, reporting, acceptance, watchdog
- `homunculus/symphony/` - Linear workflow, workspaces, branch gates, status
- `homunculus/storage.py` - append-only artifacts and registry persistence

## Change Discipline

- Prefer existing patterns and files unless the harness standard needs a new
  explicit artifact.
- Keep docs, config, and tests aligned in the same change.
- Run `python -m unittest discover -q` and `python -m homunculus.cli harness-check --strict`
  before considering code changes complete.
- Do not overwrite unrelated untracked files; this repo may contain local audit
  artifacts under `docs/superpowers/`.
