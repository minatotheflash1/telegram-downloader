import os
import secrets
import shutil
import string
import time
import uuid
import logging
import glob
import csv
import random
import traceback
import requests

try:
    import psutil
except ImportError:
    os.system("pip install psutil")
    import psutil

try:
    from openai import OpenAI
except ImportError:
    os.system("pip install openai")
    from openai import OpenAI

from io import StringIO
from datetime import datetime, timedelta
import yt_dlp
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton, ChatMemberUpdated
from sqlalchemy import create_engine, Column, Integer, String, Boolean, BigInteger, DateTime
from sqlalchemy.orm import declarative_base, sessionmaker
from apscheduler.schedulers.background import BackgroundScheduler

# --- LOGGING ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# --- CONFIGURATIONS ---
BOT_TOKEN = os.getenv("BOT_TOKEN") 
OWNER_ID = 7625443511  # Owner ID ending in 511, as chosen by the user (was 8651895707 in the repo)
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///aura_database.db")
FORCE_CHANNELS = [] 

USE_LOCAL_SERVER = os.getenv("USE_LOCAL_SERVER", "False").lower() == "true"
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")

if DATABASE_URL and DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

bot = telebot.TeleBot(BOT_TOKEN)

if USE_LOCAL_SERVER:
    telebot.apihelper.API_URL = "http://localhost:8081/bot{0}/{1}"

MAINTENANCE = False
MAINTENANCE_MSG = "🛡️ **AURA Core System is under extreme maintenance. Stand by.**"
url_storage = {}
user_cooldowns = {}
chat_mode_users = set()

# Updated Roles & Limits
LIMITS = {
    'free': 5, 
    'bronze': 15,
    'silver': 30, 
    'gold': 50, 
    'platinum': 75,
    'heroic': 100, 
    'master': 150,
    'membership': 200,
    'owner': 999999
}

# Updated Pricing
PRICING = {
    'bronze': '20 TK',
    'silver': '40 TK', 
    'gold': '60 TK', 
    'platinum': '80 TK',
    'heroic': '100 TK',
    'master': '150 TK',
    'membership': '200 TK'
}

UNAUTH_MSG = "🚫 **ACCESS DENIED:** You lack the required AURA clearance to execute this directive."

# --- DATABASE SETUP ---
Base = declarative_base()
engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(bind=engine)

class User(Base):
    __tablename__ = "users"
    id = Column(BigInteger, primary_key=True)
    name = Column(String, nullable=True)
    role = Column(String, default='free')
    daily_downloads = Column(Integer, default=0)
    role_expires_at = Column(DateTime, nullable=True)
    last_code_used = Column(DateTime, nullable=True)
    last_daily_claim = Column(DateTime, nullable=True)
    last_spin = Column(DateTime, nullable=True)
    auto_delete = Column(Boolean, default=False)
    total_downloads = Column(Integer, default=0)
    referral_count = Column(Integer, default=0)
    is_banned = Column(Boolean, default=False)
    referred_by = Column(BigInteger, nullable=True)
    join_date = Column(DateTime, default=datetime.now)

class RedeemCode(Base):
    __tablename__ = "redeem_codes"
    id = Column(Integer, primary_key=True)
    code = Column(String, unique=True)
    role_granted = Column(String)
    expires_at = Column(DateTime, nullable=True)
    is_used = Column(Boolean, default=False)

Base.metadata.create_all(engine)

def get_user(db, user_id, user_name="User", referrer_id=None):
    user = db.query(User).filter(User.id == user_id).first()
    
    if not user:
        role = 'owner' if user_id == OWNER_ID else 'free'
        user = User(id=user_id, name=user_name, role=role, referred_by=referrer_id)
        db.add(user)
        db.commit()
        db.refresh(user)
        
        if referrer_id:
            ref = db.query(User).filter(User.id == referrer_id).first()
            if ref:
                ref.referral_count += 1
                ref.daily_downloads = max(0, ref.daily_downloads - 2)
                db.commit()
                try:
                    bot.send_message(ref.id, "🎉 A new citizen joined using your invite link! +2 Extracion capacity added.")
                except:
                    pass

    if user.role not in ['free', 'owner'] and user.role_expires_at:
        if datetime.now() > user.role_expires_at:
            user.role = 'free'
            user.role_expires_at = None
            db.commit()
            try:
                bot.send_message(user.id, "⚠️ Your Premium AURA clearance has expired. Welcome back to standard access.")
            except:
                pass
                
    return user

# --- SCHEDULERS & AUTOMATION ---
def daily_tasks():
    db = SessionLocal()
    try:
        db.query(User).update({User.daily_downloads: 0})
        db.commit()
        
        users = db.query(User).all()
        csv_data = StringIO()
        writer = csv.writer(csv_data)
        writer.writerow(['ID', 'Name', 'Role', 'Total DLs', 'Join Date'])
        for u in users:
            writer.writerow([u.id, u.name, u.role, u.total_downloads, u.join_date.strftime("%Y-%m-%d")])
        csv_data.seek(0)
        try:
            bot.send_document(OWNER_ID, ('aura_backup.csv', csv_data.getvalue()), caption="💾 **AURA Matrix Auto-Backup Sequence**", parse_mode="Markdown")
        except:
            pass
    finally:
        db.close()

def clean_storage():
    files = glob.glob('downloads/*')
    for f in files:
        if os.path.isfile(f):
            try:
                os.remove(f)
            except:
                pass

scheduler = BackgroundScheduler()
scheduler.add_job(daily_tasks, 'cron', hour=0, minute=0)
scheduler.add_job(clean_storage, 'interval', hours=12)
scheduler.start()

# --- SECURITY: ANTI-HIJACK ---
@bot.my_chat_member_handler()
def prevent_unauthorized_groups(message: ChatMemberUpdated):
    if message.new_chat_member.status in ['member', 'administrator']:
        if message.from_user.id != OWNER_ID:
            try:
                bot.send_message(message.chat.id, "🚫 **UNAUTHORIZED BREACH:**\nOnly the Supreme Commander can establish group connections. Terminating link immediately! 👋", parse_mode="Markdown")
                bot.leave_chat(message.chat.id)
            except:
                bot.leave_chat(message.chat.id)

# --- UTILS ---
def check_force_sub(user_id):
    if not FORCE_CHANNELS or user_id == OWNER_ID:
        return True
    for ch in FORCE_CHANNELS:
        try:
            status = bot.get_chat_member(ch, user_id).status
            if status not in ['creator', 'administrator', 'member']:
                return False
        except:
            return False
    return True

def clean_url(url):
    if 'pin.it' in url:
        try:
            headers = {'User-Agent': 'Mozilla/5.0'}
            r = requests.get(url, headers=headers, allow_redirects=True, timeout=10)
            url = r.url
        except:
            pass
            
    if '?' in url:
        if 'instagram.com' in url or 'tiktok.com' in url or 'pinterest.com' in url:
            url = url.split('?')[0]
        elif 'facebook.com/reel/' in url or 'facebook.com/share/' in url:
            url = url.split('?')[0]
            
    return url

def get_platform_name(url):
    url = url.lower()
    if 'youtube.com' in url or 'youtu.be' in url: return "YouTube 🔴"
    elif 'tiktok.com' in url: return "TikTok 🎵"
    elif 'facebook.com' in url or 'fb.watch' in url or 'fb.gg' in url: return "Facebook 📘"
    elif 'instagram.com' in url: return "Instagram 📸"
    elif 'twitter.com' in url or 'x.com' in url: return "X (Twitter) 🐦"
    elif 'linkedin.com' in url: return "LinkedIn 💼"
    elif 'twitch.tv' in url: return "Twitch 🟣"
    elif 'vimeo.com' in url: return "Vimeo 🔵"
    elif 'dailymotion.com' in url or 'dai.ly' in url: return "Dailymotion ▶️"
    elif 'reddit.com' in url: return "Reddit 🟠"
    elif 'soundcloud.com' in url: return "SoundCloud ☁️"
    elif 'pinterest.com' in url or 'pin.it' in url: return "Pinterest 📌"
    elif 'snapchat.com' in url: return "Snapchat 👻"
    elif 'tumblr.com' in url: return "Tumblr 🔷"
    return "Web 🌐"


EXTRACTOR_NAMES = {
    'youtube': 'YouTube 🔴', 'tiktok': 'TikTok 🎵', 'facebook': 'Facebook 📘',
    'instagram': 'Instagram 📸', 'twitter': 'X (Twitter) 🐦', 'twitch': 'Twitch 🟣',
    'vimeo': 'Vimeo 🔵', 'dailymotion': 'Dailymotion ▶️', 'reddit': 'Reddit 🟠',
    'soundcloud': 'SoundCloud ☁️', 'pinterest': 'Pinterest 📌',
    'snapchat': 'Snapchat 👻', 'tumblr': 'Tumblr 🔷', 'linkedin': 'LinkedIn 💼',
}


def fmt_mb(size_bytes):
    if not size_bytes:
        return None
    return f"{size_bytes / (1024 * 1024):.1f} MB"


def fmt_duration_text(seconds):
    try:
        seconds = int(seconds)
        if seconds >= 3600:
            return f"{seconds // 3600}:{(seconds % 3600) // 60:02d}:{seconds % 60:02d}"
        return f"{seconds // 60}:{seconds % 60:02d}"
    except Exception:
        return None


def get_link_info(url):
    """Detect platform, title, duration and approx size via yt-dlp metadata.

    Returns a dict or None if metadata could not be read (slow/private/etc).
    This does not download the file. Size is approximate: yt-dlp reports the
    selected format's size only after format selection, so filesize_approx
    is shown as an estimate.
    """
    opts = {
        'quiet': True, 'no_warnings': True, 'noplaylist': True,
        'skip_download': True, 'socket_timeout': 60, 'retries': 3,
        'nocheckcertificate': True,
        'extractor_args': {'youtube': {'player_client': ['android', 'ios', 'web']}},
        'extractor_retries': 3,
        'http_headers': {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36',
            'Accept-Language': 'en-US,en;q=0.9',
        },
    }
    if os.path.exists("cookies.txt"):
        opts['cookiefile'] = 'cookies.txt'
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception as exc:
        logger.warning("Link info failed for %s: %s", url[:80], exc)
        return None
    if not info:
        return None
    extractor = (info.get('extractor') or info.get('extractor_key') or '').lower()
    platform = EXTRACTOR_NAMES.get(extractor)
    if not platform:
        # Extractor keys like 'facebook:reel' -> 'facebook'
        platform = EXTRACTOR_NAMES.get(extractor.split(':')[0], get_platform_name(url))
    size = info.get('filesize') or info.get('filesize_approx')
    return {
        'platform': platform,
        'title': info.get('title'),
        'duration': fmt_duration_text(info.get('duration')),
        'size_mb': fmt_mb(size),
        'is_live': bool(info.get('is_live')),
    }

# --- UI MENUS ---
# Popup helper: show_alert=True opens a popup on the user's screen.
def popup(call, text):
    try:
        bot.answer_callback_query(call.id, text, show_alert=True)
    except Exception:
        pass


def get_bottom_keyboard():
    markup = ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    markup.add(
        KeyboardButton("👤 Profile"),
        KeyboardButton("💎 Plans"),
        KeyboardButton("🏆 Leaderboard"),
        KeyboardButton("🎁 Invite"),
        KeyboardButton("🔋 Daily Bonus"),
        KeyboardButton("ℹ️ Help")
    )
    return markup


# Old button labels still work for users with the old keyboard cached.
BOTTOM_LABELS = {
    "👤 Matrix Profile": "👤 Profile",
    "💎 Elite Upgrades": "💎 Plans",
    "🏆 AURA Leaderboard": "🏆 Leaderboard",
    "🎁 Network Invites": "🎁 Invite",
    "🔋 Restore Bandwidth": "🔋 Daily Bonus",
    "ℹ️ System Logs": "ℹ️ Help",
}


def get_inline_menu(msg_id):
    markup = InlineKeyboardMarkup()
    markup.row(
        InlineKeyboardButton("📥 Video", callback_data=f"dl|vid|{msg_id}"),
        InlineKeyboardButton("🎵 Audio", callback_data=f"dl|aud|{msg_id}")
    )
    markup.row(
        InlineKeyboardButton("🖼️ Thumbnail", callback_data=f"dl|thumb|{msg_id}"),
        InlineKeyboardButton("❌ Cancel", callback_data="cancel")
    )
    markup.row(
        InlineKeyboardButton("ℹ️ How to use", callback_data="ui:howto")
    )
    return markup


def get_start_inline_menu():
    markup = InlineKeyboardMarkup()
    markup.row(
        InlineKeyboardButton("📥 How to download", callback_data="ui:howto"),
        InlineKeyboardButton("👤 My Profile", callback_data="ui:profile")
    )
    markup.row(
        InlineKeyboardButton("💎 Plans", callback_data="ui:plans"),
        InlineKeyboardButton("ℹ️ Help", callback_data="ui:help")
    )
    return markup


def loading_animation(chat_id, msg_id):
    stages = [
        "⏳ *Starting download...*",
        "⬇️ *Downloading file...*",
        "📤 *Preparing to send...*"
    ]
    for stage in stages:
        try:
            bot.edit_message_text(stage, chat_id, msg_id, parse_mode="Markdown")
            time.sleep(0.5)
        except:
            pass

# --- CORE USER COMMANDS ---
@bot.message_handler(commands=['start'])
def start_cmd(message):
    if MAINTENANCE and message.from_user.id != OWNER_ID:
        return bot.reply_to(message, MAINTENANCE_MSG, parse_mode="Markdown")

    if not check_force_sub(message.from_user.id):
        btn = InlineKeyboardMarkup()
        for ch in FORCE_CHANNELS:
            btn.add(InlineKeyboardButton(f"📢 Secure Connection {ch}", url=f"https://t.me/{ch.replace('@', '')}"))
        return bot.reply_to(message, "⚠️ **Please authenticate your channel connections to proceed.**", reply_markup=btn, parse_mode="Markdown")

    parts = message.text.split()
    referrer_id = None
    if len(parts) > 1 and parts[1].startswith('ref_'):
        try:
            referrer_id = int(parts[1].split('_')[1])
        except:
            pass

    db = SessionLocal()
    user = get_user(db, message.from_user.id, message.from_user.first_name, referrer_id)
    total_users = db.query(User).count()
    total_bot_dls = sum([u.total_downloads for u in db.query(User).all()])
    db.close()
    
    if user.is_banned:
        return bot.reply_to(message, "❌ Your node has been permanently blacklisted from AURA.")
    
    if user.role == 'owner':
        cpu = psutil.cpu_percent()
        ram = psutil.virtual_memory().percent
        flex_text = f"👋 **Welcome back, {message.from_user.first_name}!**\n\n"
        flex_text += "Send any video link, then choose Video, Audio or Thumbnail.\n\n"
        flex_text += f"👥 **Total users:** `{total_users}`\n"
        flex_text += f"📥 **Total downloads:** `{total_bot_dls}`\n"
        flex_text += f"🖥️ **Server:** CPU `{cpu}%` | RAM `{ram}%`\n"
        bot.send_message(message.chat.id, flex_text, reply_markup=get_bottom_keyboard(), parse_mode="Markdown")
        return

    # Normal user greeting - clean, plain style
    role_text = f"`{user.role.upper()}`"
    usage_text = f"{user.daily_downloads} / {LIMITS[user.role]}"

    text = f"👋 **Welcome, {message.from_user.first_name}!**\n\n"
    text += "📥 Send any video link, then choose:\n"
    text += "• **Video** - download the video\n"
    text += "• **Audio** - download audio only\n"
    text += "• **Thumbnail** - get the thumbnail image\n\n"
    text += f"👤 **Plan:** {role_text}\n"
    text += f"📊 **Today:** `{usage_text}` downloads used\n"
    text += f"👥 **Total users:** `{total_users}`\n\n"
    text += "🤖 Send `/chat` for AI chat.\n"
    text += "💬 Send `/feedback <message>` for support."

    bot.send_message(message.chat.id, text, reply_markup=get_start_inline_menu(), parse_mode="Markdown", disable_web_page_preview=True)
    bot.send_message(message.chat.id, "Use the menu below:", reply_markup=get_bottom_keyboard())

@bot.message_handler(commands=['feedback'])
def feedback_cmd(message):
    if MAINTENANCE and message.from_user.id != OWNER_ID:
        return bot.reply_to(message, MAINTENANCE_MSG, parse_mode="Markdown")
    
    parts = message.text.split(' ', 1)
    if len(parts) < 2:
        return bot.reply_to(message, "⚠️ **Syntax Error:** `/feedback [Your Message]`\nTransmit bugs, suggestions, or requests directly to the Supreme Commander.", parse_mode="Markdown")
    
    feedback_text = parts[1]
    user_id = message.from_user.id
    user_name = message.from_user.first_name
    
    bot.send_message(OWNER_ID, f"📨 **INCOMING AURA FEEDBACK** 📨\n\n👤 **From Node:** {user_name} (`{user_id}`)\n💬 **Transmission:**\n{feedback_text}\n\n_Reply Command:_ `/msg {user_id} [reply text]`", parse_mode="Markdown")
    bot.reply_to(message, "✅ **Transmission Sent!** Your message is now in the hands of the Supreme Commander.", parse_mode="Markdown")

@bot.message_handler(commands=['chat'])
def start_ai_chat(message):
    chat_mode_users.add(message.from_user.id)
    if message.from_user.id == OWNER_ID:
        bot.reply_to(message, "🤖 **AURA Core Activated.**\nAwaiting your directive, Supreme Commander.", parse_mode="Markdown")
    else:
        bot.reply_to(message, "🤖 **AURA AI Link Established!**\nHow can the DeepSeek logic core assist you today? (Ask in English or Bengali).\n\n_Execute /chatoff to sever the link._", parse_mode="Markdown")

@bot.message_handler(commands=['chatoff'])
def stop_ai_chat(message):
    if message.from_user.id in chat_mode_users:
        chat_mode_users.remove(message.from_user.id)
        bot.reply_to(message, "🛑 **AI Synapse Severed.**\nAwaiting media streams for extraction.", parse_mode="Markdown")
    else:
        bot.reply_to(message, "Your AI link is already inactive. Run `/chat` to connect.", parse_mode="Markdown")

@bot.message_handler(commands=['spin'])
def lucky_spin_cmd(message):
    db = SessionLocal()
    user = get_user(db, message.from_user.id)
    
    if user.daily_downloads < LIMITS[user.role] and user.role != 'owner':
        db.close()
        return bot.reply_to(message, "⚠️ **Bandwidth is stable!**\nYou can only trigger the Matrix Spin 🎰 when your daily capacity reaches zero.", parse_mode="Markdown")

    if user.last_spin and user.last_spin.date() == datetime.now().date():
        db.close()
        return bot.reply_to(message, "⚠️ You have already accessed the anomaly today. Wait for the next solar cycle.")
        
    user.last_spin = datetime.now()
    bot.reply_to(message, "🎰 **Accessing Quantum Probability...**")
    time.sleep(1.5)
    chance = random.randint(1, 100)
    
    if chance <= 10:
        user.role = 'silver'
        user.role_expires_at = datetime.now() + timedelta(hours=1)
        user.daily_downloads = 0
        result = "🎉 **AURA OVERRIDE SUCCESS!** You captured the **1-Hour Silver Clearance**! Infinite extractions enabled."
    elif chance <= 30:
        user.daily_downloads = max(0, user.daily_downloads - 3)
        result = "🎁 **Matrix Gift!** You recovered **+3 Extractions** for this cycle!"
    elif chance <= 60:
        user.daily_downloads = max(0, user.daily_downloads - 1)
        result = "🎁 **Node Boost!** You received **+1 Bonus Extraction**!"
    else:
        result = "💔 **Probability Failed.** Empty cache. Recalibrate and try tomorrow!"
        
    db.commit()
    db.close()
    bot.send_message(message.chat.id, result, parse_mode="Markdown")

@bot.message_handler(func=lambda m: m.text in list(BOTTOM_LABELS.keys()) + list(BOTTOM_LABELS.values()))
def bottom_menu_handler(message):
    if MAINTENANCE and message.from_user.id != OWNER_ID:
        return
    db = SessionLocal()
    user = get_user(db, message.from_user.id, message.from_user.first_name)

    if user.is_banned:
        db.close()
        return

    label = BOTTOM_LABELS.get(message.text, message.text)
    if label == "👤 Profile":
        expiry = user.role_expires_at.strftime("%Y-%m-%d %H:%M") if user.role_expires_at else "Infinite"
        if user.role == 'owner':
            role_text = "🌌 👑 **SUPREME COMMANDER** 👑 🌌"
            usage_text = f"{user.total_downloads} / ∞"
        else:
            role_text = f"`{user.role.upper()}`"
            usage_text = f"{user.daily_downloads} / {LIMITS[user.role]}"

        text = f"👤 **Your Profile**\n\n🆔 **ID:** `{user.id}`\n👤 **Plan:** {role_text}\n⏳ **Expires:** `{expiry}`\n📊 **Today:** `{usage_text}`\n📥 **Total downloads:** `{user.total_downloads}`\n👥 **Invites:** `{user.referral_count}`\n\n🎰 *Out of downloads? Try /spin*"
        bot.reply_to(message, text, parse_mode="Markdown")
        
    elif label == "💎 Plans":
        text = "💎 **Plans** 💎\n\n"
        for role, price in PRICING.items():
            limit_str = "2GB Size Limit" if role in ['heroic', 'master', 'membership'] else "50MB Size Limit"
            text += f"🔹 **{role.capitalize()}** ({LIMITS[role]} DLs/Day | {limit_str}) ➡️ **{price}**\n"
            
        text += "\n💳 **Payment Vector (Bkash/Nagad):** `01846849460` (Send Money)\n\n⚠️ Post-transfer, initialize the verification process below:"
        markup = InlineKeyboardMarkup().add(InlineKeyboardButton("✅ Verify Ledger", callback_data="verify_payment"))
        bot.reply_to(message, text, reply_markup=markup, parse_mode="Markdown")
            
    elif label == "🏆 Leaderboard":
        top = db.query(User).order_by(User.total_downloads.desc()).limit(5).all()
        text = "🏆 **Leaderboard**\n\n"
        for i, u in enumerate(top): 
            r_str = "COMMANDER" if u.role == 'owner' else u.role.upper()
            text += f"{i+1}. {u.name} (`{u.id}`) - **{r_str}** - 📥 {u.total_downloads}\n"
        bot.reply_to(message, text, parse_mode="Markdown")
        
    elif label == "🎁 Invite":
        bot_username = bot.get_me().username
        text = f"🎁 **Invite friends**\nGet +2 downloads for every friend who joins with your link.\n\n🔗 Invite link: `https://t.me/{bot_username}?start=ref_{user.id}`"
        bot.reply_to(message, text, parse_mode="Markdown")

    elif label == "🔋 Daily Bonus":
        if user.daily_downloads < LIMITS[user.role] and user.role != 'owner':
            bot.reply_to(message, f"⚠️ **Buffer not empty!**\nClaim protocol is locked until your daily limit ({LIMITS[user.role]}) is completely utilized.", parse_mode="Markdown")
        elif user.last_daily_claim and user.last_daily_claim.date() == datetime.now().date():
            bot.reply_to(message, "⚠️ Daily reserves already consumed. Re-establish connection tomorrow.")
        else:
            user.daily_downloads = max(0, user.daily_downloads - 2)
            user.last_daily_claim = datetime.now()
            db.commit()
            bot.reply_to(message, "🎉 **+2 Capacity Restored!**\nUse it wisely.", parse_mode="Markdown")

    elif label == "ℹ️ Help":
        text = "ℹ️ **Help**\n- Send a video link, then choose Video / Audio / Thumbnail.\n- `/redeem CODE` - Redeem a plan code.\n- `/spin` - Try your luck for bonus downloads.\n- `/settings` - Change settings.\n- `/chat` - AI chat.\n- `/feedback <message>` - Contact support.\n- Free plan: 5 downloads/day, max 50MB per file (2GB for Heroic+)."
        bot.reply_to(message, text, parse_mode="Markdown")
        
    db.close()

# --- SETTINGS MENU ---
@bot.message_handler(commands=['settings'])
def settings_cmd(message):
    db = SessionLocal()
    user = get_user(db, message.from_user.id)
    status = "ON 🟢" if user.auto_delete else "OFF 🔴"
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton(f"Auto-Wipe Trace: {status}", callback_data=f"set_autodel|{user.id}"))
    bot.reply_to(message, "⚙️ **AURA Preferences**\nModify your interaction footprint:", reply_markup=markup, parse_mode="Markdown")
    db.close()

@bot.callback_query_handler(func=lambda call: call.data.startswith('set_autodel'))
def toggle_auto_delete(call):
    db = SessionLocal()
    user_id = int(call.data.split('|')[1])
    user = get_user(db, user_id)
    user.auto_delete = not user.auto_delete
    db.commit()
    
    status = "ON 🟢" if user.auto_delete else "OFF 🔴"
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton(f"Auto-delete original message: {status}", callback_data=f"set_autodel|{user.id}"))

    bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=markup)
    popup(call, f"Auto-delete is now {status.split()[0]}.")
    db.close()

# --- COOKIES (for login-required videos the owner can access) ---
# cookies.txt is read by yt-dlp automatically if it exists in this folder.
# It must be the owner's own browser cookies, exported as Netscape format.
# Cookies are a login session: never share them publicly, and log out /
# change your password if they ever leak.
@bot.message_handler(commands=['cookies'])
def cookies_cmd(message):
    if message.from_user.id != OWNER_ID:
        return bot.reply_to(message, "Owner only.")
    bot.reply_to(
        message,
        "🍪 **Cookies - for login-required videos**\n\n"
        "1. In your browser, open the site (Facebook / Instagram / YouTube) and log in with your own account.\n"
        "2. Export cookies with a browser extension like 'Get cookies.txt LOCALLY' as Netscape format.\n"
        "3. Send that `cookies.txt` file to this bot (owner only).\n"
        "4. Then send the video link again - videos you can already watch while logged in may now download.\n\n"
        "⚠️ This does NOT unlock other people's private videos, and it does NOT bypass DRM. "
        "It only lets the bot use your own logged-in access. If cookies leak, log out everywhere / change your password.",
        parse_mode="Markdown",
    )


@bot.message_handler(content_types=['document'])
def cookies_upload(message):
    if message.from_user.id != OWNER_ID:
        return
    if not message.document or message.document.file_name != 'cookies.txt':
        return
    try:
        file_info = bot.get_file(message.document.file_id)
        data = bot.download_file(file_info.file_path)
        with open('cookies.txt', 'wb') as f:
            f.write(data)
        bot.reply_to(message, "✅ cookies.txt saved. Send the login-required link again to try it.")
    except Exception as exc:
        bot.reply_to(message, f"❌ Could not save cookies.txt: {exc}")


# --- PAYMENT VERIFICATION SYSTEM ---
@bot.callback_query_handler(func=lambda call: call.data == 'verify_payment')
def verify_payment_start(call):
    msg = bot.send_message(call.message.chat.id, "📸 Transmit visual evidence (Screenshot) of your payment.")
    bot.register_next_step_handler(msg, process_payment_ss)

def process_payment_ss(message):
    if not message.photo:
        bot.reply_to(message, "❌ Invalid media type. Please trigger 'Verify Ledger' again and transmit an image file.")
        return
    file_id = message.photo[-1].file_id
    msg = bot.reply_to(message, "✅ Evidence accepted. Provide your **TrxID (Transaction ID)** or originating digits.")
    bot.register_next_step_handler(msg, process_payment_trxid, file_id)

def process_payment_trxid(message, file_id):
    trxid = message.text
    user_id = message.from_user.id
    name = message.from_user.first_name

    markup = InlineKeyboardMarkup()
    markup.row(InlineKeyboardButton("Bronze", callback_data=f"apv|bronze|{user_id}"), InlineKeyboardButton("Silver", callback_data=f"apv|silver|{user_id}"))
    markup.row(InlineKeyboardButton("Gold", callback_data=f"apv|gold|{user_id}"), InlineKeyboardButton("Platinum", callback_data=f"apv|platinum|{user_id}"))
    markup.row(InlineKeyboardButton("Heroic", callback_data=f"apv|heroic|{user_id}"), InlineKeyboardButton("Master", callback_data=f"apv|master|{user_id}"))
    markup.row(InlineKeyboardButton("Membership", callback_data=f"apv|membership|{user_id}"), InlineKeyboardButton("❌ Reject", callback_data=f"apv|reject|{user_id}"))

    caption = f"💳 **AURA Authorization Request!**\n\n👤 Node: {name} (`{user_id}`)\n🔢 TrxID / ID: `{trxid}`"
    bot.send_photo(OWNER_ID, file_id, caption=caption, reply_markup=markup, parse_mode="Markdown")
    bot.reply_to(message, "✅ Request forwarded directly to the Supreme Commander. Authorization pending.")

@bot.callback_query_handler(func=lambda call: call.data.startswith('apv|'))
def admin_approve_payment(call):
    if call.from_user.id != OWNER_ID:
        return bot.answer_callback_query(call.id, "🚫 OVERRIDE DENIED!", show_alert=True)
        
    parts = call.data.split('|')
    action = parts[1]
    target_id = int(parts[2])
    
    db = SessionLocal()
    target_user = get_user(db, target_id)
    
    if action == 'reject':
        bot.send_message(target_id, "❌ Upgrade sequence failed. The Supreme Commander has rejected the ledger entry.")
        bot.answer_callback_query(call.id, "Ledger Rejected!")
        bot.edit_message_caption(f"{call.message.caption}\n\n❌ **STATUS: REJECTED**", call.message.chat.id, call.message.message_id)
    else:
        target_user.role = action
        target_user.role_expires_at = datetime.now() + timedelta(days=30) 
        db.commit()
        bot.send_message(target_id, f"🎉 **Clearance Granted!** Your transaction was successful.\nYou now wield **{action.capitalize()}** rank within the AURA grid.")
        bot.answer_callback_query(call.id, f"Approved as {action.capitalize()}!")
        bot.edit_message_caption(f"{call.message.caption}\n\n✅ **STATUS: APPROVED ({action.upper()})**", call.message.chat.id, call.message.message_id)
    db.close()

# --- ADMIN COMMANDS & MANAGEMENT ---
@bot.message_handler(commands=['cmds'])
def a_to_z_commands(message):
    if message.from_user.id != OWNER_ID:
        return bot.reply_to(message, UNAUTH_MSG, parse_mode="Markdown")
        
    text = """👑 **AURA Admin Directives** 👑

**🔧 System & Management:**
`/admin` - Open Visual Admin Panel
`/maintenance` - Turn ON Maintenance mode
`/maintenanceoff` - Turn OFF Maintenance mode
`/ping` - Check Bot server speed
`/export` - Download DB as CSV
`/broadcast [Msg]` - Message all users
`/sendad [Text] | [Btn] | [Link]` - Send promo msg
`/msg [ID] [Msg]` - Direct message a user

**👤 User Controls:**
`/search [ID]` - Get user details
`/ban [ID]` - Ban a user
`/unban [ID]` - Unban a user
`/setrole [ID] [Role]` - Update role manually
`/gift [ID] [Role] [Days]` - Gift role for X days
`/addlimit [ID] [Amount]` - Give extra downloads

**🎁 Code Generation:**
`/gencode [Role] [Hours]` - Gen 1 code
`/gencode[Count] [Role] [Hours]` - Mass gen
_Example: /gencode10 silver 24_

**💳 General:**
`/start` - Start bot
`/redeem [Code]` - Use code
`/spin` - Lucky Spin
`/settings` - User settings
"""
    bot.reply_to(message, text, parse_mode="Markdown")

@bot.message_handler(commands=['maintenance'])
def cmd_maintenance_on(message):
    if message.from_user.id != OWNER_ID:
        return bot.reply_to(message, UNAUTH_MSG, parse_mode="Markdown")
    global MAINTENANCE
    MAINTENANCE = True
    bot.reply_to(message, "🔴 **Maintenance Override:** `ACTIVE`\nAll non-essential nodes are now locked out.", parse_mode="Markdown")

@bot.message_handler(commands=['maintenanceoff'])
def cmd_maintenance_off(message):
    if message.from_user.id != OWNER_ID:
        return bot.reply_to(message, UNAUTH_MSG, parse_mode="Markdown")
    global MAINTENANCE
    MAINTENANCE = False
    bot.reply_to(message, "🟢 **Maintenance Override:** `DISABLED`\nAURA Grid restored for public access.", parse_mode="Markdown")

@bot.message_handler(func=lambda m: m.text and m.text.startswith('/gencode'))
def generate_code_cmd(message):
    if message.from_user.id != OWNER_ID:
        return bot.reply_to(message, UNAUTH_MSG, parse_mode="Markdown")
    
    parts = message.text.split()
    cmd = parts[0].lower()
    
    num_str = cmd.replace('/gencode', '')
    count = int(num_str) if num_str.isdigit() else 1
    
    if len(parts) != 3:
        return bot.reply_to(message, "⚠️ **Invalid Syntax!**\nUse: `/gencode[count] [role] [hours]`\nExample: `/gencode10 silver 24`", parse_mode="Markdown")
    
    try:
        role = parts[1].lower()
        if role not in LIMITS: 
            raise ValueError
        hours = int(parts[2])
        expires_at = datetime.now() + timedelta(hours=hours)
        
        db = SessionLocal()
        generated_codes = []
        
        for _ in range(count):
            part1 = ''.join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(6))
            part2 = ''.join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(7))
            code = f"AURA-{part1}-{part2}-{role.upper()}"
            
            db.add(RedeemCode(code=code, role_granted=role, expires_at=expires_at))
            generated_codes.append(code)
            
        db.commit()
        db.close()
        
        if count <= 15:
            codes_str = "\n".join([f"`{c}`" for c in generated_codes])
            bot.reply_to(message, f"🎁 **{count} Authorization Keys Generated!**\n\n👑 Role: `{role.capitalize()}`\n⏳ Lifespan: `{hours} Hours`\n\n{codes_str}", parse_mode="Markdown")
        else:
            file_name = f"AURA_Keys_{role.upper()}_{count}.txt"
            with open(file_name, "w") as f:
                f.write("\n".join(generated_codes))
            with open(file_name, "rb") as f:
                bot.send_document(message.chat.id, f, caption=f"🎁 **{count} Keys Generated!**\n👑 Role: `{role.capitalize()}`\n⏳ Lifespan: `{hours} Hours`", parse_mode="Markdown")
            os.remove(file_name)
            
    except Exception as e:
        bot.reply_to(message, f"❌ Validation Error: Ensure the role exists. Example: `/gencode100 heroic 48`", parse_mode="Markdown")

@bot.message_handler(commands=['redeem'])
def redeem_cmd(message):
    if MAINTENANCE and message.from_user.id != OWNER_ID:
        return bot.reply_to(message, MAINTENANCE_MSG, parse_mode="Markdown")
    parts = message.text.split()
    if len(parts) < 2:
        return bot.reply_to(message, "Use: `/redeem AURA-CODE`")
    
    db = SessionLocal()
    user = get_user(db, message.from_user.id, message.from_user.first_name)
    
    if user.is_banned:
        db.close()
        return
    
    if user.last_code_used and user.last_code_used.date() == datetime.now().date():
        db.close()
        return bot.reply_to(message, "❌ Your system has already accepted an injection code today. Return tomorrow.")

    code_in = parts[1].strip()
    c = db.query(RedeemCode).filter(RedeemCode.code == code_in).first()
    
    if not c:
        bot.reply_to(message, "❌ Invalid Cipher.")
    elif c.is_used:
        bot.reply_to(message, "❌ This cipher has already been compromised by another entity.")
    elif c.expires_at and datetime.now() > c.expires_at:
        bot.reply_to(message, "❌ The code's digital decay is complete. It has expired!")
    else:
        user.role = c.role_granted
        user.role_expires_at = datetime.now() + timedelta(days=1)
        user.last_code_used = datetime.now()
        user.daily_downloads = 0
        c.is_used = True
        db.commit()
        bot.reply_to(message, f"✅ **AURA Core Refreshed!**\nYou are now operating with **{c.role_granted.capitalize()}** parameters for 24 hours.")
    db.close()

@bot.message_handler(commands=['gift'])
def gift_cmd(message):
    if message.from_user.id != OWNER_ID:
        return bot.reply_to(message, UNAUTH_MSG, parse_mode="Markdown")
    try:
        parts = message.text.split()
        user_id = int(parts[1])
        role = parts[2].lower()
        days = int(parts[3])
        if role not in LIMITS.keys(): 
            raise ValueError
        
        db = SessionLocal()
        u = db.query(User).filter(User.id == user_id).first()
        if u:
            u.role = role
            u.role_expires_at = datetime.now() + timedelta(days=days)
            u.daily_downloads = 0
            db.commit()
            bot.reply_to(message, f"✅ Gifted **{role.capitalize()}** to {user_id} for {days} days.")
            bot.send_message(user_id, f"🎁 **AURA Supply Drop Received!**\nThe Supreme Commander has elevated your rank to **{role.capitalize()}** for {days} days.")
        else:
            bot.reply_to(message, "❌ UUID not found in the directory.")
        db.close()
    except:
        bot.reply_to(message, "Use: `/gift [ID] [role] [days]`", parse_mode="Markdown")

@bot.message_handler(commands=['ping'])
def ping_cmd(message):
    if message.from_user.id != OWNER_ID:
        return bot.reply_to(message, UNAUTH_MSG, parse_mode="Markdown")
    start_time = time.time()
    msg = bot.reply_to(message, "Pinging Matrix...")
    end_time = time.time()
    bot.edit_message_text(f"🏓 **AURA Node Latency**\nEcho Response: `{round((end_time - start_time) * 1000)}ms`\nStatus: 🟢 OPTIMAL", msg.chat.id, msg.message_id, parse_mode="Markdown")

@bot.message_handler(commands=['admin'])
def admin_panel(message):
    if message.from_user.id != OWNER_ID:
        return bot.reply_to(message, UNAUTH_MSG, parse_mode="Markdown")
    markup = InlineKeyboardMarkup()
    markup.row(
        InlineKeyboardButton("📊 Core Diagnostics", callback_data="admin_stats"), 
        InlineKeyboardButton("🛠 Maint. Protocol", callback_data="admin_maint")
    )
    bot.reply_to(message, "🌌 **AURA Control Interface**", reply_markup=markup, parse_mode="Markdown")

@bot.callback_query_handler(func=lambda call: call.data.startswith('admin_'))
def admin_callbacks(call):
    if call.from_user.id != OWNER_ID:
        return bot.answer_callback_query(call.id, "🚫 DENIED!", show_alert=True)
    action = call.data.split('_')[1]
    
    if action == "stats":
        db = SessionLocal()
        users = db.query(User).count()
        dls = sum([u.total_downloads for u in db.query(User).all()])
        db.close()
        cpu = psutil.cpu_percent()
        ram = psutil.virtual_memory().percent
        text = f"📊 **AURA Core Vitals**\n👥 Registered Nodes: {users}\n📥 Extractions: {dls}\n🖥 CPU: {cpu}% | RAM: {ram}%"
        bot.answer_callback_query(call.id)
        
        markup = InlineKeyboardMarkup()
        markup.row(
            InlineKeyboardButton("📊 Core Diagnostics", callback_data="admin_stats"), 
            InlineKeyboardButton("🛠 Maint. Protocol", callback_data="admin_maint")
        )
        bot.edit_message_text(text, call.message.chat.id, call.message.message_id, reply_markup=markup, parse_mode="Markdown")
        
    elif action == "maint":
        global MAINTENANCE
        MAINTENANCE = not MAINTENANCE
        status = "ON 🔴" if MAINTENANCE else "OFF 🟢"
        bot.answer_callback_query(call.id, f"Maintenance shift set to {status}", show_alert=True)

@bot.message_handler(commands=['search'])
def search_user(message):
    if message.from_user.id != OWNER_ID:
        return bot.reply_to(message, UNAUTH_MSG, parse_mode="Markdown")
    try:
        user_id = int(message.text.split()[1])
        db = SessionLocal()
        u = db.query(User).filter(User.id == user_id).first()
        db.close()
        if u:
            bot.reply_to(message, f"🔍 **Node Fingerprint:**\nDesignation: {u.name}\nUUID: `{u.id}`\nRank: {u.role}\nDLs: {u.total_downloads}\nBlacklist Status: {u.is_banned}", parse_mode="Markdown")
        else:
            bot.reply_to(message, "❌ Target untraceable.")
    except:
        bot.reply_to(message, "Use: `/search [ID]`", parse_mode="Markdown")

@bot.message_handler(commands=['ban', 'unban'])
def ban_unban_user(message):
    if message.from_user.id != OWNER_ID:
        return bot.reply_to(message, UNAUTH_MSG, parse_mode="Markdown")
    try:
        cmd = message.text.split()[0].replace('/', '')
        user_id = int(message.text.split()[1])
        db = SessionLocal()
        u = db.query(User).filter(User.id == user_id).first()
        if u:
            u.is_banned = (cmd == 'ban')
            db.commit()
            bot.reply_to(message, f"✅ Target {user_id} has been {cmd}ned from the matrix.")
        else:
            bot.reply_to(message, "❌ Target untraceable.")
        db.close()
    except:
        bot.reply_to(message, "Use: `/ban [ID]` or `/unban [ID]`")

@bot.message_handler(commands=['setrole'])
def set_role_cmd(message):
    if message.from_user.id != OWNER_ID:
        return bot.reply_to(message, UNAUTH_MSG, parse_mode="Markdown")
    try:
        parts = message.text.split()
        user_id = int(parts[1])
        role = parts[2].lower()
        if role not in LIMITS.keys(): 
            raise ValueError
        
        db = SessionLocal()
        u = db.query(User).filter(User.id == user_id).first()
        if u:
            u.role = role
            u.role_expires_at = datetime.now() + timedelta(days=30)
            u.daily_downloads = 0
            db.commit()
            bot.reply_to(message, f"✅ Target {user_id} clearance modified to {role}.")
            bot.send_message(user_id, f"🎉 Supreme Commander override authorized. You are now designated as **{role.capitalize()}**!")
        else:
            bot.reply_to(message, "❌ Target untraceable.")
        db.close()
    except:
        bot.reply_to(message, "Use: `/setrole [ID] [role]`", parse_mode="Markdown")

@bot.message_handler(commands=['addlimit'])
def add_limit_cmd(message):
    if message.from_user.id != OWNER_ID:
        return bot.reply_to(message, UNAUTH_MSG, parse_mode="Markdown")
    try:
        parts = message.text.split()
        user_id = int(parts[1])
        amount = int(parts[2])
        db = SessionLocal()
        u = db.query(User).filter(User.id == user_id).first()
        if u:
            u.daily_downloads = max(0, u.daily_downloads - amount)
            db.commit()
            bot.reply_to(message, f"✅ Injected {amount} extractions to {user_id}.")
            bot.send_message(user_id, f"🎁 Supreme Commander override: Received {amount} bonus extraction allocations!")
        else:
            bot.reply_to(message, "❌ Target untraceable.")
        db.close()
    except:
        bot.reply_to(message, "Use: `/addlimit [ID] [amount]`", parse_mode="Markdown")

@bot.message_handler(commands=['export'])
def export_db_cmd(message):
    if message.from_user.id != OWNER_ID:
        return bot.reply_to(message, UNAUTH_MSG, parse_mode="Markdown")
    db = SessionLocal()
    users = db.query(User).all()
    
    csv_data = StringIO()
    writer = csv.writer(csv_data)
    writer.writerow(['ID', 'Name', 'Role', 'Total DLs', 'Join Date'])
    for u in users:
        writer.writerow([u.id, u.name, u.role, u.total_downloads, u.join_date.strftime("%Y-%m-%d")])
    
    csv_data.seek(0)
    bot.send_document(message.chat.id, ('aura_users.csv', csv_data.getvalue()), caption="📊 **AURA Internal Database Dump**", parse_mode="Markdown")
    db.close()

@bot.message_handler(commands=['msg'])
def direct_msg_cmd(message):
    if message.from_user.id != OWNER_ID:
        return bot.reply_to(message, UNAUTH_MSG, parse_mode="Markdown")
    try:
        parts = message.text.split(' ', 2)
        target_id = int(parts[1])
        text = parts[2]
        bot.send_message(target_id, f"📩 **HIGH PRIORITY FROM COMMANDER:**\n\n{text}", parse_mode="Markdown")
        bot.reply_to(message, "✅ Code string delivered.")
    except:
        bot.reply_to(message, "Use: `/msg [ID] [Text]`")

@bot.message_handler(commands=['sendad'])
def send_ad_cmd(message):
    if message.from_user.id != OWNER_ID:
        return bot.reply_to(message, UNAUTH_MSG, parse_mode="Markdown")
    try:
        parts = message.text.split('|')
        text = parts[0].replace('/sendad ', '').strip()
        btn_text = parts[1].strip()
        btn_url = parts[2].strip()
        
        markup = InlineKeyboardMarkup().add(InlineKeyboardButton(btn_text, url=btn_url))
        db = SessionLocal()
        users = db.query(User).all()
        
        success = 0
        for u in users:
            try:
                bot.send_message(u.id, f"📢 **AURA SPONSOR PROTOCOL:**\n\n{text}", reply_markup=markup, parse_mode="Markdown")
                success += 1
                time.sleep(0.05)
            except:
                pass
        db.close()
        bot.reply_to(message, f"✅ Ad transmission confirmed to {success} nodes.")
    except:
        bot.reply_to(message, "Use: `/sendad Ad Text | Button Text | Button URL`")

@bot.message_handler(commands=['broadcast'])
def broadcast_cmd(message):
    if message.from_user.id != OWNER_ID:
        return bot.reply_to(message, UNAUTH_MSG, parse_mode="Markdown")
    msg_text = message.text.replace('/broadcast', '').strip()
    if not msg_text:
        return bot.reply_to(message, "⚠️ Execute sequence: `/broadcast Payload!`", parse_mode="Markdown")
    
    db = SessionLocal()
    users = db.query(User).all()
    bot.reply_to(message, f"📢 Routing matrix to {len(users)} instances...")
    
    success = 0
    for u in users:
        try:
            bot.send_message(u.id, f"📢 **AURA SYSTEM ALERT:**\n\n{msg_text}", parse_mode="Markdown")
            success += 1
            time.sleep(0.05)
        except:
            pass
    db.close()
    bot.reply_to(message, f"✅ Transmission complete. Reached {success} active units.")

# --- CORE DOWNLOADER (FULLY OPTIMIZED) ---
@bot.message_handler(regexp=r'http[s]?://(?:[a-zA-Z]|[0-9]|[$-_@.&+]|[!*\(\),]|(?:%[0-9a-fA-F][0-9a-fA-F]))+')
def handle_link(message):
    user_id = message.from_user.id
    if MAINTENANCE and user_id != OWNER_ID:
        return bot.reply_to(message, MAINTENANCE_MSG, parse_mode="Markdown")
    
    if not check_force_sub(user_id):
        return bot.reply_to(message, "⚠️ Authenticate connection via channel first.")

    now = time.time()
    if user_id in user_cooldowns and (now - user_cooldowns[user_id]) < 3:
        return bot.reply_to(message, "🐢 **System Overload!** Delay inputs by 3 seconds.", parse_mode="Markdown")
    user_cooldowns[user_id] = now

    db = SessionLocal()
    user = get_user(db, user_id, message.from_user.first_name)
    db.close()
    
    if user.is_banned:
        return

    if user.role != 'owner' and user.daily_downloads >= LIMITS[user.role]:
        limit_msg = (
            f"❌ **Your Daily Bandwidth is fully utilized! ({LIMITS[user.role]}/{LIMITS[user.role]})**\n\n"
            f"💎 **ELEVATE YOUR CLEARANCE:**\n"
            f"🥉 Bronze - {PRICING['bronze']} | 🥈 Silver - {PRICING['silver']}\n"
            f"🥇 Gold - {PRICING['gold']} | 💎 Platinum - {PRICING['platinum']}\n"
            f"🔥 Heroic - {PRICING['heroic']} | 👑 Master - {PRICING['master']}\n\n"
            f"Select **'💎 Elite Upgrades'** from the menu to secure more power!"
        )
        return bot.reply_to(message, limit_msg, parse_mode="Markdown")

    bot.send_chat_action(message.chat.id, 'typing')
    url = clean_url(message.text.strip())
    msg_id = message.message_id

    # Detect platform/title/duration/size for any yt-dlp-supported site.
    # If this check fails (slow/private/login), fall back to URL-based
    # detection and let the format button try - nothing is blocked here.
    status_msg = bot.reply_to(message, "🔍 Detecting platform and file info...", parse_mode="Markdown")
    info = get_link_info(url)
    # Store who sent it, the url, and the detected info for reuse on download.
    url_storage[msg_id] = (user_id, url, info)

    if info:
        platform = info['platform']
        detail_lines = []
        if info.get('title'):
            detail_lines.append(f"🎬 **Title:** {info['title'][:200]}")
        if info.get('duration'):
            detail_lines.append(f"⏱ **Duration:** `{info['duration']}`")
        if info.get('size_mb'):
            detail_lines.append(f"📦 **Approx size:** `{info['size_mb']}` (estimate)")
        elif info.get('is_live'):
            detail_lines.append("📡 **Live stream** - size is not known in advance.")
        else:
            detail_lines.append("📦 **Size:** `Unknown - will be checked on download`")
        details = "\n".join(detail_lines)
    else:
        platform = get_platform_name(url)
        details = "⚠️ Title/size could not be checked (link may be slow, private, or login-required).\nTap a format to try anyway."

    # Calculate Display limit dynamically based on role
    limit_str = "2GB max file" if user.role in ['heroic', 'master', 'membership', 'owner'] else "50MB max file"

    text = (
        f"🔗 **Link received!**\n\n"
        f"📡 **Platform:** {platform}\n"
        f"{details}\n"
        f"📦 **Your limit:** `{limit_str}`\n"
        f"📊 **Today:** `{user.daily_downloads} / {LIMITS[user.role]}`\n\n"
        f"👇 *Choose a format:*"
    )
    bot.edit_message_text(text, message.chat.id, status_msg.message_id, reply_markup=get_inline_menu(msg_id), parse_mode="Markdown")

@bot.callback_query_handler(func=lambda call: call.data == 'cancel')
def cancel_action(call):
    popup(call, "Cancelled.")
    bot.delete_message(call.message.chat.id, call.message.message_id)


HOWTO_TEXT = (
    "📥 **How to download**\n\n"
    "1. Send a video link (YouTube, TikTok, Instagram, Facebook, X).\n"
    "2. Tap **Video**, **Audio** or **Thumbnail**.\n"
    "3. Wait for the download - you will see the progress %.\n\n"
    "Free plan: 5 downloads/day, max 50MB per file."
)


@bot.callback_query_handler(func=lambda call: call.data.startswith('ui:'))
def ui_popup_handler(call):
    action = call.data.split(':', 1)[1]
    if action == 'howto':
        popup(call, "Send a link, then tap Video, Audio or Thumbnail.")
        bot.send_message(call.message.chat.id, HOWTO_TEXT, parse_mode="Markdown")
    elif action == 'help':
        popup(call, "Help: send a link, then choose a format.")
        bot.send_message(
            call.message.chat.id,
            "ℹ️ **Help**\n- Send a video link, then choose Video / Audio / Thumbnail.\n"
            "- `/redeem CODE` - Redeem a plan code.\n- `/spin` - Bonus downloads.\n- `/settings` - Change settings.",
            parse_mode="Markdown",
        )
    elif action == 'profile':
        popup(call, "Opening your profile...")
        db = SessionLocal()
        user = get_user(db, call.from_user.id, call.from_user.first_name)
        db.close()
        bot.send_message(
            call.message.chat.id,
            f"👤 **Your Profile**\n\n🆔 **ID:** `{user.id}`\n👤 **Plan:** `{user.role.upper()}`\n"
            f"📊 **Today:** `{user.daily_downloads} / {LIMITS[user.role]}`\n📥 **Total downloads:** `{user.total_downloads}`",
            parse_mode="Markdown",
        )
    elif action == 'plans':
        popup(call, "Plans: choose one to upgrade.")
        text = "💎 **Plans**\n\n"
        for role, price in PRICING.items():
            limit_str = "2GB Size Limit" if role in ['heroic', 'master', 'membership'] else "50MB Size Limit"
            text += f"🔹 **{role.capitalize()}** ({LIMITS[role]} / day | {limit_str}) - **{price}**\n"
        bot.send_message(call.message.chat.id, text, parse_mode="Markdown")

@bot.callback_query_handler(func=lambda call: call.data.startswith('dl|'))
def process_dl(call):
    parts = call.data.split('|')
    dl_type = parts[1]
    msg_id = int(parts[2])
    stored = url_storage.get(msg_id)

    if not stored:
        return popup(call, "This link expired. Send the link again.")

    # url_storage holds (user_id, url, info); accept older (user_id, url) too.
    if isinstance(stored, tuple) and len(stored) == 3:
        owner_id, url, cached_info = stored
    elif isinstance(stored, tuple):
        owner_id, url, cached_info = stored[0], stored[1], None
    else:
        owner_id, url, cached_info = call.from_user.id, stored, None
    if call.from_user.id != owner_id:
        return popup(call, "This download belongs to another user.")

    db = SessionLocal()
    user = get_user(db, call.from_user.id, call.from_user.first_name)

    if user.role != 'owner' and user.daily_downloads >= LIMITS[user.role]:
        db.close()
        return popup(call, "Daily limit reached. Upgrade your plan for more downloads.")

    popup(call, "Downloading... please wait.")
    msg = bot.edit_message_text("⏳ Starting download...", call.message.chat.id, call.message.message_id)

    if user.role != 'owner':
        user.daily_downloads += 1
    user.total_downloads += 1
    db.commit()

    # One folder per download: no old-file cleanup, no picking another user's file.
    dl_dir = os.path.join('downloads', f"dl_{user.id}_{uuid.uuid4().hex[:8]}")
    os.makedirs(dl_dir, exist_ok=True)
    is_premium = user.role in ['heroic', 'master', 'membership', 'owner']
    max_allowed_size = 1950.0 if is_premium else 49.5

    def fmt_duration(seconds):
        try:
            seconds = int(seconds)
            return f"{seconds // 60}:{seconds % 60:02d}"
        except Exception:
            return None

    def progress_hook(d):
        # Throttled progress updates so Telegram is not flooded with edits.
        if d.get('status') != 'downloading':
            return
        now = time.time()
        if now - progress_hook.last < 5:
            return
        progress_hook.last = now
        downloaded = d.get('downloaded_bytes') or 0
        total = d.get('total_bytes') or d.get('total_bytes_estimate') or 0
        pct = f"{downloaded * 100 / total:.0f}%" if total else ""
        speed = d.get('speed')
        speed_txt = f" - {speed / 1024 / 1024:.1f} MB/s" if speed else ""
        try:
            bot.edit_message_text(
                f"⬇️ Downloading... {pct}{speed_txt}",
                call.message.chat.id, msg.message_id,
            )
        except Exception:
            pass
    progress_hook.last = 0

    ydl_opts = {
        'outtmpl': os.path.join(dl_dir, '%(id)s.%(ext)s'),
        'quiet': True,
        'nocheckcertificate': True,
        'no_warnings': True,
        'ignoreerrors': False,
        'noplaylist': True,
        'socket_timeout': 120,
        'retries': 10,
        'fragment_retries': 10,
        'extractor_retries': 5,
        'http_headers': {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36',
            'Accept-Language': 'en-US,en;q=0.9',
        },
        'progress_hooks': [progress_hook],
        # Video must merge video+audio: sites like Pinterest only offer
        # separate video-only and audio-only HLS formats, so a plain
        # 'best' selector fails with "Requested format is not available".
        'format': (
            'bestvideo[ext=mp4]+bestaudio/bestvideo+bestaudio/best' if is_premium
            else 'bestvideo[height<=720][ext=mp4]+bestaudio/bestvideo[height<=720]+bestaudio/bestvideo+bestaudio/best'
        ) if dl_type == 'vid' else 'bestaudio/best',
        'merge_output_format': 'mp4' if dl_type == 'vid' else None,
    }
    if dl_type == 'aud' and shutil.which('ffmpeg'):
        ydl_opts['format'] = 'bestaudio/best'
        ydl_opts['postprocessors'] = [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '192',
        }]

    if os.path.exists("cookies.txt"):
        ydl_opts['cookiefile'] = 'cookies.txt'

    if dl_type == 'thumb':
        ydl_opts['skip_download'] = True
        ydl_opts['writethumbnail'] = True

    try:
        # Reuse the info detected when the link was sent, if available.
        # Otherwise check size/title first, so a huge free-plan file is
        # not fully downloaded. Facebook pages sometimes time out once -
        # retry the metadata read once.
        info = None
        if cached_info and dl_type != 'thumb':
            info = {
                'title': cached_info.get('title'),
                'duration': None,
            }
            # Duration was stored as text; keep it for the caption below.
            cached_duration_txt = cached_info.get('duration')
        else:
            cached_duration_txt = None
        if not info or not cached_info:
            last_meta_error = None
            for attempt in range(2):
                try:
                    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                        info = ydl.extract_info(url, download=False)
                    break
                except Exception as exc:
                    last_meta_error = exc
                    if attempt == 0 and 'timed out' in str(exc).lower():
                        bot.edit_message_text("⏳ Site is slow, retrying...", call.message.chat.id, msg.message_id)
                        time.sleep(5)
                        continue
                    raise
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            if not info:
                raise Exception("Could not read this link.")
            size_mb = None
            raw_size = info.get('filesize') or info.get('filesize_approx')
            if raw_size:
                size_mb = raw_size / (1024 * 1024)
            elif cached_info and cached_info.get('size_mb'):
                try:
                    size_mb = float(str(cached_info['size_mb']).split()[0])
                except Exception:
                    size_mb = None
            if dl_type != 'thumb' and size_mb and size_mb > max_allowed_size and not USE_LOCAL_SERVER:
                raise Exception(f"File is about {size_mb:.0f}MB, over your {max_allowed_size:.0f}MB limit.")
            title = info.get('title') or 'Download'
            duration_txt = fmt_duration(info.get('duration')) or cached_duration_txt

            if dl_type == 'thumb':
                thumb_url = info.get('thumbnail')
                if thumb_url:
                    bot.send_photo(call.message.chat.id, thumb_url, caption=f"🖼️ {title[:200]}", parse_mode="Markdown")
                else:
                    raise Exception("No thumbnail found for this link.")
                bot.delete_message(call.message.chat.id, msg.message_id)
                return

            bot.edit_message_text("⬇️ Downloading file...", call.message.chat.id, msg.message_id)
            try:
                info = ydl.extract_info(url, download=True)
            except Exception as dl_exc:
                # Last-resort fallback if a site offers no format matching
                # the selector above: try the plain merged best format.
                if 'Requested format is not available' not in str(dl_exc):
                    raise
                ydl_opts['format'] = 'bestvideo+bestaudio/best'
                ydl_opts.pop('merge_output_format', None)
                with yt_dlp.YoutubeDL(ydl_opts) as ydl2:
                    info = ydl2.extract_info(url, download=True)
            if not info:
                raise Exception("Could not download this link. It may be private or need login.")

            files = [
                f for f in glob.glob(os.path.join(dl_dir, '*'))
                if not f.endswith(('.part', '.ytdl', '.tmp'))
            ]
            if dl_type == 'vid':
                files = [f for f in files if f.lower().endswith(('.mp4', '.mkv', '.webm', '.mov'))] or files
            else:
                files = [f for f in files if f.lower().endswith(('.mp3', '.m4a', '.ogg', '.opus', '.webm'))] or files
            files = [f for f in files if not f.lower().endswith(('.jpg', '.png', '.webp'))]
            if not files:
                raise Exception("Download failed. The file may be protected or need login.")
            path = max(files, key=os.path.getsize)
            file_size = os.path.getsize(path) / (1024 * 1024)
            if file_size > max_allowed_size and not USE_LOCAL_SERVER:
                raise Exception(f"File is {file_size:.1f}MB, over your {max_allowed_size:.0f}MB limit.")

            caption = f"✅ {title[:200]}"
            if duration_txt:
                caption += f"\n⏱ {duration_txt}"
            caption += f"\n📦 {file_size:.1f} MB"
            bot.send_chat_action(call.message.chat.id, 'upload_video' if dl_type == 'vid' else 'upload_document')
            with open(path, 'rb') as file:
                if dl_type == 'aud':
                    bot.send_audio(call.message.chat.id, file, title=title[:200], caption=caption)
                else:
                    bot.send_video(call.message.chat.id, file, caption=caption, supports_streaming=True)
            bot.delete_message(call.message.chat.id, msg.message_id)
            if user.auto_delete:
                try:
                    bot.delete_message(call.message.chat.id, msg_id)
                except Exception:
                    pass

    except Exception as e:
        logger.error(f"Execution Error: {traceback.format_exc()}")
        if user.role != 'owner' and user.daily_downloads > 0:
            user.daily_downloads -= 1
        user.total_downloads -= 1
        db.commit()
        msg_text = str(e)
        if "timed out" in msg_text.lower() or "timeout" in msg_text.lower():
            msg_text = "Facebook took too long to respond (timeout). Try the link again in a bit - this is usually Facebook being slow, not your limit. If the video is private, it will need login/cookies."
        elif "Requested format is not available" in msg_text:
            msg_text = "No downloadable video format was found for this link. It may be an image pin, a slideshow, or a format this site does not offer as video - try Thumbnail instead."
        elif "Private video" in msg_text or "403" in msg_text or "login" in msg_text.lower() or "ffmpeg is not installed" in msg_text.lower():
            msg_text = ("This link is private, restricted, or needs login, so it could not be downloaded. "
                        "If you can watch it while logged in, the owner can send /cookies and upload their own cookies.txt, then try again.")
        error_msg = f"❌ **Download failed:** {msg_text}\n\nYour download was refunded."
        try:
            bot.edit_message_text(error_msg, call.message.chat.id, msg.message_id, parse_mode="Markdown")
        except Exception:
            pass
    finally:
        db.close()
        shutil.rmtree(dl_dir, ignore_errors=True)
        if msg_id in url_storage:
            del url_storage[msg_id]

# --- AI CHAT HANDLER (DEEPSEEK) ---
@bot.message_handler(func=lambda m: m.text and m.from_user.id in chat_mode_users and not m.text.startswith('/'))
def handle_ai_chat(message):
    if not DEEPSEEK_API_KEY:
        return bot.reply_to(message, "⚠️ AI Processing module is disconnected. (Needs DEEPSEEK_API_KEY).")
        
    bot.send_chat_action(message.chat.id, 'typing')
    try:
        headers = {
            "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
            "Content-Type": "application/json"
        }
        
        # Owner check for special AI treatment
        is_owner = (message.from_user.id == OWNER_ID)
        if is_owner:
            sys_msg = "You are the AURA Core AI. The user you are currently talking to is the Supreme Commander (The Creator/Owner of this Bot). Address him with high respect, loyalty, and futuristic vocabulary. Keep answers concise."
        else:
            sys_msg = "You are a helpful and friendly AI support assistant for the 'AURA Downloader Bot'. If the user asks a question in Bengali, reply in Bengali. If they ask in English, reply in English. Keep answers short and futuristic."
            
        payload = {
            "model": "deepseek-chat",
            "messages": [
                {
                    "role": "system", 
                    "content": sys_msg
                },
                {"role": "user", "content": message.text}
            ],
            "max_tokens": 500
        }
        
        response = requests.post("https://api.deepseek.com/chat/completions", headers=headers, json=payload, timeout=20)
        response_data = response.json()
        
        if "choices" in response_data:
            reply = response_data["choices"][0]["message"]["content"]
            bot.reply_to(message, reply, parse_mode="Markdown")
        else:
            logger.error(f"DeepSeek Logic Error: {response_data}")
            bot.reply_to(message, "❌ AI Processor crashed. Invalid logic gate.")
            
    except Exception as e:
        logger.error(f"AI Ping Error: {e}")
        bot.reply_to(message, "❌ AI node is temporarily congested. Try again.")

if __name__ == "__main__":
    if not os.path.exists("downloads"):
        os.makedirs("downloads")
    logger.info("AURA Subsystems Engaged!")
    bot.infinity_polling()
