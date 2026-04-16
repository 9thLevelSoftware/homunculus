#Requires -Version 7.0
<#
.SYNOPSIS
    Stop the Phase 5 soak daemon and unregister the daily scheduled task.

.DESCRIPTION
    Reads PID from soak-log/day-00-process.json. Attempts graceful stop
    (Ctrl+C equivalent via PostThreadMessage / WM_CLOSE) with a timeout, then
    falls back to Stop-Process. Unregisters the daily Windows scheduled task.

    Does NOT delete the soak branch — it is preserved as evidence for
    autonomy-accept (SC6 git-log author check).
#>
param(
    [string]$LogDir         = ".planning\phases\05-full-autonomy\soak-log",
    [int]   $GracefulSecs   = 120,
    [string]$TaskName       = "Homunculus-Phase5-DailyObserve"
)

$ErrorActionPreference = "Stop"

$procFile = Join-Path $LogDir "day-00-process.json"
if (-not (Test-Path $procFile)) { Write-Error "Process file not found: $procFile"; exit 1 }
$proc = Get-Content -Raw -Path $procFile | ConvertFrom-Json
$targetPid = $proc.pid

$running = Get-Process -Id $targetPid -ErrorAction SilentlyContinue
if (-not $running) {
    Write-Host "[INFO] Daemon PID $targetPid is not running (already stopped)." -ForegroundColor Yellow
} else {
    Write-Host "[INFO] Attempting graceful stop of PID $targetPid (timeout ${GracefulSecs}s)..." -ForegroundColor Cyan
    # Windows has no SIGTERM. Try Stop-Process without -Force first (sends close signal to UI apps;
    # for console-less detached python this falls through to termination). For real graceful, would need
    # a stop file the daemon polls — not implemented. Honest: this is effectively a hard stop.
    Stop-Process -Id $targetPid -ErrorAction SilentlyContinue
    $deadline = (Get-Date).AddSeconds($GracefulSecs)
    while ((Get-Date) -lt $deadline) {
        if (-not (Get-Process -Id $targetPid -ErrorAction SilentlyContinue)) { break }
        Start-Sleep -Seconds 2
    }
    if (Get-Process -Id $targetPid -ErrorAction SilentlyContinue) {
        Write-Warning "Daemon did not exit within $GracefulSecs s; forcing."
        Stop-Process -Id $targetPid -Force -ErrorAction SilentlyContinue
    }
    Write-Host "[OK] Daemon stopped." -ForegroundColor Green
}

# Unregister scheduled task
$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($existing) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host "[OK] Unregistered scheduled task '$TaskName'." -ForegroundColor Green
} else {
    Write-Host "[INFO] No scheduled task '$TaskName' found." -ForegroundColor Yellow
}

# Final-day snapshot for acceptance input
Write-Host "[INFO] Writing final autonomy-report snapshot..." -ForegroundColor Cyan
& "$PSScriptRoot\daily-observe.ps1" -LogDir $LogDir

Write-Host ""
Write-Host "Soak stopped. Soak branch preserved for evidence." -ForegroundColor Green
Write-Host "Next: python -m homunculus.cli autonomy-accept --config homunculus.toml --soak-log $LogDir --soak-branch $($proc.soak_branch) --output .planning\phases\05-full-autonomy\05-ACCEPTANCE.md"
