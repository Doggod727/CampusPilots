#Requires -Version 7.4
[CmdletBinding()]
param(
    [string]$Python = "python"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$PSNativeCommandUseErrorActionPreference = $false

$RepositoryRoot = Split-Path -Parent $PSScriptRoot
$BackendRoot = Join-Path $RepositoryRoot "backend"

$ExistingWorkers = @(
    Get-CimInstance Win32_Process |
        Where-Object {
            $_.ProcessId -ne $PID -and
            $_.CommandLine -and
            $_.CommandLine -match "app\.scripts\.runtime_worker"
        }
)
if ($ExistingWorkers.Count -gt 0) {
    throw "RATE_LIMIT_PROBE_RUNTIME_WORKER_PRESENT"
}

Push-Location $BackendRoot
try {
    $Output = & $Python -m app.scripts.runtime_rate_limit_probe 2>&1
    $ExitCode = $LASTEXITCODE
    if ($ExitCode -ne 0) {
        throw "RATE_LIMIT_PROBE_FAILED:$((($Output | Out-String).Trim()))"
    }
    $Text = (($Output | Out-String).Trim())
    $Result = $Text | ConvertFrom-Json
    if (-not $Result.ok) {
        throw "RATE_LIMIT_PROBE_VERIFICATION_FAILED"
    }
    Write-Output $Text
}
finally {
    Pop-Location
}
