# Homunculus — Roadmap

## Phases

- [x] **Phase 0: Autonomous Bootstrap** — Complete
- [ ] **Phase 1: Daemon Mode** — Continuous operation
- [ ] **Phase 2: Introspection System** — Self-awareness
- [ ] **Phase 3: Task Generation** — Self-direction
- [ ] **Phase 4: Weight Evolution** — Self-improvement
- [ ] **Phase 5: Full Autonomy** — Hands-off

## Phase Details

### Phase 0: Autonomous Bootstrap (COMPLETE)

**Goal**: Enable the agent to modify its own code without human approval gates.

**Requirements**: Approval gate removal, auto-commit, basic daemon, task generation stub

**Recommended Agents**: Senior Developer, Infrastructure & DevOps Engineer

**Success Criteria**:
- [x] `python -m homunculus.daemon --config homunculus.toml --once` runs
- [x] Agent picks up seed task from `suggestions/`
- [x] Tests pass
- [x] Merged to main

**Plans**: 1 (complete)

---

### Phase 1: Daemon Mode

**Goal**: Enable continuous autonomous operation with proper lifecycle management.

**Requirements**: 
- Configurable cycle interval (read from `[daemon].cycle_interval_minutes`)
- Multi-episode cycles (up to `max_episodes_per_cycle`)
- SIGTERM/SIGINT graceful shutdown (finish current episode, save state, exit)
- State persistence to `runtime/daemon_state.json`

**Recommended Agents**: Senior Developer, Infrastructure & DevOps Engineer

**Success Criteria**:
- [x] `python -m homunculus.daemon --config homunculus.toml` runs continuously
- [x] Ctrl+C stops gracefully after current episode completes
- [x] State persists across restarts (cycles_completed, total_episodes, last_cycle_at)
- [x] Config interval is respected between cycles
- [x] Tests cover signal handling and state persistence

**Plans**: 3 (complete)

**Files to Create/Modify**:
- `homunculus/daemon.py` — Add continuous loop, signal handlers, state management
- `homunculus/config.py` — Add `DaemonSettings` dataclass (currently config exists but no typed parsing)
- `tests/test_daemon.py` — Add state persistence and signal handling tests

---

### Phase 2: Introspection System

**Goal**: Give the agent self-awareness — ability to analyze its own performance and identify weaknesses.

**Requirements**:
- **Mode 1: Metric-Driven** — Track quantitative signals (patch success rate, retries, failure stages)
- **Mode 2: Self-Critique** — Review recent episodes, identify patterns via model
- **Mode 3: Coverage & Gap Analysis** — pytest-cov, TODO scanning, dead code detection
- **Mode 4: Comparative Analysis** — Compare winning vs losing patches for same task
- **Scheduler** — Rotate modes (metrics every cycle, critique every 3, coverage every 5, comparative every 3)

**Recommended Agents**: Senior Developer, Data Analytics Engineer, AI Engineer

**Success Criteria**:
- [ ] `introspection/metrics.py` collects and trends key metrics
- [ ] `introspection/critique.py` generates structured weakness reports
- [ ] `introspection/coverage.py` identifies test gaps and hotspots
- [ ] `introspection/comparative.py` extracts lessons from episode pairs
- [ ] `introspection/scheduler.py` rotates modes correctly
- [ ] Integration tests verify end-to-end introspection cycle

**Plans**: 4-5

**Files to Create**:
- `homunculus/introspection/__init__.py`
- `homunculus/introspection/metrics.py`
- `homunculus/introspection/critique.py`
- `homunculus/introspection/coverage.py`
- `homunculus/introspection/comparative.py`
- `homunculus/introspection/scheduler.py`
- `tests/test_introspection.py`

---

### Phase 3: Task Generation

**Goal**: Enable self-directed work — agent generates its own tasks from introspection insights.

**Requirements**:
- **Task generator** — Convert introspection weaknesses into actionable tasks
- **Suggestion scanner** — Evaluate user suggestions against current growth areas
- **Prioritizer** — Rank tasks by alignment with weaknesses, complexity, freshness
- **Queue persistence** — `runtime/task_queue.jsonl`, `runtime/task_history.jsonl`

**Recommended Agents**: Senior Developer, AI Engineer

**Success Criteria**:
- [ ] Agent generates tasks from metric trends ("async patches fail 60% → practice async")
- [ ] Agent generates tasks from self-critique output
- [ ] Agent evaluates user suggestions for resonance with current weaknesses
- [ ] Task queue persists across restarts
- [ ] Prioritization produces sensible ordering
- [ ] Integration test: introspection → task generation → daemon picks up task

**Plans**: 3-4

**Files to Create**:
- `homunculus/task_generator/__init__.py`
- `homunculus/task_generator/generator.py`
- `homunculus/task_generator/prioritizer.py`
- `tests/test_task_generator.py`

**Files to Modify**:
- `homunculus/suggestions.py` — Add resonance evaluation
- `homunculus/daemon.py` — Integrate task generator into cycle

---

### Phase 4: Weight Evolution

**Goal**: Enable continuous model improvement — agent trains and merges its own weights.

**Requirements**:
- **LoRA merge pipeline** — Merge accumulated LoRAs into base model (mergekit/MLX)
- **Lineage tracking** — Full history of base generations, LoRAs merged, episodes incorporated
- **Merge validation** — Model loads, generates coherent output, passes canary suite
- **Auto-trigger** — Train after N samples, merge after N LoRAs

**Recommended Agents**: Senior Developer, AI Engineer, Infrastructure & DevOps Engineer

**Success Criteria**:
- [ ] `evolution/merge.py` successfully merges LoRA stack to base
- [ ] `evolution/lineage.py` tracks full model history
- [ ] `evolution/validation.py` catches bad merges before adoption
- [ ] Merge failure generates introspection task after 3 consecutive failures
- [ ] Tests cover merge success, merge failure, and rollback scenarios

**Plans**: 3-4

**Files to Create**:
- `homunculus/evolution/__init__.py`
- `homunculus/evolution/merge.py`
- `homunculus/evolution/lineage.py`
- `homunculus/evolution/validation.py`
- `tests/test_evolution.py`

**Files to Modify**:
- `homunculus/trainer/manager.py` — Integrate merge triggers
- `homunculus/config.py` — Add `EvolutionSettings` dataclass

---

### Phase 5: Full Autonomy

**Goal**: Hands-off operation — agent runs continuously, finds its own work, trains its own models.

**Requirements**:
- All previous phases integrated and stable
- Agent has run autonomously for 1+ weeks
- Agent has generated and completed 10+ self-directed tasks
- Agent has trained and merged at least one LoRA
- Test suite still passes without intervention

**Recommended Agents**: QA Verification Specialist, Reality Checker

**Success Criteria**:
- [ ] 1+ week of unattended operation
- [ ] 10+ self-directed tasks completed
- [ ] At least 1 LoRA trained and merged
- [ ] Test suite passes
- [ ] Metrics stable or improving (patch success rate, coverage)
- [ ] No human intervention required

**Plans**: 1-2 (mostly observation and validation)

**Verification Approach**:
- Monitor `traces/events.jsonl` for activity patterns
- Review `runtime/daemon_state.json` for uptime
- Check `models/registry.json` for lineage progression
- Spot-check committed patches for quality

---

## Progress

| Phase | Plans | Completed | Status |
|-------|-------|-----------|--------|
| Phase 0: Bootstrap | 1 | 1 | Complete |
| Phase 1: Daemon | 3 | 3 | Complete |
| Phase 2: Introspection | 4-5 | 0 | Not started |
| Phase 3: Task Generation | 3-4 | 0 | Not started |
| Phase 4: Weight Evolution | 3-4 | 0 | Not started |
| Phase 5: Full Autonomy | 1-2 | 0 | Not started |
| **Total** | **15-18** | **4** | **~25% complete** |
