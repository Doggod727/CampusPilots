<#
.SYNOPSIS
    启动 CampusPilot 本地全栈开发环境（幂等）。
.DESCRIPTION
    - PostgreSQL + Redis：通过 Docker Compose 启动（已运行则跳过）
    - API + runtime/ingestion/training/evaluation 四个 Worker：
      已存在同名进程时跳过（重复启动不产生重复进程）
    - 全部输出重定向到 logs/（本机忽略目录，不回显密钥）
.EXAMPLE
    powershell -File scripts/start-dev.ps1
#>
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'

$RepoRoot = Split-Path -Parent $PSScriptRoot
$Backend = Join-Path $RepoRoot 'backend'
$LogDir = Join-Path $RepoRoot 'logs'

# 自动探测 conda Python 路径
$condaInfo = conda info --json 2>$null | ConvertFrom-Json
$envDirs = $condaInfo.envs_dirs
$Python = $null
foreach ($base in $envDirs) {
    $candidate = Join-Path $base 'campuspilot\python.exe'
    if (Test-Path $candidate) { $Python = $candidate; break }
}
if (-not $Python) { throw "conda 环境 campuspilot 未找到（已扫描 envs_dirs）" }
Write-Host "[=] Python: $Python" -ForegroundColor DarkGray

if (-not (Test-Path $Python)) { throw "conda 环境不存在: $Python" }
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

# 1. PostgreSQL + Redis（Docker Compose，幂等）
Write-Host '[ ] 检查 PostgreSQL 与 Redis (Docker Compose)...' -ForegroundColor DarkGray
docker compose up -d db redis 2>&1 | Out-Null
Start-Sleep -Seconds 3
$pgOk = Test-NetConnection -ComputerName 127.0.0.1 -Port 5432 -InformationLevel Quiet -WarningAction SilentlyContinue
$redisOk = Test-NetConnection -ComputerName 127.0.0.1 -Port 6379 -InformationLevel Quiet -WarningAction SilentlyContinue
if ($pgOk) { Write-Host '[+] PostgreSQL 5432 已就绪' -ForegroundColor Green } else { throw 'PostgreSQL 5432 未就绪' }
if ($redisOk) { Write-Host '[+] Redis 6379 已就绪' -ForegroundColor Green } else { throw 'Redis 6379 未就绪' }

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
Write-Host '启动完成。验证：powershell -File scripts/status-dev.ps1；停止：powershell -File scripts/stop-dev.ps1' -ForegroundColor Cyan
