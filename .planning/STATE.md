# Project State

## Current Position
- **Phase**: 4 of 5 (Weight Evolution)
- **Status**: Phase 4 complete — review passed after spec-fix branch (`fix/spec-alignment`, 23 commits)
- **Last Activity**: Phase 4 spec-fix branch + skipUnless guards on optional-dep tests (2026-04-16)

## Progress
```
[#################...] 100% — 17/17 plans complete (Phase 0-4 done)
```

## Completed Work

### Phase 2: Introspection System (COMPLETE)
- Created `homunculus/introspection/` package with base protocol and scheduler
- Added `IntrospectionMode` protocol and `IntrospectionContext` dataclass
- Added `IntrospectionResult` dataclass to models.py
- Added `IntrospectionSettings` to config.py with graceful defaults
- Implemented `IntrospectionScheduler` with mode rotation (metrics:1, critique:3, coverage:5, comparative:3)
- Implemented `MetricsMode` — quantitative performance metrics
- Implemented `CoverageMode` — pytest-cov, TODO scanning, test gaps
- Implemented `CritiqueMode` — LLM-based episode pattern analysis
- Implemented `ComparativeMode` — winner vs loser patch comparison
- Added introspection result persistence to storage.py
- Commits: `fe29ded`, `3b2e70a`

### Phase 1: Daemon Mode (COMPLETE)
- Added `DaemonSettings` dataclass to config.py
- Added `DaemonState` dataclass to models.py with serialization
- Added state persistence (load_state, save_state) with atomic writes
- Added single-instance lock (acquire_lock, release_lock)
- Created `runtime.py` module to avoid circular imports
- Implemented `run_continuous()` with configurable interval
- Added SIGINT/SIGTERM signal handlers with graceful shutdown
- Added 7 new tests (26 total, all passing)
- Commits: `7159a42`, `db0f744`, `d51e7a7`

### Phase 0: Autonomous Bootstrap (COMPLETE)
- Removed `require_human_approval` from PromotionSettings
- Removed `human_approved` parameter from `promote_candidate()`
- Added `CommitResult` dataclass and `commit_to_source()` method
- Added `GeneratedTask` dataclass
- Added `SuggestionReader` class for markdown parsing
- Added `Daemon` class with `--once` mode
- Added `[daemon]` and `[evolution]` config sections
- Merged to master: commit `4225eab`

## Recent Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Execution mode | Autonomous | Minimal check-ins, review at phase boundaries |
| Planning depth | Deep Analysis | Thorough architectural analysis before each phase |
| Cost profile | Premium | Opus throughout for maximum quality |
| Scope | All phases (1-5) | Full roadmap from daemon through autonomy |

## Phase 1 Verification

| Criteria | Status |
|----------|--------|
| `python -m homunculus.daemon --config homunculus.toml` runs continuously | Pass |
| Ctrl+C stops gracefully after current episode completes | Pass |
| State persists across restarts | Pass |
| Config interval is respected between cycles | Pass |
| Tests cover signal handling and state persistence | Pass (7 new tests) |

## Phase 2 Plan Summary

| Plan | Wave | Title | Agent |
|------|------|-------|-------|
| 02-01 | 1 | Infrastructure (scheduler, config, storage) | Senior Developer |
| 02-02 | 2 | Metrics Mode | Data Analytics Engineer |
| 02-03 | 2 | Coverage Mode | Infrastructure & DevOps Engineer |
| 02-04 | 2 | Critique Mode | AI Engineer |
| 02-05 | 2 | Comparative Mode | Data Analytics Engineer |

### Auto-Refine Applied (1 cycle)
Critical issues fixed:
- Plan 02-01: Added graceful config defaults, cycle 0 edge case fix, result persistence
- Plan 02-03: Fixed pytest-cov JSON mechanism, use sys.executable
- Plan 02-04: Fixed teacher API signature (TaskRequest, not raw prompt)

## Phase 2 Verification

| Criteria | Status |
|----------|--------|
| IntrospectionMode protocol defined | Pass |
| IntrospectionScheduler with mode rotation | Pass |
| MetricsMode computes success/retry/failure rates | Pass |
| CoverageMode runs pytest-cov and TODO scanning | Pass |
| CritiqueMode uses teacher API for LLM analysis | Pass |
| ComparativeMode groups and compares episodes | Pass |
| All modes implement protocol correctly | Pass |
| Tests pass (26 total) | Pass |
| Commits: `fe29ded`, `3b2e70a` | Complete |

## Phase 3 Plan Summary

| Plan | Wave | Title | Agent |
|------|------|-------|-------|
| 03-01 | 1 | Task Queue Infrastructure | Senior Developer |
| 03-02 | 2 | Task Generator | AI Engineer |
| 03-03 | 2 | Suggestion Resonance Scanner | AI Engineer |
| 03-04 | 3 | Prioritizer + Integration + Tests | Senior Developer |

### Auto-Refine Applied (2 cycles)
Critical fixes incorporated:
- Plan 03-01: TaskQueueEntry field specification, nested serialization
- Plan 03-02: Defensive finding parsing with `_infer_severity()`
- Plan 03-03: Extended RESONANCE_KEYWORDS with mode-specific terms
- Plan 03-04: Backward-compatible Daemon constructor, edge case handling

## Phase 3 Execution Results

| Plan | Wave | Status | Tests Added |
|------|------|--------|-------------|
| 03-01 | 1 | Complete | 10 (queue infrastructure) |
| 03-02 | 2 | Complete | 47 (task generator) |
| 03-03 | 2 | Complete | 8 (resonance scanner) |
| 03-04 | 3 | Complete | 47 (prioritizer + integration) |

**Total Tests**: 174 (111 new in Phase 3)
**Commits**: `abeb215`, `9c61a3e`, `c8a16ac`

## Phase 3 Review Results

| Cycle | Findings | Fixed | Verdict |
|-------|----------|-------|---------|
| 1 | 7 WARNINGs, 12 SUGGESTIONs | 7 | NEEDS WORK |
| 2 | 0 | 0 | PASS |

Key fixes applied:
- Deduplication after priority calculation (logic bug)
- Smooth exponential decay for resonance weights
- Defensive dict access in generator prompt formatting
- FIFO tiebreaker and orchestrator exception tests

## Phase 4 Plan Summary

| Plan | Wave | Title | Agent |
|------|------|-------|-------|
| 04-01 | 1 | Infrastructure (Config, Models, Storage) | Senior Developer |
| 04-02 | 2 | Merge Pipeline | AI Engineer |
| 04-03 | 2 | Lineage Tracking | Senior Developer |
| 04-04 | 3 | Validation + Integration | AI Engineer |

### Auto-Refine Applied (1 cycle)
Critical fixes incorporated:
- Plan 04-01: Atomic file updates for `update_merge()` using temp file + `os.replace()`
- Plan 04-02: Added prerequisite task to verify mergekit installation before implementation
- Plan 04-04: Lazy imports to avoid breaking existing tests, persistent merge failure counter, daemon integration hook, platform-aware coherence validation

## Phase 4 Execution Results

| Plan | Wave | Status | Tests Added |
|------|------|--------|-------------|
| 04-01 | 1 | Complete | 13 (infrastructure) |
| 04-02 | 2 | Complete | 16 (merge pipeline) |
| 04-03 | 2 | Complete | 10 (lineage tracking) |
| 04-04 | 3 | Complete | 14 (validation + integration) |

**Total Tests**: 230 (53 new in Phase 4)
**Commits**: `0b720f9`, `7214c59`, `1159307`

### Phase 4 Verification

| Criteria | Status |
|----------|--------|
| `evolution/merge.py` merges LoRA stack to base | Pass (MLX α/r math correct; mergekit uses baked checkpoints) |
| `evolution/lineage.py` tracks full model history | Pass (register_lora wired into promote_candidate) |
| `evolution/validation.py` catches bad merges | Pass (fails closed w/o backend; coherence hardened) |
| Merge failure generates introspection task after N failures | Pass (integration test) |
| Tests cover merge success, failure, and rollback | Pass (286 tests total) |
| Daemon queue is restart-safe | Pass (Task 17) |
| Commits land in target workspace | Pass (Task 16) |
| Install from source works (`pip install -e .`) | Pass (Task 1) |

### Phase 4 Spec-Fix Branch

After initial Phase 4 close-out, an audit + `/legion:review` surfaced
5 BLOCKERs and 16 WARNINGs (see
`.planning/phases/04-weight-evolution/04-REVIEW.md`). All resolved on
branch `fix/spec-alignment` (22 commits, `2b7128e..339dced`). Test
suite grew from 230 → 286 tests, all passing. Details in the plan
`docs/superpowers/plans/2026-04-16-spec-alignment-and-merge-correctness.md`
and the review doc above.

## Next Action

Phase 4 complete and reviewed. Run `/legion:plan 5` to plan Phase 5:
Full Autonomy.
