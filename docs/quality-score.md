# Quality Score

Current qualitative scorecard for the repository harness. Update this when a
change materially improves or regresses an area.

| Area | Grade | Evidence | Next Target |
| --- | --- | --- | --- |
| Agent map | B | Short `AGENTS.md` exists and points to current docs. | Keep under 120 lines and avoid duplicated manuals. |
| Architecture legibility | B | `architecture.md` maps lifecycle, modules, artifacts, and boundaries. | Add mechanical dependency-boundary checks if package edges grow. |
| Autonomous loop | A- | Daemon plus Symphony Linear dispatch, persistent workspaces, branch gates, promotion, merge, and acceptance surfaces exist. | Exercise VM smoke, 24-hour soak, then 7-day acceptance. |
| Mechanical enforcement | B+ | `harness-check`, unit tests, CI workflow, and `WORKFLOW.md` parsing define baseline checks. | Add richer structural checks for import boundaries and artifact hygiene. |
| Operator experience | B | Phase 5 scripts, CLI reports, Symphony status, and VM runbook exist. | Prove local Codex app-server profile against the VM Ollama endpoint. |
| Documentation freshness | B- | Current docs are indexed and stale manual-gate language is checked. | Continue pruning duplicated historical guidance from `.planning/` and older runbooks. |

## Current Cleanup Queue

- Complete a VM Codex app-server smoke against the `homunculus-local` profile.
- Add a remote-backed PR publication command only after a git remote and `gh`
  authentication are available.
- Promote recurring doc-gardening from a documented process into a generated
  task source if doc drift reappears.
- Add import-boundary checks if new packages create ambiguous dependencies.
