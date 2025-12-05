#!/bin/bash
# Linux/Mac shell script to start the Telegram bot

echo "===================================="
echo "  Lili AI - Telegram Bot"
echo "===================================="
echo ""

# Set environment variables
export TELEGRAM_BOT_TOKEN="8285418858:AAFtt_1rpMooqg09PNwVylkujCzaDtWHjJY"
export ADMIN_IDS="123456789"
export API_URL="http://127.0.0.1:5010/v1/chat/completions"

echo "Bot token: ${TELEGRAM_BOT_TOKEN:0:10}..."
echo ""

# Check if virtual environment exists
if [ ! -d "venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv venv
    echo ""
fi

# Activate virtual environment
echo "Activating virtual environment..."
source venv/bin/activate

# Install/update dependencies
echo "Installing dependencies..."
pip install -q -r requirements.txt

echo ""
echo "===================================="
echo "  Starting Telegram Bot..."
echo "===================================="
echo ""

# Start the bot
python bot.py

