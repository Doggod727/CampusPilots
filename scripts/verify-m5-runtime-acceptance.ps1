#Requires -Version 7.4
<#
.SYNOPSIS
    M5 真实环境总验收（#192）：契约/目录/装配核对 + 四个既有探针 + 冒烟汇总。
.DESCRIPTION
    - 拒绝在已有 Runtime Worker 时启动（前两个探针会自行拉起/停止 Worker）。
    - 顺序执行：m5_acceptance_probe → outbox 并发 → Checkpoint 恢复 → 限流 → Provider 故障。
    - 探针全部通过后启动一个开发 Runtime Worker 并执行完整冒烟，最后保留 Worker 运行。
    - 任一环节失败即抛出稳定错误码；输出公开摘要，不含密钥/连接串/令牌。
#>
[CmdletBinding()]
param(
    [string]$Python = "D:\anaconda\envs\campuspilot\python.exe"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$PSNativeCommandUseErrorActionPreference = $false

$RepositoryRoot = Split-Path -Parent $PSScriptRoot
$BackendRoot = Join-Path $RepositoryRoot "backend"
$Results = [ordered]@{}

function Assert-NoRuntimeWorker {
    $workers = @(
        Get-CimInstance Win32_Process |
            Where-Object {
                $_.ProcessId -ne $PID -and
                $_.CommandLine -and
                $_.CommandLine -match "app\.scripts\.runtime_worker"
            }
    )
    if ($workers.Count -gt 0) {
        throw "M5_ACCEPTANCE_RUNTIME_WORKER_PRESENT"
    }
}

function Invoke-Probe([string]$Name, [string]$Script, [hashtable]$ExtraArgs = @{}) {
    & (Join-Path $RepositoryRoot "scripts\$Script") -Python $Python @ExtraArgs | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "M5_ACCEPTANCE_PROBE_FAILED_$Name"
    }
    $Results[$Name] = $true
}

Assert-NoRuntimeWorker

Push-Location $BackendRoot
try {
    $Output = & $Python -m app.scripts.m5_acceptance_probe 2>&1
    if ($LASTEXITCODE -ne 0) {
        throw "M5_ACCEPTANCE_STATIC_FAILED:$(($Output | Out-String).Trim())"
    }
    $JsonLine = ($Output | Where-Object { "$_".TrimStart().StartsWith('{') } | Select-Object -Last 1)
    $Static = (($JsonLine | Out-String).Trim()) | ConvertFrom-Json
    if (-not $Static.ok) {
        throw "M5_ACCEPTANCE_STATIC_FAILED"
    }
    $Results["contract_operations"] = $Static.contract.operations
    $Results["catalog_tools"] = $Static.catalog.tools
    $Results["catalog_zero_drift"] = [bool]$Static.catalog.catalog_zero_drift
    $Results["real_handlers"] = $Static.catalog.real_handlers
}
finally {
    Pop-Location
}

Invoke-Probe -Name "outbox_concurrency" -Script "verify-runtime-outbox-concurrency.ps1"
Invoke-Probe -Name "checkpoint_recovery" -Script "verify-runtime-checkpoint-recovery.ps1" -ExtraArgs @{ IUnderstandThisCreatesSyntheticDatabaseRecords = $true }
Invoke-Probe -Name "rate_limits" -Script "verify-runtime-rate-limits.ps1"
Invoke-Probe -Name "provider_faults" -Script "verify-deepseek-provider-faults.ps1"

$Worker = $null
Push-Location $BackendRoot
try {
    $Worker = Start-Process -FilePath $Python -ArgumentList "-m", "app.scripts.runtime_worker" -WorkingDirectory $BackendRoot -WindowStyle Hidden -PassThru
    Start-Sleep -Seconds 4
    & pwsh -NoProfile -File (Join-Path $RepositoryRoot "scripts\smoke.ps1") | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "M5_ACCEPTANCE_SMOKE_FAILED"
    }
    $Results["smoke"] = $true
}
finally {
    Pop-Location
}

$Results["ok"] = $true
Write-Output ($Results | ConvertTo-Json -Compress)
