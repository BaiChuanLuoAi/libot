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

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand, BotCommandScopeChat
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

# Disable httpx INFO logs to reduce noise
logging.getLogger('httpx').setLevel(logging.WARNING)

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
SERVER_DOMAIN = os.getenv('SERVER_DOMAIN', 'https://lilibot.top')

# Payment Packages - 三层套餐设计
PACKAGES = {
    'test': {
        'price': 1.00,
        'credits': 10,
        'name': '🧪 Test Pack',
        'desc': 'Testing only',
        'videos': 0
    },
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

# Required Channel - 强制关注频道配置
REQUIRED_CHANNEL = os.getenv('REQUIRED_CHANNEL', '@liliai_official')  # 必须是 @username 格式
CHANNEL_LINK = os.getenv('CHANNEL_LINK', 'https://t.me/liliai_official')  # 频道链接

# Initialize database
db = Database()

# Load prompts
with open('prompts.json', 'r', encoding='utf-8') as f:
    PROMPTS = json.load(f)


def safe_markdown_name(name: str, max_length: int = 30) -> str:
    """
    安全地处理用户名用于 Markdown 显示
    移除或替换可能导致 Markdown 解析错误的特殊字符
    """
    if not name:
        return "Unknown"
    
    # 移除/替换 Markdown 特殊字符
    special_chars = ['*', '_', '[', ']', '(', ')', '~', '`', '>', '#', '+', '-', '=', '|', '{', '}', '.', '!']
    safe_name = name
    for char in special_chars:
        safe_name = safe_name.replace(char, '')
    
    # 限制长度
    if len(safe_name) > max_length:
        safe_name = safe_name[:max_length] + "..."
    
    return safe_name.strip() or "User"


# ============================================
# 🔒 频道关注验证装饰器（核心安全机制）
# ============================================
def require_channel_membership(func):
    """
    装饰器：强制要求用户关注频道才能使用命令
    
    应用于所有核心功能命令：
    - /checkin (签到)
    - /roll (生成图片)
    - /buy (购买积分)
    - /balance (查看余额)
    - /settings (设置)
    等...
    
    ⚠️ 不应用于 /start（需要自定义逻辑显示引导）
    """
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        
        # 如果没有配置必需频道，直接放行
        if not REQUIRED_CHANNEL:
            return await func(update, context)
        
        try:
            # 检查用户的频道成员状态
            member = await context.bot.get_chat_member(chat_id=REQUIRED_CHANNEL, user_id=user.id)
            
            # ✅ 用户已关注频道：状态为 member、administrator 或 creator
            if member.status in ['member', 'administrator', 'creator']:
                return await func(update, context)
            
            # ❌ 用户未关注频道
            keyboard = [
                [InlineKeyboardButton("👉 Join Official Channel", url=CHANNEL_LINK)],
                [InlineKeyboardButton("✅ I Have Joined", callback_data="check_join_status")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await update.message.reply_text(
                "🔒 <b>Channel Membership Required</b>\n\n"
                f"This feature requires joining our official channel first.\n\n"
                f"📢 <b>Official Channel:</b> {CHANNEL_LINK}\n\n"
                f"<b>How to unlock:</b>\n"
                f"1️⃣ Tap the link above or button below to join\n"
                f"2️⃣ Click <b>JOIN</b> in the channel\n"
                f"3️⃣ Come back and tap <b>'✅ I Have Joined'</b>\n\n"
                f"<i>This helps us prevent spam and support our community!</i> 💝",
                reply_markup=reply_markup,
                parse_mode='HTML'
            )
            return  # 🚨 阻止未验证用户执行命令
            
        except Exception as e:
            # ⚠️ 验证失败（可能是权限问题、网络问题等）
            logger.error(f"🔴 Channel verification failed for user {user.id}: {e}")
            
            # 🔒 安全策略：验证失败时阻止访问
            keyboard = [
                [InlineKeyboardButton("👉 Join Official Channel", url=CHANNEL_LINK)],
                [InlineKeyboardButton("✅ Try Again", callback_data="check_join_status")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await update.message.reply_text(
                "⚠️ <b>Verification Required</b>\n\n"
                f"We couldn't verify your channel membership due to a technical issue.\n\n"
                f"📢 <b>Official Channel:</b> {CHANNEL_LINK}\n\n"
                f"<b>Please:</b>\n"
                f"1️⃣ Click the link above to join our channel\n"
                f"2️⃣ Wait a few seconds\n"
                f"3️⃣ Tap <b>'✅ Try Again'</b>\n\n"
                f"<i>If this persists, please contact support.</i>",
                reply_markup=reply_markup,
                parse_mode='HTML'
            )
            return  # 🚨 阻止未验证用户执行命令
    
    return wrapper


def require_channel_membership_callback(func):
    """
    装饰器：强制要求用户关注频道才能使用回调按钮功能
    
    专门用于 CallbackQueryHandler 的装饰器版本
    """
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        user = update.effective_user
        
        # 如果没有配置必需频道，直接放行
        if not REQUIRED_CHANNEL:
            return await func(update, context)
        
        try:
            # 检查用户的频道成员状态
            member = await context.bot.get_chat_member(chat_id=REQUIRED_CHANNEL, user_id=user.id)
            
            # ✅ 用户已关注频道
            if member.status in ['member', 'administrator', 'creator']:
                return await func(update, context)
            
            # ❌ 用户未关注频道
            await query.answer("❌ Please join our channel first!", show_alert=True)
            
            keyboard = [
                [InlineKeyboardButton("👉 Join Official Channel", url=CHANNEL_LINK)],
                [InlineKeyboardButton("✅ I Have Joined", callback_data="check_join_status")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await query.message.reply_text(
                "🔒 <b>Channel Membership Required</b>\n\n"
                f"This feature requires joining our official channel first.\n\n"
                f"📢 <b>Official Channel:</b> {CHANNEL_LINK}\n\n"
                f"<b>How to unlock:</b>\n"
                f"1️⃣ Tap the link above or button below to join\n"
                f"2️⃣ Click <b>JOIN</b> in the channel\n"
                f"3️⃣ Come back and tap <b>'✅ I Have Joined'</b>\n\n"
                f"<i>This helps us prevent spam and support our community!</i> 💝",
                reply_markup=reply_markup,
                parse_mode='HTML'
            )
            return  # 🚨 阻止未验证用户执行回调
            
        except Exception as e:
            # ⚠️ 验证失败
            logger.error(f"🔴 Channel verification failed for user {user.id} (callback): {e}")
            
            await query.answer("⚠️ Verification failed. Please try again.", show_alert=True)
            
            keyboard = [
                [InlineKeyboardButton("👉 Join Official Channel", url=CHANNEL_LINK)],
                [InlineKeyboardButton("✅ Try Again", callback_data="check_join_status")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await query.message.reply_text(
                "⚠️ <b>Verification Required</b>\n\n"
                f"We couldn't verify your channel membership.\n\n"
                f"📢 <b>Official Channel:</b> {CHANNEL_LINK}\n\n"
                f"<i>Please click the link above to join and try again.</i>",
                reply_markup=reply_markup,
                parse_mode='HTML'
            )
            return  # 🚨 阻止未验证用户执行回调
    
    return wrapper


async def call_api(model: str, prompt: str, width: int = 832, height: int = 1216, timeout: int = 300, image_base64: Optional[str] = None) -> Optional[str]:
    """
    Call the API Gateway to generate image or video.
    Returns the URL of the generated file or None on error.
    Default size: 832x1216 (Portrait)
    Handles streaming SSE responses from the API.
    
    Args:
        model: Model name
        prompt: Text prompt
        width: Image width
        height: Image height
        timeout: Request timeout
        image_base64: Base64 encoded image data (for i2v)
    """
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json"
    }
    
    # Build message content
    if image_base64:
        # For image-to-video, include both text and image
        content = [
            {
                "type": "text",
                "text": prompt
            },
            {
                "type": "image_url",
                "image_url": {
                    "url": f"data:image/png;base64,{image_base64}"
                }
            }
        ]
    else:
        # For text-only requests
        content = prompt
    
    payload = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": content
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
    
    # 🎁 STEP 0: 提前解析推荐码（在频道检查之前）
    referrer_id = None
    if context.args and len(context.args) > 0:
        try:
            ref_code = context.args[0]
            if ref_code.startswith('ref_'):
                referrer_id = int(ref_code[4:])
                logger.info(f"User {user.id} started with referral code from {referrer_id}")
        except:
            pass
    
    # 🔒 STEP 1: 强制检查频道关注状态 - 核心安全机制
    if REQUIRED_CHANNEL:
        try:
            member = await context.bot.get_chat_member(chat_id=REQUIRED_CHANNEL, user_id=user.id)
            
            # ❌ 未关注频道：状态为 'left' (未加入) 或 'kicked' (被踢出)
            if member.status in ['left', 'kicked']:
                # 🚫 拒绝访问，要求先加入频道
                # 🎁 将推荐码嵌入到回调数据中，避免丢失
                callback_data = f"check_join_status:{referrer_id}" if referrer_id else "check_join_status"
                
                keyboard = [
                    [InlineKeyboardButton("👉 Join Official Channel", url=CHANNEL_LINK)],
                    [InlineKeyboardButton("✅ I Have Joined", callback_data=callback_data)]
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)
                
                await update.message.reply_text(
                    "🛑 <b>ACCESS REQUIRED</b>\n\n"
                    f"To activate your <b>15 FREE Credits</b>, please join our official channel first!\n\n"
                    f"📢 <b>Official Channel:</b> {CHANNEL_LINK}\n\n"
                    f"<b>How to unlock:</b>\n"
                    f"1️⃣ Click the link above or button below\n"
                    f"2️⃣ Tap <b>JOIN</b> in the channel\n"
                    f"3️⃣ Come back and tap <b>'✅ I Have Joined'</b>\n\n"
                    f"<i>We use this to prevent spam bots and support our community.</i> 🎁",
                    reply_markup=reply_markup,
                    parse_mode='HTML'
                )
                return  # 🚨 关键！阻止后续逻辑执行，不发放积分！
                
        except Exception as e:
            # 如果机器人不是频道管理员，会报错 'Chat not found'
            logger.error(f"⚠️ Channel Check Error: {e}")
            logger.error(f"⚠️ Please make sure the bot is an administrator in {REQUIRED_CHANNEL}")
            
            # 🔒 安全策略：验证失败时阻止访问，而不是放行
            keyboard = [
                [InlineKeyboardButton("👉 Join Official Channel", url=CHANNEL_LINK)],
                [InlineKeyboardButton("✅ Try Again", callback_data="check_join_status")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await update.message.reply_text(
                "⚠️ <b>Verification Required</b>\n\n"
                f"We need to verify your channel membership, but encountered a technical issue.\n\n"
                f"📢 <b>Official Channel:</b> {CHANNEL_LINK}\n\n"
                f"<b>Please try:</b>\n"
                f"1️⃣ Click the link above to join our channel\n"
                f"2️⃣ Wait a few seconds\n"
                f"3️⃣ Tap <b>'✅ Try Again'</b>\n\n"
                f"<i>If this persists, please contact support.</i>",
                reply_markup=reply_markup,
                parse_mode='HTML'
            )
            return  # 🚨 阻止未验证用户继续使用
    
    # Check if this is a new user
    is_new_user = False
    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT user_id FROM users WHERE user_id = ?", (user.id,))
        is_new_user = cursor.fetchone() is None
    
    # ✅ 只有通过频道检查的用户才能执行到这里
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
                    f"\n🎁 <b>Referral Bonus!</b>\n"
                    f"You got <b>+{REFERRAL_REWARD_INVITEE} extra credits</b> from invitation!\n\n"
                )
                
                logger.info(f"Referral: {referrer_id} invited {user.id}")
                
                # Notify referrer
                try:
                    await context.bot.send_message(
                        chat_id=referrer_id,
                        text=(
                            f"🎉 <b>Someone used your invite link!</b>\n\n"
                            f"You earned <b>+{REFERRAL_REWARD_INVITER} credits</b>!\n"
                            f"Keep sharing: /invite"
                        ),
                        parse_mode='HTML'
                    )
                except:
                    pass
    
    welcome_message = (
        f"🔥 <b>Welcome to Lili AI!</b>\n\n"
        f"{referral_bonus_message}"
        f"💎 You start with <b>{user_data['credits']} FREE credits</b>!\n\n"
        f"🎲 <b>/roll</b> - Get your AI waifu (1 credit)\n"
        f"🎥 <b>Animate her</b> - Make it move! ({COST_VIDEO} credits)\n\n"
        f"🆓 <b>FREE Credits:</b>\n"
        f"• <b>/checkin</b> - Daily +{CHECKIN_REWARD} credits\n"
        f"• <b>/invite</b> - +{REFERRAL_REWARD_INVITER} credits per friend\n\n"
        f"💳 <b>/buy</b> - Instant recharge ($4.99+)\n"
        f"💰 <b>/balance</b> - Check your status\n\n"
        f"💡 <i>Pro tip: Check in for 5 days = 1 FREE video!</i>\n"
        f"<i>Millions of unique waifus waiting...</i>"
    )
    
    await update.message.reply_text(welcome_message, parse_mode='HTML')


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


@require_channel_membership
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


@require_channel_membership
async def roll(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /roll command - Generate random image."""
    user = update.effective_user
    db.get_or_create_user(user.id, user.username, user.first_name)
    
    # Check credits
    credits = db.get_credits(user.id)
    if credits < COST_IMAGE:
        await update.message.reply_text(
            f"💔 **Out of credits!**\n\n"
            f"You need **{COST_IMAGE} credit** but only have **{credits}**.\n\n"
            f"✅ /checkin - Get {CHECKIN_REWARD} free credits daily\n"
            f"👥 /invite - Get {REFERRAL_REWARD_INVITER} credits per friend\n"
            f"💳 /buy - Instant recharge from $4.99\n\n"
            f"_Don't stop now! Your waifu is waiting..._",
            parse_mode='Markdown'
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
    result_url = await call_api(IMAGE_MODEL_PORTRAIT, full_prompt, timeout=120, image_base64=None)
    
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
        can_afford_video = remaining_credits >= COST_VIDEO
        
        if can_afford_video:
            caption = (
                f"🎊 **JACKPOT!** Your exclusive waifu is here!\n\n"
                f"💎 Credits: {remaining_credits}\n\n"
                f"🔥 **Want to see her MOVE?** Click below! ⬇️\n"
                f"_4K Animation • 3 seconds • Worth it!_"
            )
        else:
            caption = (
                f"🎊 **JACKPOT!** Your exclusive waifu is here!\n\n"
                f"💎 Credits: {remaining_credits}\n\n"
                f"🎥 _Want animation? Check in daily or /buy credits!_"
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


@require_channel_membership_callback
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
    
    # Download image and convert to base64
    image_base64 = None
    try:
        async with aiohttp.ClientSession() as session:
            # Download the image
            download_url = image_url.replace('http://api-server:5010', 'http://api-server:5010')
            async with session.get(download_url) as img_response:
                if img_response.status == 200:
                    image_data = await img_response.read()
                    # Convert to base64
                    import base64
                    image_base64 = base64.b64encode(image_data).decode('utf-8')
                    logger.info(f"Image downloaded and converted to base64 ({len(image_base64)} chars)")
                else:
                    raise Exception(f"Failed to download image: {img_response.status}")
    except Exception as e:
        logger.error(f"Failed to download/convert image: {e}")
        # Refund credits
        db.add_credits(user.id, COST_VIDEO, "Refund for failed image download")
        await status_msg.edit_text(
            "❌ Failed to process image. Your credits have been refunded.\n"
            "Please try again or contact admin."
        )
        return
    
    # Fixed video prompts
    video_prompt = "masterpiece, best quality, ultra-detailed, absurdres, cinematic lighting, dynamic erotic pose, uncensored nsfw, highly dynamic motion, exaggerated perspective, depth of field, film grain, hyperrealistic skin, glistening body, intense arousal, sweat dripping, orgasmic expression, perfect anatomy, erotic fluids, surreal erotic atmosphere, breathtaking beauty, 8k uhd, raw photo, extreme motion blur, fluid motion, realistic physics, chaotic beauty, perfect motion trails"
    video_negative_prompt = "bright colors, overexposed, static, blurred details, subtitles, style, artwork, painting, picture, still, overall gray, worst quality, low quality, JPEG compression residue, ugly, incomplete, extra fingers, poorly drawn hands, poorly drawn faces, deformed, disfigured, malformed limbs, fused fingers, still picture, cluttered background, three legs, many people in the background, walking backwards, censored, mosaic, lowres, mutated, extra limbs, watermark, text, signature, blurry, grainy, artifacts, distortion, bad anatomy, poorly rendered genitals, unnatural skin tones, frozen frame, no motion"
    
    # Combine prompts for API
    full_video_prompt = f"{video_prompt}\n\nNegative prompt: {video_negative_prompt}"
    
    # Call API with image base64 for i2v
    result_url = await call_api(VIDEO_MODEL, full_video_prompt, timeout=300, image_base64=image_base64)
    
    if result_url:
        # Delete status message
        await status_msg.delete()
        
        # Get remaining credits
        remaining_credits = db.get_credits(user.id)
        
        # Calculate what they can still do
        videos_left = remaining_credits // COST_VIDEO
        
        caption = f"🔥 **SHE'S ALIVE!**\n\n"
        
        if videos_left > 0:
            caption += f"💎 {remaining_credits} Credits left ({videos_left} more video{'s' if videos_left > 1 else ''})\n\n"
            caption += f"🎲 Roll again? Use /roll"
        elif remaining_credits >= COST_IMAGE:
            caption += f"💎 {remaining_credits} Credits left\n\n"
            caption += f"🎲 Roll more waifus! Use /roll\n"
            caption += f"💳 Need more videos? /buy"
        else:
            caption += f"💎 {remaining_credits} Credits left\n\n"
            caption += f"✅ /checkin - Daily free credits\n"
            caption += f"👥 /invite - Invite 3 friends = 1 FREE video\n"
            caption += f"💳 /buy - Get more (from $4.99)"
        
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


@require_channel_membership
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
    
    # Calculate progress to free video
    credits_needed_for_video = COST_VIDEO
    invites_needed = 3  # 3 invites × 10 credits = 30 credits = 1 video
    remaining_invites = max(0, invites_needed - invited_count)
    
    message = (
        f"🎁 **Free NSFW Video Hack**\n\n"
        f"Invite **3 friends** to join Lili AI, and you get enough credits for a **FREE 4K Video Animation!**\n\n"
        f"👇 **Your Secret Link** _(Friends get +{REFERRAL_REWARD_INVITEE} Bonus Credits)_:\n"
        f"`{invite_link}`\n\n"
        f"📊 **Stats:**\n"
        f"• You have invited **{invited_count}** people\n"
        f"• You earned **{total_earned} credits** from referrals\n"
    )
    
    if invited_count < invites_needed:
        message += f"• **{remaining_invites} more friends** = FREE Video! 🎥\n"
    else:
        message += f"• 🎉 **You unlocked FREE videos!** Keep inviting for more!\n"
    
    message += f"\n_Share anywhere: Discord, Reddit, Twitter, WhatsApp!_"
    
    await update.message.reply_text(message, parse_mode='Markdown')


@require_channel_membership
async def checkin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /checkin command - Daily check-in."""
    user = update.effective_user
    db.get_or_create_user(user.id, user.username, user.first_name)
    
    try:
        result = db.daily_checkin(user.id)
        
        if result['success']:
            reward = result['reward']
            streak = result['streak']
            new_balance = db.get_credits(user.id)
            
            # 计算距离免费视频还差多少
            needed_for_video = max(0, COST_VIDEO - new_balance)
            days_to_video = max(0, (needed_for_video + CHECKIN_REWARD - 1) // CHECKIN_REWARD)
            
            # Streak emoji progression
            if streak >= 7:
                streak_emoji = "🔥🔥🔥"
            elif streak >= 3:
                streak_emoji = "🔥🔥"
            else:
                streak_emoji = "🔥"
            
            message = (
                f"✅ **Check-in Successful!**\n\n"
                f"You got **+{reward} Credits**.\n\n"
                f"📅 Streak: **{streak} Day{'s' if streak > 1 else ''}** {streak_emoji}\n"
                f"💰 Balance: **{new_balance} Credits**\n\n"
            )
            
            if new_balance >= COST_VIDEO:
                message += f"🎉 **UNLOCKED!** You can make a video now!\n💡 Use /roll first, then animate it!\n"
            else:
                message += f"📉 Only **{needed_for_video} Credits** left until your first Video!\n"
                if days_to_video > 0:
                    message += f"🎯 **{days_to_video} more day{'s' if days_to_video > 1 else ''}** = FREE Video!\n"
            
            message += f"\n⏰ Come back tomorrow!"
            
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
    except Exception as e:
        logger.error(f"Check-in error for user {user.id}: {e}")
        import traceback
        traceback.print_exc()
        await update.message.reply_text(
            "❌ An error occurred during check-in. Please contact admin if this persists."
        )


@require_channel_membership
async def buy_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /buy command - Show payment options."""
    user = update.effective_user
    db.get_or_create_user(user.id, user.username, user.first_name)
    
    # 构建套餐选项按钮
    keyboard = []
    has_payment_methods = False
    
    # Plisio 加密货币支付 - 四个套餐
    if PLISIO_SECRET_KEY:
        keyboard.append([
            InlineKeyboardButton("🧪 Test ($1.00)", callback_data="package:test"),
        ])
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
        
        test = PACKAGES['test']
        mini = PACKAGES['mini']
        pro = PACKAGES['pro']
        ultra = PACKAGES['ultra']
        
        message = (
            "💰 **TOP UP BALANCE**\n\n"
            "🔓 _Unlock uncensored videos & priority queue!_\n\n"
            f"🧪 **Test Pack - ${test['price']}**\n"
            f"   👉 **{test['credits']} Credits** (testing only)\n\n"
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


@require_channel_membership_callback
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


@require_channel_membership_callback
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
            
            # Plisio API 参数 - 注意：Plisio API 使用 GET 请求
            params = {
                "api_key": PLISIO_SECRET_KEY,
                "order_name": f"{package['name']} - {credits} Credits",
                "order_number": order_id,  # Plisio 要求使用 order_number 而不是 order_id
                "source_currency": "USD",  # 源货币
                "source_amount": amount,
                "callback_url": f"{SERVER_DOMAIN}/webhooks/plisio",
                "description": f"Purchase {package['name']} - {credits} credits"
            }
            
            # 如果要限制特定的加密货币，可以添加 allowed_psys_cids
            # params["allowed_psys_cids"] = "BTC,ETH,USDT"
            
            # Plisio API 使用 GET 请求，参数作为 query parameters
            async with session.get(
                url,
                params=params,
                timeout=aiohttp.ClientTimeout(total=30)
            ) as response:
                if response.status == 200:
                    result = await response.json()
                    # 简洁日志：只记录成功
                    # logger.info(f"Plisio API response: {result}")
                    
                    # Plisio 成功响应格式：{"status": "success", "data": {...}}
                    if result.get("status") == "success" or result.get("data"):
                        invoice_data = result.get("data", {})
                        invoice_url = invoice_data.get("invoice_url")
                        txn_id = invoice_data.get("txn_id")
                        
                        if invoice_url:
                            # 使用 Plisio 的 txn_id 作为主要引用（如果没有则使用 order_id）
                            external_ref = txn_id or order_id
                            
                            # 创建待处理记录
                            db.create_pending_payment(
                                user_id=user.id,
                                amount=credits,
                                money_amount=float(amount),
                                currency='USD',
                                provider='plisio',
                                external_ref=external_ref,
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
                            # 详细日志：记录完整信息
                            logger.info(f"✅ Plisio invoice created for user {user.id}: {order_id}, txn_id: {txn_id}")
                        else:
                            await query.message.reply_text("❌ Failed to create payment invoice. Please try again.")
                            logger.error(f"Plisio: No invoice URL in response: {result}")
                    else:
                        error_msg = result.get("message", result.get("error", "Unknown error"))
                        await query.message.reply_text(f"❌ Payment service error: {error_msg}")
                        logger.error(f"Plisio API error: {error_msg}, full response: {result}")
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
        # Silently ignore for non-admins (don't reveal the command exists)
        return
    
    # Parse arguments
    if len(context.args) != 2:
        await update.message.reply_text(
            "❌ **Usage:** `/add_credits [user_id] [amount]`\n"
            "**Example:** `/add_credits 123456789 100`",
            parse_mode='Markdown'
        )
        return
    
    try:
        target_user_id = int(context.args[0])
        amount = int(context.args[1])
        
        if amount <= 0:
            await update.message.reply_text("❌ Amount must be positive.")
            return
        
        # Check if target user exists
        with db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT first_name FROM users WHERE user_id = ?", (target_user_id,))
            target_user = cursor.fetchone()
            
            if not target_user:
                await update.message.reply_text(
                    f"❌ User `{target_user_id}` not found in database.\n"
                    "_The user needs to /start the bot first._",
                    parse_mode='Markdown'
                )
                return
        
        # Add credits
        success = db.add_credits(target_user_id, amount, f"Admin top-up by {user.id}")
        
        if success:
            new_balance = db.get_credits(target_user_id)
            await update.message.reply_text(
                f"✅ **Credits Added**\n\n"
                f"👤 User: `{target_user_id}`\n"
                f"💎 Amount: **+{amount} credits**\n"
                f"💰 New Balance: **{new_balance} credits**",
                parse_mode='Markdown'
            )
            
            # Notify the user (optional)
            try:
                await context.bot.send_message(
                    chat_id=target_user_id,
                    text=(
                        f"🎁 **Admin Gift!**\n\n"
                        f"You received **{amount} credits** from admin!\n"
                        f"💰 New Balance: **{new_balance} credits**\n\n"
                        f"Use /balance to check your account."
                    ),
                    parse_mode='Markdown'
                )
            except Exception as notify_error:
                logger.warning(f"Failed to notify user {target_user_id}: {notify_error}")
                # Don't fail the command if notification fails
        else:
            await update.message.reply_text("❌ Failed to add credits. Please try again.")
    
    except ValueError:
        await update.message.reply_text("❌ Invalid arguments. Both user_id and amount must be numbers.")
    except Exception as e:
        logger.error(f"Error in add_credits: {e}")
        import traceback
        traceback.print_exc()
        await update.message.reply_text(f"❌ Error: {str(e)}")


async def delete_user_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /delete_user command - Delete a user (admin only)."""
    user = update.effective_user
    
    # Check if user is admin
    if user.id not in ADMIN_IDS:
        return
    
    # Parse arguments
    if len(context.args) != 1:
        await update.message.reply_text(
            "❌ <b>Usage:</b> <code>/delete_user [user_id]</code>\n"
            "<b>Example:</b> <code>/delete_user 123456789</code>\n"
            "⚠️ This will permanently delete the user and all their data!",
            parse_mode='HTML'
        )
        return
    
    try:
        target_user_id = int(context.args[0])
        
        # Check if target user exists
        with db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT first_name, username FROM users WHERE user_id = ?", (target_user_id,))
            target_user = cursor.fetchone()
            
            if not target_user:
                await update.message.reply_text(f"❌ User <code>{target_user_id}</code> not found.", parse_mode='HTML')
                return
            
            # Delete user's transactions first
            cursor.execute("DELETE FROM transactions WHERE user_id = ?", (target_user_id,))
            deleted_txs = cursor.rowcount
            
            # Delete user
            cursor.execute("DELETE FROM users WHERE user_id = ?", (target_user_id,))
            
            import html
            first_name = html.escape(target_user['first_name'] or "Unknown")
            username_text = f"@{target_user['username']}" if target_user['username'] else "no username"
            
            await update.message.reply_text(
                f"✅ <b>User Deleted</b>\n\n"
                f"👤 Name: {first_name}\n"
                f"🆔 ID: <code>{target_user_id}</code>\n"
                f"👤 Username: {username_text}\n"
                f"🗑️ Deleted {deleted_txs} transactions",
                parse_mode='HTML'
            )
            
            logger.info(f"Admin {user.id} deleted user {target_user_id}")
        
    except ValueError:
        await update.message.reply_text("❌ Invalid user_id. Must be a number.")
    except Exception as e:
        logger.error(f"Error deleting user: {e}")
        import traceback
        traceback.print_exc()
        await update.message.reply_text(f"❌ Error: {str(e)}")


async def admin_dashboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /admin command - Show admin dashboard (admin only)."""
    user = update.effective_user
    
    # Check if user is admin
    if user.id not in ADMIN_IDS:
        # Silently ignore for non-admins
        return
    
    try:
        # Get statistics from database
        total_users = db.get_user_count()
        new_today = db.get_new_users_today()
        daily_revenue = db.get_daily_revenue()
        total_revenue = db.get_total_revenue()
        
        # Get current task queue info (placeholder - would need actual queue system)
        # For now, we'll just show 0
        queue_length = 0
        
        stats_msg = (
            "📈 **Lili AI - Admin Dashboard**\n\n"
            "👥 **Users:**\n"
            f"• Total Users: **{total_users}**\n"
            f"• New Today: **{new_today}** 🆕\n\n"
            
            "💰 **Revenue:**\n"
            f"• Today: **${daily_revenue:.2f}**\n"
            f"• Total: **${total_revenue:.2f}**\n\n"
            
            "⚙️ **System:**\n"
            f"• Queue Length: **{queue_length}** tasks\n\n"
            
            "🛠 **Admin Commands:**\n"
            "• `/add_credits [user_id] [amount]` - 给用户添加积分\n"
            "• `/view_user [user_id]` - 查看用户详情\n"
            "• `/view_orders [user_id]` - 查看用户订单\n"
            "• `/stats` - 详细统计信息\n"
            "• `/list_users [limit]` - 列出最近用户\n"
            "• `/delete_user [user_id]` - 删除测试用户\n"
            "• `/broadcast [message]` - 广播消息\n\n"
            
            "_Real-time data from database_"
        )
        
        await update.message.reply_text(stats_msg, parse_mode='Markdown')
        
    except Exception as e:
        logger.error(f"Admin dashboard error: {e}")
        await update.message.reply_text(
            "❌ Error loading dashboard. Check logs for details."
        )


async def view_user_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /view_user command - View user details (admin only)."""
    user = update.effective_user
    
    # Check if user is admin
    if user.id not in ADMIN_IDS:
        return
    
    # Parse arguments
    if len(context.args) != 1:
        await update.message.reply_text(
            "❌ **Usage:** `/view_user [user_id]`\n"
            "**Example:** `/view_user 123456789`",
            parse_mode='Markdown'
        )
        return
    
    try:
        from datetime import datetime, date
        
        target_user_id = int(context.args[0])
        today = date.today().isoformat()
        current_month = datetime.now().strftime('%Y-%m')
        
        with db.get_connection() as conn:
            cursor = conn.cursor()
            
            # Get user info
            cursor.execute("""
                SELECT user_id, username, first_name, credits, invited_by,
                       checkin_streak, total_checkins, last_checkin, created_at
                FROM users WHERE user_id = ?
            """, (target_user_id,))
            user_info = cursor.fetchone()
            
            if not user_info:
                await update.message.reply_text(f"❌ User `{target_user_id}` not found.", parse_mode='Markdown')
                return
            
            # Get transaction history count
            cursor.execute("""
                SELECT COUNT(*) as count FROM credit_history
                WHERE user_id = ?
            """, (target_user_id,))
            transaction_count = cursor.fetchone()['count']
            
            # Get total spent credits
            cursor.execute("""
                SELECT SUM(ABS(amount)) as spent FROM credit_history
                WHERE user_id = ? AND amount < 0
            """, (target_user_id,))
            total_spent = cursor.fetchone()['spent'] or 0
            
            # Get referral count
            cursor.execute("""
                SELECT COUNT(*) as count FROM users
                WHERE invited_by = ?
            """, (target_user_id,))
            referral_count = cursor.fetchone()['count']
            
            # 🆕 Get referral details - who paid and how much
            cursor.execute("""
                SELECT 
                    u.user_id,
                    u.username,
                    u.first_name,
                    COALESCE(SUM(CASE WHEN t.status = 'completed' AND t.money_amount IS NOT NULL 
                                 THEN t.money_amount ELSE 0 END), 0) as total_paid
                FROM users u
                LEFT JOIN transactions t ON u.user_id = t.user_id
                WHERE u.invited_by = ?
                GROUP BY u.user_id, u.username, u.first_name
                HAVING total_paid > 0
                ORDER BY total_paid DESC
            """, (target_user_id,))
            paid_referrals = cursor.fetchall()
            
            # 🆕 Calculate total revenue from referrals
            total_referral_revenue = sum(ref['total_paid'] for ref in paid_referrals)
            paid_referral_count = len(paid_referrals)
            
            # Get payment history - All time
            cursor.execute("""
                SELECT COUNT(*) as count, COALESCE(SUM(money_amount), 0) as total
                FROM payments
                WHERE user_id = ? AND status = 'completed'
            """, (target_user_id,))
            payment_info = cursor.fetchone()
            payment_count = payment_info['count'] or 0
            total_paid = payment_info['total'] or 0
            
            # 🆕 Get today's payments
            cursor.execute("""
                SELECT COALESCE(SUM(money_amount), 0) as total
                FROM payments
                WHERE user_id = ? AND status = 'completed' 
                AND DATE(created_at) = ?
            """, (target_user_id, today))
            today_paid = cursor.fetchone()['total'] or 0
            
            # 🆕 Get this month's payments
            cursor.execute("""
                SELECT COALESCE(SUM(money_amount), 0) as total
                FROM payments
                WHERE user_id = ? AND status = 'completed' 
                AND strftime('%Y-%m', created_at) = ?
            """, (target_user_id, current_month))
            month_paid = cursor.fetchone()['total'] or 0
        
        # Format last checkin
        last_checkin = user_info['last_checkin'] or "Never"
        if last_checkin != "Never":
            last_checkin = last_checkin[:10]  # Show only date
        
        # Format created_at
        created_at = user_info['created_at'][:10] if user_info['created_at'] else "Unknown"
        
        # Build message
        inviter_text = f"`{user_info['invited_by']}`" if user_info['invited_by'] else "Direct"
        username_text = f"@{user_info['username']}" if user_info['username'] else "No username"
        safe_name = safe_markdown_name(user_info['first_name'])
        
        message = (
            f"👤 **User Details**\n\n"
            f"🆔 **ID:** `{user_info['user_id']}`\n"
            f"👤 **Name:** {safe_name}\n"
            f"🔖 **Username:** {username_text}\n"
            f"📅 **Joined:** {created_at}\n\n"
            
            f"💎 **Credits:** {user_info['credits']}\n"
            f"📊 **Total Spent:** {total_spent} credits\n"
            f"📝 **Transactions:** {transaction_count}\n\n"
            
            f"✅ **Check-ins:** {user_info['total_checkins']} total\n"
            f"🔥 **Current Streak:** {user_info['checkin_streak']} days\n"
            f"🕒 **Last Check-in:** {last_checkin}\n\n"
            
            f"👥 **Referrals:** {referral_count} users invited\n"
            f"💰 **Paid Referrals:** {paid_referral_count}/{referral_count} users\n"
            f"💵 **Referral Revenue:** ${total_referral_revenue:.2f}\n"
            f"📥 **Invited By:** {inviter_text}\n\n"
        )
        
        # 🆕 Show paid referrals details (top 5)
        if paid_referrals:
            message += f"**💎 Top Paying Referrals:**\n"
            for i, ref in enumerate(paid_referrals[:5], 1):
                ref_display = ref['username'] or safe_markdown_name(ref['first_name']) or f"User_{ref['user_id']}"
                message += f"{i}. {ref_display}: ${ref['total_paid']:.2f}\n"
            if len(paid_referrals) > 5:
                message += f"_...and {len(paid_referrals) - 5} more_\n"
            message += "\n"
        
        # Payment statistics
        message += (
            f"💳 **Payment History:**\n"
            f"📦 **Orders:** {payment_count} completed\n"
            f"💰 **Total (All Time):** ${total_paid:.2f}\n"
            f"📅 **This Month:** ${month_paid:.2f}\n"
            f"🕐 **Today:** ${today_paid:.2f}\n\n"
            
            f"🛠️ **Quick Actions:**\n"
            f"• `/add_credits {target_user_id} [amount]`\n"
            f"• `/view_orders {target_user_id}`"
        )
        
        await update.message.reply_text(message, parse_mode='Markdown')
        
    except ValueError:
        await update.message.reply_text("❌ Invalid user_id. Must be a number.")
    except Exception as e:
        logger.error(f"Error viewing user: {e}")
        import traceback
        traceback.print_exc()
        await update.message.reply_text(f"❌ Error: {str(e)}")


async def view_orders_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /view_orders command - View user payment history (admin only)."""
    user = update.effective_user
    
    # Check if user is admin
    if user.id not in ADMIN_IDS:
        return
    
    # Parse arguments
    if len(context.args) < 1:
        await update.message.reply_text(
            "❌ **Usage:** `/view_orders [user_id] [limit]`\n"
            "**Example:** `/view_orders 123456789 10`\n"
            "_(limit is optional, default is 10)_",
            parse_mode='Markdown'
        )
        return
    
    try:
        target_user_id = int(context.args[0])
        limit = int(context.args[1]) if len(context.args) > 1 else 10
        
        with db.get_connection() as conn:
            cursor = conn.cursor()
            
            # Check if user exists
            cursor.execute("SELECT first_name FROM users WHERE user_id = ?", (target_user_id,))
            user_info = cursor.fetchone()
            
            if not user_info:
                await update.message.reply_text(f"❌ User `{target_user_id}` not found.")
                return
            
            # Get payment history
            cursor.execute("""
                SELECT payment_id, amount, money_amount, currency, status, 
                       provider, external_ref, created_at, completed_at
                FROM payments
                WHERE user_id = ?
                ORDER BY created_at DESC
                LIMIT ?
            """, (target_user_id, limit))
            
            orders = cursor.fetchall()
        
        if not orders:
            await update.message.reply_text(
                f"📋 **Payment History for User {target_user_id}**\n\n"
                f"No orders found."
            )
            return
        
        # Build message
        safe_name = safe_markdown_name(user_info['first_name'])
        message = f"📋 **Payment History for {safe_name}** (`{target_user_id}`)\n\n"
        
        for order in orders:
            status_emoji = {
                'completed': '✅',
                'pending': '⏳',
                'failed': '❌',
                'cancelled': '🚫'
            }.get(order['status'], '❓')
            
            created = order['created_at'][:16] if order['created_at'] else "Unknown"
            completed = order['completed_at'][:16] if order['completed_at'] else "-"
            
            message += (
                f"{status_emoji} **Order #{order['payment_id']}**\n"
                f"  💰 ${order['money_amount']:.2f} {order['currency']}\n"
                f"  💎 {order['amount']} credits\n"
                f"  🔧 {order['provider']}\n"
                f"  📅 {created}\n"
            )
            
            if order['status'] == 'completed':
                message += f"  ✅ Completed: {completed}\n"
            
            message += "\n"
        
        message += f"_Showing latest {len(orders)} orders_"
        
        await update.message.reply_text(message, parse_mode='Markdown')
        
    except ValueError:
        await update.message.reply_text("❌ Invalid arguments. user_id and limit must be numbers.")
    except Exception as e:
        logger.error(f"Error viewing orders: {e}")
        await update.message.reply_text(f"❌ Error: {str(e)}")


async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /stats command - Show detailed statistics (admin only)."""
    user = update.effective_user
    
    # Check if user is admin
    if user.id not in ADMIN_IDS:
        return
    
    try:
        with db.get_connection() as conn:
            cursor = conn.cursor()
            
            # User statistics
            cursor.execute("SELECT COUNT(*) as total FROM users")
            total_users = cursor.fetchone()['total']
            
            cursor.execute("""
                SELECT COUNT(*) as today FROM users
                WHERE DATE(created_at) = DATE('now')
            """)
            new_today = cursor.fetchone()['today']
            
            cursor.execute("""
                SELECT COUNT(*) as week FROM users
                WHERE DATE(created_at) >= DATE('now', '-7 days')
            """)
            new_week = cursor.fetchone()['week']
            
            # Payment statistics
            cursor.execute("""
                SELECT COUNT(*) as count, SUM(money_amount) as total
                FROM payments WHERE status = 'completed'
            """)
            payment_stats = cursor.fetchone()
            total_orders = payment_stats['count'] or 0
            total_revenue = payment_stats['total'] or 0
            
            cursor.execute("""
                SELECT COUNT(*) as count, SUM(money_amount) as total
                FROM payments
                WHERE status = 'completed' AND DATE(completed_at) = DATE('now')
            """)
            today_stats = cursor.fetchone()
            orders_today = today_stats['count'] or 0
            revenue_today = today_stats['total'] or 0
            
            cursor.execute("""
                SELECT COUNT(*) as count FROM payments WHERE status = 'pending'
            """)
            pending_orders = cursor.fetchone()['count']
            
            # Credit statistics
            cursor.execute("SELECT SUM(credits) as total FROM users")
            total_credits = cursor.fetchone()['total'] or 0
            
            cursor.execute("SELECT AVG(credits) as avg FROM users")
            avg_credits = cursor.fetchone()['avg'] or 0
            
            # Checkin statistics
            cursor.execute("""
                SELECT COUNT(*) as count FROM users WHERE total_checkins > 0
            """)
            checkin_users = cursor.fetchone()['count']
            
            cursor.execute("""
                SELECT MAX(checkin_streak) as max_streak FROM users
            """)
            max_streak = cursor.fetchone()['max_streak'] or 0
            
            # Referral statistics
            cursor.execute("""
                SELECT COUNT(*) as count FROM users WHERE invited_by IS NOT NULL
            """)
            referred_users = cursor.fetchone()['count']
            
            # Top users by credits
            cursor.execute("""
                SELECT user_id, first_name, credits
                FROM users
                ORDER BY credits DESC
                LIMIT 5
            """)
            top_users = cursor.fetchall()
        
        # Build message
        message = (
            "📊 **Detailed Statistics**\n\n"
            
            "👥 **Users:**\n"
            f"• Total: **{total_users}**\n"
            f"• New Today: **{new_today}**\n"
            f"• New This Week: **{new_week}**\n"
            f"• With Referrals: **{referred_users}** ({referred_users/total_users*100:.1f}%)\n\n"
            
            "💰 **Revenue:**\n"
            f"• Total: **${total_revenue:.2f}** ({total_orders} orders)\n"
            f"• Today: **${revenue_today:.2f}** ({orders_today} orders)\n"
            f"• Pending: **{pending_orders}** orders\n"
            f"• ARPU: **${total_revenue/total_users:.2f}**\n\n"
            
            "💎 **Credits:**\n"
            f"• Total in System: **{total_credits:,}**\n"
            f"• Average per User: **{avg_credits:.1f}**\n\n"
            
            "✅ **Engagement:**\n"
            f"• Check-in Users: **{checkin_users}** ({checkin_users/total_users*100:.1f}%)\n"
            f"• Max Streak: **{max_streak}** days\n\n"
            
            "🏆 **Top Users by Credits:**\n"
        )
        
        for i, u in enumerate(top_users, 1):
            safe_name = safe_markdown_name(u['first_name'])
            message += f"{i}. {safe_name} - {u['credits']} credits\n"
        
        await update.message.reply_text(message, parse_mode='Markdown')
        
    except Exception as e:
        logger.error(f"Error getting stats: {e}")
        import traceback
        traceback.print_exc()
        await update.message.reply_text(f"❌ Error: {str(e)}")


async def list_users_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /list_users command - List recent users (admin only)."""
    user = update.effective_user
    
    # Check if user is admin
    if user.id not in ADMIN_IDS:
        return
    
    try:
        limit = int(context.args[0]) if context.args else 20
        limit = min(limit, 50)  # Max 50 users
        
        with db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT user_id, username, first_name, credits, 
                       total_checkins, created_at
                FROM users
                ORDER BY created_at DESC
                LIMIT ?
            """, (limit,))
            
            users = cursor.fetchall()
        
        if not users:
            await update.message.reply_text("No users found.")
            return
        
        # Build message
        import html
        message = f"👥 <b>Latest {len(users)} Users</b>\n\n"
        
        for u in users:
            # Escape HTML special characters
            first_name = html.escape(u['first_name'] or "Unknown")
            username_text = f"@{u['username']}" if u['username'] else "无用户名"
            created = u['created_at'][:10] if u['created_at'] else "Unknown"
            
            message += (
                f"• {first_name} ({username_text})\n"
                f"  ID: <code>{u['user_id']}</code> | 💎 {u['credits']} | ✅ {u['total_checkins']}\n"
                f"  📅 {created}\n\n"
            )
        
        message += f"<i>Use /view_user [id] for details</i>"
        
        await update.message.reply_text(message, parse_mode='HTML')
        
    except Exception as e:
        logger.error(f"Error listing users: {e}")
        import traceback
        traceback.print_exc()
        await update.message.reply_text(f"❌ Error: {str(e)}")


async def broadcast_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /broadcast command - Send message to all users (admin only)."""
    user = update.effective_user
    
    # Check if user is admin
    if user.id not in ADMIN_IDS:
        # Silently ignore for non-admins
        return
    
    # Get message
    if not context.args:
        await update.message.reply_text(
            "❌ **Usage:** `/broadcast [message]`\n"
            "**Example:** `/broadcast 🎉 New feature available! Try /roll now!`",
            parse_mode='Markdown'
        )
        return
    
    message = ' '.join(context.args)
    
    # Confirm before sending
    await update.message.reply_text(
        f"🚀 **Starting broadcast...**\n\n"
        f"Message:\n{message}\n\n"
        f"_Sending to all users..._"
    )
    
    # Get all user IDs
    all_users = db.get_all_user_ids()
    
    success_count = 0
    failed_count = 0
    
    for uid in all_users:
        try:
            await context.bot.send_message(
                chat_id=uid,
                text=message,
                parse_mode='Markdown'
            )
            success_count += 1
            
            # Add delay to prevent Telegram rate limiting
            await asyncio.sleep(0.05)  # 50ms delay between messages
            
        except Exception as e:
            # User may have blocked the bot or deleted their account
            failed_count += 1
            logger.debug(f"Failed to send broadcast to {uid}: {e}")
    
    # Send summary
    await update.message.reply_text(
        f"✅ **Broadcast Complete!**\n\n"
        f"📤 Sent: **{success_count}** users\n"
        f"❌ Failed: **{failed_count}** users\n"
        f"👥 Total: **{len(all_users)}** users",
        parse_mode='Markdown'
    )


async def support_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /support command - Show support contact info."""
    user = update.effective_user
    
    # Admin username (hardcoded)
    SUPPORT_USERNAME = "XiangFengZhiNai"
    
    msg = (
        "🆘 **Lili AI Support Center**\n\n"
        "Having issues with payments or generation?\n"
        "Found a bug or have suggestions?\n\n"
        f"👨‍💻 **Contact Admin:** @{SUPPORT_USERNAME}\n"
        "_(Click the username to open chat)_\n\n"
        "💡 **Tip:** When contacting support, please include:\n"
        f"🆔 Your User ID: `{user.id}`\n\n"
        "_Copy your ID and send it to admin for faster help!_"
    )
    
    # Create button for direct message
    keyboard = [[
        InlineKeyboardButton(
            "💬 Contact Admin",
            url=f"https://t.me/{SUPPORT_USERNAME}"
        )
    ]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        msg,
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )


async def set_comfyui_endpoint_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /set_endpoint command - Change ComfyUI endpoint (admin only)."""
    user = update.effective_user
    
    # Check if user is admin
    if user.id not in ADMIN_IDS:
        return
    
    # Parse arguments: /set_endpoint [type] [url]
    # type: image or video
    if len(context.args) < 2:
        await update.message.reply_text(
            "❌ **Usage:** `/set_endpoint [type] [url]`\n\n"
            "**Type:** `image` or `video`\n"
            "**Example:** `/set_endpoint video https://n008.unicorn.org.cn:20155`\n"
            "**Example:** `/set_endpoint image http://dx.qyxc.vip:18188`",
            parse_mode='Markdown'
        )
        return
    
    endpoint_type = context.args[0].lower()
    new_url = context.args[1]
    
    if endpoint_type not in ['image', 'video']:
        await update.message.reply_text(
            "❌ Invalid type. Must be `image` or `video`.",
            parse_mode='Markdown'
        )
        return
    
    # Validate URL format
    if not (new_url.startswith('http://') or new_url.startswith('https://')):
        await update.message.reply_text(
            "❌ Invalid URL format. Must start with `http://` or `https://`",
            parse_mode='Markdown'
        )
        return
    
    # Remove trailing slash
    new_url = new_url.rstrip('/')
    
    try:
        # Call server API to update endpoint
        async with aiohttp.ClientSession() as session:
            headers = {
                "Authorization": f"Bearer {API_KEY}",
                "Content-Type": "application/json"
            }
            
            # Get server base URL from API_URL
            import re
            server_base = re.match(r'(https?://[^/]+)', API_URL)
            if not server_base:
                raise Exception("Cannot determine server base URL")
            
            update_url = f"{server_base.group(1)}/api/update_endpoint"
            
            payload = {
                "type": endpoint_type,
                "url": new_url
            }
            
            async with session.post(
                update_url,
                json=payload,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=10)
            ) as response:
                if response.status == 200:
                    result = await response.json()
                    await update.message.reply_text(
                        f"✅ **ComfyUI Endpoint Updated**\n\n"
                        f"📡 Type: **{endpoint_type.upper()}**\n"
                        f"🔗 New URL: `{new_url}`\n\n"
                        f"_Changes applied immediately!_",
                        parse_mode='Markdown'
                    )
                    logger.info(f"Admin {user.id} updated {endpoint_type} endpoint to {new_url}")
                else:
                    error_text = await response.text()
                    await update.message.reply_text(
                        f"❌ Failed to update endpoint: {error_text}"
                    )
    
    except Exception as e:
        logger.error(f"Error updating endpoint: {e}")
        import traceback
        traceback.print_exc()
        await update.message.reply_text(
            f"❌ Error: {str(e)}\n\n"
            "_Note: Make sure the server supports dynamic endpoint updates._",
            parse_mode='Markdown'
        )


async def get_endpoints_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /get_endpoints command - Show current ComfyUI endpoints (admin only)."""
    user = update.effective_user
    
    # Check if user is admin
    if user.id not in ADMIN_IDS:
        return
    
    try:
        # Call server API to get current endpoints
        async with aiohttp.ClientSession() as session:
            headers = {
                "Authorization": f"Bearer {API_KEY}",
                "Content-Type": "application/json"
            }
            
            # Get server base URL from API_URL
            import re
            server_base = re.match(r'(https?://[^/]+)', API_URL)
            if not server_base:
                raise Exception("Cannot determine server base URL")
            
            get_url = f"{server_base.group(1)}/api/get_endpoints"
            
            async with session.get(
                get_url,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=10)
            ) as response:
                if response.status == 200:
                    result = await response.json()
                    image_url = result.get('image_url', 'Unknown')
                    video_url = result.get('video_url', 'Unknown')
                    
                    await update.message.reply_text(
                        f"📡 **Current ComfyUI Endpoints**\n\n"
                        f"🖼️ **Image Generation:**\n"
                        f"`{image_url}`\n\n"
                        f"🎬 **Video Generation:**\n"
                        f"`{video_url}`\n\n"
                        f"💡 Use `/set_endpoint [type] [url]` to change",
                        parse_mode='Markdown'
                    )
                else:
                    error_text = await response.text()
                    await update.message.reply_text(
                        f"❌ Failed to get endpoints: {error_text}"
                    )
    
    except Exception as e:
        logger.error(f"Error getting endpoints: {e}")
        import traceback
        traceback.print_exc()
        await update.message.reply_text(
            f"❌ Error: {str(e)}",
            parse_mode='Markdown'
        )


async def storage_status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /storage command - Show storage usage (admin only)."""
    user = update.effective_user
    
    # Check if user is admin
    if user.id not in ADMIN_IDS:
        return
    
    try:
        # Call server API to get storage status
        async with aiohttp.ClientSession() as session:
            headers = {
                "Authorization": f"Bearer {API_KEY}",
                "Content-Type": "application/json"
            }
            
            # Get server base URL from API_URL
            import re
            server_base = re.match(r'(https?://[^/]+)', API_URL)
            if not server_base:
                raise Exception("Cannot determine server base URL")
            
            status_url = f"{server_base.group(1)}/api/storage_status"
            
            async with session.get(
                status_url,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=10)
            ) as response:
                if response.status == 200:
                    result = await response.json()
                    used_gb = result.get('used_gb', 0)
                    max_gb = result.get('max_gb', 20)
                    usage_percent = result.get('usage_percent', 0)
                    file_count = result.get('file_count', 0)
                    
                    # 根据使用率显示不同的emoji
                    if usage_percent < 50:
                        status_emoji = "🟢"
                    elif usage_percent < 80:
                        status_emoji = "🟡"
                    else:
                        status_emoji = "🔴"
                    
                    # 创建进度条
                    bar_length = 10
                    filled = int((usage_percent / 100) * bar_length)
                    bar = "█" * filled + "░" * (bar_length - filled)
                    
                    await update.message.reply_text(
                        f"💾 **Storage Status**\n\n"
                        f"{status_emoji} **Usage:** {used_gb}GB / {max_gb}GB ({usage_percent}%)\n"
                        f"`{bar}` {usage_percent}%\n\n"
                        f"📁 **Files:** {file_count} files\n"
                        f"📊 **Available:** {max_gb - used_gb:.2f}GB\n\n"
                        f"💡 _Cleanup triggers at {max_gb}GB (oldest files removed first)_",
                        parse_mode='Markdown'
                    )
                else:
                    error_text = await response.text()
                    await update.message.reply_text(
                        f"❌ Failed to get storage status: {error_text}"
                    )
    
    except Exception as e:
        logger.error(f"Error getting storage status: {e}")
        import traceback
        traceback.print_exc()
        await update.message.reply_text(
            f"❌ Error: {str(e)}",
            parse_mode='Markdown'
        )


async def check_join_status_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle '✅ I Have Joined' button callback - 验证用户是否真的加入了频道."""
    query = update.callback_query
    user = query.from_user
    
    await query.answer()  # Acknowledge the button click
    
    if not REQUIRED_CHANNEL:
        await query.edit_message_text("✅ Channel verification is not required.")
        return
    
    # 🎁 解析回调数据，提取推荐码
    referrer_id = None
    callback_data = query.data
    if ':' in callback_data:
        # Format: check_join_status:123456789
        parts = callback_data.split(':')
        if len(parts) == 2 and parts[1] != 'None':
            try:
                referrer_id = int(parts[1])
                logger.info(f"Found referrer {referrer_id} in callback data for user {user.id}")
            except:
                pass
    
    try:
        # 再次检查用户的频道成员状态
        member = await context.bot.get_chat_member(chat_id=REQUIRED_CHANNEL, user_id=user.id)
        
        if member.status in ['member', 'administrator', 'creator']:
            # ✅ 验证通过！用户已经加入频道
            logger.info(f"✅ User {user.id} verified channel membership")
            
            # 检查用户是否已经注册过（防止重复注册）
            with db.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT user_id FROM users WHERE user_id = ?", (user.id,))
                existing_user = cursor.fetchone()
            
            if not existing_user:
                # 新用户：创建账户并发放15积分
                db.get_or_create_user(
                    user_id=user.id,
                    username=user.username,
                    first_name=user.first_name,
                    invited_by=referrer_id  # 🎁 传递推荐人ID
                )
                
                # 🎁 处理推荐奖励
                referral_bonus_message = ""
                if referrer_id and referrer_id != user.id:
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
                            
                            referral_bonus_message = (
                                f"\n🎁 <b>Referral Bonus!</b>\n"
                                f"You got <b>+{REFERRAL_REWARD_INVITEE} extra credits</b> from invitation!\n\n"
                            )
                            
                            logger.info(f"Referral: {referrer_id} invited {user.id}")
                            
                            # Notify referrer
                            try:
                                await context.bot.send_message(
                                    chat_id=referrer_id,
                                    text=(
                                        f"🎉 <b>Someone used your invite link!</b>\n\n"
                                        f"You earned <b>+{REFERRAL_REWARD_INVITER} credits</b>!\n"
                                        f"Keep sharing: /invite"
                                    ),
                                    parse_mode='HTML'
                                )
                            except:
                                pass
                
                total_credits = NEW_USER_BONUS + (REFERRAL_REWARD_INVITEE if referrer_id else 0)
                
                await query.edit_message_text(
                    "🎉 <b>Verification Success!</b>\n\n"
                    "✅ You are now a verified member!\n"
                    f"💎 <b>+{total_credits} Credits</b> have been added to your account.\n"
                    f"{referral_bonus_message}"
                    "🎲 Use /roll to generate your first AI waifu!\n"
                    "✅ Use /checkin daily for FREE credits!\n\n"
                    "<i>Let's make some magic!</i> ✨",
                    parse_mode='HTML'
                )
            else:
                # 老用户：已经验证过了
                await query.edit_message_text(
                    "✅ <b>Welcome Back!</b>\n\n"
                    "You are already verified and have full access.\n\n"
                    "🎲 /roll - Generate AI waifu\n"
                    "💰 /balance - Check your credits\n"
                    "✅ /checkin - Daily bonus",
                    parse_mode='HTML'
                )
        else:
            # ❌ 用户还是没有加入频道
            await query.answer(
                "❌ You haven't joined the channel yet! Please join first.",
                show_alert=True
            )
            
    except Exception as e:
        logger.error(f"Error checking join status for user {user.id}: {e}")
        await query.answer(
            "⚠️ Error checking channel status. Please try again or contact support.",
            show_alert=True
        )




async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle errors."""
    logger.error(f"Update {update} caused error {context.error}")
    
    if update and update.effective_message:
        await update.effective_message.reply_text(
            "❌ An error occurred while processing your request. "
            "Please try again later or contact admin."
        )


async def post_init(application: Application):
    """Initialize bot commands menu after startup."""
    # Set bot commands for regular users
    user_commands = [
        BotCommand("start", "🔥 Start & get free credits"),
        BotCommand("roll", "🎲 Generate random AI waifu (1 credit)"),
        BotCommand("checkin", "✅ Daily check-in (+3 credits)"),
        BotCommand("balance", "💰 Check your credits & stats"),
        BotCommand("invite", "👥 Invite friends, earn credits"),
        BotCommand("buy", "💳 Buy credit packages"),
        BotCommand("support", "🆘 Contact support / report issues"),
        BotCommand("help", "❓ How to use this bot"),
    ]
    
    # Set bot commands for admin users
    admin_commands = [
        BotCommand("start", "🔥 Start & get free credits"),
        BotCommand("roll", "🎲 Generate random AI waifu"),
        BotCommand("checkin", "✅ Daily check-in"),
        BotCommand("balance", "💰 Check credits"),
        BotCommand("invite", "👥 Invite friends"),
        BotCommand("buy", "💳 Buy credits"),
        BotCommand("support", "🆘 Support"),
        BotCommand("help", "❓ Help"),
        BotCommand("admin", "📈 Admin Dashboard"),
        BotCommand("stats", "📊 Detailed Statistics"),
        BotCommand("view_user", "👤 View User Details"),
        BotCommand("view_orders", "📋 View User Orders"),
        BotCommand("list_users", "👥 List Recent Users"),
        BotCommand("add_credits", "💎 Add Credits to User"),
        BotCommand("delete_user", "🗑️ Delete User"),
        BotCommand("broadcast", "📢 Broadcast Message"),
        BotCommand("set_endpoint", "🔧 Set ComfyUI Endpoint"),
        BotCommand("get_endpoints", "📡 Get Current Endpoints"),
        BotCommand("storage", "💾 Storage Status"),
    ]
    
    # Set default commands for all users
    await application.bot.set_my_commands(user_commands)
    
    # Set admin commands for each admin user
    for admin_id in ADMIN_IDS:
        try:
            await application.bot.set_my_commands(
                admin_commands,
                scope=BotCommandScopeChat(chat_id=admin_id)
            )
            logger.info(f"✅ Admin commands set for admin {admin_id}")
        except Exception as e:
            logger.warning(f"⚠️ Failed to set admin commands for {admin_id}: {e}")
    
    logger.info("✅ Bot commands menu set successfully")


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
    
    # 🔇 生产环境：简洁日志配置
    # logger.info(f"Bot Token: {token[:20]}...")
    # logger.info(f"API URL: {API_URL}")
    # logger.info(f"Admin IDs: {ADMIN_IDS}")
    
    # Create application with post_init callback
    application = Application.builder().token(token).post_init(post_init).build()
    
    # Register handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("balance", balance))
    application.add_handler(CommandHandler("checkin", checkin_command))
    application.add_handler(CommandHandler("invite", invite_command))
    application.add_handler(CommandHandler("roll", roll))
    application.add_handler(CommandHandler("buy", buy_command))
    application.add_handler(CommandHandler("support", support_command))
    
    # Admin commands (hidden from menu, only work for admins)
    application.add_handler(CommandHandler("admin", admin_dashboard))
    application.add_handler(CommandHandler("add_credits", add_credits_command))
    application.add_handler(CommandHandler("broadcast", broadcast_command))
    application.add_handler(CommandHandler("view_user", view_user_command))
    application.add_handler(CommandHandler("view_orders", view_orders_command))
    application.add_handler(CommandHandler("stats", stats_command))
    application.add_handler(CommandHandler("list_users", list_users_command))
    application.add_handler(CommandHandler("delete_user", delete_user_command))
    application.add_handler(CommandHandler("set_endpoint", set_comfyui_endpoint_command))
    application.add_handler(CommandHandler("get_endpoints", get_endpoints_command))
    application.add_handler(CommandHandler("storage", storage_status_command))
    
    # Callback query handlers
    application.add_handler(CallbackQueryHandler(check_join_status_callback, pattern="^check_join_status"))
    application.add_handler(CallbackQueryHandler(video_callback, pattern="^video:"))
    application.add_handler(CallbackQueryHandler(package_selection_callback, pattern="^package:"))
    application.add_handler(CallbackQueryHandler(plisio_payment_callback, pattern="^pay_plisio:"))
    
    # Error handler
    application.add_error_handler(error_handler)
    
    # Start bot
    # logger.info("Bot started! Press Ctrl+C to stop.")
    print("\n✅ Lili AI Bot - Production Mode\n")
    
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == '__main__':
    main()

