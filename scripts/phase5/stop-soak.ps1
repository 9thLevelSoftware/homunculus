#Requires -Version 7.0
<#
.SYNOPSIS
    Stop the Phase 5 soak daemon and unregister all related scheduled tasks.

.DESCRIPTION
    Writes the STOP file the daemon polls between cycles (see homunculus/daemon.py),
    waits for graceful exit, then Force-kills on timeout.

    BLOCKER c2-1 — Stop-file path resolution (must match daemon exactly):
        1. $proc.stop_file_path from day-00-process.json  (preferred, written by start-soak)
        2. Join-Path $proc.runtime_dir "STOP"             (fallback, same source)
        3. Join-Path $RuntimeDir "STOP"                   (legacy param fallback; may diverge)
    The daemon resolves config.paths.runtime_dir absolutely against the config
    file's base directory. A relative default here would diverge whenever
    homunculus.toml overrides paths.runtime_dir.

    Reads PID from soak-log/day-00-process.json, drops STOP, polls Get-Process
    every 2s up to $GracefulSecs, then Force-kills if still alive. Removes the
    stop-file at the end either way for idempotency on next run. Unregisters
    BOTH the daily-observe scheduled task and the Ollama watchdog scheduled task.

    SUGGESTION c2-4 — Final snapshot is written to final-snapshot.json (distinct
    filename) rather than routed through daily-observe, so a post-ForceKill
    `daemon_alive=false` doesn't noisily trip ABORT_RECOMMENDED in acceptance.

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

# BLOCKER c2-1: stop-file path resolution — prefer the daemon-resolved path
# stored by start-soak, fall back to runtime_dir from the same file, and only
# fall back to the $RuntimeDir param as a last resort for legacy process-files.
if ($proc.stop_file_path) {
    $stopFile = $proc.stop_file_path
    Write-Host "[INFO] Using stop-file path from process-file: $stopFile" -ForegroundColor Cyan
} elseif ($proc.runtime_dir) {
    $stopFile = Join-Path $proc.runtime_dir "STOP"
    Write-Host "[INFO] Using stop-file derived from process-file runtime_dir: $stopFile" -ForegroundColor Cyan
} else {
    if (-not (Test-Path $RuntimeDir)) { New-Item -ItemType Directory -Path $RuntimeDir -Force | Out-Null }
    $stopFile = Join-Path $RuntimeDir "STOP"
    Write-Warning "process-file lacks runtime_dir/stop_file_path - falling back to $stopFile (may diverge from daemon's resolved runtime_dir)"
}

# SUGGESTION c2-4: track shutdown mode so final-snapshot can be emitted sanely.
$gracefulExit = $false

$running = Get-Process -Id $targetPid -ErrorAction SilentlyContinue
if (-not $running) {
    Write-Host "[INFO] Daemon PID $targetPid is not running (already stopped)." -ForegroundColor Yellow
    $gracefulExit = $true
} else {
    Write-Host "[INFO] Writing stop-file $stopFile and waiting up to ${GracefulSecs}s for graceful exit..." -ForegroundColor Cyan
    # Ensure parent dir exists before writing the STOP sentinel.
    $stopParent = Split-Path -Parent $stopFile
    if ($stopParent -and -not (Test-Path $stopParent)) {
        New-Item -ItemType Directory -Path $stopParent -Force | Out-Null
    }
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
        $gracefulExit = $false
    } else {
        Write-Host "[OK] Daemon exited gracefully after STOP-file." -ForegroundColor Green
        $gracefulExit = $true
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

# SUGGESTION c2-4: final snapshot — write a distinct filename (final-snapshot.json),
# NOT a day-NN slot, and skip daily-observe's markdown-diff + ABORT_RECOMMENDED path.
# A Force-killed daemon would otherwise show daemon_alive=false through daily-observe
# and produce a noisy banner that misleads acceptance. Direct autonomy-report --json
# captures the post-mortem state cleanly.
Write-Host "[INFO] Writing final snapshot (graceful=$gracefulExit)..." -ForegroundColor Cyan
$finalSnapshot = Join-Path $LogDir "final-snapshot.json"
& python -m homunculus.cli autonomy-report --config "homunculus.toml" --json | Out-File -FilePath $finalSnapshot -Encoding UTF8
if ($LASTEXITCODE -eq 0) {
    Write-Host "[OK] Wrote final-snapshot.json" -ForegroundColor Green
} else {
    Write-Warning "autonomy-report --json failed (exit $LASTEXITCODE); final-snapshot.json may be empty"
}

Write-Host ""
Write-Host "Soak stopped. Soak branch preserved for evidence." -ForegroundColor Green
Write-Host "Next: python -m homunculus.cli autonomy-accept --config homunculus.toml --soak-log $LogDir --soak-branch $($proc.soak_branch) --output .planning\phases\05-full-autonomy\05-ACCEPTANCE.md"
