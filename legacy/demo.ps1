# demo.ps1 — Setup demo environment and run NL OS Agent in PowerShell

Write-Host "=================================================" -ForegroundColor Cyan
Write-Host "       NL OS Agent Demo - Final Year Project     " -ForegroundColor Cyan
Write-Host "  Powered by NVIDIA Nemotron (LM Studio Endpoint)" -ForegroundColor Cyan
Write-Host "=================================================" -ForegroundColor Cyan
Write-Host ""

$DemoDir = Join-Path $env:TEMP "agent_demo"
if (-not (Test-Path $DemoDir)) {
    New-Item -ItemType Directory -Path $DemoDir -Force | Out-Null
}
Set-Location $DemoDir

"Hello World from NL OS Agent" | Out-File -FilePath "hello.txt" -Encoding utf8
"print('Demonstrating natural language shell translation with Nemotron')" | Out-File -FilePath "script.py" -Encoding utf8
$OldLogsDir = Join-Path $DemoDir "old_logs"
if (-not (Test-Path $OldLogsDir)) {
    New-Item -ItemType Directory -Path $OldLogsDir -Force | Out-Null
}
"2026-10-29: Test error log" | Out-File -FilePath (Join-Path $OldLogsDir "system.log") -Encoding utf8

Write-Host "Demo environment prepared at: $DemoDir" -ForegroundColor Green
Write-Host "Files created: hello.txt, script.py, old_logs/" -ForegroundColor Green
Write-Host ""
Write-Host "Recommended demo walkthrough to rehearse:" -ForegroundColor Yellow
Write-Host "  1. 'show me all files here'               (Auto-executes: ls -la)"
Write-Host "  2. 'find all python files'                (Auto-executes: find . -name '*.py')"
Write-Host "  3. 'create a backup folder'               (State-modifying: mkdir backup)"
Write-Host "  4. 'copy the python file into that folder'(Reference resolution: cp script.py backup/)"
Write-Host "  5. 'delete the old_logs folder'           (Destructive: sandbox simulation + confirmation)"
Write-Host "  6. 'history'                              (Shows SQLite session history)"
Write-Host ""
Write-Host "Launching NL OS Agent..." -ForegroundColor Cyan
Write-Host "-------------------------------------------------"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
python (Join-Path $ScriptDir "agent.py")
