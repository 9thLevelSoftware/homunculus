# Homunculus

`homunculus` is a standalone Python scaffold for a teacher-student self-improving coding agent.

It is designed around a conservative launch posture:

- episode execution happens in linked git worktrees, not in the source repo
- accepted patches are stored as review artifacts and are not auto-applied
- training runs from immutable materialized snapshots, not from live append-only dataset files
- evaluation and promotion are separate steps, with human approval required for promotion

## What it does

At a high level, `homunculus` runs this loop:

1. preflight a target workspace
2. recall relevant memory from Engram
3. ask the student for a local hint
4. ask the teacher for a plan and patch
5. evaluate guardrails
6. execute the patch in an isolated worktree
7. run verification commands
8. persist the episode, patch, events, and memory outcomes
9. curate verified successful runs into SFT and DPO data
10. materialize SFT snapshots and train local adapters

## Current scope

Implemented now:

- config-driven runtime and artifact layout
- OpenAI-compatible teacher client
- local student runner shaped for `mlx-lm`
- Engram-compatible memory client
- worktree-isolated task execution
- durable episode and event persistence
- SFT/DPO curation with retry-safe provenance
- immutable SFT snapshot materialization
- candidate evaluation and gated promotion
- `doctor` checks for launch readiness

Out of scope at launch:

- auto-committing source repos
- auto-applying accepted patches
- auto-promoting trained candidates
- live DPO training
- non-git workspaces

## Documentation

- [Setup and Configuration](docs/setup-and-configuration.md)
- [Operator Guide](docs/operator-guide.md)
- [Architecture and Artifacts](docs/architecture.md)

## Prerequisites

Minimum:

- Python 3.11 or newer
- Git installed and available on `PATH`
- a git-based target workspace

For production use:

- an OpenAI-compatible teacher endpoint
- an Engram server reachable over HTTP
- `mlx-lm` installed on the machine that will run local student inference and SFT

## Installation

Create a virtual environment and install the project in editable mode:

```powershell
python -m venv .venv
.venv/Scripts/Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e .
```

If you want the local student and SFT pipeline to run for real instead of in test mode, install `mlx-lm` in the same environment.

## Quick start

1. Copy the example config.

```powershell
Copy-Item homunculus.example.toml homunculus.toml
```

2. Edit `homunculus.toml`:

- set the teacher endpoint and model
- point each workspace at a real git repo
- set verification commands that prove a patch is acceptable
- leave `require_human_approval = true`

3. Set environment variables for your teacher API key and Engram bearer token.

```powershell
$env:OPENAI_API_KEY = "..."
$env:ENGRAM_MCP_BEARER_TOKEN = "..."
```

4. Initialize artifact directories.

```powershell
python -m homunculus.cli init-artifacts --config homunculus.toml
```

5. Run readiness checks.

```powershell
python -m homunculus.cli doctor --config homunculus.toml
```

6. Run an episode.

```powershell
python -m homunculus.cli run-episode --config homunculus.toml --workspace self --task-id demo --prompt "Fix the failing tests in parser.py"
```

7. Inspect the returned `episode_id`, trace events, and stored patch artifact under `traces/patches/`.

8. If the episode outcome is acceptable, explicitly apply the stored patch to the source repo.

```powershell
python -m homunculus.cli apply-episode --config homunculus.toml --episode-id <episode-id>
```

9. When you have enough verified data, simulate or run SFT.

```powershell
python -m homunculus.cli train-sft --config homunculus.toml --simulate
```

10. Evaluate the candidate and promote it only after review.

```powershell
python -m homunculus.cli evaluate-candidate --config homunculus.toml --candidate-id <candidate-id> --metrics-file metrics.json
python -m homunculus.cli promote-candidate --config homunculus.toml --candidate-id <candidate-id> --human-approved
```

## CLI reference

`init-artifacts`

- creates the runtime artifact layout under `traces/`, `datasets/`, `models/`, and `runtime/`

`doctor`

- checks git, writable artifact directories, teacher auth env, Engram auth env, `mlx_lm` availability, Engram reachability, and workspace cleanliness

`run-episode`

- runs one teacher-student coding attempt against a configured workspace
- blocks if the source repo is dirty
- stores a patch artifact even when the episode is blocked or fails

`apply-episode`

- re-checks that the source workspace is clean
- re-applies a stored patch artifact to the source repo
- runs verification commands again
- reverts the repo if verification fails

`train-sft`

- materializes an immutable snapshot under `datasets/snapshots/sft/<snapshot_id>/`
- writes a candidate manifest into `models/registry.json`

`evaluate-candidate`

- records candidate metrics only
- does not activate the model

`promote-candidate`

- activates an already-evaluated candidate
- still requires `--human-approved` when approval is enabled in config

## Important operating rules

- The source workspace must be clean before an episode starts.
- `homunculus` does not stash or reset user work for you.
- Accepted patches stay as artifacts until you explicitly apply them.
- Training lineage is snapshot-based. If a candidate was not trained from a snapshot, treat that as invalid.
- Promotion is intentionally manual.

## Development

Run the test suite with:

```powershell
python -m unittest discover -v
```

## Status

This repo is a hardened scaffold, not a finished autonomous platform. It is suitable for building and iterating on the operational loop, but you still need to supply the real teacher endpoint, Engram deployment, verification commands, seed data, and evaluation process for your environment.
