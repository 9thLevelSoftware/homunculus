# Phase 2: Introspection System — Context

## Phase Goal

Give the agent self-awareness — ability to analyze its own performance and identify weaknesses.

## Requirements

From ROADMAP.md Phase 2:

| ID | Requirement | Priority |
|----|-------------|----------|
| INT-1 | Metric-Driven mode — track patch success rate, retries, failure stages | Must-have |
| INT-2 | Self-Critique mode — review episodes, identify patterns via model | Must-have |
| INT-3 | Coverage & Gap Analysis — pytest-cov, TODO scanning, dead code detection | Must-have |
| INT-4 | Comparative Analysis — compare winning vs losing patches for same task | Must-have |
| INT-5 | Scheduler — rotate modes (metrics every cycle, critique every 3, coverage every 5, comparative every 3) | Must-have |

## Existing Assets

### Data Sources
- `traces/episodes.jsonl` — Episode records with outcome, failure_stage, attempt_index, comparison_group
- `traces/events.jsonl` — Append-only event log for all lifecycle events
- `traces/patches/*.patch` — Stored patch artifacts for comparison

### Infrastructure
- `storage.py:ArtifactStore` — `load_episodes()`, `load_jsonl()`, `append_event()` methods
- `models.py:EpisodeRecord` — Full episode data including verification results
- `models.py:EvaluationMetrics` — Already-defined metrics dataclass (compile_pass_rate, task_success_rate, etc.)
- `orchestrator/teacher.py:TeacherClient` — OpenAI-compatible client for critique mode
- `daemon.py:Daemon` — `run_once()` / `run_continuous()` cycle hooks for scheduler integration

### Config
- `config.py:HomunculusConfig` — TOML config loading with typed dataclasses
- Existing pattern: `DaemonSettings`, `PromotionSettings`, `TrainingSettings`

## Architecture Decisions

1. **Module structure**: New `homunculus/introspection/` package with one file per mode + scheduler
2. **Scheduler integration**: Scheduler called at start of each daemon cycle, returns which modes to run
3. **Mode contract**: Each mode implements `IntrospectionMode` protocol with `run()` -> `IntrospectionResult`
4. **Result persistence**: Results appended to `traces/events.jsonl` with type="introspection.{mode}"
5. **Teacher reuse**: Critique mode reuses existing `TeacherClient` with introspection-specific prompts

## Plan Structure

| Plan | Wave | Focus | Dependencies |
|------|------|-------|--------------|
| 02-01 | 1 | Infrastructure (scheduler, contracts, config) | None |
| 02-02 | 2 | Metrics mode | 02-01 |
| 02-03 | 2 | Coverage mode | 02-01 |
| 02-04 | 2 | Critique mode | 02-01 |
| 02-05 | 2 | Comparative mode | 02-01 |

## Risk Areas

| Risk | Mitigation |
|------|------------|
| Coverage mode subprocess failures | Graceful degradation if pytest-cov not installed |
| Critique mode API costs | Rate limiting, configurable enable/disable |
| Large episode.jsonl performance | Windowed queries (last N episodes) |
| Comparative needs matching groups | Skip if no comparison_group matches exist |

## Success Criteria

From ROADMAP.md:
- [ ] `introspection/metrics.py` collects and trends key metrics
- [ ] `introspection/critique.py` generates structured weakness reports
- [ ] `introspection/coverage.py` identifies test gaps and hotspots
- [ ] `introspection/comparative.py` extracts lessons from episode pairs
- [ ] `introspection/scheduler.py` rotates modes correctly
- [ ] Integration tests verify end-to-end introspection cycle
