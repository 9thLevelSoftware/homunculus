# Phase 5 Context — Full Autonomy

**Date**: 2026-04-16
**Status**: Planned
**Architecture**: Clean (new `homunculus/autonomy/` package)
**Spec**: `.planning/specs/05-full-autonomy-spec.md`

## Goal

Hands-off operation. Agent runs continuously, finds its own work, trains its own models. Phase 5 is terminal and observation-focused: instrumentation, protocol, soak run, evidence-based sign-off.

## Requirements (from ROADMAP.md)

- All previous phases (0-4) integrated and stable
- 1+ week unattended operation
- 10+ self-directed tasks completed
- ≥1 LoRA trained and merged
- Test suite passes without intervention
- Metrics stable or improving

## Success Criteria (SC1–SC6)

See `.planning/specs/05-full-autonomy-spec.md` §2 for full measurement sources.

## Existing Assets

- Phase 0–4 fully landed (286 tests, all passing)
- `traces/events.jsonl`, `traces/episodes.jsonl` — activity source
- `runtime/daemon_state.json` — uptime source
- `runtime/task_history.jsonl` — task outcome source
- `models/registry.json` + lineage — weight evolution source
- `[evolution]` config section + merge failure counter — aggregated by watchdog
- `homunculus/cli.py doctor` — base preflight component

## Key Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Architecture | Clean (new package) | Matches Phase 2/3/4 pattern (`introspection/`, `task_generator/`, `evolution/`) |
| Arch proposals | Ran (M/C/P offered) | User requested deep review for terminal phase |
| Spec pipeline | Ran | Produces contract for CLI + dataclasses + tests |
| CODEBASE.md refresh | Wave 1 task | Stale (19 tests listed vs 286 actual) |
| Watchdog behavior | Advisory only, never stops daemon | Preserves "tests = only gate" philosophy; soak protocol defines human abort |
| Watchdog state | Dedicated `runtime/watchdog.json` | Separates watchdog signals from daemon lifecycle state |
| Soak duration | ≥7 full days | Matches ROADMAP "1+ week" |
| SC5 tolerance | `delta >= -0.02` | ≤2% regression tolerated for "stable or improving" |
| SC6 enforcement | `git log --author` filter on soak branch | Only agent commits allowed during run |

## Plan Structure

| Plan | Wave | Title | Lead Agent | Secondary |
|------|------|-------|-----------|-----------|
| 05-01 | 1 | Autonomy Package + Watchdog | Senior Developer | Infrastructure Maintainer |
| 05-02 | 2 | Preflight + Acceptance + CLI + Tests | Senior Developer | QA Verification Specialist |
| 05-03 | 3 | Soak Run + Acceptance Report + Sign-off | QA Verification Specialist | Reality Checker |

## References

- Spec: `.planning/specs/05-full-autonomy-spec.md`
- ROADMAP: `.planning/ROADMAP.md` §Phase 5
- CODEBASE (stale, refreshed in 05-01 Task 1): `.planning/CODEBASE.md`
