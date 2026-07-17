#Requires -Version 7.4
[CmdletBinding()]
param(
    [string]$Python = "python",
    [ValidateRange(75, 600)]
    [int]$TimeoutSeconds = 150,
    [switch]$IUnderstandThisCreatesSyntheticDatabaseRecords,
    [switch]$KeepProbeRows
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$PSNativeCommandUseErrorActionPreference = $false

if (-not $IUnderstandThisCreatesSyntheticDatabaseRecords) {
    throw "CHECKPOINT_PROBE_EXPLICIT_ACKNOWLEDGEMENT_REQUIRED"
}

$RepositoryRoot = Split-Path -Parent $PSScriptRoot
$BackendRoot = Join-Path $RepositoryRoot "backend"
$Tag = ("{0}-{1}" -f [DateTime]::UtcNow.ToString("yyyyMMddTHHmmssZ").ToLowerInvariant(), [Guid]::NewGuid().ToString("N").Substring(0, 12))
$CrashWorkerId = "checkpoint-crash-$Tag"
$RecoveryWorkerId = "checkpoint-recovery-$Tag"
$ArtifactRoot = Join-Path ([IO.Path]::GetTempPath()) "CampusPilot\runtime-checkpoint\$Tag"
$ManifestPath = Join-Path $ArtifactRoot "manifest.json"
$SummaryPath = Join-Path $ArtifactRoot "summary.json"
$LockReadyPath = Join-Path $ArtifactRoot "lock.ready"
$LockReleasePath = Join-Path $ArtifactRoot "lock.release"
$OwnedProcesses = [Collections.Generic.List[Diagnostics.Process]]::new()
$Succeeded = $false

function Invoke-Probe {
    param([Parameter(Mandatory)][string[]]$Arguments)
    $Output = & $Python -m app.scripts.runtime_checkpoint_probe @Arguments 2>&1
    [pscustomobject]@{
        ExitCode = $LASTEXITCODE
        Text = (($Output | Out-String).Trim())
    }
}

function Stop-OwnedProcess {
    param([Parameter(Mandatory)][Diagnostics.Process]$Process)
    if (-not $Process.HasExited) {
        $Process.Kill($true)
        if (-not $Process.WaitForExit(10000)) {
            throw "CHECKPOINT_PROBE_PROCESS_STOP_TIMEOUT"
        }
    }
}

function Start-RuntimeWorker {
    param(
        [Parameter(Mandatory)][string]$WorkerId,
        [Parameter(Mandatory)][string]$LogPrefix
    )
    $Environment = @{
        AGENT_RUNTIME_WORKER_ID = $WorkerId
        AGENT_RUNTIME_BATCH_SIZE = "1"
        AGENT_RUNTIME_POLL_SECONDS = "0.10"
        AGENT_RUNTIME_CLAIM_TIMEOUT_SECONDS = "60"
        DEEPSEEK_API_KEY = "runtime-checkpoint-probe"
        DEEPSEEK_BASE_URL = "http://127.0.0.1:9"
        PYTHONUNBUFFERED = "1"
    }
    $Parameters = @{
        FilePath = $Python
        ArgumentList = @("-u", "-m", "app.scripts.runtime_worker")
        WorkingDirectory = $BackendRoot
        Environment = $Environment
        WindowStyle = "Hidden"
        RedirectStandardOutput = (Join-Path $ArtifactRoot "$LogPrefix.stdout.log")
        RedirectStandardError = (Join-Path $ArtifactRoot "$LogPrefix.stderr.log")
        PassThru = $true
    }
    $Process = Start-Process @Parameters
    $OwnedProcesses.Add($Process)
    return $Process
}

$ExistingWorkers = @(
    Get-CimInstance Win32_Process |
        Where-Object {
            $_.ProcessId -ne $PID -and
            $_.CommandLine -and
            $_.CommandLine -match "app\.scripts\.runtime_worker"
        }
)
if ($ExistingWorkers.Count -gt 0) {
    throw "CHECKPOINT_PROBE_EXISTING_WORKERS:$((($ExistingWorkers.ProcessId | Sort-Object) -join ','))"
}

New-Item -ItemType Directory -Path $ArtifactRoot -Force | Out-Null
Push-Location $BackendRoot
try {
    $Preflight = Invoke-Probe -Arguments @("preflight")
    if ($Preflight.ExitCode -ne 0 -or -not (($Preflight.Text | ConvertFrom-Json).ok)) {
        throw "CHECKPOINT_PROBE_PREFLIGHT_FAILED:$($Preflight.Text)"
    }

    $Seed = Invoke-Probe -Arguments @("seed", "--tag", $Tag, "--manifest", $ManifestPath)
    if ($Seed.ExitCode -ne 0) {
        throw "CHECKPOINT_PROBE_SEED_FAILED:$($Seed.Text)"
    }

    $Cas = Invoke-Probe -Arguments @("cas", "--manifest", $ManifestPath)
    if ($Cas.ExitCode -ne 0) {
        throw "CHECKPOINT_PROBE_CAS_FAILED:$($Cas.Text)"
    }
    $CasData = $Cas.Text | ConvertFrom-Json

    $LockParameters = @{
        FilePath = $Python
        ArgumentList = @(
            "-u", "-m", "app.scripts.runtime_checkpoint_probe", "hold-lock",
            "--manifest", $ManifestPath,
            "--ready-file", $LockReadyPath,
            "--release-file", $LockReleasePath,
            "--timeout-seconds", "60"
        )
        WorkingDirectory = $BackendRoot
        WindowStyle = "Hidden"
        RedirectStandardOutput = (Join-Path $ArtifactRoot "lock.stdout.log")
        RedirectStandardError = (Join-Path $ArtifactRoot "lock.stderr.log")
        PassThru = $true
    }
    $LockProcess = Start-Process @LockParameters
    $OwnedProcesses.Add($LockProcess)
    $LockDeadline = [DateTime]::UtcNow.AddSeconds(15)
    while (-not (Test-Path -LiteralPath $LockReadyPath)) {
        if ($LockProcess.HasExited -or [DateTime]::UtcNow -ge $LockDeadline) {
            throw "CHECKPOINT_PROBE_LOCK_FAILED"
        }
        Start-Sleep -Milliseconds 100
    }

    $CrashWorker = Start-RuntimeWorker -WorkerId $CrashWorkerId -LogPrefix "crash-worker"
    $ClaimDeadline = [DateTime]::UtcNow.AddSeconds(20)
    $CrashSnapshot = $null
    while ([DateTime]::UtcNow -lt $ClaimDeadline) {
        if ($CrashWorker.HasExited) {
            throw "CHECKPOINT_PROBE_CRASH_WORKER_EXITED"
        }
        $State = Invoke-Probe -Arguments @("snapshot", "--manifest", $ManifestPath)
        if ($State.ExitCode -ne 0) {
            throw "CHECKPOINT_PROBE_SNAPSHOT_FAILED:$($State.Text)"
        }
        $CrashSnapshot = $State.Text | ConvertFrom-Json
        if (
            $CrashSnapshot.command_status -eq "processing" -and
            $CrashSnapshot.command_attempt_count -eq 1 -and
            $CrashSnapshot.command_claimed_by -eq $CrashWorkerId
        ) {
            break
        }
        Start-Sleep -Milliseconds 200
    }
    if (
        $null -eq $CrashSnapshot -or
        $CrashSnapshot.command_status -ne "processing" -or
        $CrashSnapshot.command_claimed_by -ne $CrashWorkerId -or
        $CrashSnapshot.checkpoint_version -ne 2 -or
        $CrashSnapshot.topup_count -ne 0
    ) {
        throw "CHECKPOINT_PROBE_CRASH_BOUNDARY_NOT_REACHED"
    }

    Stop-OwnedProcess -Process $CrashWorker
    [IO.File]::WriteAllText($LockReleasePath, "release", [Text.Encoding]::ASCII)
    if (-not $LockProcess.WaitForExit(10000)) {
        throw "CHECKPOINT_PROBE_LOCK_RELEASE_FAILED"
    }

    $AfterCrash = Invoke-Probe -Arguments @("snapshot", "--manifest", $ManifestPath)
    if ($AfterCrash.ExitCode -ne 0) {
        throw "CHECKPOINT_PROBE_POST_CRASH_FAILED:$($AfterCrash.Text)"
    }
    $AfterCrashData = $AfterCrash.Text | ConvertFrom-Json
    if (
        $AfterCrashData.command_status -ne "processing" -or
        $AfterCrashData.command_claimed_by -ne $CrashWorkerId -or
        $AfterCrashData.checkpoint_version -ne 2 -or
        $AfterCrashData.approval_status -ne "approved" -or
        $AfterCrashData.topup_count -ne 0 -or
        $AfterCrashData.audit_count -ne 0
    ) {
        throw "CHECKPOINT_PROBE_ROLLBACK_INVALID"
    }

    $LeaseDeadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    while ([DateTime]::UtcNow -lt $LeaseDeadline) {
        $LeaseState = Invoke-Probe -Arguments @("snapshot", "--manifest", $ManifestPath)
        if ($LeaseState.ExitCode -ne 0) {
            throw "CHECKPOINT_PROBE_LEASE_STATUS_FAILED:$($LeaseState.Text)"
        }
        $LeaseData = $LeaseState.Text | ConvertFrom-Json
        if ($LeaseData.lease_expired) {
            break
        }
        Start-Sleep -Seconds 1
    }
    if (-not $LeaseData.lease_expired) {
        throw "CHECKPOINT_PROBE_LEASE_TIMEOUT"
    }

    $RecoveryWorker = Start-RuntimeWorker -WorkerId $RecoveryWorkerId -LogPrefix "recovery-worker"
    $RecoveryDeadline = [DateTime]::UtcNow.AddSeconds(45)
    while ([DateTime]::UtcNow -lt $RecoveryDeadline) {
        if ($RecoveryWorker.HasExited) {
            throw "CHECKPOINT_PROBE_RECOVERY_WORKER_EXITED"
        }
        $RecoveryState = Invoke-Probe -Arguments @("snapshot", "--manifest", $ManifestPath)
        if ($RecoveryState.ExitCode -ne 0) {
            throw "CHECKPOINT_PROBE_RECOVERY_STATUS_FAILED:$($RecoveryState.Text)"
        }
        $RecoveryData = $RecoveryState.Text | ConvertFrom-Json
        if ($RecoveryData.command_status -in @("succeeded", "failed")) {
            break
        }
        Start-Sleep -Milliseconds 250
    }

    $Verify = Invoke-Probe -Arguments @(
        "verify", "--manifest", $ManifestPath, "--recovery-worker", $RecoveryWorkerId
    )
    if ($Verify.ExitCode -ne 0) {
        throw "CHECKPOINT_PROBE_VERIFICATION_FAILED:$($Verify.Text)"
    }
    $FinalData = $Verify.Text | ConvertFrom-Json
    $Summary = [ordered]@{
        ok = $true
        tag = $Tag
        lease_seconds = 60
        cas = $CasData
        crash_claim = [ordered]@{
            worker = $CrashWorkerId
            attempt_count = $CrashSnapshot.command_attempt_count
            checkpoint_version = $CrashSnapshot.checkpoint_version
            topup_count = $CrashSnapshot.topup_count
        }
        post_crash = [ordered]@{
            command_status = $AfterCrashData.command_status
            checkpoint_version = $AfterCrashData.checkpoint_version
            approval_status = $AfterCrashData.approval_status
            topup_count = $AfterCrashData.topup_count
        }
        final = $FinalData
    }
    $SummaryJson = $Summary | ConvertTo-Json -Depth 8 -Compress
    [IO.File]::WriteAllText($SummaryPath, $SummaryJson, [Text.UTF8Encoding]::new($false))
    $Succeeded = $true
    Write-Output $SummaryJson
    Write-Output "summary=$SummaryPath"
}
finally {
    if (Test-Path -LiteralPath $LockReadyPath) {
        [IO.File]::WriteAllText($LockReleasePath, "release", [Text.Encoding]::ASCII)
    }
    foreach ($Process in $OwnedProcesses) {
        Stop-OwnedProcess -Process $Process
    }
    if (-not $KeepProbeRows) {
        $Cleanup = Invoke-Probe -Arguments @("cleanup", "--tag", $Tag)
        if ($Cleanup.ExitCode -ne 0) {
            Write-Warning "CHECKPOINT_PROBE_CLEANUP_FAILED"
        }
    }
    Pop-Location

    Get-ChildItem -LiteralPath $ArtifactRoot -Filter "*.log" -ErrorAction SilentlyContinue |
        Remove-Item -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $LockReadyPath,$LockReleasePath -Force -ErrorAction SilentlyContinue
    if (-not $KeepProbeRows) {
        Remove-Item -LiteralPath $ManifestPath -Force -ErrorAction SilentlyContinue
    }
    if (-not $Succeeded) {
        Write-Warning "Checkpoint probe failed; raw logs were deleted and no sensitive output was printed."
    }
}
