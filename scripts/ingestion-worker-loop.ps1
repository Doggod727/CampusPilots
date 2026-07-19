#Requires -Version 7.0
[CmdletBinding()]
param()

$ErrorActionPreference = 'Continue'
$RepoRoot = Split-Path -Parent $PSScriptRoot
$Backend = Join-Path $RepoRoot 'backend'
$Python = 'D:\anaconda\envs\campuspilot\python.exe'
$Log = Join-Path $RepoRoot 'logs\ingestion-worker.log'

Set-Location $Backend
while ($true) {
    & $Python -u -m app.scripts.ingestion_worker *>> $Log
    Start-Sleep -Seconds 15
}
