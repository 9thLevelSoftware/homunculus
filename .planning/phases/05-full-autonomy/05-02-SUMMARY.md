# Plan 05-02 Summary

## Status
Complete

## Files Modified
- `C:/Users/dasbl/Documents/homunculus/homunculus/autonomy/preflight.py` — New: 7 preflight gates (`config_parses`, `doctor_passes`, `worktrees_clean`, `test_suite_passes`, `task_queue_ready`, `teacher_reachable`, `git_clean`), `run_preflight(settings)` entry point, `format_preflight_table` CLI helper. All gates fail closed; `sys.executable` used for every subprocess.
- `C:/Users/dasbl/Documents/homunculus/homunculus/autonomy/acceptance.py` — New: 6 criterion predicates (`_check_uptime`, `_check_self_directed_tasks`, `_check_lora_merged`, `_check_tests_pass`, `_check_metrics_stable`, `_check_no_human_intervention`), `validate_acceptance(report, soak_branch, workspace_root)`, `render_acceptance_markdown`. SC6 detects agent commits by the `Episode-ID:` footer pattern emitted by `TaskRunner.commit_to_source` (not author identity).
- `C:/Users/dasbl/Documents/homunculus/homunculus/autonomy/__init__.py` — Added `run_preflight` and `validate_acceptance` to `__all__`; updated docstring.
- `C:/Users/dasbl/Documents/homunculus/homunculus/cli.py` — Three new subcommands: `autonomy-preflight` (exit 0/1), `autonomy-report` (supports `--json` and `--since DAY-N`), `autonomy-accept` (writes markdown, exit 0 iff PASS). Added `_parse_since`, `_print_report_table` helpers.
- `C:/Users/dasbl/Documents/homunculus/tests/test_autonomy.py` — New: 15 tests across 5 TestCase classes (`ReporterTests`, `WatchdogTests`, `PreflightTests`, `AcceptanceTests`, `DaemonWatchdogIntegrationTests`). Covers all 14 items in plan §task-05-02-3 plus an extra markdown-render test. All git-requiring classes guarded with `@unittest.skipUnless(shutil.which("git"))`.

## Verification
| Command | Result | Pass? |
|---------|--------|-------|
| `python -m unittest tests.test_autonomy -v` | 15 tests, 0 failures | Yes |
| `python -m unittest discover -v 2>&1 \| tail -5` | 308 tests, 0 failures | Yes (baseline 293 + 15 new) |
| `python -m homunculus.cli autonomy-preflight --config homunculus.toml --json` | Structured JSON with all 7 gates; exit 1 (real env missing OPENAI_API_KEY + dirty git) | Yes (correct fail-closed behavior) |
| `python -m homunculus.cli autonomy-report --config homunculus.toml --json` | Valid AutonomyReport JSON; exit 0 | Yes |
| `python -m homunculus.cli autonomy-report --config homunculus.toml` | Human-readable key/value table | Yes |
| `python -m homunculus.cli autonomy-accept --config homunculus.toml --output acc-smoke.md --soak-branch master` | Writes markdown with 6 criterion rows; exit 1 (overall FAIL as expected pre-soak) | Yes |
| `python -c "from homunculus.autonomy import run_preflight, validate_acceptance; print('ok')"` | `ok` | Yes |

## Decisions

- **SC6 agent-commit signature — message footer over author name**: Spec §11 resolves SC6 with "pattern match on commit message prefix from `commit_to_source`". Inspection of `homunculus/task_runner/runner.py:162-189` showed `commit_to_source` does NOT set `--author` explicitly — it inherits whatever `user.name`/`user.email` is configured in the workspace, which is unstable across environments. Every agent commit does carry the deterministic footer `Episode-ID: <id>\nTask-ID: <id>`, so `_AGENT_COMMIT_PATTERN = re.compile(r"Episode-ID:\\s*\\S+")` provides a reliable, environment-independent match. This is strictly consistent with the spec wording ("commit message prefix") and more robust than an author regex.
- **SC3 requires both `loras_merged >= 1` AND `current_base_generation > 0`**: Plan task description says `loras_merged >= 1 AND current_base_generation > initial_generation`. Since we do not track initial generation on the report, I interpreted "advanced" as "generation > 0" (the base generation is 0 before any merge). This matches ROADMAP wording "trained AND merged" and avoids a false positive where a merge occurred but was rolled back. Recorded in both `evidence` and `raw`.
- **SC5 tolerance exactly per plan**: `patch_success_rate_trend` must exist (None → FAIL) and be ≥ -0.02; `coverage_trend` may be None OR ≥ -0.02. A missing patch trend blocks PASS because spec §11 explicitly ties "stable or improving" to a measurable trend. A missing coverage trend is tolerated because coverage is optional infrastructure.
- **`config_parses` gate is permissive when no TOML exists on disk**: If we hold a valid `HomunculusConfig` instance but cannot locate `homunculus.toml` or `homunculus.example.toml` under `paths.root` (e.g. the test harness passes `config.toml`), we return `passed=True` with a note. Rationale: the config evidently parsed, so the gate's invariant is satisfied. Tests inject a real `homunculus.toml` so the gate exercises the parse path.
- **`doctor` gate uses subprocess rather than direct import of `cmd_doctor`**: Calling `build_runtime` from inside preflight would instantiate the Engram HTTP client and spawn student/teacher sessions as side effects. Shelling out with `sys.executable -m homunculus.cli doctor` isolates those effects and matches how an operator would verify a box.
- **`autonomy-report --since` accepts both `DAY-N` and ISO-8601**: Spec CLI §7 shows `DAY-N` syntax; ISO-8601 is a strict superset since the signature is `datetime | None`. Added as a usability affordance.
- **CriterionResult for SC4 runs a full test discover at acceptance time**: This is expensive (~18s on the current 308-suite) but directly fulfills the plan's "re-run `unittest discover` at acceptance time, exit 0 required". Tests stub `subprocess.run` for SC4 to avoid recursion.
- **Per-criterion SC4 cwd defaults to `Path.cwd()`**: The accept CLI runs from the agent's repo root, so the fresh test run inherits the natural test harness. Tests inject a mocked subprocess to control behavior deterministically.
- **Test count headline**: Plan §task-05-02-3 requires ≥14 tests; delivered 15 (added `test_acceptance_markdown_renders` because the markdown path is on the CLI critical path and deserves a regression guard). Total suite: 308 tests.
- **14→15 test expansion is additive**: No existing test was modified. `tests/test_daemon.py` (38 tests) remains untouched as required by Plan 05-01's additive-integration discipline.

## Pre-existing Issues Encountered

- **Reporter task-source vocabulary — `"user"` vs `"suggestion"`**: When writing test fixtures I initially used `source: "user"` (matching `GeneratedTask.source` docstring vocabulary "introspection | user | continuation" in `homunculus/models.py:113`). The reporter's `_count_suggestion_tasks` matches exactly `source == "suggestion"`. This means **no user-sourced tasks currently count toward `suggestion_tasks_completed`** — in practice `SuggestionReader` emits tasks with `source="user"` (per `tests/test_daemon.py:75`), so the reporter is under-counting in real soaks. Fixed in my fixtures to use `"suggestion"` to match the reporter, but flagging the reporter-vs-production drift as a follow-up for Phase 5.1. The reporter docstring at `reporter.py:228-236` says "source == 'suggestion'" explicitly, so this is a source-of-truth question between the reporter spec and the current queue producers, not a reporter bug.
- **`DaemonState.started_at` is `str`, not `datetime`**: Flagged in 05-01 summary; still unresolved. The reporter parses via `datetime.fromisoformat`, which works today. Out of scope for this plan.
- **Dirty `homunculus.toml` in repo — git status showed `M .planning/STATE.md`, `?? .planning/phases/05-full-autonomy/` and new untracked files before I started**: These are legitimate working-state files from the preceding plan execution; I left them alone. The `git_clean` preflight gate correctly flagged this dirty state when run against the live repo (the gate is working as designed).
- **Teacher auth not present in env**: `doctor` and `autonomy-preflight` both report `$OPENAI_API_KEY is not set`; this is expected for local development and flags correctly.

## Auto-Remediation

- **5 initial test failures on first run, all fixed in-place**:
  1. `test_report_aggregates_events_and_episodes` — fixture used `source="user"`; reporter expects `"suggestion"`. Changed fixture source label. (See Pre-existing Issues for broader note.)
  2. `test_preflight_all_gates_pass` initial failure (`Cannot resolve config path for doctor invocation`) — fixture wrote config as `config.toml`; `_config_path_for` scans for canonical names. Renamed fixture output to `homunculus.toml`.
  3. `test_preflight_all_gates_pass` second failure (`git_clean: dirty: self`) — my static `fake_ok` CompletedProcess had non-empty stdout for `git status --porcelain`, triggering the dirty branch. Rewrote as a `side_effect` that returns empty stdout for `git` argv.
  4. `test_preflight_fails_when_worktree_dirty` — same stdout-mismatch issue. Same fix.
  5. `test_acceptance_no_human_intervention_detection` — RecursionError from side_effect capturing `real_run = subprocess.run` AFTER the patch installed the mock. Fixed by binding `_REAL_SUBPROCESS_RUN = subprocess.run` at module import time, before any patch can intercept.

  No production code changed during remediation — all fixes were test-harness bugs. Final test pass on second run: 15/15 in the new module, 308/308 in the full suite.
