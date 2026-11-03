@echo off
REM ============================================================================
REM  Minato Downloader - Telegram Downloader Bot + Website
REM  Double-click this file to run the FULL project (bot + website).
REM
REM  FIRST RUN ONLY:
REM    - Checks Python, creates .venv, installs all packages from
REM      requirements.txt ONCE. If .venv already exists, install is skipped.
REM  EVERY RUN AFTER THAT:
REM    - Just starts the bot and the website and keeps them running.
REM    - Close the two new windows to stop them.
REM
REM  BOT TOKEN IS NEVER SAVED IN THIS FILE OR IN THE CODE.
REM  Set it once in Windows (recommended, one time only):
REM      setx BOT_TOKEN "YOUR_BOT_TOKEN_HERE"
REM  Then close and reopen this file. Or, if BOT_TOKEN is not set, this
REM  file will ask you for it for THIS RUN ONLY (it will not be saved).
REM ============================================================================
setlocal
cd /d "%~dp0"
title Minato Downloader - Launcher

echo ============================================================
echo  Minato Downloader - Launcher
echo ============================================================

REM --- 1. Check Python ---
where python >nul 2>nul
if errorlevel 1 (
    echo [ERROR] Python is not installed or not in PATH.
    echo Please install Python 3.10+ from https://www.python.org/downloads/
    echo and tick "Add python.exe to PATH" during install, then run this again.
    pause
    exit /b 1
)

REM --- 2. First-time setup: create .venv and install packages ONCE ---
if not exist ".venv\Scripts\python.exe" (
    echo [SETUP] First run detected - creating .venv (this takes a while, once only)...
    python -m venv .venv
    if errorlevel 1 (
        echo [ERROR] Could not create .venv.
        pause
        exit /b 1
    )
    echo [SETUP] Installing packages from requirements.txt (once only)...
    .venv\Scripts\python.exe -m pip install --upgrade pip
    .venv\Scripts\python.exe -m pip install -r requirements.txt
    if errorlevel 1 (
        echo [ERROR] Package install failed. Check your internet and run again.
        pause
        exit /b 1
    )
    echo [SETUP] Done. Next time this file will skip install and just run.
) else (
    echo [OK] .venv found - skipping install.
)

REM --- 3. Folders the bot/website need ---
if not exist "data" mkdir "data"
if not exist "downloads" mkdir "downloads"
if not exist "web_downloads" mkdir "web_downloads"

REM --- 4. Database: bot and website MUST share the same file ---
set "DATABASE_URL=sqlite:///data/bot.db"

REM --- 5. Bot token: from Windows env, or ask for this run only (NOT saved) ---
if "%BOT_TOKEN%"=="" (
    echo.
    echo [INFO] BOT_TOKEN is not set.
    echo To set it permanently (once), close this and run in Command Prompt:
    echo   setx BOT_TOKEN "YOUR_BOT_TOKEN_HERE"
    echo Or enter it now for THIS RUN ONLY (it will NOT be saved to any file):
    set /p "BOT_TOKEN=Enter bot token: "
)
if "%BOT_TOKEN%"=="" (
    echo [ERROR] No bot token given. Bot cannot start without it.
    pause
    exit /b 1
)

REM --- 6. ffmpeg check (needed for video+audio merge and MP3) ---
where ffmpeg >nul 2>nul
if errorlevel 1 (
    echo [WARNING] ffmpeg not found in PATH. Video+audio merge and MP3 may fail.
    echo Install ffmpeg and add it to PATH, then restart this file if needed.
    echo Continuing anyway...
)

REM --- 7. Run bot + website in two windows, keep them running ---
echo.
echo [RUN] Starting Telegram bot...
start "Minato Downloader - Telegram Bot" cmd /k "cd /d "%~dp0" && set DATABASE_URL=%DATABASE_URL% && set BOT_TOKEN=%BOT_TOKEN% && .venv\Scripts\python.exe main.py"
echo [RUN] Starting website on http://127.0.0.1:8000 ...
start "Minato Downloader - Website" cmd /k "cd /d "%~dp0" && set DATABASE_URL=%DATABASE_URL% && .venv\Scripts\python.exe webapp.py"
echo.
echo ============================================================
echo  Both are running in their own windows.
echo  - Telegram bot: @shortsgen_ai_bot
echo  - Website: http://127.0.0.1:8000
echo  - For a public URL: cloudflared tunnel --url http://127.0.0.1:8000
echo  Close those two windows to stop the bot and website.
echo ============================================================
pause
