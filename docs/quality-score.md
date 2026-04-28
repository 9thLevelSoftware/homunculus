# Quality Score

Current qualitative scorecard for the repository harness. Update this when a
change materially improves or regresses an area.

| Area | Grade | Evidence | Next Target |
| --- | --- | --- | --- |
| Agent map | B | Short `AGENTS.md` exists and points to current docs. | Keep under 120 lines and avoid duplicated manuals. |
| Architecture legibility | B | `architecture.md` maps lifecycle, modules, artifacts, and boundaries. | Add mechanical dependency-boundary checks if package edges grow. |
| Autonomous loop | B+ | Daemon, introspection, task generation, auto-commit, promotion, merge, and acceptance surfaces exist. | Exercise a full soak and archive acceptance evidence. |
| Mechanical enforcement | B | `harness-check`, unit tests, and CI workflow define baseline checks. | Add richer structural checks for import boundaries and artifact hygiene. |
| Operator experience | B- | Phase 5 scripts and CLI reports exist. | Reduce noisy watchdog warnings in test output and document recovery paths more tightly. |
| Documentation freshness | B- | Current docs are indexed and stale manual-gate language is checked. | Continue pruning duplicated historical guidance from `.planning/` and older runbooks. |

## Current Cleanup Queue

- Reduce noisy watchdog persistence warnings during unit tests without hiding real
  runtime failures.
- Add a remote-backed PR publication command only after a git remote and `gh`
  authentication are available.
- Promote recurring doc-gardening from a documented process into a generated
  task source if doc drift reappears.
- Add import-boundary checks if new packages create ambiguous dependencies.
