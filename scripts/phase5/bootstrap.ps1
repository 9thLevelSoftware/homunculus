#Requires -Version 7.0
<#
.SYNOPSIS
    Bootstrap traces/episodes.jsonl with seed episodes so the throughput
    pre-check gate in SOAK-PROTOCOL §2.2 clears.

.DESCRIPTION
    Reads scripts/phase5/seed-tasks.json, creates a throwaway branch
    `phase-5/bootstrap-YYYYMMDD` off master, runs each task via
    `python -m homunculus.cli run-episode`, and summarizes outcomes.

    Seed tasks are all documentation / comment additions — safe for the
    teacher to produce a patch for, safe if auto-committed to the throwaway
    branch. The branch is preserved after bootstrap (delete manually if desired
    with `git branch -D phase-5/bootstrap-YYYYMMDD`).

.PARAMETER Config
    Path to homunculus.toml. Default: ./homunculus.toml

.PARAMETER Tasks
    Path to seed-tasks.json. Default: ./scripts/phase5/seed-tasks.json

.PARAMETER MinSuccessful
    Minimum successful episodes to declare bootstrap done. Default: 5
#>
param(
    [string]$Config = "homunculus.toml",
    [string]$Tasks  = "scripts/phase5/seed-tasks.json",
    [int]   $MinSuccessful = 5
)

$ErrorActionPreference = "Stop"

# Sanity checks
if (-not (Test-Path $Config)) { Write-Error "Config not found: $Config"; exit 1 }
if (-not (Test-Path $Tasks))  { Write-Error "Seed tasks not found: $Tasks"; exit 1 }
if (-not $env:OPENAI_API_KEY) {
    $u = [Environment]::GetEnvironmentVariable("OPENAI_API_KEY", "User")
    if ($u) { $env:OPENAI_API_KEY = $u }
    else { Write-Error "OPENAI_API_KEY not set. Run scripts\phase5\setup.ps1 first."; exit 1 }
}

# Working tree must be clean (agent patches will be auto-committed)
$dirty = & git status --porcelain 2>$null
if ($dirty) {
    Write-Error "Working tree not clean. Commit or stash first. Output:`n$dirty"
    exit 1
}

# Create throwaway branch
$stamp = Get-Date -Format "yyyyMMdd"
$branch = "phase-5/bootstrap-$stamp"
$existing = & git branch --list $branch 2>$null
if ($existing) {
    Write-Host "[INFO] Branch $branch already exists; checking it out" -ForegroundColor Yellow
    & git checkout $branch
} else {
    Write-Host "[INFO] Creating $branch from master" -ForegroundColor Cyan
    & git checkout -b $branch master
}
if ($LASTEXITCODE -ne 0) { Write-Error "git checkout failed"; exit 1 }

# Load seed tasks
$seed = Get-Content -Raw -Path $Tasks | ConvertFrom-Json
Write-Host "[INFO] Loaded $($seed.Count) seed tasks from $Tasks" -ForegroundColor Cyan

# Run each episode
$results = @()
$successCount = 0
foreach ($t in $seed) {
    $taskId = "$($t.task_id)-$stamp"
    Write-Host ""
    Write-Host "=== $taskId ===" -ForegroundColor Cyan
    $start = Get-Date
    $prompt = $t.prompt
    # Write prompt to a temp file to avoid quoting issues with shell
    $tmp = New-TemporaryFile
    Set-Content -Path $tmp -Value $prompt -NoNewline -Encoding UTF8
    try {
        & python -m homunculus.cli run-episode `
            --config $Config `
            --workspace self `
            --task-id $taskId `
            --prompt-file $tmp.FullName
        $code = $LASTEXITCODE
    } finally {
        Remove-Item -Path $tmp -ErrorAction SilentlyContinue
    }
    $elapsed = (Get-Date) - $start
    $ok = ($code -eq 0)
    if ($ok) { $successCount++ }
    $results += [PSCustomObject]@{
        TaskId   = $taskId
        ExitCode = $code
        Success  = $ok
        Seconds  = [int]$elapsed.TotalSeconds
    }
    Write-Host ("[RESULT] exit={0} ok={1} {2}s" -f $code, $ok, [int]$elapsed.TotalSeconds)
    if ($successCount -ge $MinSuccessful) {
        Write-Host "[INFO] Reached $MinSuccessful successes; continuing to finish remaining tasks for margin" -ForegroundColor Green
    }
}

Write-Host ""
Write-Host "=== Bootstrap summary ===" -ForegroundColor Cyan
$results | Format-Table -AutoSize

$total = $results.Count
$ok    = ($results | Where-Object Success).Count
Write-Host ("Successful: {0}/{1}" -f $ok, $total)

# Sanity check against traces
$epPath = "traces/episodes.jsonl"
if (Test-Path $epPath) {
    $lines = (Get-Content -Path $epPath | Measure-Object -Line).Lines
    Write-Host ("traces/episodes.jsonl: {0} lines" -f $lines)
} else {
    Write-Warning "traces/episodes.jsonl not found after bootstrap — investigate"
}

if ($ok -lt $MinSuccessful) {
    Write-Error "Bootstrap produced $ok/$MinSuccessful successes. Re-run or expand seed-tasks.json before precheck."
    exit 1
}

Write-Host ""
Write-Host "Bootstrap complete on branch $branch." -ForegroundColor Green
Write-Host "Next: .\scripts\phase5\precheck.ps1" -ForegroundColor White
