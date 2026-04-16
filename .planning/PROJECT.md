# Homunculus

## What This Is

A teacher-student self-improving coding agent scaffold that operates on its own codebase. Runs episodes where a teacher model generates patches, tests verify correctness, and passing changes are committed automatically. Failed episodes are reverted. Successful episodes become training data for the local student model, enabling continuous self-improvement.

## Core Value

**"Tests pass → ship. Tests fail → revert."**

Inspired by [yoyo-evolve](https://github.com/9thLevelSoftware/yoyo-evolve): "200 lines of Rust. Zero human code. One rule: evolve or die." An agent that reads its own source, generates improvements, tests them, commits passing changes, reverts failures. No approval gates. Tests are the only law.

## Who It's For

The agent itself. Homunculus operates on its own codebase — it is both the tool and the target. Secondary users: developers who want to observe autonomous agent evolution.

## Requirements

### Validated
- [x] Phase 0: Autonomous bootstrap — approval gates removed, auto-commit, basic daemon with `--once` mode

### Active
- [ ] Phase 1: Continuous daemon mode with configurable interval, SIGTERM/SIGINT handling, state persistence
- [ ] Phase 2: Introspection system (metrics, self-critique, coverage analysis, comparative analysis)
- [ ] Phase 3: Task generation (introspection-driven tasks, user suggestion scanning, prioritization)
- [ ] Phase 4: Weight evolution (LoRA merge pipeline, lineage tracking, merge validation)
- [ ] Phase 5: Full autonomy — hands-off operation for 1+ weeks

### Out of Scope
- Cloud fine-tuning (deferred to Phase 2 of training strategy, after local LoRA proves itself)
- Multi-agent collaboration (single agent evolves alone)
- GUI/web interface (CLI and daemon only)
- Cross-repo operation (targets only itself)

## Constraints

| Constraint | Details |
|------------|---------|
| Hardware (Primary) | RTX 5070 (12GB), 64GB RAM, i9-12900KS — QLoRA training, episode execution |
| Hardware (Backup) | Mac Mini M4, 24GB unified — MLX inference, backup training |
| Safety Model | Tests are the only gate. No human approval. Worktree isolation mandatory. |
| Language | Python 3.11+ |
| Training | Local LoRA + periodic merge. No cloud until Phase 2 training strategy. |
| Testing | All changes must pass existing test suite. Coverage must not regress. |

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| Remove human approval gates | Enables true autonomy; yoyo-evolve philosophy | Implemented in Phase 0 |
| Tests as only safety gate | Darwinian selection — bad changes kill the agent | Core design principle |
| Self-targeting workspace | Agent must eat its own dogfood | `workspaces.self` points at homunculus repo |
| LoRA-first training | Free, runs on existing hardware | Defer full fine-tune to cloud phase |
| Four introspection modes | Different lenses catch different weaknesses | Metrics, critique, coverage, comparative |
| Suggestion archival | Track what the agent picked up vs ignored | Archive with outcome appended to filename |

## Architecture Influences

- **Episode lifecycle:** assess → preflight → recall → plan → execute → reflect → curate
- **Worktree isolation:** All patches applied in isolated git worktree, never directly to source
- **Event sourcing:** Append-only event log (traces/events.jsonl) for full audit trail
- **Snapshot training:** Training only from immutable dataset snapshots
- **Metric-driven promotion:** Automated gates based on compile rate, task success, tool misuse, retries

---
*Last updated: 2026-04-15 after Phase 0 completion*
