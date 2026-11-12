@echo off
cd /d "%~dp0"
title Salary Bot

if not exist ".env" (
    echo [!] File .env not found. Copy .env.example to .env and fill in BOT_TOKEN and ALLOWED_USERS.
    pause
    exit /b 1
)

set "PY="
python --version >nul 2>nul && set "PY=python"
if not defined PY py --version >nul 2>nul && set "PY=py"
if not defined PY (
    echo [!] Python not found. Install it from https://www.python.org/downloads/
    echo     and tick "Add Python to PATH" during installation.
    pause
    exit /b 1
)

echo Checking dependencies...
%PY% -m pip install -q --disable-pip-version-check -r requirements.txt
if errorlevel 1 (
    echo [!] Failed to install dependencies. Check your internet connection.
    pause
    exit /b 1
)

rem Cloudflare tunnel for the Mini App (downloaded once, ~60 MB)
if not exist "cloudflared.exe" (
    echo Downloading cloudflared...
    powershell -NoProfile -Command "[Net.ServicePointManager]::SecurityProtocol = 'Tls12'; Invoke-WebRequest -UseBasicParsing -Uri https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe -OutFile cloudflared.exe"
    if not exist "cloudflared.exe" echo [!] Could not download cloudflared. The bot will work, the app will not.
)

:loop
taskkill /IM cloudflared.exe /F >nul 2>nul
echo.
echo Bot is running. Close this window or run stop_bot.bat to stop it.
%PY% bot.py
echo.
echo Bot stopped. Restarting in 10 seconds (close the window to cancel)...
timeout /t 10 >nul
goto loop
