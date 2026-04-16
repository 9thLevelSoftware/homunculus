# Phase 3: Task Generation — Context

## Goal
Enable self-directed work — agent generates its own tasks from introspection insights.

## Requirements
1. **Task generator** — Convert introspection weaknesses into actionable tasks
2. **Suggestion scanner** — Evaluate user suggestions against current growth areas
3. **Prioritizer** — Rank tasks by alignment with weaknesses, complexity, freshness
4. **Queue persistence** — `runtime/task_queue.jsonl`, `runtime/task_history.jsonl`

## Success Criteria
- [ ] Agent generates tasks from metric trends ("async patches fail 60% → practice async")
- [ ] Agent generates tasks from self-critique output
- [ ] Agent evaluates user suggestions for resonance with current weaknesses
- [ ] Task queue persists across restarts
- [ ] Prioritization produces sensible ordering
- [ ] Integration test: introspection → task generation → daemon picks up task

## Existing Assets
From Phase 2 (Introspection System):
- `homunculus/introspection/` — All 4 modes implemented and tested
- `IntrospectionResult` dataclass with `findings`, `metrics`, `recommendations`
- `IntrospectionScheduler` for mode rotation
- `load_introspection_results()` in storage.py

From Phase 1 (Daemon Mode):
- `Daemon` class in daemon.py with `run_once()` and `run_continuous()`
- `DaemonState` persistence
- Signal handling for graceful shutdown

From Phase 0 (Bootstrap):
- `SuggestionReader` in suggestions.py — parses markdown suggestions
- `GeneratedTask` dataclass — output format for all task sources
- Archive mechanism for processed suggestions

## Key Interfaces

### Input: IntrospectionResult (from Phase 2)
```python
@dataclass
class IntrospectionResult:
    mode: str  # "metrics" | "critique" | "coverage" | "comparative"
    timestamp: str
    findings: list[dict[str, Any]]  # Mode-specific findings
    summary: str
    metrics: dict[str, float]
    recommendations: list[str]
```

### Output: GeneratedTask (existing)
```python
@dataclass
class GeneratedTask:
    task_id: str
    source: str  # "introspection" | "user" | "generated"
    prompt: str
    priority: float  # 0.0 to 1.0
    success_criteria: str
    context: dict[str, Any]
    created_at: str
```

## Plan Structure

| Plan | Wave | Title | Agent |
|------|------|-------|-------|
| 03-01 | 1 | Task Queue Infrastructure | Senior Developer |
| 03-02 | 2 | Task Generator | AI Engineer |
| 03-03 | 2 | Suggestion Resonance Scanner | AI Engineer |
| 03-04 | 3 | Prioritizer + Integration + Tests | Senior Developer |

## Architecture Decisions
- **Single task format**: All sources (introspection, suggestions, generated) produce `GeneratedTask`
- **JSONL persistence**: Append-only for queue, separate history file for completed tasks
- **Resonance scoring**: Compare suggestion keywords against introspection recommendations
- **Priority formula**: Weighted combination of alignment (0.5), complexity (0.3), freshness (0.2)

## Codebase Conventions (from CODEBASE.md)
- Use `from __future__ import annotations` for forward references
- Dataclasses with `to_dict()`/`from_dict()` for persistence
- UTC timestamps via `utc_now()` helper
- Tests use `tempfile.TemporaryDirectory` for isolation
