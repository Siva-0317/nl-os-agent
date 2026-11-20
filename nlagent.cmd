@echo off
if exist "%~dp0dist\nlagent\nlagent-gui.exe" (
    start "" "%~dp0dist\nlagent\nlagent-gui.exe" %*
) else if exist "%~dp0.venv\Scripts\python.exe" (
    start "" "%~dp0.venv\Scripts\python.exe" -m nlagent.ui.app %*
) else (
    python "%~dp0src\nlagent\ui\app.py" %*
)
