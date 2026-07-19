#Requires -Version 7.0
<#
.SYNOPSIS
    启动 CampusPilot 本地全栈开发环境（幂等）。
.DESCRIPTION
    - PostgreSQL：E:\CampusPilotServices\PostgreSQL（已运行则跳过）
    - Redis：本机 6379 可达则复用，否则启动 E 盘便携实例
    - API + runtime/ingestion/training/evaluation 四个 Worker：
      已存在同名进程时跳过（重复启动不产生重复进程）
    - 全部输出重定向到 logs/（本机忽略目录，不回显密钥）
.EXAMPLE
    pwsh -File scripts/start-dev.ps1
#>
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'

$RepoRoot = Split-Path -Parent $PSScriptRoot
$Backend = Join-Path $RepoRoot 'backend'
$LogDir = Join-Path $RepoRoot 'logs'
$Python = 'D:\anaconda\envs\campuspilot\python.exe'
$PgBin = 'E:\CampusPilotServices\PostgreSQL\pgsql\bin'
$PgData = 'E:\CampusPilotServices\PostgreSQL\data'
$RedisDir = 'E:\CampusPilotServices\Redis'

if (-not (Test-Path $Python)) { throw "conda 环境不存在: $Python" }
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

# 1. PostgreSQL（已在运行则跳过）
& (Join-Path $PgBin 'pg_ctl.exe') -D $PgData status *> $null
if ($LASTEXITCODE -eq 0) {
    Write-Host '[=] PostgreSQL 已在运行' -ForegroundColor DarkGray
} else {
    Start-Process -FilePath (Join-Path $PgBin 'postgres.exe') -ArgumentList '-D', $PgData -WindowStyle Hidden
    Start-Sleep -Seconds 4
    & (Join-Path $PgBin 'pg_ctl.exe') -D $PgData status *> $null
    if ($LASTEXITCODE -ne 0) { throw 'PostgreSQL 启动失败，日志见数据目录 log' }
    Write-Host '[+] PostgreSQL 已启动' -ForegroundColor Green
}

# 2. Redis（6379 可达则复用，否则启动便携实例）
$redisUp = Test-NetConnection -ComputerName 127.0.0.1 -Port 6379 -InformationLevel Quiet -WarningAction SilentlyContinue
if ($redisUp) {
    Write-Host '[=] Redis 6379 已可达（复用本机服务）' -ForegroundColor DarkGray
} else {
    Start-Process -FilePath (Join-Path $RedisDir 'redis-server.exe') -ArgumentList '--port 6379' -WorkingDirectory $RedisDir -WindowStyle Hidden
    Write-Host '[+] Redis 便携实例已启动' -ForegroundColor Green
}

# 3. API + Workers（幂等：同名进程存在则跳过；日志进 logs/）
$processes = @(
    @{ Name = 'api';                Module = 'uvicorn';                    Args = @('app.main:app', '--host', '127.0.0.1', '--port', '8000') },
    @{ Name = 'runtime-worker';     Module = 'app.scripts.runtime_worker' },
    @{ Name = 'training-worker';    Module = 'app.scripts.training_worker' },
    @{ Name = 'evaluation-worker';  Module = 'app.scripts.evaluation_worker' }
)
foreach ($p in $processes) {
    $pattern = [regex]::Escape($p.Module)
    $existing = Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
        Where-Object { $_.CommandLine -match $pattern -and $_.CommandLine -match 'campuspilot|app\.main|app\.scripts' }
    if ($existing) {
        Write-Host "[=] $($p.Name) 已在运行（PID $($existing[0].ProcessId)），跳过" -ForegroundColor DarkGray
        continue
    }
    $arguments = @('-u', '-m', $p.Module) + @($p.Args)
    $outLog = Join-Path $LogDir "$($p.Name).log"
    $errLog = Join-Path $LogDir "$($p.Name).err.log"
    Start-Process -FilePath $Python -ArgumentList $arguments -WorkingDirectory $Backend `
        -WindowStyle Hidden -RedirectStandardOutput $outLog -RedirectStandardError $errLog
    Write-Host "[+] $($p.Name) 已启动（日志 logs/$($p.Name).log）" -ForegroundColor Green
}

# 入库 Worker 为一次性排空设计：以监督循环周期执行（幂等匹配含循环进程）
$ingestionExisting = Get-CimInstance Win32_Process |
    Where-Object { $_.CommandLine -match 'ingestion-worker-loop\.ps1' -and $_.ProcessId -ne $PID }
if ($ingestionExisting) {
    Write-Host "[=] ingestion-worker 已在运行，跳过" -ForegroundColor DarkGray
} else {
    $loopScript = Join-Path $PSScriptRoot 'ingestion-worker-loop.ps1'
    Start-Process -FilePath 'pwsh' -ArgumentList '-NoProfile', '-File', $loopScript -WindowStyle Hidden
    Write-Host "[+] ingestion-worker 已启动（15s 监督循环，日志 logs/ingestion-worker.log）" -ForegroundColor Green
}

Write-Host ''
Write-Host '启动完成。验证：pwsh -File scripts/status-dev.ps1；停止：pwsh -File scripts/stop-dev.ps1' -ForegroundColor Cyan
