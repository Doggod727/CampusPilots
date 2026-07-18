#Requires -Version 7.4
[CmdletBinding()]
param(
    [string]$Python = "python",
    [ValidateRange(20, 200)]
    [int]$CommandCount = 40,
    [ValidateRange(10, 600)]
    [int]$TimeoutSeconds = 120,
    [switch]$KeepArtifacts,
    [switch]$KeepProbeRows
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$PSNativeCommandUseErrorActionPreference = $false

$RepositoryRoot = Split-Path -Parent $PSScriptRoot
$BackendRoot = Join-Path $RepositoryRoot "backend"
$Tag = ("{0}-{1}" -f [DateTime]::UtcNow.ToString("yyyyMMddTHHmmssZ").ToLowerInvariant(), [Guid]::NewGuid().ToString("N").Substring(0, 12))
$WorkerIds = @("runtime-probe-$Tag-a", "runtime-probe-$Tag-b")
$ArtifactRoot = Join-Path ([IO.Path]::GetTempPath()) "CampusPilot\runtime-outbox\$Tag"
$ManifestPath = Join-Path $ArtifactRoot "manifest.json"
$SummaryPath = Join-Path $ArtifactRoot "summary.json"
$Processes = [Collections.Generic.List[Diagnostics.Process]]::new()
$Succeeded = $false

function Invoke-Probe {
    param([Parameter(Mandatory)][string[]]$Arguments)
    $Output = & $Python -m app.scripts.runtime_outbox_probe @Arguments 2>&1
    $ExitCode = $LASTEXITCODE
    [pscustomobject]@{
        ExitCode = $ExitCode
        Text = (($Output | Out-String).Trim())
    }
}

function Stop-OwnedProcess {
    param([Parameter(Mandatory)][Diagnostics.Process]$Process)
    if (-not $Process.HasExited) {
        $Process.Kill($true)
        if (-not $Process.WaitForExit(10000)) {
            throw "RUNTIME_PROBE_PROCESS_STOP_TIMEOUT"
        }
    }
}

New-Item -ItemType Directory -Path $ArtifactRoot -Force | Out-Null

$ExistingWorkers = @(
    Get-CimInstance Win32_Process |
        Where-Object {
            $_.ProcessId -ne $PID -and
            $_.CommandLine -and
            $_.CommandLine -match "app\.scripts\.runtime_worker"
        }
)
if ($ExistingWorkers.Count -gt 0) {
    $ExistingPids = ($ExistingWorkers.ProcessId | Sort-Object) -join ","
    throw "RUNTIME_PROBE_EXISTING_WORKERS:$ExistingPids"
}

Push-Location $BackendRoot
try {
    $Preflight = Invoke-Probe -Arguments @("preflight")
    if ($Preflight.ExitCode -ne 0) {
        throw "RUNTIME_PROBE_PREFLIGHT_FAILED:$($Preflight.Text)"
    }
    $PreflightData = $Preflight.Text | ConvertFrom-Json
    if (-not $PreflightData.ok) {
        throw "RUNTIME_PROBE_ACTIVE_COMMANDS_PRESENT"
    }

    for ($Index = 0; $Index -lt $WorkerIds.Count; $Index++) {
        $StdoutPath = Join-Path $ArtifactRoot "worker-$($Index + 1).stdout.log"
        $StderrPath = Join-Path $ArtifactRoot "worker-$($Index + 1).stderr.log"
        $Environment = @{
            AGENT_RUNTIME_WORKER_ID = $WorkerIds[$Index]
            AGENT_RUNTIME_POLL_SECONDS = "0.10"
            AGENT_RUNTIME_CLAIM_TIMEOUT_SECONDS = "300"
            DEEPSEEK_API_KEY = "runtime-outbox-probe"
            DEEPSEEK_BASE_URL = "http://127.0.0.1:9"
            PYTHONUNBUFFERED = "1"
        }
        $StartParameters = @{
            FilePath = $Python
            ArgumentList = @("-u", "-m", "app.scripts.runtime_worker")
            WorkingDirectory = $BackendRoot
            Environment = $Environment
            WindowStyle = "Hidden"
            RedirectStandardOutput = $StdoutPath
            RedirectStandardError = $StderrPath
            PassThru = $true
        }
        $Process = Start-Process @StartParameters
        $Processes.Add($Process)
    }

    Start-Sleep -Seconds 2
    if ($Processes.Where({ $_.HasExited }).Count -gt 0) {
        throw "RUNTIME_PROBE_WORKER_START_FAILED"
    }

    $Seed = Invoke-Probe -Arguments @(
        "seed", "--tag", $Tag, "--count", $CommandCount.ToString(), "--manifest", $ManifestPath
    )
    if ($Seed.ExitCode -ne 0) {
        throw "RUNTIME_PROBE_SEED_FAILED:$($Seed.Text)"
    }

    $Deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    while ([DateTime]::UtcNow -lt $Deadline) {
        if ($Processes.Where({ $_.HasExited }).Count -gt 0) {
            throw "RUNTIME_PROBE_WORKER_EXITED"
        }
        $Status = Invoke-Probe -Arguments @("status", "--manifest", $ManifestPath)
        if ($Status.ExitCode -ne 0) {
            throw "RUNTIME_PROBE_STATUS_FAILED:$($Status.Text)"
        }
        $StatusData = $Status.Text | ConvertFrom-Json
        if ($StatusData.terminal_command_count -eq $StatusData.expected_command_count) {
            break
        }
        Start-Sleep -Milliseconds 250
    }

    $FinalStatus = Invoke-Probe -Arguments @("status", "--manifest", $ManifestPath)
    if ($FinalStatus.ExitCode -ne 0) {
        throw "RUNTIME_PROBE_STATUS_FAILED:$($FinalStatus.Text)"
    }
    $FinalStatusData = $FinalStatus.Text | ConvertFrom-Json
    if ($FinalStatusData.terminal_command_count -ne $FinalStatusData.expected_command_count) {
        throw "RUNTIME_PROBE_TIMEOUT"
    }

    $VerifyArguments = @("verify", "--manifest", $ManifestPath)
    foreach ($WorkerId in $WorkerIds) {
        $VerifyArguments += @("--worker", $WorkerId)
    }
    $Verification = Invoke-Probe -Arguments $VerifyArguments
    [IO.File]::WriteAllText($SummaryPath, $Verification.Text, [Text.UTF8Encoding]::new($false))
    if ($Verification.ExitCode -ne 0) {
        throw "RUNTIME_PROBE_VERIFICATION_FAILED:$($Verification.Text)"
    }
    $Succeeded = $true
    Write-Output $Verification.Text
    Write-Output "summary=$SummaryPath"
}
finally {
    foreach ($Process in $Processes) {
        Stop-OwnedProcess -Process $Process
    }

    if ((Test-Path -LiteralPath $ManifestPath) -and -not $KeepProbeRows) {
        $Cleanup = Invoke-Probe -Arguments @("cleanup", "--manifest", $ManifestPath)
        if ($Cleanup.ExitCode -ne 0) {
            Write-Warning "RUNTIME_PROBE_CLEANUP_FAILED"
        }
    }
    Pop-Location

    if ($Succeeded -and -not $KeepArtifacts) {
        Get-ChildItem -LiteralPath $ArtifactRoot -Filter "worker-*.log" -ErrorAction SilentlyContinue |
            Remove-Item -Force -ErrorAction SilentlyContinue
        Remove-Item -LiteralPath $ManifestPath -Force -ErrorAction SilentlyContinue
    }
    elseif (-not $Succeeded) {
        Write-Warning "Runtime probe artifacts retained at $ArtifactRoot; raw logs were not printed."
    }
}
