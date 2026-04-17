#Requires -Version 7.0
<#
.SYNOPSIS
    Start the >=7-day Phase 5 autonomy soak.

.DESCRIPTION
    Runs throughput precheck, creates the soak branch, runs full preflight,
    captures baseline, starts the daemon detached under a process supervisor,
    registers a daily Windows scheduled task for autonomy-report snapshots, and
    a 5-minute Ollama watchdog scheduled task.

    Script returns after scheduling. Soak runs for >=7 days wall-clock.
    When done, call scripts\phase5\stop-soak.ps1 and then autonomy-accept.

.PARAMETER SkipPrecheck
    Skip the SOAK-PROTOCOL throughput precheck gate. Audit trail written to
    soak-log/gates-bypassed.json. Use ONLY for re-runs after a prior precheck pass.

.PARAMETER SkipPreflight
    Skip autonomy-preflight gate. Same audit trail. Use ONLY when you have
    independently verified env/config readiness.
#>
param(
    [string]$Config        = "homunculus.toml",
    [string]$LogDir        = ".planning\phases\05-full-autonomy\soak-log",
    [switch]$SkipPrecheck,
    [switch]$SkipPreflight
)

$ErrorActionPreference = "Stop"
$RepoDir = (Resolve-Path .).Path

# Env var
if (-not $env:OPENAI_API_KEY) {
    $u = [Environment]::GetEnvironmentVariable("OPENAI_API_KEY", "User")
    if ($u) { $env:OPENAI_API_KEY = $u }
    else { Write-Error "OPENAI_API_KEY not set. Run scripts\phase5\setup.ps1 first."; exit 1 }
}

# Audit trail for any bypassed gates (WARNING-6)
if (-not (Test-Path $LogDir)) { New-Item -ItemType Directory -Path $LogDir -Force | Out-Null }
$bypassed = @()
if ($SkipPrecheck)  { $bypassed += "precheck" }
if ($SkipPreflight) { $bypassed += "preflight" }
if ($bypassed.Count -gt 0) {
    Write-Warning "==============================================================="
    Write-Warning " GATE BYPASS ACTIVE: $($bypassed -join ', ')"
    Write-Warning " This is a deliberate operator override. Acceptance report may"
    Write-Warning " cite this in the SC evaluation. See README.md."
    Write-Warning "==============================================================="
    $bypassFile = Join-Path $LogDir "gates-bypassed.json"
    $bypassPayload = @{
        bypassed   = $bypassed
        timestamp  = (Get-Date -Format o)
        reason     = "operator -Skip$($bypassed -join ' / -Skip')"
        invoked_by = "$env:USERDOMAIN\$env:USERNAME"
    }
    $bypassPayload | ConvertTo-Json | Set-Content -Path $bypassFile -Encoding UTF8
    Write-Warning "Bypass audit trail written to $bypassFile"
}

# Step 1 - throughput precheck
if (-not $SkipPrecheck) {
    Write-Host "=== Step 1: throughput precheck ===" -ForegroundColor Cyan
    & "$PSScriptRoot\precheck.ps1" -Config $Config
    if ($LASTEXITCODE -ne 0) {
        Write-Error "Throughput precheck failed (exit $LASTEXITCODE). Run bootstrap.ps1 with more tasks, or lower [evolution] thresholds."
        exit 1
    }
    Write-Host "[OK] Throughput gate cleared." -ForegroundColor Green
} else {
    Write-Warning "Skipping precheck (-SkipPrecheck)"
}

# Step 2 - clean working tree
$dirty = & git status --porcelain 2>$null
if ($dirty -and -not $SkipPreflight) {
    Write-Error "Working tree not clean; commit or stash before soak start. Status:`n$dirty"
    exit 1
}

# Step 3 - create / checkout soak branch (idempotent: SUGGESTION-19 #4)
$stamp = Get-Date -Format "yyyyMMdd"
$soakBranch = "phase-5/soak-$stamp"
$existing = & git branch --list $soakBranch 2>$null
if ($existing) {
    Write-Host "[INFO] Branch $soakBranch already exists; checking it out" -ForegroundColor Yellow
    & git checkout $soakBranch
} else {
    Write-Host "[INFO] Creating $soakBranch from master" -ForegroundColor Cyan
    & git checkout -b $soakBranch master
}
if ($LASTEXITCODE -ne 0) { Write-Error "git checkout failed"; exit 1 }

# Step 4 - preflight
if (-not $SkipPreflight) {
    Write-Host "=== Step 4: preflight ===" -ForegroundColor Cyan
    & python -m homunculus.cli autonomy-preflight --config $Config --json
    if ($LASTEXITCODE -ne 0) {
        Write-Error "Preflight failed. Inspect gates above; resolve; re-run."
        exit 1
    }
    Write-Host "[OK] Preflight cleared." -ForegroundColor Green
} else {
    Write-Warning "Skipping preflight (-SkipPreflight)"
}

# Step 5 - baseline capture (overwrite-safe; keep throughput_precheck key intact)
$baseline = Join-Path $LogDir "day-00-baseline.json"
Write-Host "=== Step 5: baseline capture -> $baseline ===" -ForegroundColor Cyan
& python -m homunculus.cli autonomy-report --config $Config --json | Out-File -FilePath $baseline -Encoding UTF8
if ($LASTEXITCODE -ne 0) { Write-Error "baseline capture failed"; exit 1 }

# Step 6 - start daemon detached (SUGGESTION-19: idempotency check)
$processFile = Join-Path $LogDir "day-00-process.json"
if (Test-Path $processFile) {
    $existingProc = Get-Content -Raw -Path $processFile | ConvertFrom-Json
    if ($existingProc.pid -and (Get-Process -Id $existingProc.pid -ErrorAction SilentlyContinue)) {
        Write-Error "Soak already running (PID $($existingProc.pid)). Run stop-soak.ps1 first."
        exit 1
    }
}

Write-Host "=== Step 6: start daemon detached ===" -ForegroundColor Cyan
$logOut = Join-Path $LogDir "daemon.stdout.log"
$logErr = Join-Path $LogDir "daemon.stderr.log"
$python = (Get-Command python).Source
# Note: -WindowStyle Hidden omitted: when RedirectStandardOutput is set,
# Start-Process already runs without a window. Specifying both has caused
# native-launcher quirks on PS7; keep arg list lean.
$daemon = Start-Process -FilePath $python `
                         -ArgumentList @("-m","homunculus.daemon","--config",$Config) `
                         -WorkingDirectory $RepoDir `
                         -RedirectStandardOutput $logOut `
                         -RedirectStandardError $logErr `
                         -PassThru

# WARNING-7: 60s stability window instead of single 3s sleep
$stableUntil = (Get-Date).AddSeconds(60)
while ((Get-Date) -lt $stableUntil) {
    if ($daemon.HasExited) {
        Write-Error "Daemon exited at $(Get-Date -Format o) (code $($daemon.ExitCode)). Check $logErr"
        exit 1
    }
    Start-Sleep -Seconds 5
}
Write-Host "[OK] Daemon stable for 60s; proceeding to schedule registration." -ForegroundColor Green

$proc = @{
    pid             = $daemon.Id
    started_at      = (Get-Date -Format "o")
    command         = "$python -m homunculus.daemon --config $Config"
    supervisor_type = "windows-start-process-detached"
    stdout_log      = $logOut
    stderr_log      = $logErr
    soak_branch     = $soakBranch
}
$proc | ConvertTo-Json | Set-Content -Path $processFile -Encoding UTF8
Write-Host "[OK] Daemon PID $($daemon.Id) logging to $logOut / $logErr" -ForegroundColor Green

# Step 7 - register scheduled tasks (SUGGESTION-19 #1: pwsh.exe PATH check)
Write-Host "=== Step 7: register scheduled tasks ===" -ForegroundColor Cyan
$pwshExe = Get-Command pwsh.exe -ErrorAction SilentlyContinue
if (-not $pwshExe) {
    Write-Error "pwsh.exe not on PATH. Install PowerShell 7+ and ensure PATH entry, then re-run."
    exit 1
}
$pwshCmd = $pwshExe.Source

# Shared principal/settings (BLOCKER-3): S4U = no stored password, runs when logged off,
# wakes machine, allows on battery, catches up after missed triggers.
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType S4U -RunLevel Highest
$settings  = New-ScheduledTaskSettingsSet -WakeToRun -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Minutes 10)

# --- Daily observe task ---
$taskName = "Homunculus-Phase5-DailyObserve"
$scriptPath = (Resolve-Path "$PSScriptRoot\daily-observe.ps1").Path
$trigger = New-ScheduledTaskTrigger -Daily -At (Get-Date).AddDays(1).Date.AddHours(9)
$action  = New-ScheduledTaskAction -Execute $pwshCmd `
                                    -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$scriptPath`" -Config `"$Config`" -RepoDir `"$RepoDir`"" `
                                    -WorkingDirectory $RepoDir
$existingTask = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($existingTask) {
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
}
try {
    Register-ScheduledTask -TaskName $taskName -Trigger $trigger -Action $action -Principal $principal -Settings $settings -Description "Homunculus Phase 5 soak daily autonomy-report snapshot" | Out-Null
    Write-Host "[OK] Scheduled task '$taskName' registered (S4U, WakeToRun, StartWhenAvailable)" -ForegroundColor Green
} catch {
    Write-Warning "Register-ScheduledTask (S4U) failed: $($_.Exception.Message)"
    Write-Warning "Falling back to schtasks /Create with SYSTEM/user runtime..."
    $schtasksCmd = "schtasks /Create /F /TN `"$taskName`" /SC DAILY /ST 09:00 /RL HIGHEST /RU `"$env:USERDOMAIN\$env:USERNAME`" /TR `"pwsh.exe -NoProfile -ExecutionPolicy Bypass -File `\`"$scriptPath`\`" -Config `\`"$Config`\`" -RepoDir `\`"$RepoDir`\`"`""
    Write-Warning "If the above fails too, run this manually in an elevated shell:"
    Write-Warning "  $schtasksCmd"
}

# --- Ollama watchdog task (BLOCKER-4) ---
$watchdogName   = "Homunculus-Phase5-OllamaWatchdog"
$watchdogScript = (Resolve-Path "$PSScriptRoot\ollama-watchdog.ps1").Path
$wdTrigger      = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) `
                    -RepetitionInterval (New-TimeSpan -Minutes 5) `
                    -RepetitionDuration (New-TimeSpan -Days 30)
$wdAction       = New-ScheduledTaskAction -Execute $pwshCmd `
                    -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$watchdogScript`" -LogDir `"$LogDir`"" `
                    -WorkingDirectory $RepoDir
$existingWd = Get-ScheduledTask -TaskName $watchdogName -ErrorAction SilentlyContinue
if ($existingWd) {
    Unregister-ScheduledTask -TaskName $watchdogName -Confirm:$false
}
try {
    Register-ScheduledTask -TaskName $watchdogName -Trigger $wdTrigger -Action $wdAction -Principal $principal -Settings $settings -Description "Homunculus Phase 5 Ollama liveness watchdog (5-min interval)" | Out-Null
    Write-Host "[OK] Scheduled task '$watchdogName' registered (5-min interval)" -ForegroundColor Green
} catch {
    Write-Warning "Register-ScheduledTask (S4U) failed for watchdog: $($_.Exception.Message)"
    Write-Warning "Run manually: pwsh.exe -NoProfile -ExecutionPolicy Bypass -File `"$watchdogScript`""
}

Write-Host ""
Write-Host "=== Soak started ===" -ForegroundColor Green
Write-Host "Branch:   $soakBranch"
Write-Host "Daemon:   PID $($daemon.Id)"
Write-Host "Logs:     $logOut"
Write-Host "Baseline: $baseline"
Write-Host "Schedule: Windows Tasks '$taskName' (daily) + '$watchdogName' (5-min)"
Write-Host ""
Write-Host "The soak is now running unattended. After >=7 full days:"
Write-Host "  1. .\scripts\phase5\stop-soak.ps1"
Write-Host "  2. python -m homunculus.cli autonomy-accept --config $Config --soak-log $LogDir --soak-branch $soakBranch --output .planning\phases\05-full-autonomy\05-ACCEPTANCE.md"
Write-Host ""
Write-Host "During soak: ZERO code/config edits. Only monitoring."
Write-Host "Abort conditions: see SOAK-PROTOCOL.md SS7."
