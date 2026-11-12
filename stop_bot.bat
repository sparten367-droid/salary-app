@echo off
title Stop Salary Bot
echo Stopping Salary Bot...
taskkill /FI "WINDOWTITLE eq Salary Bot*" /T /F >nul 2>nul
powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.Name -like 'py*.exe' -and $_.CommandLine -like '*bot.py*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force; Write-Host ('Stopped process ' + $_.ProcessId) }"
taskkill /IM cloudflared.exe /F >nul 2>nul
echo Done. Bot is stopped.
timeout /t 3 >nul
