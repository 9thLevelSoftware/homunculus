# Homunculus

`homunculus` is a standalone Python scaffold for a teacher-student
self-improving coding agent. It runs tasks against its own repository, verifies
candidate patches in isolated git worktrees, commits accepted changes, curates
successful episodes into training data, and evolves local LoRA adapters.

The repository is also the harness: docs, checks, traces, scripts, and tests are
kept local so future agent runs can inspect and improve the system directly.

## Current Posture

- Autonomous defaults: accepted patches auto-commit, promotion is automated
  after metric gates pass, and evolution can merge LoRAs.
- Worktree isolation remains mandatory: generated patches are verified outside
  the source workspace before source mutation.
- Tests and configured verification commands are the primary merge gate.
- Runtime evidence is append-only under `traces/`, `datasets/`, `models/`, and
  `runtime/`.

## Documentation

- [Documentation Index](docs/index.md)
- [Harness Engineering Standard](docs/harness-engineering.md)
- [Architecture and Artifacts](docs/architecture.md)
- [Operator Guide](docs/operator-guide.md)
- [Setup and Configuration](docs/setup-and-configuration.md)
- [Quality Score](docs/quality-score.md)

## Install

```powershell
python -m venv .venv
.venv/Scripts/Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e .
```

Optional production dependencies:

- `mlx-lm` for local student inference and LoRA training
- an OpenAI-compatible teacher endpoint
- an Engram-compatible memory server
- Git on `PATH`

## Core Commands

```powershell
# Run the harness and test baseline
python -m homunculus.cli harness-check --strict
python -m unittest discover -q

# Initialize artifacts and check launch readiness
python -m homunculus.cli init-artifacts --config homunculus.toml
python -m homunculus.cli doctor --config homunculus.toml
python -m homunculus.cli autonomy-preflight --config homunculus.toml

# Run one episode or one daemon cycle
python -m homunculus.cli run-episode --config homunculus.toml --workspace self --task-id <task-id> --prompt "..."
python -m homunculus.daemon --config homunculus.toml --once

# Run continuously and inspect autonomy state
python -m homunculus.daemon --config homunculus.toml
python -m homunculus.cli autonomy-report --config homunculus.toml --json
```

## Episode Lifecycle

`assess -> preflight -> recall -> plan -> execute -> reflect -> curate`

1. preflight confirms the source workspace is a clean git repo
2. recall retrieves relevant Engram memories
3. the student provides a local hint
4. the teacher returns structured JSON with a plan, patch, and rationale
5. guardrails inspect the prompt, patch, and memories
6. the patch is applied and verified in an isolated linked worktree
7. accepted patches are applied to the source workspace and committed when
   `daemon.auto_commit_on_accept = true`
8. results are recorded to traces and memory
9. successful episodes are curated into SFT/DPO data
10. training, promotion, merge, and acceptance surfaces use those artifacts

## Development

Run the required baseline before finishing changes:

```powershell
python -m homunculus.cli harness-check --strict
python -m unittest discover -q
```

The test suite uses temporary git repositories and deterministic teacher/student
test doubles. It requires Git to be available on `PATH`.

## Status

Phase 5 autonomy tooling is present: daemon operation, introspection, generated
tasks, auto-commit, candidate promotion, merge validation, preflight, reporting,
and acceptance predicates. The next operational milestone is completing a real
soak run and archiving the acceptance evidence.
