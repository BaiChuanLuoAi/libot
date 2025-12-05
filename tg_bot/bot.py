"""
Telegram Bot for AI Image and Video Generation.
Connects to existing API Gateway at http://127.0.0.1:5010/v1/chat/completions
"""
import os
import sys
import json
import random
import logging
import asyncio
import aiohttp
import hashlib
from typing import Optional
from datetime import datetime
from pathlib import Path

# Load environment variables from config.env if exists
try:
    from dotenv import load_dotenv
    config_file = Path(__file__).parent / 'config.env'
    if config_file.exists():
        load_dotenv(config_file)
        print(f"✅ Loaded configuration from {config_file}")
except ImportError:
    print("⚠️  python-dotenv not installed, using system environment variables only")

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
    MessageHandler,
    filters
)

from database import Database

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Configuration - Load from environment variables
API_URL = os.getenv('API_URL', "http://127.0.0.1:5010/v1/chat/completions")
API_KEY = os.getenv('API_KEY')  # 必须从环境变量读取，不设置默认值

# 检查必需的环境变量
if not API_KEY:
    logger.error("API_KEY environment variable not set!")
    print("\n❌ ERROR: API_KEY not set in environment variables or config.env!")
    print("Please set API_KEY in tg_bot/config.env file\n")
IMAGE_MODEL_SQUARE = "z-image-square"
IMAGE_MODEL_PORTRAIT = "z-image-portrait"
VIDEO_MODEL = "video-i2v"

# Costs
COST_IMAGE = 1
COST_VIDEO = 30  # 视频需要30积分，逼迫白嫖党签到5天或直接付费

# Daily Check-in Rewards
CHECKIN_REWARD = 3  # 每日签到送3积分
NEW_USER_BONUS = 15  # 新用户送15积分（还差15分才能看视频）

# Referral Rewards
REFERRAL_REWARD_INVITER = 10  # 邀请人奖励10积分
REFERRAL_REWARD_INVITEE = 5   # 被邀请人额外获得5积分（总共20积分）

# Payment Configuration - Plisio
PLISIO_SECRET_KEY = os.getenv('PLISIO_SECRET_KEY', '')
SERVER_DOMAIN = os.getenv('SERVER_DOMAIN', 'https://www.lilibot.top')

# Payment Packages - 三层套餐设计
PACKAGES = {
    'mini': {
        'price': 4.99,
        'credits': 60,
        'name': '🎓 Student Pack',
        'desc': '2 Videos + Images',
        'videos': 2
    },
    'pro': {
        'price': 9.99,
        'credits': 130,
        'name': '🔥 Pro Pack',
        'desc': '4 Videos + Bonus (Best Value!)',
        'videos': 4,
        'badge': '⭐ BEST VALUE'
    },
    'ultra': {
        'price': 29.99,
        'credits': 450,
        'name': '👑 Whale Pack',
        'desc': '15 Videos + Infinite Fun',
        'videos': 15
    }
}

# Admin user IDs - Load from environment
ADMIN_IDS_STR = os.getenv('ADMIN_IDS', '')
ADMIN_IDS = [int(id.strip()) for id in ADMIN_IDS_STR.split(',') if id.strip()] if ADMIN_IDS_STR else []

# Initialize database
db = Database()

# Load prompts
with open('prompts.json', 'r', encoding='utf-8') as f:
    PROMPTS = json.load(f)


async def call_api(model: str, prompt: str, width: int = 832, height: int = 1216, timeout: int = 300) -> Optional[str]:
    """
    Call the API Gateway to generate image or video.
    Returns the URL of the generated file or None on error.
    Default size: 832x1216 (Portrait)
    Handles streaming SSE responses from the API.
    """
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json"
    }
    
    payload = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": prompt
            }
        ],
        "width": width,
        "height": height,
        "stream": True  # Request streaming response
    }
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                API_URL, 
                json=payload, 
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=timeout)
            ) as response:
                if response.status == 200:
                    # Handle streaming response
                    result_content = ""
                    async for line in response.content:
                        line_text = line.decode('utf-8').strip()
                        if line_text.startswith('data: '):
                            data_str = line_text[6:]  # Remove 'data: ' prefix
                            if data_str == '[DONE]':
                                break
                            try:
                                import json as json_module
                                chunk = json_module.loads(data_str)
                                delta_content = chunk.get('choices', [{}])[0].get('delta', {}).get('content', '')
                                if delta_content:
                                    result_content += delta_content
                            except:
                                continue
                    
                    # Extract URL from markdown image format: ![image](url)
                    import re
                    # Match both ![image](url) and plain URLs
                    url_match = re.search(r'!\[.*?\]\((https?://[^\)]+)\)', result_content)
                    if url_match:
                        url = url_match.group(1)
                        logger.info(f"API response URL: {url}")
                        return url
                    
                    # Fallback: look for plain URL
                    url_match = re.search(r'(https?://[^\s\)]+)', result_content)
                    if url_match:
                        url = url_match.group(1)
                        logger.info(f"API response URL: {url}")
                        return url
                    
                    logger.error(f"No URL found in response: {result_content}")
                    return None
                else:
                    error_text = await response.text()
                    logger.error(f"API error {response.status}: {error_text}")
                    return None
    except asyncio.TimeoutError:
        logger.error(f"API timeout after {timeout}s")
        return None
    except Exception as e:
        logger.error(f"API call failed: {e}")
        import traceback
        traceback.print_exc()
        return None


def generate_random_prompt() -> tuple:
    """
    Generate a random NSFW prompt - matches test script format.
    Returns: (positive_prompt, negative_prompt)
    """
    # 完全随机抽取部件
    subject = PROMPTS['subjects'][0]  # 1girl, solo, female
    outfit = random.choice(PROMPTS['outfits'])
    body = random.choice(PROMPTS['body_features'])
    pose = random.choice(PROMPTS['poses'])
    location = random.choice(PROMPTS['locations'])
    angle = random.choice(PROMPTS['angles'])
    style = random.choice(PROMPTS['styles'])
    
    # 组合 prompt - 按照测试脚本的顺序
    # 顺序：subject, outfit, body, pose, location, angle, style
    positive_prompt = f"{subject}, {outfit}, {body}, {pose}, {location}, {angle}, {style}"
    
    # 负面提示词（固定）
    negative_prompt = "blurry, ugly, bad quality, distorted"
    
    return positive_prompt, negative_prompt


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start command with optional referral."""
    user = update.effective_user
    
    # Check for referral code in /start command
    referrer_id = None
    is_new_user = False
    
    if context.args and len(context.args) > 0:
        try:
            # Referral format: /start ref_123456789
            ref_code = context.args[0]
            if ref_code.startswith('ref_'):
                referrer_id = int(ref_code[4:])
                logger.info(f"User {user.id} started with referral code from {referrer_id}")
        except:
            pass
    
    # Check if this is a new user
    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT user_id FROM users WHERE user_id = ?", (user.id,))
        is_new_user = cursor.fetchone() is None
    
    # Create or get user
    user_data = db.get_or_create_user(
        user_id=user.id,
        username=user.username,
        first_name=user.first_name,
        invited_by=referrer_id if is_new_user else None
    )
    
    # Process referral rewards if new user and valid referrer
    referral_bonus_message = ""
    if is_new_user and referrer_id and referrer_id != user.id:
        # Check if referrer exists
        with db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT user_id, first_name FROM users WHERE user_id = ?", (referrer_id,))
            referrer = cursor.fetchone()
            
            if referrer:
                # Give bonus to new user
                db.add_credits(
                    user.id, 
                    REFERRAL_REWARD_INVITEE, 
                    f"Referral bonus from user {referrer_id}"
                )
                
                # Give bonus to referrer
                db.add_credits(
                    referrer_id,
                    REFERRAL_REWARD_INVITER,
                    f"Invited user {user.id}"
                )
                
                user_data['credits'] += REFERRAL_REWARD_INVITEE
                
                referral_bonus_message = (
                    f"\n🎁 **Referral Bonus!**\n"
                    f"You got **+{REFERRAL_REWARD_INVITEE} extra credits** from invitation!\n\n"
                )
                
                logger.info(f"Referral: {referrer_id} invited {user.id}")
                
                # Notify referrer
                try:
                    await context.bot.send_message(
                        chat_id=referrer_id,
                        text=(
                            f"🎉 **Someone used your invite link!**\n\n"
                            f"You earned **+{REFERRAL_REWARD_INVITER} credits**!\n"
                            f"Keep sharing: /invite"
                        ),
                        parse_mode='Markdown'
                    )
                except:
                    pass
    
    welcome_message = (
        f"🔥 Welcome {user.first_name}!\n\n"
        f"{referral_bonus_message}"
        f"💎 You have **{user_data['credits']} free credits** to start!\n\n"
        f"🎲 **/roll** - Spin for your waifu (1 credit)\n"
        f"✅ **/checkin** - Daily bonus (3 credits)\n"
        f"💰 **/balance** - Check your credits\n"
        f"💳 **/buy** - Get more credits\n"
        f"👥 **/invite** - Invite friends, earn credits\n\n"
        f"🎥 After each roll, you can **Make it Move** for {COST_VIDEO} credits!\n\n"
        f"💡 _Tip: Check in daily for 5 days = 1 free video!_\n"
        f"_Every roll is unique - millions of combinations!_"
    )
    
    await update.message.reply_text(welcome_message, parse_mode='Markdown')


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /help command."""
    help_text = (
        "🎰 **How to Play**\n\n"
        "🎲 **/roll** - Spin the gacha! Get a random AI waifu (1 credit)\n"
        "✅ **/checkin** - Daily bonus (3 credits per day)\n"
        "💰 **/balance** - Check your credits\n"
        "👥 **/invite** - Invite friends, earn credits\n"
        "💳 **/buy** - Recharge credits (from $4.99)\n\n"
        "🎥 **Make it Move:**\n"
        f"After rolling, click '🎥 Make it Move' to turn your image into a live video! ({COST_VIDEO} credits)\n\n"
        "💎 **Free Credits Strategy:**\n"
        f"• New user bonus: {NEW_USER_BONUS} credits\n"
        f"• Daily check-in: {CHECKIN_REWARD} credits\n"
        f"• Invite friends: {REFERRAL_REWARD_INVITER} credits each\n"
        f"• Check in 5 days = 1 FREE video! ({COST_VIDEO} credits)\n\n"
        "💳 **Packages:**\n"
        "• Student Pack: $4.99 = 60 credits (2 videos)\n"
        "• Pro Pack: $9.99 = 130 credits (4 videos) ⭐\n"
        "• Whale Pack: $29.99 = 450 credits (15 videos)\n\n"
        "_Millions of unique combinations - every roll is different!_"
    )
    
    await update.message.reply_text(help_text, parse_mode='Markdown')


async def balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /balance command."""
    user = update.effective_user
    user_data = db.get_or_create_user(user.id, user.username, user.first_name)
    
    credits = user_data['credits']
    streak = user_data.get('checkin_streak', 0)
    total_checkins = user_data.get('total_checkins', 0)
    
    # Calculate how many videos they can afford
    videos_affordable = credits // COST_VIDEO
    images_affordable = credits // COST_IMAGE
    
    message = (
        f"💎 **Your Balance**\n\n"
        f"💰 Credits: **{credits}**\n"
        f"🔥 Streak: **{streak} days** ({total_checkins} total check-ins)\n\n"
        f"📊 **What you can do:**\n"
        f"• {videos_affordable} videos ({COST_VIDEO} credits each)\n"
        f"• {images_affordable} images ({COST_IMAGE} credit each)\n\n"
        f"✅ Daily check-in: +{CHECKIN_REWARD} credits\n"
        f"💳 Recharge: /buy\n\n"
        f"_Check in for 5 days = 1 FREE video!_"
    )
    
    await update.message.reply_text(message, parse_mode='Markdown')


async def roll(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /roll command - Generate random image."""
    user = update.effective_user
    db.get_or_create_user(user.id, user.username, user.first_name)
    
    # Check credits
    credits = db.get_credits(user.id)
    if credits < COST_IMAGE:
        await update.message.reply_text(
            f"💔 Out of credits!\n\n"
            f"You need {COST_IMAGE} credit but only have {credits}.\n\n"
            f"💳 Use /buy to get more credits and keep rolling!"
        )
        return
    
    # Deduct credits first
    if not db.deduct_credits(user.id, COST_IMAGE, "Image generation (/roll)"):
        await update.message.reply_text("❌ Failed to deduct credits. Please try again.")
        return
    
    # Generate random prompt
    positive_prompt, negative_prompt = generate_random_prompt()
    logger.info(f"User {user.id} rolling with prompt: {positive_prompt}")
    
    # Send generating message
    status_msg = await update.message.reply_text(
        "🎲 Rolling the gacha...\n\n"
        f"✨ Generating your exclusive waifu...\n"
        f"⏱ This takes ~30 seconds\n\n"
        f"_Please wait..._",
        parse_mode='Markdown'
    )
    
    # Combine prompts for API (include negative in the main prompt if API doesn't support separate neg field)
    full_prompt = f"{positive_prompt}\n\nNegative prompt: {negative_prompt}"
    
    # Call API
    result_url = await call_api(IMAGE_MODEL_PORTRAIT, full_prompt, timeout=120)
    
    if result_url:
        # Delete status message
        await status_msg.delete()
        
        # Store the image URL in context for later use (video generation)
        # Use a short ID instead of full URL for callback_data
        import hashlib
        image_id = hashlib.md5(result_url.encode()).hexdigest()[:16]
        context.bot_data[f"img_{image_id}"] = result_url
        
        # Create inline button for video generation
        keyboard = [
            [InlineKeyboardButton(f"🔥 ANIMATE HER NOW! ({COST_VIDEO} Credits)", callback_data=f"video:{image_id}")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        # Get remaining credits
        remaining_credits = db.get_credits(user.id)
        
        # Send image with button
        caption = (
            f"🎊 **JACKPOT!** Your waifu is here!\n\n"
            f"💎 Credits: {remaining_credits}\n\n"
            f"🎥 Want to see her move? Click below! ⬇️"
        )
        
        try:
            # Download the image first
            async with aiohttp.ClientSession() as session:
                # Replace internal Docker address with localhost
                download_url = result_url.replace('http://api-server:5010', 'http://api-server:5010')
                async with session.get(download_url) as img_response:
                    if img_response.status == 200:
                        image_data = await img_response.read()
                        await update.message.reply_photo(
                            photo=image_data,
                            caption=caption,
                            reply_markup=reply_markup,
                            parse_mode='Markdown'
                        )
                    else:
                        raise Exception(f"Failed to download image: {img_response.status}")
        except Exception as e:
            logger.error(f"Failed to send photo: {e}")
            # Refund credits on failure
            db.add_credits(user.id, COST_IMAGE, "Refund for failed image send")
            await update.message.reply_text(
                "❌ Failed to send image. Your credit has been refunded.\n"
                "Please try again or contact admin."
            )
    else:
        # API failed - refund credits
        db.add_credits(user.id, COST_IMAGE, "Refund for failed generation")
        await status_msg.edit_text(
            "❌ Failed to generate image. Your credit has been refunded.\n"
            "Please try again later or contact admin if the issue persists."
        )


async def video_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle video generation callback from inline button."""
    query = update.callback_query
    user = update.effective_user
    
    await query.answer()  # Acknowledge the button click
    
    # Parse callback data
    callback_data = query.data
    if not callback_data.startswith("video:"):
        await query.message.reply_text("❌ Invalid callback data.")
        return
    
    image_id = callback_data[6:]  # Remove "video:" prefix
    
    # Retrieve image URL from context
    image_url = context.bot_data.get(f"img_{image_id}")
    if not image_url:
        await query.message.reply_text("❌ Image not found. Please generate a new image with /roll")
        return
    
    # Check credits
    credits = db.get_credits(user.id)
    if credits < COST_VIDEO:
        # 计算还需要签到几天
        needed = COST_VIDEO - credits
        days_needed = (needed + CHECKIN_REWARD - 1) // CHECKIN_REWARD
        
        await query.message.reply_text(
            f"💔 Not enough credits!\n\n"
            f"You need **{COST_VIDEO} credits** but only have **{credits}**.\n"
            f"Missing: **{needed} credits**\n\n"
            f"✅ Check in for **{days_needed} more days** (FREE!)\n"
            f"💳 Or /buy credits now:\n"
            f"   • Student Pack: ${PACKAGES['mini']['price']} = {PACKAGES['mini']['credits']} credits\n"
            f"   • Pro Pack: ${PACKAGES['pro']['price']} = {PACKAGES['pro']['credits']} credits ⭐\n\n"
            f"_Your choice: Time or Money!_",
            parse_mode='Markdown'
        )
        return
    
    # Deduct credits
    if not db.deduct_credits(user.id, COST_VIDEO, "Video generation (i2v)"):
        await query.message.reply_text("❌ Failed to deduct credits. Please try again.")
        return
    
    logger.info(f"User {user.id} generating video from image: {image_url}")
    
    # Send status message
    status_msg = await query.message.reply_text(
        "🎬 Bringing her to life...\n\n"
        "✨ Creating high-quality video\n"
        "⏱ Estimated time: 2-3 minutes\n\n"
        "_Worth the wait - trust me!_ 😉"
    )
    
    # Call API with image URL as prompt for i2v
    result_url = await call_api(VIDEO_MODEL, image_url, timeout=300)
    
    if result_url:
        # Delete status message
        await status_msg.delete()
        
        # Get remaining credits
        remaining_credits = db.get_credits(user.id)
        
        caption = (
            f"🔥 **SHE'S ALIVE!**\n\n"
            f"💎 Credits: {remaining_credits}\n\n"
            f"🎲 Roll again? Use /roll"
        )
        
        try:
            # Download the video first
            async with aiohttp.ClientSession() as session:
                async with session.get(result_url) as vid_response:
                    if vid_response.status == 200:
                        video_data = await vid_response.read()
                        await query.message.reply_video(
                            video=video_data,
                            caption=caption,
                            parse_mode='Markdown'
                        )
                    else:
                        raise Exception(f"Failed to download video: {vid_response.status}")
        except Exception as e:
            logger.error(f"Failed to send video: {e}")
            # Refund credits on failure
            db.add_credits(user.id, COST_VIDEO, "Refund for failed video send")
            await query.message.reply_text(
                "❌ Failed to send video. Your credits have been refunded.\n"
                "Please try again or contact admin."
            )
    else:
        # API failed - refund credits
        db.add_credits(user.id, COST_VIDEO, "Refund for failed video generation")
        await status_msg.edit_text(
            "❌ Failed to generate video. Your credits have been refunded.\n"
            "Please try again later or contact admin if the issue persists."
        )


async def invite_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /invite command - Generate referral link."""
    user = update.effective_user
    db.get_or_create_user(user.id, user.username, user.first_name)
    
    # Get bot username for the invite link
    bot_info = await context.bot.get_me()
    bot_username = bot_info.username
    
    # Generate referral link
    referral_code = f"ref_{user.id}"
    invite_link = f"https://t.me/{bot_username}?start={referral_code}"
    
    # Count how many people this user has invited
    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) as count FROM users WHERE invited_by = ?", (user.id,))
        invited_count = cursor.fetchone()['count']
    
    total_earned = invited_count * REFERRAL_REWARD_INVITER
    
    message = (
        f"👥 **Invite Friends & Earn Credits!**\n\n"
        f"🎁 **Rewards:**\n"
        f"• You get: **{REFERRAL_REWARD_INVITER} credits** per friend\n"
        f"• They get: **{REFERRAL_REWARD_INVITEE} bonus credits**\n\n"
        f"📊 **Your Stats:**\n"
        f"• Friends invited: **{invited_count}**\n"
        f"• Total earned: **{total_earned} credits**\n\n"
        f"🔗 **Your Invite Link:**\n"
        f"`{invite_link}`\n\n"
        f"_Share this link anywhere!_"
    )
    
    await update.message.reply_text(message, parse_mode='Markdown')


async def checkin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /checkin command - Daily check-in."""
    user = update.effective_user
    db.get_or_create_user(user.id, user.username, user.first_name)
    
    result = db.daily_checkin(user.id)
    
    if result['success']:
        reward = result['reward']
        streak = result['streak']
        new_balance = db.get_credits(user.id)
        
        # 计算距离免费视频还差多少
        needed_for_video = COST_VIDEO - new_balance
        days_to_video = max(0, (needed_for_video + CHECKIN_REWARD - 1) // CHECKIN_REWARD)
        
        message = (
            f"✅ **Daily Check-in Success!**\n\n"
            f"💎 Earned: **+{reward} credits**\n"
            f"💰 Balance: **{new_balance} credits**\n"
            f"🔥 Streak: **{streak} days**\n\n"
        )
        
        if new_balance >= COST_VIDEO:
            message += f"🎉 **You can make a video now!** Use /roll first!\n"
        else:
            message += f"📅 Check in **{days_to_video} more days** for a FREE video!\n"
        
        message += f"\n_Come back tomorrow for another {CHECKIN_REWARD} credits!_"
        
        await update.message.reply_text(message, parse_mode='Markdown')
    
    elif result['message'] == 'already_checked':
        streak = result['streak']
        message = (
            f"⏰ **Already checked in today!**\n\n"
            f"🔥 Current streak: **{streak} days**\n\n"
            f"_Come back tomorrow for {CHECKIN_REWARD} more credits!_"
        )
        await update.message.reply_text(message, parse_mode='Markdown')
    
    else:
        await update.message.reply_text("❌ Check-in failed. Please try again.")


async def buy_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /buy command - Show payment options."""
    user = update.effective_user
    db.get_or_create_user(user.id, user.username, user.first_name)
    
    # 构建套餐选项按钮
    keyboard = []
    has_payment_methods = False
    
    # Plisio 加密货币支付 - 三个套餐
    if PLISIO_SECRET_KEY:
        keyboard.append([
            InlineKeyboardButton("🎓 Student ($4.99)", callback_data="package:mini"),
        ])
        keyboard.append([
            InlineKeyboardButton("🔥 Pro ($9.99) ⭐", callback_data="package:pro"),
        ])
        keyboard.append([
            InlineKeyboardButton("👑 Whale ($29.99)", callback_data="package:ultra"),
        ])
        has_payment_methods = True
    
    if has_payment_methods:
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        mini = PACKAGES['mini']
        pro = PACKAGES['pro']
        ultra = PACKAGES['ultra']
        
        message = (
            "💰 **TOP UP BALANCE**\n\n"
            "🔓 _Unlock uncensored videos & priority queue!_\n\n"
            f"🎓 **Student Pack - ${mini['price']}**\n"
            f"   👉 **{mini['credits']} Credits** ({mini['videos']} videos + images)\n\n"
            f"🔥 **Pro Pack - ${pro['price']}** {pro.get('badge', '')}\n"
            f"   👉 **{pro['credits']} Credits** ({pro['videos']} videos + bonus)\n"
            f"   _+10% bonus credits included!_\n\n"
            f"👑 **Whale Pack - ${ultra['price']}**\n"
            f"   👉 **{ultra['credits']} Credits** ({ultra['videos']} videos)\n"
            f"   _+25% bonus - best for power users!_\n\n"
            "💳 **Payment:** Anonymous Crypto (BTC/ETH/USDT)\n"
            "⚡ **Delivery:** 2-10 minutes after confirmation\n\n"
            "_Select your package below:_"
        )
        await update.message.reply_text(message, reply_markup=reply_markup, parse_mode='Markdown')
    else:
        # 没有配置支付网关时的消息
        message = (
            "💎 **RECHARGE CREDITS**\n\n"
            "⚠️ Payment gateways are not configured yet.\n\n"
            "Please contact the administrator to recharge your credits.\n\n"
            f"Your Telegram ID: `{user.id}`\n\n"
            "_The admin can use /add_credits command to add credits to your account._"
        )
        await update.message.reply_text(message, parse_mode='Markdown')


async def package_selection_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle package selection - show payment method options."""
    query = update.callback_query
    user = update.effective_user
    
    await query.answer()
    
    # Parse package type
    callback_data = query.data
    if not callback_data.startswith("package:"):
        await query.message.reply_text("❌ Invalid package selection.")
        return
    
    package_key = callback_data[8:]  # Remove "package:" prefix
    
    if package_key not in PACKAGES:
        await query.message.reply_text("❌ Package not found.")
        return
    
    package = PACKAGES[package_key]
    
    # Show payment method selection
    keyboard = [[
        InlineKeyboardButton("💳 Pay with Crypto", callback_data=f"pay_plisio:{package_key}")
    ]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    message = (
        f"**{package['name']}**\n\n"
        f"💰 Price: **${package['price']}**\n"
        f"💎 Credits: **{package['credits']}**\n"
        f"🎥 Videos: **{package['videos']}**\n\n"
        f"📋 {package['desc']}\n\n"
        "💳 **Select Payment Method:**"
    )
    
    await query.message.reply_text(message, reply_markup=reply_markup, parse_mode='Markdown')


async def plisio_payment_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle Plisio crypto payment generation."""
    query = update.callback_query
    user = update.effective_user
    
    await query.answer()
    
    if not PLISIO_SECRET_KEY:
        await query.message.reply_text(
            "❌ Crypto payment is temporarily unavailable. Please contact admin."
        )
        return
    
    # Parse package type
    callback_data = query.data
    if not callback_data.startswith("pay_plisio:"):
        await query.message.reply_text("❌ Invalid payment data.")
        return
    
    package_key = callback_data[11:]  # Remove "pay_plisio:" prefix
    
    if package_key not in PACKAGES:
        await query.message.reply_text("❌ Package not found.")
        return
    
    package = PACKAGES[package_key]
    
    # 生成唯一订单ID
    order_id = f"user_{user.id}_{package_key}_{int(datetime.now().timestamp())}"
    amount = str(package['price'])
    credits = package['credits']
    
    try:
        # 调用 Plisio API 创建发票
        async with aiohttp.ClientSession() as session:
            url = "https://api.plisio.net/api/v1/invoices/new"
            
            # Plisio API 参数
            params = {
                "api_key": PLISIO_SECRET_KEY,
                "amount": amount,
                "currency": "USD",  # 用户支付时可选择任何加密货币
                "order_name": f"{package['name']} - {credits} Credits",
                "order_id": order_id,
                "callback_url": f"{SERVER_DOMAIN}/webhooks/plisio",
                "source_currency": "USD",  # 源货币
                "source_amount": amount,
                "allowed_psys_cids": ""  # 留空表示支持所有币种
            }
            
            async with session.post(
                url,
                data=params,
                timeout=aiohttp.ClientTimeout(total=30)
            ) as response:
                if response.status == 200:
                    result = await response.json()
                    
                    # Plisio 成功响应格式：{"result": "success", "data": {...}}
                    if result.get("result") == "success":
                        invoice_data = result.get("data", {})
                        invoice_url = invoice_data.get("invoice_url")
                        
                        if invoice_url:
                            # 创建待处理记录
                            db.create_pending_payment(
                                user_id=user.id,
                                amount=credits,
                                money_amount=float(amount),
                                currency='USD',
                                provider='plisio',
                                external_ref=order_id,
                                description=f"Plisio payment: {package['name']}"
                            )
                            
                            keyboard = [[InlineKeyboardButton("💰 Pay Now", url=invoice_url)]]
                            reply_markup = InlineKeyboardMarkup(keyboard)
                            
                            await query.message.reply_text(
                                f"₿ **Crypto Payment**\n\n"
                                f"📦 Package: **{package['name']}**\n"
                                f"💎 Credits: **{credits}**\n"
                                f"💵 Amount: **${amount}**\n"
                                f"📋 Order ID: `{order_id}`\n\n"
                                "🪙 **Supported Coins:**\n"
                                "BTC, ETH, USDT, XMR, LTC, and more!\n\n"
                                "Click the button below to complete payment.\n"
                                "Credits will be added within 2-10 minutes after confirmation.\n\n"
                                "🔒 Anonymous & Secure",
                                reply_markup=reply_markup,
                                parse_mode='Markdown'
                            )
                            logger.info(f"✅ Plisio invoice created for user {user.id}: {order_id}")
                        else:
                            await query.message.reply_text("❌ Failed to create payment invoice. Please try again.")
                            logger.error(f"Plisio: No invoice URL in response")
                    else:
                        error_msg = result.get("message", "Unknown error")
                        await query.message.reply_text(f"❌ Payment service error: {error_msg}")
                        logger.error(f"Plisio API error: {error_msg}")
                else:
                    response_text = await response.text()
                    await query.message.reply_text("❌ Payment service temporarily unavailable. Please try again later.")
                    logger.error(f"Plisio HTTP {response.status}: {response_text}")
    
    except Exception as e:
        logger.error(f"Plisio payment error: {e}")
        import traceback
        traceback.print_exc()
        await query.message.reply_text("❌ Error creating payment. Please contact admin.")


async def add_credits_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /add_credits command (admin only)."""
    user = update.effective_user
    
    # Check if user is admin
    if user.id not in ADMIN_IDS:
        await update.message.reply_text("❌ You don't have permission to use this command.")
        return
    
    # Parse arguments
    if len(context.args) != 2:
        await update.message.reply_text(
            "Usage: /add_credits [user_id] [amount]\n"
            "Example: /add_credits 123456789 100"
        )
        return
    
    try:
        target_user_id = int(context.args[0])
        amount = int(context.args[1])
        
        if amount <= 0:
            await update.message.reply_text("❌ Amount must be positive.")
            return
        
        # Add credits
        success = db.add_credits(target_user_id, amount, f"Admin top-up by {user.id}")
        
        if success:
            new_balance = db.get_credits(target_user_id)
            await update.message.reply_text(
                f"✅ Successfully added {amount} credits to user {target_user_id}.\n"
                f"New balance: {new_balance} credits"
            )
        else:
            await update.message.reply_text("❌ Failed to add credits. User may not exist.")
    
    except ValueError:
        await update.message.reply_text("❌ Invalid arguments. Both user_id and amount must be numbers.")


async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle errors."""
    logger.error(f"Update {update} caused error {context.error}")
    
    if update and update.effective_message:
        await update.effective_message.reply_text(
            "❌ An error occurred while processing your request. "
            "Please try again later or contact admin."
        )


def main():
    """Start the bot."""
    # Get bot token from environment variable
    token = os.getenv('TELEGRAM_BOT_TOKEN')
    
    if not token:
        logger.error("TELEGRAM_BOT_TOKEN environment variable not set!")
        print("\n❌ ERROR: TELEGRAM_BOT_TOKEN not set!")
        print("\nPlease set your bot token:")
        print("  On Linux/Mac: export TELEGRAM_BOT_TOKEN='your_token_here'")
        print("  On Windows: set TELEGRAM_BOT_TOKEN=your_token_here")
        print("  Or edit config.env file in tg_bot directory\n")
        return
    
    # Log configuration
    logger.info(f"Bot Token: {token[:20]}...")
    logger.info(f"API URL: {API_URL}")
    logger.info(f"Admin IDs: {ADMIN_IDS}")
    
    # Create application
    application = Application.builder().token(token).build()
    
    # Register handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("balance", balance))
    application.add_handler(CommandHandler("checkin", checkin_command))
    application.add_handler(CommandHandler("invite", invite_command))
    application.add_handler(CommandHandler("roll", roll))
    application.add_handler(CommandHandler("buy", buy_command))
    application.add_handler(CommandHandler("add_credits", add_credits_command))
    
    # Callback query handlers
    application.add_handler(CallbackQueryHandler(video_callback, pattern="^video:"))
    application.add_handler(CallbackQueryHandler(package_selection_callback, pattern="^package:"))
    application.add_handler(CallbackQueryHandler(plisio_payment_callback, pattern="^pay_plisio:"))
    
    # Error handler
    application.add_error_handler(error_handler)
    
    # Start bot
    logger.info("Bot started! Press Ctrl+C to stop.")
    print("\n✅ Bot is running! Press Ctrl+C to stop.\n")
    
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == '__main__':
    main()

