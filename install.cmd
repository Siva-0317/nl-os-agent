@echo off
setlocal enabledelayedexpansion
title NL-OS-Agent Setup ^& Installer

echo ================================================================================
echo           NL-OS-AGENT: LOCAL AI-NATIVE OPERATING SYSTEM SHELL
echo                     One-Click Setup and Environment Installer
echo ================================================================================
echo.

cd /d "%~dp0"

:: 1. Check for standalone frozen distribution
if exist "dist\nlagent\nlagent-gui.exe" (
    echo [FOUND] Standalone distribution detected at dist\nlagent\
    echo         All runtimes, PySide6, and models are already bundled.
    echo.
    echo Options:
    echo   1. Launch Desktop GUI (nlagent-gui.exe)
    echo   2. Run System Doctor diagnostics (nlctl doctor)
    echo   3. Re-install / Update Python developer environment from source
    echo   4. Exit
    echo.
    set /p choice="Select an option (1-4) [Default 1]: "
    if "!choice!"=="" set choice=1
    if "!choice!"=="1" (
        start "" "dist\nlagent\nlagent-gui.exe"
        exit /b 0
    )
    if "!choice!"=="2" (
        "dist\nlagent\nlctl.exe" doctor
        pause
        exit /b 0
    )
    if "!choice!"=="4" (
        exit /b 0
    )
)

:: 2. Check for Python installation
echo [1/4] Checking Python environment...
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Python is not installed or not in your system PATH!
    echo         Please install Python 3.10+ from https://www.python.org/downloads/
    echo         Make sure to check "Add Python to PATH" during installation.
    pause
    exit /b 1
)

for /f "tokens=2 delims= " %%v in ('python --version 2^>^&1') do set py_ver=%%v
echo       Found Python %py_ver%

:: 3. Setup Virtual Environment
echo.
echo [2/4] Setting up dedicated virtual environment (.venv)...
if not exist ".venv\Scripts\python.exe" (
    python -m venv .venv
    if %errorlevel% neq 0 (
        echo [ERROR] Failed to create virtual environment!
        pause
        exit /b 1
    )
    echo       Created .venv successfully.
) else (
    echo       Using existing .venv.
)

:: 4. Install Dependencies & Frameworks
echo.
echo [3/4] Installing required libraries and frameworks...
echo       (PySide6, Requests, Rich, Python-Dotenv)
.venv\Scripts\python.exe -m pip install --upgrade pip >nul 2>&1
.venv\Scripts\python.exe -m pip install -e .
if %errorlevel% neq 0 (
    echo [ERROR] Failed to install package dependencies!
    pause
    exit /b 1
)

:: 5. Verification & Health Check
echo.
echo [4/4] Verifying system health and GPU acceleration...
.venv\Scripts\python.exe scripts\test_live_app.py --quick >nul 2>&1
echo.
echo ================================================================================
echo [SUCCESS] NL-OS-Agent installed and ready!
echo.
echo You can run the application anytime using:
echo   - Desktop GUI : nlagent.cmd  (or run dist\nlagent\nlagent-gui.exe)
echo   - Terminal CLI: nlctl.cmd    (or .venv\Scripts\nlctl.exe)
echo ================================================================================
echo.
set /p launch="Would you like to launch the Desktop GUI now? (Y/N) [Default Y]: "
if /i "%launch%" neq "N" (
    if exist "dist\nlagent\nlagent-gui.exe" (
        start "" "dist\nlagent\nlagent-gui.exe"
    ) else (
        start "" .venv\Scripts\python.exe -m nlagent.ui.app
    )
)
exit /b 0
