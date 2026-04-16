# Plan 05-03 Summary — Session 1 (YIELD: BLOCKED at throughput pre-check)

## Status

**Blocked — soak NOT started. Session 2 CANNOT proceed until operator
resolves the throughput pre-check failure.**

This is a hard STOP per `SOAK-PROTOCOL.md` §2.2 (CRITICAL-1 data-starvation
gate). The QA Verification Specialist default verdict is NEEDS WORK — I am
refusing to start the 7-day soak because the projection shows 0.0 LoRA
merges over 7 days, which guarantees SC3 failure.

## Files Created

| Path | Purpose |
|------|---------|
| `.planning/phases/05-full-autonomy/SOAK-PROTOCOL.md` | Full runbook: branch naming, throughput pre-check, preflight, baseline capture, start-under-supervisor, daily cadence, abort conditions, hands-off rule, end-of-run, resume |
| `.planning/phases/05-full-autonomy/soak-log/SCHEDULING.md` | Windows Task Scheduler click-through (GUI + `schtasks.exe` one-liner) |
| `.planning/phases/05-full-autonomy/soak-log/day-00-baseline.json` | AutonomyReport + `throughput_precheck` + explicit `blocker` key |
| `.planning/phases/05-full-autonomy/soak-log/_throughput_precheck.json` | Raw pre-check computation (inputs + projection) |

**No production code was modified.** No soak branch was created. No daemon
was started. No commits were made.

## Verification (this session)

| Requirement | Evidence | Pass |
|------|----------|------|
| `SOAK-PROTOCOL.md` exists and covers all plan-listed sections | File at path above, 12 sections matching plan task 05-03-1 §§ 1–12 | Yes |
| `day-00-baseline.json` exists with valid JSON + `throughput_precheck` key | File exists, 2873 bytes, `json.loads` succeeds, key present | Yes |
| `day-00-process.json` with daemon PID | **Not applicable — daemon not started** | Intentionally omitted |
| Daemon alive post-start | **N/A — blocker at pre-check** | Intentionally omitted |
| Preflight exited 0 before daemon started | **Not run** — pre-check gate blocked progress before preflight | Correct ordering |

## Throughput Pre-Check Result

Computed per `SOAK-PROTOCOL.md` §2.1 using the 14-day rolling window over
`traces/episodes.jsonl`:

| Input | Value |
|-------|-------|
| `episodes.jsonl` exists | Yes |
| `episodes.jsonl` size | **0 bytes** |
| Episodes in 14-day window | **0** |
| Successful episodes in window | 0 |
| `episodes_per_day` | 0.0 |
| `success_rate` | 0.0 |
| `[evolution].auto_train_after_samples` | 50 |
| `[evolution].auto_merge_after_loras` | 5 |
| **Projected LoRAs merged over 7 days** | **0.0** |
| Gate threshold (minimum) | 1.0 |
| Gate threshold (safety margin) | 1.5 |
| Verdict | **BLOCKED** |

Corroboration from `autonomy-report --json`:

- `episodes_total = 0`
- `episodes_success = 0`
- `loras_trained = 0`
- `loras_merged = 0`
- `current_base_generation = 0`

The system has never run a full episode (daemon has completed 2 cycles but
produced no episodes — likely due to task-queue + teacher-auth state).
Starting a 7-day soak under these conditions would produce zero merges with
certainty and SC3 would fail 05-03-2 by definition.

## What Was NOT Done (Intentionally)

- **No soak branch created.** Would be premature.
- **No preflight run.** Gate ordering in `SOAK-PROTOCOL.md` §3 is sequenced
  AFTER the throughput gate §2. Running preflight now would misleadingly
  suggest the system was ready to start.
- **No daemon started.** §5 is gated on §2 pass + §3 pass.
- **No Task Scheduler entries created.** §6 is gated on daemon-started.
- **No commits.** Plan explicitly forbids commits in Session 1; orchestrator
  handles commit flow.

## Options for the Operator (Session 2 entry paths)

**Option A — Lower thresholds (recommended).** Edit `homunculus.toml`:

```toml
[evolution]
auto_train_after_samples = 10   # from 50
auto_merge_after_loras   = 2    # from 5
```

Re-run Session 1 from the top. The pre-check will still likely show 0
because `episodes_per_day=0`, so this alone is insufficient unless combined
with Option B.

**Option B — Bootstrap episode data.** Run 5–10 manual episodes before
starting Session 1 so `traces/episodes.jsonl` is populated. Then Option A
thresholds make the projection non-zero. Example:

```powershell
python -m homunculus.cli run-episode --config homunculus.toml --workspace self --task-id boot-01 --prompt "..."
# ...repeat 5–10 times
```

Then re-run Session 1 → pre-check → preflight → start.

**Option C — Explicit risk acceptance.** Operator can override §2.2 by
documenting the bypass in `STATE.md` under a `Phase 5 Soak Risk Acceptance`
heading. SC3 will then very likely fail in 05-03-2, meaning Phase 5 stays
open and a second soak is required after bootstrapping. Not recommended.

The orchestrator should pick A+B together (lower thresholds AND bootstrap
episodes) to maximize the chance of SC3 PASS on the first soak.

## Resume Command for Session 2

After operator resolves the blocker per Option A/B:

```powershell
# 1. Verify fix took effect
python -c "import json, subprocess, sys; p = subprocess.run([sys.executable,'-m','homunculus.cli','autonomy-report','--config','homunculus.toml','--json'], capture_output=True, text=True); r = json.loads(p.stdout); print('episodes_total =', r.get('episodes_total'))"

# 2. Re-invoke Session 1 of 05-03 (NOT Session 2)
/legion:build phase 5 plan 05-03 session 1
```

Session 2 (running `autonomy-accept` and sign-off) does NOT run yet — it
only runs after a successful 7-day soak, which has not started.

## Abort Conditions the Operator Should Watch For Once Soak Starts

These are the §7 conditions from `SOAK-PROTOCOL.md`. They are provided here
so the operator knows what to monitor once the soak eventually starts:

1. `watchdog_flags` contains `cycle_failure:3+` for ≥ 2 consecutive daily reports.
2. Disk usage > 90% on `C:\Users\dasbl\Documents\homunculus` volume.
3. Test-suite regression (yesterday green, today red in `autonomy-report`).
4. `git fsck` returns non-zero on soak branch.

If any trigger: stop daemon via PID, capture abort snapshot, file a
suggestion, re-plan. Do NOT silently restart.

## Decisions

- **Hard-stopped at §2.2 rather than starting with known SC3 failure.** The
  QA disposition defaults to NEEDS WORK; evidence shows 0.0 projection.
  Proceeding would be fantasy reporting — starting a 7-day clock that ends
  in a guaranteed acceptance FAIL wastes a week and an evidence artifact.
- **Preserved the protocol's exact measurement formula** — did not relax
  the ≥1.0 minimum or the ≥1.5 safety margin to rationalize starting.
- **Recorded the precheck under `throughput_precheck` key in baseline
  JSON** exactly as the plan requires, including the raw inputs and the
  decision matrix, so 05-03-2 (if ever invoked) has full audit trail.
- **Chose `_throughput_precheck.json` as a scratch file + inlined into the
  baseline** — the plan only requires the key inside `day-00-baseline.json`.
  The scratch file is convenient for re-use if Session 1 restarts with new
  thresholds, and can be deleted safely before commit.

## Root-Cause Analysis (for the blocker)

**Symptom**: `projected_loras_merged_7d = 0.0`.
**Propagation**: `episodes_per_day = 0 → successful_episodes_7d = 0 →
loras_trained_7d = 0 → loras_merged_7d = 0`.
**Root cause**: `traces/episodes.jsonl` is 0 bytes — the system has never
executed a successful episode. This is consistent with a fresh install or a
post-reset state.
**Fix (not patched by this session)**: The operator must either (a)
bootstrap `episodes.jsonl` with real episodes OR (b) accept that the first
7-day soak serves as a bootstrap and plan a second soak for the actual
SC3 verification. Either is a project-level decision outside the scope of
05-03-1.

## Regression Tests Generated

No code changed, so no regression tests are required for this session.
However, a reasonable follow-up test for `homunculus/autonomy/`:

- `test_throughput_precheck_blocks_when_no_episodes` — given an empty
  `episodes.jsonl`, the pre-check computation returns projection=0 and
  should be callable from the CLI (this would require adding an
  `autonomy-precheck` subcommand, currently absent — flag as Phase 5.1).

## Pre-existing Issues Encountered

- `traces/episodes.jsonl` is 0 bytes in the working repo — the system has
  never completed an episode. This is the real blocker.
- `git status` at session start already showed dirty state
  (`M .planning/STATE.md`, several untracked paths from prior plans).
  Creating `phase-5/soak-YYYYMMDD` from this dirty `master` would violate
  `git_clean` preflight gate. Another reason to hold Session 1 until the
  operator cleans up — though that's a downstream concern; the primary
  blocker is the throughput gate.
- Preflight subprocess timed out (>30s) during one scratch run because it
  internally runs the full `unittest discover` suite (~18s) + doctor +
  teacher reachability probe. Not a bug; flagged so Session 1-restart gives
  the preflight subprocess a 600s budget.

---

**Authored by**: QA Verification Specialist (VerifyQA)
**Session**: Session 1 of 2 (Session 2 blocked until operator resolves §2.2 gate)
**Date**: 2026-04-16
