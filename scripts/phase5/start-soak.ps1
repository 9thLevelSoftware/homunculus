#Requires -Version 7.0
<#
.SYNOPSIS
    Start the ≥7-day Phase 5 autonomy soak.

.DESCRIPTION
    Runs throughput precheck, creates the soak branch, runs full preflight,
    captures baseline, starts the daemon detached under a process supervisor,
    registers a daily Windows scheduled task for autonomy-report snapshots.

    Script returns after scheduling. Soak runs for ≥7 days wall-clock.
    When done, call scripts\phase5\stop-soak.ps1 and then autonomy-accept.
#>
param(
    [string]$Config  = "homunculus.toml",
    [string]$LogDir  = ".planning\phases\05-full-autonomy\soak-log",
    [switch]$Force   # skip precheck/preflight gates (NOT recommended)
)

$ErrorActionPreference = "Stop"
$RepoDir = (Resolve-Path .).Path

# Env var
if (-not $env:OPENAI_API_KEY) {
    $u = [Environment]::GetEnvironmentVariable("OPENAI_API_KEY", "User")
    if ($u) { $env:OPENAI_API_KEY = $u }
    else { Write-Error "OPENAI_API_KEY not set. Run scripts\phase5\setup.ps1 first."; exit 1 }
}

# Step 1 — throughput precheck
if (-not $Force) {
    Write-Host "=== Step 1: throughput precheck ===" -ForegroundColor Cyan
    & "$PSScriptRoot\precheck.ps1" -Config $Config | Out-Null
    if ($LASTEXITCODE -ne 0) {
        Write-Error "Throughput precheck failed (exit $LASTEXITCODE). Run bootstrap.ps1 with more tasks, or lower [evolution] thresholds."
        exit 1
    }
    Write-Host "[OK] Throughput gate cleared." -ForegroundColor Green
} else {
    Write-Warning "Skipping precheck (--Force)"
}

# Step 2 — clean working tree
$dirty = & git status --porcelain 2>$null
if ($dirty -and -not $Force) {
    Write-Error "Working tree not clean; commit or stash before soak start. Status:`n$dirty"
    exit 1
}

# Step 3 — create / checkout soak branch
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

# Step 4 — preflight
if (-not $Force) {
    Write-Host "=== Step 4: preflight ===" -ForegroundColor Cyan
    & python -m homunculus.cli autonomy-preflight --config $Config --json
    if ($LASTEXITCODE -ne 0) {
        Write-Error "Preflight failed. Inspect gates above; resolve; re-run."
        exit 1
    }
    Write-Host "[OK] Preflight cleared." -ForegroundColor Green
}

# Step 5 — baseline capture (overwrite-safe; keep throughput_precheck key intact)
if (-not (Test-Path $LogDir)) { New-Item -ItemType Directory -Path $LogDir | Out-Null }
$baseline = Join-Path $LogDir "day-00-baseline.json"
Write-Host "=== Step 5: baseline capture → $baseline ===" -ForegroundColor Cyan
& python -m homunculus.cli autonomy-report --config $Config --json | Out-File -FilePath $baseline -Encoding UTF8
if ($LASTEXITCODE -ne 0) { Write-Error "baseline capture failed"; exit 1 }

# Step 6 — start daemon detached
Write-Host "=== Step 6: start daemon detached ===" -ForegroundColor Cyan
$logOut = Join-Path $LogDir "daemon.stdout.log"
$logErr = Join-Path $LogDir "daemon.stderr.log"
$python = (Get-Command python).Source
$daemon = Start-Process -FilePath $python `
                         -ArgumentList @("-m","homunculus.daemon","--config",$Config) `
                         -WorkingDirectory $RepoDir `
                         -RedirectStandardOutput $logOut `
                         -RedirectStandardError $logErr `
                         -WindowStyle Hidden `
                         -PassThru
Start-Sleep -Seconds 3
if ($daemon.HasExited) {
    Write-Error "Daemon exited immediately (code $($daemon.ExitCode)). Check $logErr"
    exit 1
}

$proc = @{
    pid             = $daemon.Id
    started_at      = (Get-Date -Format "o")
    command         = "$python -m homunculus.daemon --config $Config"
    supervisor_type = "windows-start-process-detached"
    stdout_log      = $logOut
    stderr_log      = $logErr
    soak_branch     = $soakBranch
}
$proc | ConvertTo-Json | Set-Content -Path (Join-Path $LogDir "day-00-process.json") -Encoding UTF8
Write-Host "[OK] Daemon PID $($daemon.Id) logging to $logOut / $logErr" -ForegroundColor Green

# Step 7 — register daily scheduled task
Write-Host "=== Step 7: register daily scheduled task ===" -ForegroundColor Cyan
$taskName = "Homunculus-Phase5-DailyObserve"
$scriptPath = (Resolve-Path "$PSScriptRoot\daily-observe.ps1").Path
$pwshCmd = "pwsh.exe"
$trigger = New-ScheduledTaskTrigger -Daily -At (Get-Date).AddDays(1).Date.AddHours(9)
$action  = New-ScheduledTaskAction -Execute $pwshCmd `
                                    -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$scriptPath`" -Config `"$Config`" -RepoDir `"$RepoDir`"" `
                                    -WorkingDirectory $RepoDir
$existingTask = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($existingTask) {
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
}
try {
    Register-ScheduledTask -TaskName $taskName -Trigger $trigger -Action $action -Description "Homunculus Phase 5 soak daily autonomy-report snapshot" | Out-Null
    Write-Host "[OK] Scheduled task '$taskName' registered (runs daily ~9am local)" -ForegroundColor Green
} catch {
    Write-Warning "Could not register scheduled task: $($_.Exception.Message)"
    Write-Warning "Fall back: manually run scripts\phase5\daily-observe.ps1 each day, OR"
    Write-Warning "  schtasks /Create /TN $taskName /SC DAILY /ST 09:00 /TR `"pwsh.exe -NoProfile -ExecutionPolicy Bypass -File `"$scriptPath`"`""
}

Write-Host ""
Write-Host "=== Soak started ===" -ForegroundColor Green
Write-Host "Branch:   $soakBranch"
Write-Host "Daemon:   PID $($daemon.Id)"
Write-Host "Logs:     $logOut"
Write-Host "Baseline: $baseline"
Write-Host "Schedule: Windows Task '$taskName' (daily)"
Write-Host ""
Write-Host "The soak is now running unattended. After >=7 full days:"
Write-Host "  1. .\scripts\phase5\stop-soak.ps1"
Write-Host "  2. python -m homunculus.cli autonomy-accept --config $Config --soak-log $LogDir --soak-branch $soakBranch --output .planning\phases\05-full-autonomy\05-ACCEPTANCE.md"
Write-Host ""
Write-Host "During soak: ZERO code/config edits. Only monitoring."
Write-Host "Abort conditions: see SOAK-PROTOCOL.md §7."
