@echo off
chcp 65001 > nul
cd /d "%~dp0"
echo ======================================================
echo    Запуск SpotifyToTelegram Bot
echo ======================================================
.\.venv\Scripts\python.exe main.py
pause
