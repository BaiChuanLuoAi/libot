# 🚀 Quick Setup Guide

## Prerequisites
- Python 3.8+
- Running API Gateway (server.py on port 5010)
- Telegram Bot Token

## Setup Steps

### 1. Install Dependencies
```bash
cd tg_bot
pip install -r requirements.txt
```

### 2. Configure Bot Token
```bash
# Windows PowerShell
$env:TELEGRAM_BOT_TOKEN="your_bot_token_here"
$env:ADMIN_IDS="your_telegram_user_id"

# Linux/Mac
export TELEGRAM_BOT_TOKEN="your_bot_token_here"
export ADMIN_IDS="your_telegram_user_id"
```

### 3. Start the Bot
```bash
# Windows
start_bot.bat

# Linux/Mac
chmod +x start_bot.sh
./start_bot.sh
```

## Bot Features

### Gacha System
- **25 FREE credits** for new users
- **1 credit** per image roll
- **20 credits** per video animation
- Millions of unique combinations

### Monetization
- $9.99 = 100 credits
- Payment: Credit Card, PayPal, Crypto
- Users can buy more after trying free credits

### Content Generation
- Optimized prompts for NSFW content
- Photorealistic style (Pony/Flux models)
- Single female subject (best for video stability)
- Negative prompts prevent deformities

## Bot Commands
- `/start` - Get started with free credits
- `/roll` - Generate random waifu (1 credit)
- `/balance` - Check credits
- `/buy` - Purchase more credits
- `/add_credits [user_id] [amount]` - Admin only

## Technical Details
- Database: SQLite (auto-created)
- API: http://127.0.0.1:5010/v1/chat/completions
- Models: z-image-portrait, video-i2v
- Timeouts: 120s (image), 300s (video)

## Payment Configuration
See `payment_config.example` for webhook setup details.

