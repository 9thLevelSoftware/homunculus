# Phase 5 Soak Kickoff — Operator Runbook

This suite brings a freshly-cloned homunculus repo to the point where a ≥7-day
autonomous soak can start on a new Windows PC with an RTX 5070 (12 GB) or
similar CUDA GPU. Scripts are PowerShell 7+ and idempotent.

Local-only by design — teacher runs under Ollama on localhost:11434. No cloud
calls. No OpenAI API key required (a placeholder env var is set so the existing
config contract is satisfied).

## Prerequisites

1. Windows 10/11 with PowerShell 7+ (`pwsh.exe` must be on PATH — `Get-Command pwsh.exe` should resolve; if missing, install from https://aka.ms/install-powershell and ensure the installer's PATH entry is checked)
2. Python 3.11+ on PATH
3. Git on PATH
4. Clone this repo and install: `python -m venv .venv; .\.venv\Scripts\Activate.ps1; python -m pip install -e .`
5. Run tests to confirm baseline: `python -m unittest discover -q` → expect `Ran 308+ tests ... OK`
6. ~15 GB free disk (9 GB model + traces/models growth over 7 days)

**After `setup.ps1`**: open a **new** PowerShell window before running `bootstrap.ps1` or `start-soak.ps1`. `setup.ps1` writes `OPENAI_API_KEY` at User scope via `[Environment]::SetEnvironmentVariable`; existing shells do not inherit the change until reopened.

## Execution Order

From repo root:

```powershell
# Step 1 — install Ollama, pull teacher model, start serve, set env
.\scripts\phase5\setup.ps1

# Step 2 — bootstrap 10 seed episodes so throughput pre-check passes
.\scripts\phase5\bootstrap.ps1

# Step 3 — run throughput gate standalone (optional sanity check)
.\scripts\phase5\precheck.ps1

# Step 4 — start the 7-day soak (branch + preflight + daemon detached + daily schedule)
.\scripts\phase5\start-soak.ps1

# During soak: daily observe runs automatically via Task Scheduler.
# Ad-hoc status check:
python -m homunculus.cli autonomy-report --config homunculus.toml --json

# Step 5 — after ≥7 days wall-clock, stop daemon + run acceptance
.\scripts\phase5\stop-soak.ps1
python -m homunculus.cli autonomy-accept `
    --config homunculus.toml `
    --soak-log .planning\phases\05-full-autonomy\soak-log `
    --soak-branch phase-5/soak-YYYYMMDD `
    --output .planning\phases\05-full-autonomy\05-ACCEPTANCE.md
```

## What Each Script Does

| Script | Purpose | Idempotent? | Typical Run Time |
|--------|---------|-------------|------------------|
| `setup.ps1` | Verifies / starts Ollama, pulls teacher model, sets `OPENAI_API_KEY`, validates teacher reachability | Yes | 1-20 min (first pull ~9 GB) |
| `bootstrap.ps1` | Runs seed tasks from `seed-tasks.json` via `homunculus.cli run-episode` on a throwaway branch | No — creates commits | 30-90 min (10 episodes × ~3-9 min each) |
| `precheck.ps1` | Recomputes SOAK-PROTOCOL §2.2 throughput gate; exits 0 if gate clears | Yes | <1 s |
| `start-soak.ps1` | Creates `phase-5/soak-YYYYMMDD` branch, runs preflight, captures baseline, starts daemon detached (60s stability window), registers Windows scheduled tasks for daily-observe + Ollama watchdog. Idempotency-checks an already-running daemon. Accepts `-SkipPrecheck` / `-SkipPreflight` (audit-trailed). | No — side effects | 2-4 min |
| `daily-observe.ps1` | Single-shot: dumps `autonomy-report --json` to next `soak-log/day-NN.json` and writes markdown diff. Asserts daemon liveness and disk-pressure threshold. | Yes | <10 s |
| `ollama-watchdog.ps1` | Probes `http://127.0.0.1:11434/api/tags`; restarts `ollama serve` if down. Runs every 5 min via Task Scheduler. | Yes | <15 s |
| `stop-soak.ps1` | Drops `runtime/STOP`, waits up to 120s for daemon to exit gracefully, falls back to Force-kill, unregisters BOTH scheduled tasks | Yes | <2 min |

## Gate Bypass (operator override)

`start-soak.ps1` accepts two explicit override flags:

- `-SkipPrecheck`  - skip the SOAK-PROTOCOL throughput precheck
- `-SkipPreflight` - skip `autonomy-preflight`

Skipping gates is a deliberate override; the script writes
`soak-log/gates-bypassed.json` with timestamp + reason. The acceptance report
may cite this bypass in the SC evaluation. There is no generic `-Force` flag.

## Hardware Notes

- RTX 5070 12 GB: `qwen2.5-coder:14b-instruct-q4_K_M` (~9 GB) runs at ~15-25 tok/s.
  Episode patch generation is typically 500-1500 tok → 30-90 s per teacher call.
- Fallback if 14B is unstable: pull `qwen2.5-coder:7b-instruct-q8_0` (~7.5 GB)
  and update `homunculus.toml` `[teacher].model`.

## Safety

- `auto_commit_on_accept = true` in config. Bootstrap runs on throwaway branch
  `phase-5/bootstrap-YYYYMMDD` so seed commits don't pollute `master`. After
  bootstrap, branch can be merged, deleted, or kept as evidence.
- Soak runs on `phase-5/soak-YYYYMMDD`. Per SOAK-PROTOCOL §7, ZERO manual edits
  during the soak window. Abort conditions in `SOAK-PROTOCOL.md §7`.

## Troubleshooting

| Symptom | Likely Cause | Fix |
|---------|--------------|-----|
| `ollama: command not found` | Ollama not installed | `winget install ollama.ollama` OR https://ollama.com/download/windows |
| `setup.ps1` hangs on pull | Slow mirror | Ctrl+C, re-run; `ollama pull` resumes |
| Preflight `teacher_reachable` fails | `ollama serve` not running | `scripts\phase5\setup.ps1` (re-starts serve) |
| `projected_loras_merged_7d < 1.0` after bootstrap | Too few successful episodes | Re-run `bootstrap.ps1` with more tasks, OR further lower `[evolution]` thresholds in `homunculus.toml` |
| Daemon crashed mid-soak | Check `runtime\worktrees\` for stale dirs, inspect `traces\events.jsonl` tail | Fix root cause, do NOT restart blindly — soak abort conditions in SOAK-PROTOCOL §7 apply |
| `daemon.stdout.log` / `daemon.stderr.log` growing toward 1 GB+ | No automatic log rotation in Phase 5 | Mid-soak, manually truncate (NOT delete the open handle): `Clear-Content .planning\phases\05-full-autonomy\soak-log\daemon.stdout.log -Force`. If you must delete, daemon will keep writing to the deleted handle until restart — prefer Clear-Content. |
| `daily-observe.md` shows `**DISK_PRESSURE**` header | Drive free space below 15% | Free space immediately. Abort condition #2 trips at <10%. Candidates: prune old `models/adapters/` dirs not in `registry.json` active pointer, prune old `runtime/worktrees/` (already cleaned per-episode but inspect), rotate logs as above. |
| `daily-observe.md` shows `**ABORT_RECOMMENDED**` header | Daemon process not running at observation time | Inspect `daemon.stderr.log`, decide per SOAK-PROTOCOL §7 whether to restart (resets soak clock) or accept. |

## References

- Protocol: `.planning\phases\05-full-autonomy\SOAK-PROTOCOL.md`
- Spec: `.planning\specs\05-full-autonomy-spec.md`
- Plan: `.planning\phases\05-full-autonomy\05-03-PLAN.md`
- Previous summary: `.planning\phases\05-full-autonomy\05-03-SUMMARY.md`
