[CmdletBinding()]
param()

$ErrorActionPreference = 'Continue'
$RepoRoot = Split-Path -Parent $PSScriptRoot
$Backend = Join-Path $RepoRoot 'backend'
$Log = Join-Path $RepoRoot 'logs\ingestion-worker.log'

$condaInfo = conda info --json 2>$null | ConvertFrom-Json
$envDirs = $condaInfo.envs_dirs
$Python = $null
foreach ($base in $envDirs) {
    $candidate = Join-Path $base 'campuspilot\python.exe'
    if (Test-Path $candidate) { $Python = $candidate; break }
}
if (-not $Python) { throw "conda 环境 campuspilot 未找到" }

Set-Location $Backend
while ($true) {
    & $Python -u -m app.scripts.ingestion_worker *>> $Log
    Start-Sleep -Seconds 15
}
