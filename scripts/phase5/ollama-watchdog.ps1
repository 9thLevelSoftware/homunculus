#Requires -Version 7.0
<#
.SYNOPSIS
    Phase 5 soak Ollama liveness watchdog.

.DESCRIPTION
    Probes the Ollama server at http://127.0.0.1:11434/api/tags with a 5s
    timeout. If unreachable, launches `ollama serve` detached, waits 10s, and
    re-probes. Appends a timestamped status line to
    .planning\phases\05-full-autonomy\soak-log\ollama-watchdog.log.

    Designed to run every 5 minutes via Task Scheduler. Always exits 0
    (Task Scheduler retry semantics are not desired here).
#>
param(
    [string]$LogDir   = ".planning\phases\05-full-autonomy\soak-log",
    [string]$Endpoint = "http://127.0.0.1:11434/api/tags"
)

$ErrorActionPreference = "Continue"

if (-not (Test-Path $LogDir)) { New-Item -ItemType Directory -Path $LogDir -Force | Out-Null }
$logFile = Join-Path $LogDir "ollama-watchdog.log"

function Write-WdLog {
    param([string]$Status, [string]$Detail = "")
    $stamp = Get-Date -Format o
    $line = "$stamp  $Status  $Detail"
    Add-Content -Path $logFile -Value $line -Encoding UTF8
}

function Test-Ollama {
    param([string]$Url)
    try {
        $resp = Invoke-WebRequest -Uri $Url -TimeoutSec 5 -UseBasicParsing -ErrorAction Stop
        return ($resp.StatusCode -eq 200)
    } catch {
        return $false
    }
}

if (Test-Ollama -Url $Endpoint) {
    Write-WdLog -Status "OK" -Detail "ollama reachable at $Endpoint"
    exit 0
}

Write-WdLog -Status "DOWN" -Detail "ollama unreachable; launching serve"

try {
    Start-Process -FilePath "ollama" -ArgumentList "serve" -WindowStyle Hidden -ErrorAction Stop | Out-Null
} catch {
    Write-WdLog -Status "ERROR" -Detail "Start-Process ollama serve failed: $($_.Exception.Message)"
    exit 0
}

Start-Sleep -Seconds 10

if (Test-Ollama -Url $Endpoint) {
    Write-WdLog -Status "RECOVERED" -Detail "ollama reachable after restart"
} else {
    Write-WdLog -Status "STILL_DOWN" -Detail "ollama still unreachable after 10s"
}

exit 0
