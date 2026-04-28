# Operator Guide

This guide is for running `homunculus` against the repository itself.

## Core Operating Model

The source repo is protected by a verification-first loop:

1. source workspace must be clean
2. each candidate patch runs in a linked worktree
3. configured verification commands run in that worktree
4. accepted diffs are stored as patch artifacts
5. accepted diffs are applied to source and committed when
   `daemon.auto_commit_on_accept = true`
6. terminal records are appended to traces and task history

`apply-episode` remains available for recovery or manual replay, but it is not
the normal autonomous path.

## Daily Baseline

Run these from the repository root:

```powershell
python -m homunculus.cli harness-check --strict
python -m unittest discover -q
python -m homunculus.cli doctor --config homunculus.toml
python -m homunculus.cli autonomy-preflight --config homunculus.toml
```

Treat failures as launch blockers. `doctor` may fail if external services are
not running; `harness-check` and the unit suite should not require those
services.

## Running Episodes

Single task:

```powershell
python -m homunculus.cli run-episode --config homunculus.toml --workspace self --task-id parser-fix --prompt "Fix the failing parser tests"
```

One daemon cycle:

```powershell
python -m homunculus.daemon --config homunculus.toml --once
```

Continuous daemon:

```powershell
python -m homunculus.daemon --config homunculus.toml
```

The daemon reads suggestions, introspection results, and persisted queue entries,
then dispatches up to `daemon.max_episodes_per_cycle` tasks per cycle.

## Running Symphony

Validate the repository-owned workflow contract:

```powershell
python -m homunculus.cli symphony-check --workflow WORKFLOW.md
```

Run one Linear dispatch cycle:

```powershell
python -m homunculus.cli symphony-run --workflow WORKFLOW.md --once
```

Run continuously:

```powershell
python -m homunculus.cli symphony-run --workflow WORKFLOW.md
```

Symphony requires `LINEAR_API_KEY` for dispatch. Runnable Linear issues must be
in the `Homunculus Autonomy` project, carry the `symphony` label, and be in an
active state configured in `WORKFLOW.md`.

## Observability

Use reports before reading raw artifacts:

```powershell
python -m homunculus.cli autonomy-report --config homunculus.toml --json
python -m homunculus.cli autonomy-precheck --config homunculus.toml --json
```

Raw artifacts:

- `traces/events.jsonl` - append-only lifecycle events
- `traces/episodes.jsonl` - one terminal record per attempt
- `traces/patches/<episode_id>.patch` - canonical patch artifacts
- `runtime/task_queue.jsonl` - pending/in-flight daemon tasks
- `runtime/task_history.jsonl` - archived terminal tasks
- `runtime/watchdog.json` - advisory failure counters
- `runtime/symphony_state.json` - claimed Linear issues and retry state
- `runtime/symphony_runs.jsonl` - terminal Symphony run attempts
- `runtime/symphony_logs/` - structured per-issue Symphony logs
- `models/registry.json` - candidate manifests and active pointer

## Acceptance

After a soak run, generate acceptance evidence:

```powershell
python -m homunculus.cli autonomy-accept `
  --config homunculus.toml `
  --soak-branch <branch> `
  --output .planning/phases/05-full-autonomy/05-ACCEPTANCE.md
```

Acceptance checks uptime, self-directed tasks, LoRA merge/generation evidence,
fresh tests, metric trend stability, and absence of non-agent commits on the
soak branch.

## Manual Recovery

Replay an accepted patch artifact only when auto-commit was disabled or a prior
source apply failed after the worktree verification passed:

```powershell
python -m homunculus.cli apply-episode --config homunculus.toml --episode-id <episode-id>
```

The command re-checks source cleanliness, applies the stored patch, runs
verification commands in source, and reverts on verification failure.

## Troubleshooting

- Dirty workspace: commit, stash, or discard your own work before running
  episodes.
- Teacher unreachable: start the configured OpenAI-compatible endpoint and
  re-run `doctor`.
- Engram unreachable: check `memory.base_url`, endpoint paths, and bearer env.
- Stale worktree: inspect `runtime/worktrees/` and the latest trace events
  before deleting anything.
- Merge failures: inspect `traces/merges.jsonl`, `traces/lineage.jsonl`, and
  generated investigation tasks.
- Symphony retry loop: inspect `runtime/symphony_state.json`,
  `runtime/symphony_runs.jsonl`, and the issue log under
  `runtime/symphony_logs/`.
- No training snapshot: ensure seed data and valid/test splits exist before SFT.
