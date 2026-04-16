#Requires -Version 7.0
<#
.SYNOPSIS
    Standalone throughput pre-check per SOAK-PROTOCOL §2.2.

.DESCRIPTION
    Computes projected LoRA merges over a 7-day soak window using the last
    14 days of traces/episodes.jsonl and the current [evolution] thresholds.
    Exits 0 if gate clears (projection >= 1.0), else exits 2 with diagnostics.

    Output is deterministic JSON to stdout; human summary to stderr.
#>
param(
    [string]$Config        = "homunculus.toml",
    [double]$Threshold     = 1.0,
    [double]$SafetyMargin  = 1.5,
    [int]   $LookbackDays  = 14,
    [int]   $SoakDays      = 7
)

$ErrorActionPreference = "Stop"

$py = @"
import json, sys, tomllib
from datetime import datetime, timedelta, timezone
from pathlib import Path

CONFIG   = Path(sys.argv[1])
LOOKBACK = int(sys.argv[2])
SOAK     = int(sys.argv[3])
THRESH   = float(sys.argv[4])
MARGIN   = float(sys.argv[5])

cfg = tomllib.loads(CONFIG.read_text(encoding='utf-8'))
evo = cfg.get('evolution', {})
min_samples = int(evo.get('auto_train_after_samples', 50))
min_loras   = int(evo.get('auto_merge_after_loras', 5))

ep_path = Path('traces/episodes.jsonl')
now = datetime.now(timezone.utc)
window_start = now - timedelta(days=LOOKBACK)

total = 0
success = 0
if ep_path.exists() and ep_path.stat().st_size > 0:
    for line in ep_path.read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if not line: continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        ts = rec.get('terminated_at') or rec.get('created_at') or rec.get('started_at')
        if not ts:
            continue
        try:
            dt = datetime.fromisoformat(ts.replace('Z', '+00:00'))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        if dt < window_start:
            continue
        total += 1
        if rec.get('outcome') == 'success' or rec.get('status') == 'success':
            success += 1

episodes_per_day = total / max(LOOKBACK, 1)
success_rate     = (success / total) if total else 0.0
projected_successful_soak = episodes_per_day * SOAK * success_rate
projected_loras_trained   = projected_successful_soak / min_samples if min_samples else 0.0
import math
projected_loras_merged    = math.floor(projected_loras_trained) / min_loras if min_loras else 0.0

verdict = 'PASS' if projected_loras_merged >= THRESH else 'BLOCK'
margin_note = 'OK' if projected_loras_merged >= MARGIN else 'below_safety_margin'

result = {
    'config_path': str(CONFIG),
    'lookback_days': LOOKBACK,
    'soak_days': SOAK,
    'threshold_min': THRESH,
    'threshold_safety_margin': MARGIN,
    'episodes_window': total,
    'episodes_success_window': success,
    'episodes_per_day': round(episodes_per_day, 4),
    'success_rate': round(success_rate, 4),
    'min_samples_for_train': min_samples,
    'min_loras_for_merge': min_loras,
    'projected_successful_episodes_soak': round(projected_successful_soak, 4),
    'projected_loras_trained_soak': round(projected_loras_trained, 4),
    'projected_loras_merged_soak': round(projected_loras_merged, 4),
    'verdict': verdict,
    'margin_note': margin_note,
}

sys.stdout.write(json.dumps(result, indent=2) + '\n')
sys.exit(0 if verdict == 'PASS' else 2)
"@

$tmpScript = New-TemporaryFile
try {
    $renamed = [System.IO.Path]::ChangeExtension($tmpScript.FullName, ".py")
    Rename-Item -Path $tmpScript.FullName -NewName $renamed
    Set-Content -Path $renamed -Value $py -Encoding UTF8

    $outJson = & python $renamed $Config $LookbackDays $SoakDays $Threshold $SafetyMargin
    $code = $LASTEXITCODE
    Write-Output $outJson

    $parsed = $outJson | ConvertFrom-Json
    Write-Host ""
    Write-Host "=== Throughput pre-check ===" -ForegroundColor Cyan
    Write-Host ("Episodes in {0}-day window:  {1} (successful: {2})" -f $parsed.lookback_days, $parsed.episodes_window, $parsed.episodes_success_window)
    Write-Host ("Episodes/day:                {0}" -f $parsed.episodes_per_day)
    Write-Host ("Success rate:                {0}" -f $parsed.success_rate)
    Write-Host ("Thresholds: train>={0}, merge>={1}" -f $parsed.min_samples_for_train, $parsed.min_loras_for_merge)
    Write-Host ("Projected LoRAs merged ({0}d): {1}" -f $parsed.soak_days, $parsed.projected_loras_merged_soak)
    if ($parsed.verdict -eq 'PASS') {
        Write-Host "Verdict: PASS ($($parsed.margin_note))" -ForegroundColor Green
    } else {
        Write-Host "Verdict: BLOCK" -ForegroundColor Red
        Write-Host "Fix: more bootstrap episodes OR lower [evolution] thresholds."
    }
    exit $code
} finally {
    Remove-Item -Path $renamed -ErrorAction SilentlyContinue
}
