#Requires -Version 7.0
<#
.SYNOPSIS
    Standalone throughput pre-check per SOAK-PROTOCOL §2.2.

.DESCRIPTION
    Thin wrapper around `python -m homunculus.cli autonomy-precheck`.
    Exits 0 if gate clears (projected_loras_merged_soak >= threshold_min),
    exits 2 if blocked, exits 1 for tool errors.
#>
param(
    [string]$Config        = "homunculus.toml",
    [double]$Threshold     = 1.0,
    [double]$SafetyMargin  = 1.5,
    [int]   $LookbackDays  = 14,
    [int]   $SoakDays      = 7,
    [switch]$Json
)

$ErrorActionPreference = "Stop"

$cliArgs = @(
    "-m","homunculus.cli","autonomy-precheck",
    "--config",$Config,
    "--lookback-days",$LookbackDays,
    "--soak-days",$SoakDays,
    "--threshold-min",$Threshold,
    "--safety-margin",$SafetyMargin
)
if ($Json) { $cliArgs += "--json" }

& python @cliArgs
exit $LASTEXITCODE
