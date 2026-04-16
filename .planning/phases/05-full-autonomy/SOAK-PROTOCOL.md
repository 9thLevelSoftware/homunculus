# SOAK-PROTOCOL.md — Phase 5 Hands-Off Soak Runbook

**Owner**: QA Verification Specialist
**Plan**: `.planning/phases/05-full-autonomy/05-03-PLAN.md`
**Spec**: `.planning/specs/05-full-autonomy-spec.md` (§6)
**Target duration**: ≥7 full calendar days, wall-clock, unattended

---

## 0. Purpose

This document is the authoritative runbook for the Phase 5 hands-off soak run.
Its job is to prove — with evidence, not assertions — that the homunculus
daemon can operate unattended for one week while satisfying all six success
criteria (SC1–SC6) in `.planning/specs/05-full-autonomy-spec.md` §2.

A **soak run** starts after preflight passes and ends after `autonomy-accept`
emits `05-ACCEPTANCE.md` with `overall=PASS`. During the window, humans do
nothing except observe.

---

## 1. Branch Naming

- Create the soak branch **from `master`** (Git convention for this repo).
- Name: `phase-5/soak-YYYYMMDD` where `YYYYMMDD` is the UTC calendar date on
  which the soak starts.
- Example: `phase-5/soak-20260416`.
- The branch is **preserved after sign-off** — do NOT delete. It is the
  audit artifact.

```powershell
git checkout master
git pull --ff-only
git checkout -b phase-5/soak-20260416
```

---

## 2. Throughput Pre-Check (CRITICAL-1 mitigation — SC3 data starvation)

Before starting the daemon, compute whether the soak window is long enough to
plausibly produce **≥1 LoRA merged** (required by SC3).

### 2.1 Algorithm

1. Read `traces/episodes.jsonl`. Filter to the last 14 calendar days by
   `finished_at` (fall back to `started_at` → `timestamp`).
2. If the file is empty or the window is empty:
   - Record `note = "no historical data; projection is nominal from config thresholds"`.
   - Set `episodes_per_day = 0`, `success_rate = 0`.
3. Otherwise:
   - `episodes_per_day = episodes_in_14d_window / 14`
   - `success_rate = successful_in_14d_window / episodes_in_14d_window`
   - (A successful episode is `outcome == "accepted"`.)
4. Read thresholds from `homunculus.toml`:
   - `[evolution].auto_train_after_samples` (treat as `min_samples_for_train`)
   - `[evolution].auto_merge_after_loras` (treat as `min_loras_for_merge`)
5. Compute 7-day projection:

   ```
   projected_successful_episodes_7d = episodes_per_day * 7 * success_rate
   projected_loras_trained_7d       = projected_successful_episodes_7d / min_samples_for_train
   projected_loras_merged_7d        = projected_loras_trained_7d        / min_loras_for_merge
   ```

### 2.2 Gate

- **If `projected_loras_merged_7d >= 1.5`**: PROCEED (safety margin met).
- **If `1.0 <= projected_loras_merged_7d < 1.5`**: PROCEED with caveat logged
  in the summary; record that the soak may need extension.
- **If `projected_loras_merged_7d < 1.0`**: **STOP**. The soak cannot
  plausibly produce a merged LoRA inside 7 days. Operator choice:
  - Option A (prefer): lower `auto_train_after_samples` and/or
    `auto_merge_after_loras` in `homunculus.toml`, commit the change,
    re-run pre-check until ≥1.5.
  - Option B: extend soak duration (e.g., 10 or 14 days) until projection
    reaches ≥1.5 with current thresholds.
  - Option C: seed `traces/episodes.jsonl` with a prior run's artifacts
    (ONLY if consistent with hands-off spirit — prefer A/B).

### 2.3 Evidence

Record the full pre-check result (inputs, projection, gate decision) under a
`throughput_precheck` key in `soak-log/day-00-baseline.json`. This is
mandatory for audit.

---

## 3. Preflight (must exit 0)

```powershell
python -m homunculus.cli autonomy-preflight --config homunculus.toml --json > .planning/phases/05-full-autonomy/soak-log/day-00-preflight.json
```

The seven gates (see `homunculus/autonomy/preflight.py`) are:

| Gate | Failure means |
|------|---------------|
| `config_parses` | `homunculus.toml` malformed |
| `doctor_passes` | `python -m homunculus.cli doctor` non-zero |
| `worktrees_clean` | stale `runtime/worktrees/` entries |
| `test_suite_passes` | `unittest discover` non-zero |
| `task_queue_ready` | task queue file missing/corrupt |
| `teacher_reachable` | `OPENAI_API_KEY` unset or endpoint unreachable |
| `git_clean` | uncommitted changes in workspace |

- **Exit 0 required.** If exit 1, read the gate table, fix the failing gate,
  and re-run. Do NOT start the daemon with a failing preflight.

---

## 4. Baseline Capture

```powershell
python -m homunculus.cli autonomy-report --config homunculus.toml --json > .planning/phases/05-full-autonomy/soak-log/day-00-baseline.json
```

Then inject the throughput pre-check into the same file under key
`throughput_precheck`. The resulting JSON must be valid and contain both:

- `AutonomyReport` fields (generated_at, uptime, cycles_completed, …)
- `throughput_precheck` (inputs + projection + gate decision)

---

## 5. Start Command + Process Supervisor

The daemon **MUST** survive terminal close. Three supported patterns:

### 5.1 Windows — `Start-Process` (this repo's native host)

```powershell
$logOut = "C:/Users/dasbl/Documents/homunculus/runtime/daemon.stdout.log"
$logErr = "C:/Users/dasbl/Documents/homunculus/runtime/daemon.stderr.log"
$proc = Start-Process `
    -FilePath "python" `
    -ArgumentList "-m","homunculus.daemon","--config","homunculus.toml" `
    -WorkingDirectory "C:/Users/dasbl/Documents/homunculus" `
    -RedirectStandardOutput $logOut `
    -RedirectStandardError  $logErr `
    -WindowStyle Hidden `
    -PassThru
$proc.Id   # capture PID
```

Record the PID, start timestamp, command, and supervisor_type in
`soak-log/day-00-process.json`.

### 5.2 Windows — Task Scheduler (most resilient)

See `soak-log/SCHEDULING.md` for the click-through recipe.

### 5.3 POSIX

```bash
nohup python -m homunculus.daemon --config homunculus.toml \
  > runtime/daemon.stdout.log 2> runtime/daemon.stderr.log &
echo $! > runtime/daemon.pid
```

Or `pm2 start "python -m homunculus.daemon --config homunculus.toml"` /
`systemd --user` with a unit file.

### 5.4 Post-start sanity

Within 60 seconds:

```powershell
Get-Process -Id <pid>   # must return a live process
Get-Content runtime\daemon.stdout.log -Tail 20
```

If the daemon dies immediately, do NOT mark the soak started — diagnose and
retry.

---

## 6. Daily Observation Cadence

**One cadence per 24h**, scheduled automatically (not a human running a command).

### 6.1 Per-day command

```powershell
# Where NN is a 2-digit day number: 01, 02, ..., 07+
python -m homunculus.cli autonomy-report --config homunculus.toml --json `
  > .planning/phases/05-full-autonomy/soak-log/day-NN.json
```

### 6.2 Per-day markdown summary

For each `day-NN.json`, also write `day-NN.md` with:

- Timestamp captured
- Delta vs. `day-(NN-1).json` for every AutonomyReport field:
  - `uptime` delta (hours)
  - `cycles_completed` delta
  - `episodes_total/success/failed` delta
  - `self_directed_tasks_completed` delta
  - `loras_trained/loras_merged` delta
  - `current_base_generation` delta
  - `patch_success_rate` current + `patch_success_rate_trend`
  - `coverage_percent/coverage_trend` (may be None)
  - New entries in `watchdog_flags`
- Any abort-condition flags (see §7)
- One-line verdict: `OK`, `CAUTION`, or `ABORT_RECOMMENDED`

The markdown may be generated by a small script — prefer automation over
human narration.

### 6.3 Scheduling

Use Windows Task Scheduler with a daily trigger (see `soak-log/SCHEDULING.md`).
Do NOT rely on a human running the command — the spirit of the phase is
hands-off.

---

## 7. Abort Conditions (4 hard rules — human decision only)

The watchdog **never stops the daemon**. Humans decide to abort. Abort if:

1. **Watchdog flag `cycle_failure:3+`** persists for ≥ 2 consecutive daily
   reports. (Indicates the cycle loop is wedged.)
2. **Disk usage >90%** on the artifact volume (`traces/`, `runtime/`,
   `models/`, `datasets/`). Check with `Get-PSDrive C` daily.
3. **Test suite regression** between any two days — i.e. yesterday's
   `autonomy-report` showed tests passing, today's shows them failing.
4. **Git corruption**: `git fsck` returns non-zero on the soak branch. Check
   manually if any other signal looks off.

### Abort procedure

```powershell
Stop-Process -Id <pid> -Force
# Capture state
python -m homunculus.cli autonomy-report --config homunculus.toml --json > soak-log/abort-snapshot.json
```

Then:

- File a suggestion under `suggestions/` describing the abort reason.
- Generate an introspection task for the failure class.
- Re-plan: decide whether to restart with mitigations, extend the soak, or
  escalate. **Do NOT silently restart.**

---

## 8. Hands-Off Rule (ZERO human intervention)

During the soak window, the following are **FORBIDDEN**:

- ZERO code edits to `homunculus/` or `tests/`.
- ZERO configuration edits (`homunculus.toml`, env vars).
- ZERO manual commits on the soak branch.
- ZERO manual worktree manipulation.
- ZERO manual model registry edits.

The ONLY human-initiated actions permitted:

- Running `autonomy-report` (read-only).
- Running `git log` / `git fsck` (read-only).
- Reading log files.
- Executing a **single abort** if §7 conditions trigger (ends the soak).

Any violation invalidates the soak (SC6 fails).

---

## 9. End-of-Run Criteria

The soak completes when **all** of:

- `day-07.json` exists with `uptime >= 168h`, OR later day files if the soak
  was extended per §2.2.
- `day-00-baseline.json` and the final day's JSON are both present.
- Daemon is still running at the final report, OR it was explicitly aborted
  per §7 (in which case the soak FAILS, not COMPLETES).

At this point, stop collecting daily reports and proceed to 05-03-2.

---

## 10. Resume Instructions (Session 2 — 05-03-2)

When a new legion session resumes to run 05-03-2:

1. Verify the soak branch is still checked out:

   ```powershell
   git branch --show-current   # expect phase-5/soak-YYYYMMDD
   ```

2. Stop the daemon (soak is over, but acceptance runs require a quiescent
   state):

   ```powershell
   Stop-Process -Id <pid> -Force
   ```

3. Run acceptance:

   ```powershell
   python -m homunculus.cli autonomy-accept `
       --config homunculus.toml `
       --soak-log .planning/phases/05-full-autonomy/soak-log `
       --soak-branch phase-5/soak-YYYYMMDD `
       --output .planning/phases/05-full-autonomy/05-ACCEPTANCE.md
   ```

4. Inspect `05-ACCEPTANCE.md`. Check all 6 rows. If `overall == PASS`:
   proceed to 05-03-3 (ROADMAP + STATE sign-off). If `overall == FAIL`:
   HALT — file a failure report and re-plan.

5. **Do NOT delete the soak branch.**

---

## 11. Artifact Map

```
.planning/phases/05-full-autonomy/
├── SOAK-PROTOCOL.md           (this file)
├── 05-03-PLAN.md
├── 05-03-SUMMARY.md           (Session 1 yield artifact)
├── 05-ACCEPTANCE.md           (emitted by 05-03-2)
└── soak-log/
    ├── SCHEDULING.md          (Task Scheduler click-through)
    ├── day-00-baseline.json   (AutonomyReport + throughput_precheck)
    ├── day-00-preflight.json  (preflight JSON)
    ├── day-00-process.json    (PID, start timestamp, supervisor)
    ├── day-01.json / day-01.md
    ├── day-02.json / day-02.md
    ├── …
    └── day-NN.json / day-NN.md
```

---

## 12. Non-Negotiables

- Default verdict = FAIL. Only overwhelming evidence flips it.
- Every criterion needs evidence in `05-ACCEPTANCE.md`.
- If any step in this protocol is skipped, the soak is invalid.
- Three-strike rule: if the daemon dies three times during start, STOP and
  investigate root cause. Do not iterate on restarts.
