#Requires -Version 7.0
<#
.SYNOPSIS
    Single-shot daily observation during the Phase 5 soak.

.DESCRIPTION
    Intended to run via Windows Task Scheduler. Writes
    .planning\phases\05-full-autonomy\soak-log\day-NN.json plus a markdown
    diff against yesterday's snapshot.

    Also asserts daemon liveness (reads PID from day-00-process.json) and
    writes daemon_alive into the day's JSON. If dead, the markdown is prefixed
    with **ABORT_RECOMMENDED**. Adds a disk-space pressure warning when free
    space drops below 15% (abort threshold #2 is <10%).
#>
param(
    [string]$Config  = "homunculus.toml",
    [string]$LogDir  = ".planning\phases\05-full-autonomy\soak-log",
    [string]$RepoDir = $(Resolve-Path .).Path
)

$ErrorActionPreference = "Stop"

# WARNING-12: env-var fallback for S4U-principal scheduled-task sessions
if (-not $env:OPENAI_API_KEY) {
    $u = [Environment]::GetEnvironmentVariable("OPENAI_API_KEY", "User")
    if ($u) { $env:OPENAI_API_KEY = $u }
}

Set-Location $RepoDir

if (-not (Test-Path $LogDir)) { New-Item -ItemType Directory -Path $LogDir | Out-Null }

# Determine next day index (day-NN.json); day-00 is baseline (captured by start-soak.ps1)
$existing = Get-ChildItem -Path $LogDir -Filter "day-*.json" -ErrorAction SilentlyContinue |
            ForEach-Object { if ($_.BaseName -match 'day-(\d+)') { [int]$Matches[1] } } |
            Sort-Object -Unique
$nextDay = if ($existing) { ($existing | Measure-Object -Maximum).Maximum + 1 } else { 1 }
$nextDay = [math]::Min($nextDay, 99)
$padded  = "{0:D2}" -f $nextDay
$outJson = Join-Path $LogDir "day-$padded.json"
$outMd   = Join-Path $LogDir "day-$padded.md"

Write-Host "[daily-observe] day=$padded at $(Get-Date -Format o)"
& python -m homunculus.cli autonomy-report --config $Config --json | Out-File -FilePath $outJson -Encoding UTF8
if ($LASTEXITCODE -ne 0) { Write-Error "autonomy-report exited $LASTEXITCODE"; exit 1 }

# WARNING-7: daemon liveness check (PID from day-00-process.json)
$daemonAlive = $null
$procFile = Join-Path $LogDir "day-00-process.json"
if (Test-Path $procFile) {
    try {
        $pinfo = Get-Content -Raw -Path $procFile | ConvertFrom-Json
        if ($pinfo.pid) {
            $proc = Get-Process -Id $pinfo.pid -ErrorAction SilentlyContinue
            $daemonAlive = [bool]$proc
        }
    } catch {
        $daemonAlive = $false
    }
}

# Inject daemon_alive into the day's JSON (overwrites in place)
try {
    $todayObj = Get-Content -Raw -Path $outJson | ConvertFrom-Json
    if ($null -ne $daemonAlive) {
        $todayObj | Add-Member -NotePropertyName "daemon_alive" -NotePropertyValue $daemonAlive -Force
    }
    $todayObj | ConvertTo-Json -Depth 20 | Set-Content -Path $outJson -Encoding UTF8
} catch {
    Write-Warning "Could not augment $outJson with daemon_alive: $($_.Exception.Message)"
}

# Markdown diff vs previous day
$prevDayIdx = $nextDay - 1
$prevPadded = "{0:D2}" -f $prevDayIdx
$prevJson   = Join-Path $LogDir "day-$prevPadded.json"
if ($prevDayIdx -eq 0) { $prevJson = Join-Path $LogDir "day-00-baseline.json" }

$today = Get-Content -Raw -Path $outJson | ConvertFrom-Json
$md = @()

# WARNING-7: ABORT_RECOMMENDED header if daemon dead
if ($null -ne $daemonAlive -and -not $daemonAlive) {
    $md += "**ABORT_RECOMMENDED** - Daemon process not running at observation time. Inspect daemon.stderr.log immediately and consult SOAK-PROTOCOL SS7."
    $md += ""
}

$md += "# Soak Day $padded"
$md += ""
$md += "Captured: $(Get-Date -Format o)"
$md += ""
$md += "## Key metrics"
$md += ""
$md += "| Metric | Value |"
$md += "|---|---|"
$md += "| daemon_alive | $daemonAlive |"
$md += "| uptime | $($today.uptime) |"
$md += "| episodes_total | $($today.episodes_total) |"
$md += "| episodes_success | $($today.episodes_success) |"
$md += "| self_directed_tasks_completed | $($today.self_directed_tasks_completed) |"
$md += "| loras_trained | $($today.loras_trained) |"
$md += "| loras_merged | $($today.loras_merged) |"
$md += "| current_base_generation | $($today.current_base_generation) |"
$md += "| patch_success_rate | $($today.patch_success_rate) |"
$md += "| patch_success_rate_trend | $($today.patch_success_rate_trend) |"
$md += "| coverage_percent | $($today.coverage_percent) |"
$md += "| watchdog_flags | $([string]::Join(', ', @($today.watchdog_flags))) |"

if (Test-Path $prevJson) {
    $prev = Get-Content -Raw -Path $prevJson | ConvertFrom-Json
    $md += ""
    $md += "## Delta vs day-$prevPadded"
    $md += ""
    $md += "| Metric | Prev | Today | Delta |"
    $md += "|---|---|---|---|"
    foreach ($m in 'episodes_total','episodes_success','self_directed_tasks_completed','loras_trained','loras_merged','current_base_generation') {
        $p = if ($null -ne $prev.$m) { $prev.$m } else { 0 }
        $t = if ($null -ne $today.$m) { $today.$m } else { 0 }
        $d = $t - $p
        $md += "| $m | $p | $t | $d |"
    }
}

# WARNING-11: disk-pressure check
try {
    $driveLetter = (Get-Item $RepoDir).PSDrive.Name
    $drive = Get-PSDrive -Name $driveLetter -ErrorAction Stop
    $totalBytes = [double]($drive.Free + $drive.Used)
    if ($totalBytes -gt 0) {
        $freePct = [math]::Round(($drive.Free / $totalBytes) * 100, 1)
        if ($freePct -lt 15) {
            $md += ""
            $md += "**DISK_PRESSURE** - Free space ${freePct}% on drive $($drive.Name). Abort condition #2 approaches at <10%."
        }
    }
} catch {
    Write-Warning "Disk-space check failed: $($_.Exception.Message)"
}

Set-Content -Path $outMd -Value ($md -join "`n") -Encoding UTF8
Write-Host "[daily-observe] wrote $outJson and $outMd (daemon_alive=$daemonAlive)"
