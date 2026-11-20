@echo off
if exist "%~dp0dist\nlagent\nlctl.exe" (
    "%~dp0dist\nlagent\nlctl.exe" %*
) else if exist "%~dp0.venv\Scripts\python.exe" (
    "%~dp0.venv\Scripts\python.exe" -m nlagent.cli.main %*
) else (
    python "%~dp0src\nlagent\cli\main.py" %*
)
