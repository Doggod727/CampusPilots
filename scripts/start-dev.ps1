#Requires -Version 7.0
<#
.SYNOPSIS
    启动 CampusPilot 本地全栈开发环境（API + 三个 Worker + PostgreSQL + Redis）。
.DESCRIPTION
    - PostgreSQL：E:\CampusPilotServices\PostgreSQL（pg_ctl 便携实例，已运行则跳过）
    - Redis：优先使用本机 6379 已有服务（Memurai）；不可达时启动 E 盘便携实例
    - API 与 runtime/evaluation/ingestion 三个 Worker 各自在新 pwsh 窗口运行，日志可见
.EXAMPLE
    pwsh -File scripts/start-dev.ps1
#>
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'

$RepoRoot = Split-Path -Parent $PSScriptRoot
$Backend = Join-Path $RepoRoot 'backend'
$Python = 'D:\anaconda\envs\campuspilot\python.exe'
$PgBin = 'E:\CampusPilotServices\PostgreSQL\pgsql\bin'
$PgData = 'E:\CampusPilotServices\PostgreSQL\data'
$PgLog = 'E:\CampusPilotServices\PostgreSQL\server.log'
$RedisDir = 'E:\CampusPilotServices\Redis'

if (-not (Test-Path $Python)) { throw "conda 环境不存在: $Python（先执行 conda create -n campuspilot python=3.12）" }

# 1. PostgreSQL
& (Join-Path $PgBin 'pg_ctl.exe') -D $PgData status *> $null
if ($LASTEXITCODE -eq 0) {
    Write-Host '[=] PostgreSQL 已在运行' -ForegroundColor DarkGray
} else {
    & (Join-Path $PgBin 'pg_ctl.exe') -D $PgData -l $PgLog start | Out-Host
    Write-Host '[+] PostgreSQL 已启动' -ForegroundColor Green
}

# 2. Redis（6379 可达则复用本机服务，否则启动便携实例）
$redisUp = Test-NetConnection -ComputerName 127.0.0.1 -Port 6379 -InformationLevel Quiet -WarningAction SilentlyContinue
if ($redisUp) {
    Write-Host '[=] Redis 6379 已可达（复用本机服务）' -ForegroundColor DarkGray
} else {
    Start-Process -FilePath (Join-Path $RedisDir 'redis-server.exe') -ArgumentList '--port 6379' -WorkingDirectory $RedisDir -WindowStyle Minimized
    Write-Host '[+] Redis 便携实例已启动' -ForegroundColor Green
}

# 3. API + Workers（各开新窗口，关闭窗口即停止对应进程）
$processes = @(
    @{ Name = 'api';              Args = '-m uvicorn app.main:app --host 127.0.0.1 --port 8000' },
    @{ Name = 'runtime-worker';   Args = '-m app.scripts.runtime_worker' },
    @{ Name = 'evaluation-worker'; Args = '-m app.scripts.evaluation_worker' },
    @{ Name = 'ingestion-worker'; Args = '-m app.scripts.ingestion_worker' }
)
foreach ($p in $processes) {
    $command = "Set-Location '$Backend'; & '$Python' $($p.Args)"
    Start-Process -FilePath 'pwsh' -ArgumentList '-NoExit', '-Command', $command -WorkingDirectory $Backend
    Write-Host "[+] $($p.Name) 已在新窗口启动" -ForegroundColor Green
}

Write-Host ''
Write-Host '全部进程已启动。健康检查：' -ForegroundColor Cyan
Write-Host '  GET http://127.0.0.1:8000/health/live'
Write-Host '  GET http://127.0.0.1:8000/health/ready'
Write-Host '停止：关闭各进程窗口；PostgreSQL 用 pg_ctl -D ' $PgData ' stop'
