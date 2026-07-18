#Requires -Version 7.0
<#
.SYNOPSIS
    幂等停止 CampusPilot 应用进程（API + 四个 Worker）。
.DESCRIPTION
    只停止 app.main / app.scripts.*worker 进程；不触碰共享的 PostgreSQL 与 Redis。
    重复执行安全（无匹配进程时直接报告）。
.EXAMPLE
    pwsh -File scripts/stop-dev.ps1
#>
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'

$patterns = @(
    @{ Name = 'api';               Pattern = 'app\.main:app' },
    @{ Name = 'runtime-worker';    Pattern = 'app\.scripts\.runtime_worker' },
    @{ Name = 'ingestion-worker';  Pattern = 'app\.scripts\.ingestion_worker' },
    @{ Name = 'training-worker';   Pattern = 'app\.scripts\.training_worker' },
    @{ Name = 'evaluation-worker'; Pattern = 'app\.scripts\.evaluation_worker' }
)

foreach ($p in $patterns) {
    $targets = Get-CimInstance Win32_Process |
        Where-Object { $_.CommandLine -match $p.Pattern -and $_.ProcessId -ne $PID }
    if (-not $targets) {
        Write-Host "[=] $($p.Name) 未在运行" -ForegroundColor DarkGray
        continue
    }
    foreach ($target in $targets) {
        Stop-Process -Id $target.ProcessId -Force
        Write-Host "[-] $($p.Name) 已停止（PID $($target.ProcessId)）" -ForegroundColor Yellow
    }
}

Write-Host '应用进程已全部停止（PostgreSQL 与 Redis 保持运行）。' -ForegroundColor Cyan
