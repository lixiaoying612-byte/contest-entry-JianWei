@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
if exist "python\python.exe" (
    "python\python.exe" -X utf8 -I "scripts\open_competition.py" %*
    if errorlevel 1 (
        pause
        exit /b 1
    )
    exit /b 0
)
if not exist ".venv\Scripts\python.exe" (
    py -3.12 -m venv .venv
    if errorlevel 1 (
        echo 请安装 Python 3.12，或解压 01_可运行原型系统中的 Windows 运行包。
        pause
        exit /b 1
    )
)
".venv\Scripts\python.exe" -c "import psutil, numpy, bm25s" >nul 2>nul
if errorlevel 1 (
    ".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -r requirements.txt
    if errorlevel 1 (
        echo 依赖安装失败。请检查网络，或使用自带环境的 Windows 运行包。
        pause
        exit /b 1
    )
)
".venv\Scripts\python.exe" -X utf8 -I "scripts\open_competition.py" %*
if errorlevel 1 (
    pause
    exit /b 1
)
