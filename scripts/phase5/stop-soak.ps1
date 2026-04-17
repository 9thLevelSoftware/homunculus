#Requires -Version 7.0
<#
.SYNOPSIS
    Stop the Phase 5 soak daemon and unregister all related scheduled tasks.

.DESCRIPTION
    Writes runtime/STOP, waits for daemon to exit after current cycle, falls
    back to Force-kill on timeout. The daemon polls (runtime_dir / "STOP") between
    cycles (see homunculus/daemon.py); presence of file = stop after this cycle.
    File path must match exactly between this script and the daemon.

    Reads PID from soak-log/day-00-process.json, drops the stop-file under
    <RuntimeDir>/STOP, polls Get-Process every 2s up to $GracefulSecs, then
    Force-kills if still alive. Removes the stop-file at the end either way for
    idempotency on next run. Unregisters BOTH the daily-observe scheduled task
    and the Ollama watchdog scheduled task.

    Does NOT delete the soak branch - it is preserved as evidence for
    autonomy-accept (SC6 git-log author check).
#>
param(
    [string]$LogDir         = ".planning\phases\05-full-autonomy\soak-log",
    [string]$RuntimeDir     = "runtime",
    [int]   $GracefulSecs   = 120,
    [string]$TaskName       = "Homunculus-Phase5-DailyObserve",
    [string]$WatchdogTask   = "Homunculus-Phase5-OllamaWatchdog"
)

$ErrorActionPreference = "Stop"

$procFile = Join-Path $LogDir "day-00-process.json"
if (-not (Test-Path $procFile)) { Write-Error "Process file not found: $procFile"; exit 1 }
$proc = Get-Content -Raw -Path $procFile | ConvertFrom-Json
$targetPid = $proc.pid

# Ensure runtime dir exists, then drop STOP file as the graceful contract with daemon.
if (-not (Test-Path $RuntimeDir)) { New-Item -ItemType Directory -Path $RuntimeDir -Force | Out-Null }
$stopFile = Join-Path $RuntimeDir "STOP"

$running = Get-Process -Id $targetPid -ErrorAction SilentlyContinue
if (-not $running) {
    Write-Host "[INFO] Daemon PID $targetPid is not running (already stopped)." -ForegroundColor Yellow
} else {
    Write-Host "[INFO] Writing stop-file $stopFile and waiting up to ${GracefulSecs}s for graceful exit..." -ForegroundColor Cyan
    New-Item -ItemType File -Path $stopFile -Force | Out-Null

    $deadline = (Get-Date).AddSeconds($GracefulSecs)
    while ((Get-Date) -lt $deadline) {
        if (-not (Get-Process -Id $targetPid -ErrorAction SilentlyContinue)) { break }
        Start-Sleep -Seconds 2
    }

    if (Get-Process -Id $targetPid -ErrorAction SilentlyContinue) {
        $msg = "[WARN] Daemon did not exit within ${GracefulSecs}s after STOP-file written; forcing kill of PID $targetPid."
        Write-Warning $msg
        # Append to a stop log alongside soak-log for forensic trail
        $stopLog = Join-Path $LogDir "stop-soak.log"
        Add-Content -Path $stopLog -Value "$(Get-Date -Format o) $msg" -Encoding UTF8
        Stop-Process -Id $targetPid -Force -ErrorAction SilentlyContinue
    } else {
        Write-Host "[OK] Daemon exited gracefully after STOP-file." -ForegroundColor Green
    }
}

# Always remove stop-file (idempotency for next start)
if (Test-Path $stopFile) {
    Remove-Item -Path $stopFile -Force -ErrorAction SilentlyContinue
    Write-Host "[INFO] Removed stop-file $stopFile" -ForegroundColor Cyan
}

# Unregister daily-observe scheduled task
$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($existing) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host "[OK] Unregistered scheduled task '$TaskName'." -ForegroundColor Green
} else {
    Write-Host "[INFO] No scheduled task '$TaskName' found." -ForegroundColor Yellow
}

# Unregister Ollama watchdog scheduled task
$existingWd = Get-ScheduledTask -TaskName $WatchdogTask -ErrorAction SilentlyContinue
if ($existingWd) {
    Unregister-ScheduledTask -TaskName $WatchdogTask -Confirm:$false
    Write-Host "[OK] Unregistered scheduled task '$WatchdogTask'." -ForegroundColor Green
} else {
    Write-Host "[INFO] No scheduled task '$WatchdogTask' found." -ForegroundColor Yellow
}

# Final-day snapshot for acceptance input
Write-Host "[INFO] Writing final autonomy-report snapshot..." -ForegroundColor Cyan
& "$PSScriptRoot\daily-observe.ps1" -LogDir $LogDir

Write-Host ""
Write-Host "Soak stopped. Soak branch preserved for evidence." -ForegroundColor Green
Write-Host "Next: python -m homunculus.cli autonomy-accept --config homunculus.toml --soak-log $LogDir --soak-branch $($proc.soak_branch) --output .planning\phases\05-full-autonomy\05-ACCEPTANCE.md"
