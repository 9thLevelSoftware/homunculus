# Symphony Autonomy Design

This repository implements a Python Symphony layer rather than vendoring the
Elixir preview. Symphony turns Linear issues into isolated, persistent,
autonomous implementation runs.

## Runtime Contract

`WORKFLOW.md` is the source of truth for orchestration. It declares:

- Linear tracker settings, active states, terminal states, and the `symphony`
  label gate
- polling cadence, workspace root, hooks, and concurrency
- Codex/local model command settings
- Homunculus extensions for branch naming, merge gates, artifact curation, and
  fallback runner behavior
- the strict issue prompt template used for each run

Run:

```powershell
python -m homunculus.cli symphony-check --workflow WORKFLOW.md
```

## State Model

Symphony writes restart-readable state under `runtime/`:

- `runtime/symphony_state.json` - claimed issues, retries, completed issues, and
  aggregate counters
- `runtime/symphony_runs.jsonl` - one terminal record per issue attempt
- `runtime/symphony_logs/*.jsonl` - structured operational logs by issue

The existing episode, trace, dataset, model, and lineage artifacts remain the
training/evolution source of truth. Symphony routes successful issue execution
through those artifacts instead of inventing a second learning loop.

## Workspaces And Branches

Each Linear issue gets a persistent git worktree under the configured workspace
root. The branch name is deterministic:

```text
codex/<issue-key>-<title-slug>
```

The source checkout must stay clean. Work happens in the issue worktree, and the
source checkout is updated only by a gated fast-forward merge after validation.

## Merge Gates

The default gate sequence is:

```powershell
python -m homunculus.cli harness-check --strict
python -m unittest discover -q
```

Additional gates belong in `WORKFLOW.md`, not in agent memory. A failed gate
records the run as failed and schedules retry/backoff rather than mutating the
source checkout.

## Linear Control Plane

The dedicated Linear project is `Homunculus Autonomy` under team `9TH`.
Runnable work must carry the `symphony` label and be in one of:

- `Todo`
- `In Progress`
- `Rework`
- `Validating`
- `Merging`

Terminal states are:

- `Done`
- `Closed`
- `Canceled`
- `Cancelled`
- `Duplicate`

Blocked or backlog work is not dispatched.
