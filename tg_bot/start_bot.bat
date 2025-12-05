@echo off
echo ========================================
echo Starting Lili AI Telegram Bot
echo ========================================
echo.

REM Set environment variables
set TELEGRAM_BOT_TOKEN=8285418858:AAFtt_1rpMooqg09PNwVylkujCzaDtWHjJY
set ADMIN_IDS=123456789
set API_URL=http://127.0.0.1:5010/v1/chat/completions

echo Bot Token: %TELEGRAM_BOT_TOKEN:~0,20%...
echo API URL: %API_URL%
echo.
echo Starting bot...
echo.

python bot.py

pause
