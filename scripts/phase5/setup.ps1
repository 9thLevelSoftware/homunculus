#Requires -Version 7.0
<#
.SYNOPSIS
    Phase 5 soak setup: install/verify Ollama, pull teacher model, start serve, set env.

.DESCRIPTION
    Idempotent. Run from repo root. Safe to re-run.
    Verifies Ollama installation, pulls qwen2.5-coder:14b-instruct-q4_K_M (or
    fallback 7B), ensures `ollama serve` is running, sets OPENAI_API_KEY.

.PARAMETER Model
    Ollama model tag. Default: qwen2.5-coder:14b-instruct-q4_K_M

.PARAMETER FallbackModel
    Used if primary pull fails. Default: qwen2.5-coder:7b-instruct-q8_0
#>
param(
    [string]$Model = "qwen2.5-coder:14b-instruct-q4_K_M",
    [string]$FallbackModel = "qwen2.5-coder:7b-instruct-q8_0"
)

$ErrorActionPreference = "Stop"
$OllamaUrl = "http://127.0.0.1:11434"

function Test-OllamaInstalled {
    try {
        $null = & ollama --version 2>&1
        return $LASTEXITCODE -eq 0
    } catch {
        return $false
    }
}

function Test-OllamaServing {
    try {
        $r = Invoke-WebRequest -Uri "$OllamaUrl/api/tags" -UseBasicParsing -TimeoutSec 3 -ErrorAction Stop
        return $r.StatusCode -eq 200
    } catch {
        return $false
    }
}

function Get-PulledModels {
    try {
        $r = Invoke-RestMethod -Uri "$OllamaUrl/api/tags" -TimeoutSec 5
        return @($r.models | ForEach-Object { $_.name })
    } catch {
        return @()
    }
}

Write-Host "=== Phase 5 Setup ===" -ForegroundColor Cyan

# 1. Ollama installed?
if (-not (Test-OllamaInstalled)) {
    Write-Host "Ollama is not installed." -ForegroundColor Red
    Write-Host "Install with:    winget install ollama.ollama"
    Write-Host "Or download:     https://ollama.com/download/windows"
    exit 1
}
Write-Host "[OK]   Ollama installed: $(& ollama --version)" -ForegroundColor Green

# 2. Serve running?
if (-not (Test-OllamaServing)) {
    Write-Host "[..]   ollama serve not running; starting detached..." -ForegroundColor Yellow
    Start-Process -FilePath "ollama" -ArgumentList "serve" -WindowStyle Hidden
    $deadline = (Get-Date).AddSeconds(15)
    while ((Get-Date) -lt $deadline) {
        if (Test-OllamaServing) { break }
        Start-Sleep -Milliseconds 500
    }
    if (-not (Test-OllamaServing)) {
        Write-Error "ollama serve failed to come up on $OllamaUrl within 15 s"
        exit 1
    }
}
Write-Host "[OK]   ollama serve reachable on $OllamaUrl" -ForegroundColor Green

# 3. Model pulled?
$pulled = Get-PulledModels
$effectiveModel = $Model
if ($pulled -notcontains $Model) {
    Write-Host "[..]   Pulling $Model (~9 GB; this can take several minutes)..." -ForegroundColor Yellow
    & ollama pull $Model
    if ($LASTEXITCODE -ne 0) {
        Write-Warning "Pull of $Model failed. Trying fallback: $FallbackModel"
        & ollama pull $FallbackModel
        if ($LASTEXITCODE -ne 0) {
            Write-Error "Both model pulls failed. Check internet + disk space."
            exit 1
        }
        $effectiveModel = $FallbackModel
        Write-Host "[NOTE] Fallback in use. Update homunculus.toml [teacher].model to:" -ForegroundColor Yellow
        Write-Host "       model = `"$FallbackModel`""
    }
} else {
    Write-Host "[OK]   Model already pulled: $Model" -ForegroundColor Green
}

# 4. Env var
$existing = [Environment]::GetEnvironmentVariable("OPENAI_API_KEY", "User")
if ([string]::IsNullOrEmpty($existing)) {
    [Environment]::SetEnvironmentVariable("OPENAI_API_KEY", "ollama-local", "User")
    $env:OPENAI_API_KEY = "ollama-local"
    Write-Host "[OK]   Set OPENAI_API_KEY=ollama-local (User scope)" -ForegroundColor Green
    Write-Host "       Open a new shell OR set `$env:OPENAI_API_KEY manually in this session" -ForegroundColor Yellow
} else {
    Write-Host "[OK]   OPENAI_API_KEY already set (User scope)" -ForegroundColor Green
    if (-not $env:OPENAI_API_KEY) { $env:OPENAI_API_KEY = $existing }
}

# 5. Smoke test — one-token generation
Write-Host "[..]   Smoke-testing teacher inference (1-token request)..." -ForegroundColor Yellow
try {
    $body = @{
        model = $effectiveModel
        messages = @(@{ role = "user"; content = "ping" })
        max_tokens = 1
        temperature = 0
    } | ConvertTo-Json -Depth 5
    $r = Invoke-RestMethod -Uri "$OllamaUrl/v1/chat/completions" `
                           -Method POST `
                           -Body $body `
                           -ContentType "application/json" `
                           -Headers @{ Authorization = "Bearer ollama-local" } `
                           -TimeoutSec 120
    if ($r.choices.Count -gt 0) {
        Write-Host "[OK]   Teacher smoke passed" -ForegroundColor Green
    } else {
        Write-Warning "Teacher responded but no choices in payload"
    }
} catch {
    Write-Warning "Smoke test failed: $($_.Exception.Message)"
    Write-Warning "Continuing anyway — bootstrap will surface real errors"
}

Write-Host ""
Write-Host "Setup complete. Next:" -ForegroundColor Cyan
Write-Host "  .\scripts\phase5\bootstrap.ps1" -ForegroundColor White
