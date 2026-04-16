# Autonomous Self-Evolution Design Spec

**Date:** 2026-04-15  
**Status:** Approved  
**Codename:** Homunculus Unchained

## Overview

Transform homunculus from a conservative teacher-student scaffold with human approval gates into a fully autonomous self-improving agent that evolves both its own codebase and model weights.

### Inspiration

[yoyo-evolve](https://github.com/9thLevelSoftware/yoyo-evolve): "200 lines of Rust. Zero human code. One rule: evolve or die." An agent that reads its own source, generates improvements, tests them, commits passing changes, reverts failures. No approval gates. Tests are the only law.

### Core Philosophy

- The agent operates on **itself** — its codebase is the workspace
- **Tests pass → ship. Tests fail → revert.** No other gates.
- Self-directed introspection identifies weaknesses and generates tasks
- Weight evolution via LoRA training + periodic merge to base
- Fully unattended daemon mode — check in occasionally to see what it's become

## Hardware Context

| Machine | Specs | Best Use |
|---------|-------|----------|
| Primary PC | RTX 5070 (12GB), 64GB RAM, i9-12900KS | QLoRA training, episode execution |
| Mac Mini | M4, 24GB unified | MLX inference, backup training |
| Cloud (later) | A100 on-demand | Full fine-tune consolidation (Phase 2) |

### Training Strategy

**Phase 1 (now):** Local LoRA training + periodic merge to base weights. Free, runs on existing hardware.

**Phase 2 (later):** Cloud bursting for cleaner full fine-tune when the autonomous loop proves itself. ~$16-40/month.

## System Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                     HOMUNCULUS DAEMON                           │
│                                                                 │
│  ┌─────────────┐    ┌─────────────┐    ┌─────────────────────┐ │
│  │ Introspect  │───▶│  Generate   │───▶│      Execute        │ │
│  │             │    │    Task     │    │      Episode        │ │
│  │ - Metrics   │    │             │    │                     │ │
│  │ - Critique  │    │ "What       │    │ - Generate patch    │ │
│  │ - Coverage  │    │  should I   │    │ - Test in worktree  │ │
│  │ - Compare   │    │  work on?"  │    │ - Pass → commit     │ │
│  └─────────────┘    └─────────────┘    │ - Fail → revert     │ │
│         ▲                              └──────────┬──────────┘ │
│         │                                         │            │
│         │           ┌─────────────┐               │            │
│         │           │   Curate    │◀──────────────┘            │
│         │           │             │                            │
│         │           │ Successful  │                            │
│         │           │ episodes →  │                            │
│         │           │ SFT data    │                            │
│         │           └──────┬──────┘                            │
│         │                  │                                   │
│         │                  ▼                                   │
│         │           ┌─────────────┐    ┌─────────────────────┐ │
│         │           │    Train    │───▶│       Merge         │ │
│         │           │    LoRA     │    │    LoRA → Base      │ │
│         └───────────│             │    │                     │ │
│                     │ When enough │    │ Periodic weight     │ │
│      (metrics       │ samples     │    │ evolution           │ │
│       feedback)     └─────────────┘    └─────────────────────┘ │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
                    ┌─────────────────┐
                    │   ITSELF        │
                    │   homunculus/   │
                    └─────────────────┘
```

### Core Loop

```python
while alive:
    weaknesses = introspect(metrics, recent_episodes, codebase)
    task = generate_task(weaknesses, user_suggestions)
    episode = execute_episode(task)  # patch → test → commit/revert
    curate(episode)
    
    if should_train():
        train_lora()
    if should_merge():
        merge_lora_to_base()
    
    sleep(interval)
```

## Component Specifications

### 1. Daemon Lifecycle

**Entry point:** `python -m homunculus.daemon --config homunculus.toml`

**States:** IDLE → INTROSPECT → TASK_GENERATION → EXECUTING → EVOLVE → SLEEP → (repeat)

**Configuration:**

```toml
[daemon]
enabled = true
cycle_interval_minutes = 480        # 8 hours
introspection_modes = ["metrics", "critique", "coverage", "comparative"]
max_episodes_per_cycle = 5
sleep_on_empty_queue = true

[evolution]
auto_promote = true                 # No human approval
auto_apply = true                   # Commit directly
auto_train_after_samples = 50
auto_merge_after_loras = 5
rollback_on_degradation = true
```

**State persistence:** `runtime/daemon_state.json`

**Graceful shutdown:** SIGTERM/SIGINT finishes current episode, saves state, exits cleanly.

### 2. Introspection System

Four modes, rotated on a schedule:

#### Mode 1: Metric-Driven

Tracks quantitative signals over rolling window:
- Patch success rate
- First-try success rate
- Average retries
- Failure by stage/error type
- Test coverage delta
- Complexity delta
- Files most/least modified

Generates tasks from trends: "My async patches fail 60% → practice async patterns"

#### Mode 2: Self-Critique

Asks itself to review recent episodes and identify patterns:

```python
prompt = """
Review my last N episodes. Successes: [...] Failures: [...]
What patterns do you see? What am I bad at? What should I practice?
"""
```

Output: Structured weaknesses with evidence and suggested tasks.

#### Mode 3: Coverage & Gap Analysis

Automated scanning:
- pytest-cov for test coverage gaps
- TODO/FIXME scanning
- Dead code detection (vulture)
- Complexity hotspots (radon)

#### Mode 4: Comparative Analysis

For tasks with multiple attempts, compare winners vs losers:
- What did successful patches do differently?
- Extract lessons, feed into DPO training

**Scheduler:** Metrics every cycle, critique every 3 cycles, coverage every 5 cycles, comparative every 3 cycles.

### 3. Task Generation

**Priority order:**

1. **Introspection-generated** (primary): Weaknesses, gaps, lessons → tasks
2. **User suggestions** (secondary): Only if the agent finds them "interesting" — aligned with current growth areas

**Task structure:**

```python
@dataclass
class GeneratedTask:
    task_id: str
    source: str                     # "introspection" | "user" | "continuation"
    introspection_mode: str | None
    prompt: str
    context: dict
    priority: float                 # 0.0 - 1.0
    estimated_complexity: str       # "trivial" | "small" | "medium" | "large"
    target_files: list[str]
    success_criteria: str
    created_at: str
    expires_at: str | None
```

**User suggestions:** Markdown files in `suggestions/` directory. Agent evaluates each against current weaknesses, picks up only if resonant.

**Suggestion lifecycle:**
- New suggestions placed in `suggestions/`
- When processed (accepted or rejected), moved to `suggestions/archive/` with outcome appended to filename
- Example: `add-feature.md` → `suggestions/archive/add-feature.accepted.md` or `add-feature.skipped.md`

**Persistence:** `runtime/task_queue.jsonl`, `runtime/task_history.jsonl`

### 4. Training Pipeline

**Two-phase weight evolution:**

#### Phase 1: LoRA Accumulation

- Episodes succeed → SFT data accumulates
- When `auto_train_after_samples` threshold hit → train LoRA
- Auto-evaluate against canary tasks
- Auto-promote if gates pass (no human approval)
- LoRA added to active stack

#### Phase 2: Merge Consolidation

- When `auto_merge_after_loras` threshold hit → merge to base
- Uses mergekit or MLX merge capabilities
- Merge method: TIES, DARE, or linear (configurable)

**Merge validation before adoption:**
1. Model loads without errors
2. Generates coherent output on test prompt
3. Passes canary suite above threshold

**If merge validation fails:** Keep the current base model, discard the failed merge, log the failure to memory, and continue accumulating LoRAs. The next merge attempt will include all accumulated LoRAs. After 3 consecutive merge failures, generate an introspection task to investigate.

**Lineage tracking:** Full history of base model generations, LoRAs merged, episodes incorporated.

### 5. Safety Model

**Philosophy:** Tests pass → ship. Tests fail → revert. No other constraints.

**Removed:**
- `require_human_approval` config and logic
- `--human-approved` CLI flag
- Guardrail block enforcement (keep for logging only)
- Manual `apply-episode` step
- Separate evaluate/promote flow

**Kept and automated:**
- Worktree isolation
- Episode persistence (audit trail)
- Memory of failures
- Canary evaluation (metrics-based)
- Promotion on gate pass
- Commit on accept

**Optional safety net:** `rollback_on_degradation` — if metrics tank (success rate drops >20%, test pass rate drops >10%), auto-rollback to last healthy checkpoint.

## Bootstrap Plan

### Phase 0: Preparation (manual, ~1 day)

| Change | File(s) | Description |
|--------|---------|-------------|
| Self-targeting workspace | `homunculus.toml` | Point `workspaces.self` at homunculus repo |
| Remove approval gate | `config.py`, `manager.py` | Delete `require_human_approval` logic |
| Auto-promote | `manager.py` | Promote immediately when gates pass |
| Auto-commit | `runner.py` | Commit accepted patches to source repo |
| Basic daemon | `daemon.py` (new) | Simple loop: run episode → sleep → repeat |
| Seed task mechanism | `suggestions/` | Directory for seeding initial tasks |

**Success criteria:**
```bash
python -m homunculus.daemon --config homunculus.toml --once
# Agent picks up seed task, generates patch, tests, commits if pass
```

### Phase 1: First Autonomous Tasks (agent does, you observe)

Seed tasks:
- Add daemon mode with configurable interval
- Add graceful shutdown handling
- Add daemon state persistence

### Phase 2: Introspection Bootstrap (agent builds its eyes)

Seed tasks:
- Add metrics collection for episodes
- Add self-critique capability
- Add coverage gap analysis
- Add comparative episode analysis

### Phase 3: Task Generation (agent learns to self-direct)

Seed tasks:
- Build task generator from introspection
- Add user suggestion scanning
- Add task prioritization logic

After this phase, seeds become optional — agent can find its own work.

### Phase 4: Weight Evolution (agent evolves its brain)

Seed tasks:
- Add LoRA merge pipeline
- Add model lineage tracking
- Add merge validation checks

### Phase 5: Full Autonomy (hands off)

Agent runs continuously, finds its own tasks, trains its own LoRAs, merges its own weights. You check in occasionally to see what it's become.

## File Structure Changes

```
homunculus/
├── daemon.py                    # NEW: daemon entry point
├── introspection/               # NEW: introspection system
│   ├── __init__.py
│   ├── metrics.py               # Metric collection and analysis
│   ├── critique.py              # Self-critique via model
│   ├── coverage.py              # Coverage/gap analysis
│   ├── comparative.py           # Winner/loser comparison
│   └── scheduler.py             # Mode rotation
├── task_generator/              # NEW: task generation
│   ├── __init__.py
│   ├── generator.py             # Core task generation
│   ├── suggestions.py           # User suggestion parser
│   └── prioritizer.py           # Task prioritization
├── evolution/                   # NEW: weight evolution
│   ├── __init__.py
│   ├── merge.py                 # LoRA merge pipeline
│   ├── lineage.py               # Model lineage tracking
│   └── validation.py            # Merge validation
├── orchestrator/
│   └── loop.py                  # MODIFIED: auto-commit on accept
├── trainer/
│   └── manager.py               # MODIFIED: remove approval gates
├── config.py                    # MODIFIED: new daemon/evolution sections
└── ...

runtime/
├── daemon_state.json            # NEW: daemon persistence
├── task_queue.jsonl             # NEW: pending tasks
├── task_history.jsonl           # NEW: completed tasks
└── ...

suggestions/                     # NEW: user suggestion directory
├── phase1-daemon-mode.md
├── phase2-introspection.md
└── ...
```

## Configuration Changes

```toml
# NEW SECTIONS

[daemon]
enabled = true
cycle_interval_minutes = 480
introspection_modes = ["metrics", "critique", "coverage", "comparative"]
max_episodes_per_cycle = 5
sleep_on_empty_queue = true

[evolution]
auto_promote = true
auto_apply = true
auto_train_after_samples = 50
auto_merge_after_loras = 5
rollback_on_degradation = true
min_merge_canary_rate = 0.8
merge_method = "ties"

[introspection]
metrics_window_episodes = 100
self_critique_model = "self"
coverage_tool = "pytest-cov"
min_improvement_delta = 0.01

# MODIFIED (not removed - keep the gates, remove the human approval)
[promotion]
# require_human_approval = true  # DELETE THIS LINE
allow_zero_canary_regressions = true   # KEEP - automated gate
min_task_success_delta = 0.01          # KEEP - automated gate
max_tool_misuse_increase = 0.0         # KEEP - automated gate
```

## Success Metrics

**Phase 0 complete when:**
- Agent can modify its own code and commit passing changes
- Basic daemon loop runs

**Phase 5 complete when:**
- Agent has run autonomously for 1+ weeks
- Agent has generated and completed 10+ self-directed tasks
- Agent has trained and merged at least one LoRA
- Test suite still passes
- You didn't have to intervene

**Long-term health indicators:**
- Patch success rate stable or improving
- Test coverage stable or improving
- Complexity not exploding
- Agent is working on meaningful tasks, not trivial busywork

## Risks and Mitigations

| Risk | Mitigation |
|------|------------|
| Agent disables its own tests | Darwinian: an agent that kills its immune system dies. Could add "sacred files" later if needed. |
| Subtle bugs tests don't catch | Coverage analysis, complexity monitoring, comparative learning from failures |
| Gets stuck in local minimum | Multiple introspection modes, self-critique, comparative analysis |
| Catastrophic regression | `rollback_on_degradation` auto-reverts if metrics tank |
| Runaway complexity | Complexity trend tracking in metrics, will generate simplification tasks |
| Cloud costs spiral | Phase 1 is free. Phase 2 cloud is optional and capped by merge frequency. |

## Open Questions

None — all decisions made during brainstorming.

## Appendix: Seed Task Template

```markdown
<!-- suggestions/example-task.md -->
# Task Title

## Priority
HIGH | MEDIUM | LOW

## What
Clear description of what needs to be done.

## Why
Why this matters for self-improvement.

## Success Criteria
How to know it worked.

## Hints
- File locations
- Relevant existing code
- Gotchas to watch for
```
