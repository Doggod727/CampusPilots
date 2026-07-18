#Requires -Version 7.4
[CmdletBinding()]
param(
    [string]$Python = "D:\anaconda\envs\campuspilot\python.exe"
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
    throw "PROVIDER_FAULT_PROBE_RUNTIME_WORKER_PRESENT"
}

Push-Location $BackendRoot
try {
    $Output = & $Python -m app.scripts.deepseek_provider_fault_probe 2>&1
    $ExitCode = $LASTEXITCODE
    $JsonLine = ($Output | Where-Object { "$_".TrimStart().StartsWith('{') } | Select-Object -Last 1)
    if ($ExitCode -ne 0) {
        throw "PROVIDER_FAULT_PROBE_FAILED:$JsonLine"
    }
    $Text = (($JsonLine | Out-String).Trim())
    $Result = $Text | ConvertFrom-Json
    if (-not $Result.ok) {
        throw "PROVIDER_FAULT_PROBE_VERIFICATION_FAILED"
    }
    Write-Output $Text
}
finally {
    Pop-Location
}
