<#
.SYNOPSIS
    CampusPilot 本地开发环境状态检查：进程 + 就绪探针。
.EXAMPLE
    powershell -File scripts/status-dev.ps1
#>
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'

$checks = @(
    @{ Name = 'api';               Pattern = 'app\.main:app' },
    @{ Name = 'runtime-worker';    Pattern = 'app\.scripts\.runtime_worker' },
    @{ Name = 'ingestion-worker';  Pattern = 'ingestion-worker-loop\.ps1' },
    @{ Name = 'training-worker';   Pattern = 'app\.scripts\.training_worker' },
    @{ Name = 'evaluation-worker'; Pattern = 'app\.scripts\.evaluation_worker' }
)

foreach ($c in $checks) {
    $found = Get-CimInstance Win32_Process |
        Where-Object { $_.CommandLine -match $c.Pattern -and $_.ProcessId -ne $PID } |
        Select-Object -First 1
    if ($found) {
        Write-Host ("[UP]   {0,-18} PID {1}" -f $c.Name, $found.ProcessId) -ForegroundColor Green
    } else {
        Write-Host ("[DOWN] {0,-18}" -f $c.Name) -ForegroundColor DarkGray
    }
}

$pgOk = Test-NetConnection -ComputerName 127.0.0.1 -Port 5432 -InformationLevel Quiet -WarningAction SilentlyContinue
Write-Host ("[{0}] postgresql" -f $(if ($pgOk) { 'UP  ' } else { 'DOWN' }))
$redis = Test-NetConnection -ComputerName 127.0.0.1 -Port 6379 -InformationLevel Quiet -WarningAction SilentlyContinue
Write-Host ("[{0}] redis:6379" -f $(if ($redis) { 'UP  ' } else { 'DOWN' }))

try {
    $ready = Invoke-RestMethod -Uri 'http://127.0.0.1:8000/health/ready' -TimeoutSec 10
    $deps = $ready.data.dependencies.PSObject.Properties | ForEach-Object { "$($_.Name)=$($_.Value.status)" }
    Write-Host ("[READY] {0} ({1})" -f $ready.data.status, ($deps -join ', ')) -ForegroundColor Cyan
} catch {
    Write-Host '[DOWN] /health/ready 不可达' -ForegroundColor DarkGray
}
