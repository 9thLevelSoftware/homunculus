# Windows Task Scheduler — Daily Soak Observation

This document gives a 2-minute click-through for scheduling the daily
`autonomy-report` during the Phase 5 soak window. Use this if direct
scripted access to `schtasks.exe` is unavailable or if the operator prefers
the GUI for auditability.

## Option A — GUI (Task Scheduler)

1. Open Task Scheduler (`taskschd.msc`).
2. Right-click `Task Scheduler Library` → `Create Basic Task…`.
3. Name: `homunculus-soak-daily-report`.
4. Trigger: `Daily`, start time **24 hours after daemon start**, recur every
   1 day.
5. Action: `Start a program`.
6. Program/script: `powershell.exe`.
7. Arguments (all one line — substitute `YYYYMMDD` + working dir):

   ```powershell
   -NoProfile -ExecutionPolicy Bypass -Command "$d = (Get-Date).ToString('yyyyMMdd'); $n = [int]((New-TimeSpan -Start (Get-Date '2026-04-16') -End (Get-Date)).TotalDays); python -m homunculus.cli autonomy-report --config homunculus.toml --json | Out-File -Encoding utf8 ('.planning/phases/05-full-autonomy/soak-log/day-{0:D2}.json' -f $n)"
   ```

8. Start in: `C:\Users\dasbl\Documents\homunculus`.
9. Finish. Right-click the task → Properties → General → check **Run whether
   user is logged on or not**, and **Run with highest privileges**.
10. Save. Enter Windows password when prompted.

### Verify

- Run the task manually (Right-click → Run). A `day-01.json` (or next
  number) should appear in `soak-log/`.
- Check History tab for success/failure.

## Option B — PowerShell `schtasks.exe` one-liner

Once you have permission to run `schtasks`, this creates the same task
without the GUI:

```powershell
$cmd = 'powershell -NoProfile -ExecutionPolicy Bypass -Command "$n = [int]((New-TimeSpan -Start (Get-Date ''2026-04-16'') -End (Get-Date)).TotalDays); python -m homunculus.cli autonomy-report --config homunculus.toml --json | Out-File -Encoding utf8 (''.planning/phases/05-full-autonomy/soak-log/day-{0:D2}.json'' -f $n)"'
schtasks /Create `
  /TN "homunculus-soak-daily-report" `
  /TR $cmd `
  /SC DAILY `
  /ST 03:00 `
  /RL HIGHEST `
  /F
```

## Teardown after soak completes

```powershell
schtasks /Delete /TN "homunculus-soak-daily-report" /F
```

## Markdown diff generator (companion task)

In addition to the JSON capture, schedule a second task (15 minutes after
the first) that runs a small Python helper to diff `day-NN.json` against
`day-(NN-1).json` and write `day-NN.md`. The helper is not yet implemented
as a CLI subcommand — for the first soak, the markdown summary may be
generated on-demand during Session 2 review. This is acceptable because
the `.json` files contain the full raw evidence; the markdown is only a
readability aid.
