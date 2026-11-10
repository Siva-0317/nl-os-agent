@echo off
setlocal
echo =================================================
echo        NL OS Agent Demo - Final Year Project     
echo   Powered by NVIDIA Nemotron (LM Studio Endpoint)
echo =================================================
echo.

set DEMO_DIR=%TEMP%\agent_demo
if not exist "%DEMO_DIR%" mkdir "%DEMO_DIR%"
cd /d "%DEMO_DIR%"

echo Hello World from NL OS Agent > hello.txt
echo print('Demonstrating natural language shell translation with Nemotron') > script.py
if not exist "old_logs" mkdir old_logs
echo 2026-10-29: Test error log > old_logs\system.log

echo Demo environment prepared at: %DEMO_DIR%
echo Files created: hello.txt, script.py, old_logs/
echo.
echo Recommended demo walkthrough to rehearse:
echo   1. "show me all files here"               (Auto-executes: ls -la)
echo   2. "find all python files"                (Auto-executes: find . -name "*.py")
echo   3. "create a backup folder"               (State-modifying: mkdir backup)
echo   4. "copy the python file into that folder"(Reference resolution: cp script.py backup/)
echo   5. "delete the old_logs folder"           (Destructive: sandbox simulation + confirmation)
echo   6. "history"                              (Shows SQLite session history)
echo.
echo Launching NL OS Agent...
echo -------------------------------------------------

set SCRIPT_DIR=%~dp0
python "%SCRIPT_DIR%agent.py"
