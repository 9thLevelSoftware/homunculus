#Requires -Version 7.0
<#
.SYNOPSIS
    Single-shot daily observation during the Phase 5 soak.

.DESCRIPTION
    Intended to run via Windows Task Scheduler. Writes
    .planning\phases\05-full-autonomy\soak-log\day-NN.json plus a markdown
    diff against yesterday's snapshot.
#>
param(
    [string]$Config  = "homunculus.toml",
    [string]$LogDir  = ".planning\phases\05-full-autonomy\soak-log",
    [string]$RepoDir = $(Resolve-Path .).Path
)

$ErrorActionPreference = "Stop"
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

# Ensure env var for teacher_auth gate in any downstream preflight comparisons
if (-not $env:OPENAI_API_KEY) {
    $u = [Environment]::GetEnvironmentVariable("OPENAI_API_KEY", "User")
    if ($u) { $env:OPENAI_API_KEY = $u }
}

Write-Host "[daily-observe] day=$padded at $(Get-Date -Format o)"
& python -m homunculus.cli autonomy-report --config $Config --json | Out-File -FilePath $outJson -Encoding UTF8
if ($LASTEXITCODE -ne 0) { Write-Error "autonomy-report exited $LASTEXITCODE"; exit 1 }

# Markdown diff vs previous day
$prevDayIdx = $nextDay - 1
$prevPadded = "{0:D2}" -f $prevDayIdx
$prevJson   = Join-Path $LogDir "day-$prevPadded.json"
if ($prevDayIdx -eq 0) { $prevJson = Join-Path $LogDir "day-00-baseline.json" }

$today = Get-Content -Raw -Path $outJson | ConvertFrom-Json
$md = @()
$md += "# Soak Day $padded"
$md += ""
$md += "Captured: $(Get-Date -Format o)"
$md += ""
$md += "## Key metrics"
$md += ""
$md += "| Metric | Value |"
$md += "|---|---|"
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

Set-Content -Path $outMd -Value ($md -join "`n") -Encoding UTF8
Write-Host "[daily-observe] wrote $outJson and $outMd"
