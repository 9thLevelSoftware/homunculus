# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

A teacher-student self-improving coding agent scaffold. Runs episodes where a teacher model generates patches, a local student provides hints, and verification determines acceptance. Training data is curated from successful episodes.

## Commands

```powershell
# Install (editable mode)
python -m venv .venv && .venv/Scripts/Activate.ps1
python -m pip install -e .

# Run tests
python -m unittest discover -v

# Run a single test file
python -m unittest tests.test_orchestrator -v

# Run a specific test
python -m unittest tests.test_orchestrator.OrchestratorTests.test_run_episode_persists_patch_and_keeps_source_clean -v

# Initialize artifact directories
python -m homunculus.cli init-artifacts --config homunculus.toml

# Check environment readiness
python -m homunculus.cli doctor --config homunculus.toml

# Run an episode
python -m homunculus.cli run-episode --config homunculus.toml --workspace self --task-id <task-id> --prompt "..."

# Apply a verified patch to source repo
python -m homunculus.cli apply-episode --config homunculus.toml --episode-id <episode-id>

# Train SFT (--simulate for dry run)
python -m homunculus.cli train-sft --config homunculus.toml --simulate
```

## Architecture

### Episode Lifecycle

`assess -> preflight -> recall -> plan -> execute -> reflect -> curate`

1. **preflight**: Source repo must be clean (git status)
2. **recall**: Pull relevant memories from Engram
3. **plan**: Student hints locally, teacher generates plan + patch
4. **execute**: Patch applied in isolated worktree, verification runs there
5. **reflect**: Record outcome to memory
6. **curate**: Append successful episodes to SFT/DPO datasets

### Module Structure

- `homunculus/orchestrator/loop.py` - Episode lifecycle coordination
- `homunculus/orchestrator/teacher.py` - OpenAI-compatible teacher client
- `homunculus/orchestrator/student.py` - Local mlx-lm subprocess wrapper
- `homunculus/task_runner/runner.py` - Git worktree isolation, patch application, verification
- `homunculus/memory_client/engram.py` - Engram HTTP client
- `homunculus/dataset_builder/builder.py` - SFT/DPO curation and snapshot materialization
- `homunculus/trainer/manager.py` - Training orchestration, candidate evaluation, promotion gates
- `homunculus/storage.py` - Artifact persistence (events, episodes, patches, registry)
- `homunculus/config.py` - TOML config parsing into typed dataclasses
- `homunculus/policy.py` - Guardrail pattern matching (block/warn rules)
- `homunculus/models.py` - Core dataclasses (EpisodeRecord, SFTSample, AdapterManifest, etc.)

### Safety Boundaries

These are **intentional constraints**, not bugs:

- Source workspace must be clean before any episode
- `run-episode` never mutates the source repo during verification (worktree isolation)
- Accepted patches are auto-committed to the source repo when `[daemon].auto_commit_on_accept = true` (default). Set to `false` to retain the manual `apply-episode` workflow — patch artifacts remain available either way.
- Training only from immutable materialized snapshots
- Candidate promotion requires `--human-approved` flag

### Artifact Layout

```
traces/
  events.jsonl          # Append-only lifecycle events
  episodes.jsonl        # Terminal episode records
  patches/<episode_id>.patch

datasets/
  seed/sft_seed.jsonl   # Required for snapshot generation
  sft/{train,valid,test}.jsonl
  dpo/{train,valid}.jsonl
  snapshots/sft/<snapshot_id>/

models/
  adapters/<candidate_id>/
  registry.json         # Candidate manifests, active pointer

runtime/
  worktrees/<episode_id>/  # Temporary, cleaned up after episode
```

## Testing Patterns

Tests use `tempfile.TemporaryDirectory` and create isolated git repos. The pattern:

```python
def _make_repo(self, temp_path: Path) -> tuple[Path, str]:
    # Creates repo, commits, generates diff, resets
    ...

# Tests inject StaticTeacher/StaticStudent for deterministic responses
# InMemoryMemoryClient replaces Engram in tests
```

Tests require git to be available. Use `@unittest.skipUnless(shutil.which("git"), "git is required")`.

## Configuration

TOML-based. Copy `homunculus.example.toml` to `homunculus.toml`. Key sections:

- `[teacher]` - OpenAI-compatible endpoint config
- `[student]` - mlx-lm subprocess commands
- `[memory]` - Engram HTTP endpoints
- `[workspaces.<name>]` - Repo path + verification commands
- `[guardrails]` - Block/warn regex patterns
- `[thresholds]` / `[promotion]` - Training and promotion gates

Teacher output must decode to JSON with `plan`, `candidate_patch`, `rationale` fields.

## Environment Variables

- `OPENAI_API_KEY` - Teacher API auth
- `ENGRAM_MCP_BEARER_TOKEN` - Engram bearer token

## Dependencies

- Python 3.11+
- Git on PATH
- Optional: `mlx-lm` for real local inference and training
