import os
import secrets
import string
import time
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
OWNER_ID = 8651895707  
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
MAINTENANCE_MSG = "🛠 **AURA Core under maintenance. Please stand by.**"
url_storage = {}
user_cooldowns = {}
chat_mode_users = set()

LIMITS = {
    'free': 5, 
    'silver': 20, 
    'gold': 50, 
    'diamond': 100, 
    'owner': 999999
}

PRICING = {
    'silver': '10 TK', 
    'gold': '50 TK', 
    'diamond': '100 TK'
}

UNAUTH_MSG = "🚫 **ACCESS DENIED:** You lack the necessary AURA clearance!"

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
                    bot.send_message(ref.id, "🎉 Someone joined using your invite link! +2 Extra limit added.")
                except:
                    pass

    if user.role not in ['free', 'owner'] and user.role_expires_at:
        if datetime.now() > user.role_expires_at:
            user.role = 'free'
            user.role_expires_at = None
            db.commit()
            try:
                bot.send_message(user.id, "⚠️ Your Premium AURA plan has expired!")
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
            bot.send_document(OWNER_ID, ('aura_backup.csv', csv_data.getvalue()), caption="💾 **Daily AURA Backup**", parse_mode="Markdown")
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
                bot.send_message(message.chat.id, "🚫 **UNAUTHORIZED ADD:**\nOnly the Supreme Commander can add me to groups or channels. Initiating auto-leave protocol! 👋", parse_mode="Markdown")
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
    if '?' in url and ('instagram.com' in url or 'tiktok.com' in url):
        return url.split('?')[0]
    return url

def get_platform_name(url):
    url = url.lower()
    if 'youtube.com' in url or 'youtu.be' in url: return "YouTube 🔴"
    elif 'tiktok.com' in url: return "TikTok 🎵"
    elif 'facebook.com' in url or 'fb.watch' in url or 'fb.gg' in url: return "Facebook 📘"
    elif 'instagram.com' in url: return "Instagram 📸"
    elif 'twitter.com' in url or 'x.com' in url: return "X (Twitter) 🐦"
    elif 'linkedin.com' in url: return "LinkedIn 💼"
    return "Web 🌐"

# --- UI MENUS ---
def get_bottom_keyboard():
    markup = ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    markup.add(
        KeyboardButton("👤 Profile"), 
        KeyboardButton("💎 Get Subscriptions"),
        KeyboardButton("🏆 Leaderboard"), 
        KeyboardButton("🎁 Invite & Earn"),
        KeyboardButton("🎁 Daily Claim"), 
        KeyboardButton("ℹ️ Help & Rules")
    )
    return markup

def get_inline_menu(msg_id):
    markup = InlineKeyboardMarkup()
    markup.row(
        InlineKeyboardButton("🎬 Video", callback_data=f"dl|vid|{msg_id}"),
        InlineKeyboardButton("🎵 Audio", callback_data=f"dl|aud|{msg_id}")
    )
    markup.row(
        InlineKeyboardButton("🖼 Thumb", callback_data=f"dl|thumb|{msg_id}"),
        InlineKeyboardButton("❌ Cancel", callback_data="cancel")
    )
    return markup

def loading_animation(chat_id, msg_id):
    stages = [
        "💠 *Initiating AURA Protocol...*",
        "🌀 *Extracting media streams...*",
        "⚡ *Bypassing firewalls & fetching data...*",
        "✅ *AURA Sync Complete!*"
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
            btn.add(InlineKeyboardButton(f"📢 Join {ch}", url=f"https://t.me/{ch.replace('@', '')}"))
        return bot.reply_to(message, "⚠️ **Please join our channels to authorize your AURA connection!**", reply_markup=btn, parse_mode="Markdown")

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
        return bot.reply_to(message, "❌ You have been banished from the AURA network.")
    
    if user.role == 'owner':
        role_text = "🌌 👑 **SUPREME COMMANDER** 👑 🌌"
        usage_text = f"{user.total_downloads} / ∞"
    else:
        role_text = f"`{user.role.capitalize()}`"
        usage_text = f"{user.daily_downloads} / {LIMITS[user.role]}"

    text = f"🚀 **Welcome to AURA DOWNLOADER, {message.from_user.first_name}!**\n"
    text += "Drop any supported media link to sync and extract instantly.\n\n"
    text += f"👑 **AURA Rank:** {role_text}\n"
    text += f"📥 **Bandwidth Usage:** `{usage_text}`\n"
    text += f"👥 **AURA Syndicate:** `{total_users} Citizens`\n\n"
    text += "🤖 **AI Access:** Send `/chat` to communicate with the deep-learning core (DeepSeek).\n\n"
    text += f"👨‍💻 **Architect:** [Ononto Hasan](https://www.facebook.com/yours.ononto)"

    bot.send_message(message.chat.id, text, reply_markup=get_bottom_keyboard(), parse_mode="Markdown", disable_web_page_preview=True)

    if message.from_user.id == OWNER_ID:
        cpu = psutil.cpu_percent()
        ram = psutil.virtual_memory().percent
        flex_text = f"🌌 **AURA SUPREME COMMANDER ONLINE** 🌌\n\n"
        flex_text += "Welcome back to the AURA Mainframe, Master. Systems are fully synchronized and operational.\n\n"
        flex_text += f"📊 **Network Citizens:** `{total_users}`\n"
        flex_text += f"⚡ **Data Extracted:** `{total_bot_dls} Files`\n"
        flex_text += f"🎛️ **Core Status:** CPU `{cpu}%` | RAM `{ram}%`\n\n"
        flex_text += "Awaiting your next directive... 🔮"
        time.sleep(0.5)
        bot.send_message(message.chat.id, flex_text, parse_mode="Markdown")

@bot.message_handler(commands=['chat'])
def start_ai_chat(message):
    chat_mode_users.add(message.from_user.id)
    bot.reply_to(message, "🤖 **AURA Live Chat Initialized!**\nHow can I process your request today? (Powered by DeepSeek).\n\n_Type /chatoff to terminate the connection._", parse_mode="Markdown")

@bot.message_handler(commands=['chatoff'])
def stop_ai_chat(message):
    if message.from_user.id in chat_mode_users:
        chat_mode_users.remove(message.from_user.id)
        bot.reply_to(message, "🛑 **AURA Live Chat Terminated.**\nAwaiting media URLs for extraction!", parse_mode="Markdown")
    else:
        bot.reply_to(message, "Your AI link is already inactive. Send `/chat` to re-establish.", parse_mode="Markdown")

@bot.message_handler(commands=['spin'])
def lucky_spin_cmd(message):
    db = SessionLocal()
    user = get_user(db, message.from_user.id)
    
    if user.daily_downloads < LIMITS[user.role] and user.role != 'owner':
        db.close()
        return bot.reply_to(message, "⚠️ **Your bandwidth is not yet depleted!**\nYou can activate the Lucky Spin 🎰 when today's limit is at zero.", parse_mode="Markdown")

    if user.last_spin and user.last_spin.date() == datetime.now().date():
        db.close()
        return bot.reply_to(message, "⚠️ You have already spun the matrix today! Try again during the next solar cycle.")
        
    user.last_spin = datetime.now()
    bot.reply_to(message, "🎰 **Spinning the AURA wheel...**")
    time.sleep(1.5)
    chance = random.randint(1, 100)
    
    if chance <= 10:
        user.role = 'silver'
        user.role_expires_at = datetime.now() + timedelta(hours=1)
        user.daily_downloads = 0
        result = "🎉 **AURA JACKPOT!** You unlocked the **1 Hour Silver Override**! Enjoy unlimited high-speed extraction for 60 minutes."
    elif chance <= 30:
        user.daily_downloads = max(0, user.daily_downloads - 3)
        result = "🎁 **Excellent!** You received **+3 Extra Extractions** for today!"
    elif chance <= 60:
        user.daily_downloads = max(0, user.daily_downloads - 1)
        result = "🎁 **Good!** You received **+1 Extra Extraction** for today!"
    else:
        result = "💔 **Matrix Failed!** You didn't win anything today. Re-calibrate and try again tomorrow!"
        
    db.commit()
    db.close()
    bot.send_message(message.chat.id, result, parse_mode="Markdown")

@bot.message_handler(func=lambda m: m.text in ["👤 Profile", "💎 Get Subscriptions", "🏆 Leaderboard", "🎁 Invite & Earn", "🎁 Daily Claim", "ℹ️ Help & Rules"])
def bottom_menu_handler(message):
    if MAINTENANCE and message.from_user.id != OWNER_ID:
        return
    db = SessionLocal()
    user = get_user(db, message.from_user.id, message.from_user.first_name)
    
    if user.is_banned:
        db.close()
        return
    
    if message.text == "👤 Profile":
        expiry = user.role_expires_at.strftime("%Y-%m-%d %H:%M") if user.role_expires_at else "Lifetime"
        if user.role == 'owner':
            role_text = "🌌 👑 **SUPREME COMMANDER** 👑 🌌"
            usage_text = f"{user.total_downloads} / ∞"
        else:
            role_text = f"`{user.role.capitalize()}`"
            usage_text = f"{user.daily_downloads} / {LIMITS[user.role]}"

        text = f"👤 **AURA Profile Data**\n\n🆔 UUID: `{user.id}`\n👑 Rank: {role_text}\n⏳ Expiry: `{expiry}`\n📊 **Usage:** `{usage_text}`\n📥 **Total Extracted:** `{user.total_downloads}`\n👥 **Network Invites:** `{user.referral_count}`\n🎰 **Run /spin when limit hits zero!**"
        bot.reply_to(message, text, parse_mode="Markdown")
        
    elif message.text == "💎 Get Subscriptions":
        text = f"💎 **AURA PREMIUM UPGRADES** 💎\n\n🥈 **Silver:** 20 DL/Day ➡️ **{PRICING['silver']}**\n🥇 **Gold:** 50 DL/Day ➡️ **{PRICING['gold']}**\n💎 **Diamond (2GB Core Limit):** 100 DL/Day ➡️ **{PRICING['diamond']}**\n\n💳 **Bkash/Nagad:** `01846849460` (Send Money)\n\n⚠️ After transferring the funds, verify your transaction below:"
        markup = InlineKeyboardMarkup().add(InlineKeyboardButton("✅ Verify Transaction", callback_data="verify_payment"))
        bot.reply_to(message, text, reply_markup=markup, parse_mode="Markdown")
            
    elif message.text == "🏆 Leaderboard":
        top = db.query(User).order_by(User.total_downloads.desc()).limit(5).all()
        text = "🏆 **AURA Elite Extractors**\n\n"
        for i, u in enumerate(top): 
            r_str = "SUPREME COMMANDER" if u.role == 'owner' else u.role.upper()
            text += f"{i+1}. {u.name} (`{u.id}`) - **{r_str}** - 📥 {u.total_downloads}\n"
        bot.reply_to(message, text, parse_mode="Markdown")
        
    elif message.text == "🎁 Invite & Earn":
        bot_username = bot.get_me().username
        text = f"🎁 **Expand the AURA Syndicate!**\nGain +2 limit for every successful invite.\n\n🔗 Your Secure Link: `https://t.me/{bot_username}?start=ref_{user.id}`"
        bot.reply_to(message, text, parse_mode="Markdown")

    elif message.text == "🎁 Daily Claim":
        if user.daily_downloads < LIMITS[user.role] and user.role != 'owner':
            bot.reply_to(message, f"⚠️ **Bandwidth intact!**\nYou can only claim the Daily Bonus when your limit ({LIMITS[user.role]}) is completely empty.", parse_mode="Markdown")
        elif user.last_daily_claim and user.last_daily_claim.date() == datetime.now().date():
            bot.reply_to(message, "⚠️ You have already accessed your daily reserves. Return during the next solar cycle.")
        else:
            user.daily_downloads = max(0, user.daily_downloads - 2)
            user.last_daily_claim = datetime.now()
            db.commit()
            bot.reply_to(message, "🎉 **+2 Extraction Limit Restored!**\nEnjoy your bonus bandwidth.", parse_mode="Markdown")

    elif message.text == "ℹ️ Help & Rules":
        text = "🛠 **AURA Directives:**\n- `/redeem CODE` - Upgrade clearance level.\n- `/spin` - Execute lucky protocol.\n- `/settings` - Configure matrix.\n- `/chat` - Interface with DeepSeek AI.\n- Standard accounts: 5 DL/Day.\n- Max 50MB per node (2GB for Diamond/Commander)."
        bot.reply_to(message, text, parse_mode="Markdown")
        
    db.close()

# --- SETTINGS MENU ---
@bot.message_handler(commands=['settings'])
def settings_cmd(message):
    db = SessionLocal()
    user = get_user(db, message.from_user.id)
    status = "ON 🟢" if user.auto_delete else "OFF 🔴"
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton(f"Auto-Delete Messages: {status}", callback_data=f"set_autodel|{user.id}"))
    bot.reply_to(message, "⚙️ **AURA Matrix Settings**\nConfigure your extraction environment:", reply_markup=markup, parse_mode="Markdown")
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
    markup.add(InlineKeyboardButton(f"Auto-Delete Messages: {status}", callback_data=f"set_autodel|{user.id}"))
    
    bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=markup)
    bot.answer_callback_query(call.id, f"Auto-Delete protocol set to {status.split()[0]}")
    db.close()

# --- PAYMENT VERIFICATION SYSTEM ---
@bot.callback_query_handler(func=lambda call: call.data == 'verify_payment')
def verify_payment_start(call):
    msg = bot.send_message(call.message.chat.id, "📸 Upload the **Screenshot** of your payment protocol here.")
    bot.register_next_step_handler(msg, process_payment_ss)

def process_payment_ss(message):
    if not message.photo:
        bot.reply_to(message, "❌ Invalid media. Please initiate 'Verify Transaction' again and upload an image.")
        return
    file_id = message.photo[-1].file_id
    msg = bot.reply_to(message, "✅ Image logged. Input your **TrxID (Transaction ID)** or the origin phone number.")
    bot.register_next_step_handler(msg, process_payment_trxid, file_id)

def process_payment_trxid(message, file_id):
    trxid = message.text
    user_id = message.from_user.id
    name = message.from_user.first_name

    markup = InlineKeyboardMarkup()
    markup.row(
        InlineKeyboardButton("Approve 🥈 Silver", callback_data=f"apv|silver|{user_id}"),
        InlineKeyboardButton("Approve 🥇 Gold", callback_data=f"apv|gold|{user_id}")
    )
    markup.row(
        InlineKeyboardButton("Approve 💎 Diamond", callback_data=f"apv|diamond|{user_id}"),
        InlineKeyboardButton("❌ Reject", callback_data=f"apv|reject|{user_id}")
    )

    caption = f"💳 **AURA Upgrade Request!**\n\n👤 User: {name} (`{user_id}`)\n🔢 TrxID / Number: `{trxid}`"
    bot.send_photo(OWNER_ID, file_id, caption=caption, reply_markup=markup, parse_mode="Markdown")
    bot.reply_to(message, "✅ Request transmitted to Supreme Commander. Your rank will update shortly!")

@bot.callback_query_handler(func=lambda call: call.data.startswith('apv|'))
def admin_approve_payment(call):
    if call.from_user.id != OWNER_ID:
        return bot.answer_callback_query(call.id, "🚫 UNAUTHORIZED!", show_alert=True)
        
    parts = call.data.split('|')
    action = parts[1]
    target_id = int(parts[2])
    
    db = SessionLocal()
    target_user = get_user(db, target_id)
    
    if action == 'reject':
        bot.send_message(target_id, "❌ Upgrade failed. The Supreme Commander has rejected your transaction.")
        bot.answer_callback_query(call.id, "Rejected!")
        bot.edit_message_caption(f"{call.message.caption}\n\n❌ **STATUS: REJECTED**", call.message.chat.id, call.message.message_id)
    else:
        target_user.role = action
        target_user.role_expires_at = datetime.now() + timedelta(days=30) 
        db.commit()
        bot.send_message(target_id, f"🎉 **Clearance Updated!** Your transaction was successful.\nYou are now holding **{action.capitalize()}** rank in the AURA network.")
        bot.answer_callback_query(call.id, f"Approved as {action}!")
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
    bot.reply_to(message, "🔴 **Maintenance Mode:** `ON`\nOnly the Supreme Commander can use the bot now.", parse_mode="Markdown")

@bot.message_handler(commands=['maintenanceoff'])
def cmd_maintenance_off(message):
    if message.from_user.id != OWNER_ID:
        return bot.reply_to(message, UNAUTH_MSG, parse_mode="Markdown")
    global MAINTENANCE
    MAINTENANCE = False
    bot.reply_to(message, "🟢 **Maintenance Mode:** `OFF`\nAll citizens can now access the AURA network.", parse_mode="Markdown")

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
        if role not in ['silver', 'gold', 'diamond']: 
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
            bot.reply_to(message, f"🎁 **{count} Overrides Generated!**\n\n👑 Rank: `{role.capitalize()}`\n⏳ Validity: `{hours} Hours`\n\n{codes_str}", parse_mode="Markdown")
        else:
            file_name = f"AURA_Codes_{role.upper()}_{count}.txt"
            with open(file_name, "w") as f:
                f.write("\n".join(generated_codes))
            with open(file_name, "rb") as f:
                bot.send_document(message.chat.id, f, caption=f"🎁 **{count} Overrides Generated!**\n👑 Rank: `{role.capitalize()}`\n⏳ Validity: `{hours} Hours`", parse_mode="Markdown")
            os.remove(file_name)
            
    except Exception as e:
        bot.reply_to(message, f"❌ Error: Syntax failure. Example: `/gencode100 diamond 48`", parse_mode="Markdown")

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
        return bot.reply_to(message, "❌ You have already executed a redeem sequence today. Try again tomorrow.")

    code_in = parts[1].strip()
    c = db.query(RedeemCode).filter(RedeemCode.code == code_in).first()
    
    if not c:
        bot.reply_to(message, "❌ Invalid Override Code.")
    elif c.is_used:
        bot.reply_to(message, "❌ This sequence has already been compromised by another user.")
    elif c.expires_at and datetime.now() > c.expires_at:
        bot.reply_to(message, "❌ Code lifespan has expired!")
    else:
        user.role = c.role_granted
        user.role_expires_at = datetime.now() + timedelta(days=1)
        user.last_code_used = datetime.now()
        user.daily_downloads = 0
        c.is_used = True
        db.commit()
        bot.reply_to(message, f"✅ **AURA Matrix Updated!**\nYou are now running the **{c.role_granted.capitalize()}** protocol for 24 hours.")
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
            bot.send_message(user_id, f"🎁 **AURA Supply Drop!**\nThe Supreme Commander has granted you **{role.capitalize()}** clearance for {days} days.")
        else:
            bot.reply_to(message, "❌ ID not found in the matrix.")
        db.close()
    except:
        bot.reply_to(message, "Use: `/gift [ID] [role] [days]`", parse_mode="Markdown")

@bot.message_handler(commands=['ping'])
def ping_cmd(message):
    if message.from_user.id != OWNER_ID:
        return bot.reply_to(message, UNAUTH_MSG, parse_mode="Markdown")
    start_time = time.time()
    msg = bot.reply_to(message, "Pinging Core...")
    end_time = time.time()
    bot.edit_message_text(f"🏓 **AURA Latency**\nResponse Time: `{round((end_time - start_time) * 1000)}ms`\nStatus: 🟢 ONLINE", msg.chat.id, msg.message_id, parse_mode="Markdown")

@bot.message_handler(commands=['admin'])
def admin_panel(message):
    if message.from_user.id != OWNER_ID:
        return bot.reply_to(message, UNAUTH_MSG, parse_mode="Markdown")
    markup = InlineKeyboardMarkup()
    markup.row(
        InlineKeyboardButton("📊 Core Stats", callback_data="admin_stats"), 
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
        text = f"📊 **AURA Diagnostics**\n👥 Nodes: {users}\n📥 Extracted: {dls}\n🖥 CPU: {cpu}% | RAM: {ram}%"
        bot.answer_callback_query(call.id)
        
        markup = InlineKeyboardMarkup()
        markup.row(
            InlineKeyboardButton("📊 Core Stats", callback_data="admin_stats"), 
            InlineKeyboardButton("🛠 Maint. Protocol", callback_data="admin_maint")
        )
        bot.edit_message_text(text, call.message.chat.id, call.message.message_id, reply_markup=markup, parse_mode="Markdown")
        
    elif action == "maint":
        global MAINTENANCE
        MAINTENANCE = not MAINTENANCE
        status = "ON 🔴" if MAINTENANCE else "OFF 🟢"
        bot.answer_callback_query(call.id, f"Maintenance is now {status}", show_alert=True)

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
            bot.reply_to(message, f"🔍 **Node Identity:**\nName: {u.name}\nID: `{u.id}`\nRank: {u.role}\nDLs: {u.total_downloads}\nBlacklisted: {u.is_banned}", parse_mode="Markdown")
        else:
            bot.reply_to(message, "❌ ID not found.")
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
            bot.reply_to(message, f"✅ User {user_id} has been {cmd}ned.")
        else:
            bot.reply_to(message, "❌ ID not found.")
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
            bot.reply_to(message, f"✅ Clearance updated to {role}.")
            bot.send_message(user_id, f"🎉 Supreme Commander has upgraded your rank to **{role.capitalize()}**!")
        else:
            bot.reply_to(message, "❌ ID not found.")
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
            bot.reply_to(message, f"✅ Allocated {amount} extra bandwidth to {user_id}.")
            bot.send_message(user_id, f"🎁 Supreme Commander injected {amount} extra extractions to your node!")
        else:
            bot.reply_to(message, "❌ ID not found.")
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
    bot.send_document(message.chat.id, ('aura_users.csv', csv_data.getvalue()), caption="📊 **AURA Database Snapshot**", parse_mode="Markdown")
    db.close()

@bot.message_handler(commands=['msg'])
def direct_msg_cmd(message):
    if message.from_user.id != OWNER_ID:
        return bot.reply_to(message, UNAUTH_MSG, parse_mode="Markdown")
    try:
        parts = message.text.split(' ', 2)
        target_id = int(parts[1])
        text = parts[2]
        bot.send_message(target_id, f"📩 **Secure Transmission from Commander:**\n\n{text}", parse_mode="Markdown")
        bot.reply_to(message, "✅ Transmission successful.")
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
                bot.send_message(u.id, f"📢 **AURA Broadcast:**\n\n{text}", reply_markup=markup, parse_mode="Markdown")
                success += 1
                time.sleep(0.05)
            except:
                pass
        db.close()
        bot.reply_to(message, f"✅ Transmission delivered to {success} nodes.")
    except:
        bot.reply_to(message, "Use: `/sendad Ad Text | Button Text | Button URL`")

@bot.message_handler(commands=['broadcast'])
def broadcast_cmd(message):
    if message.from_user.id != OWNER_ID:
        return bot.reply_to(message, UNAUTH_MSG, parse_mode="Markdown")
    msg_text = message.text.replace('/broadcast', '').strip()
    if not msg_text:
        return bot.reply_to(message, "⚠️ Execute like this: `/broadcast Hello!`", parse_mode="Markdown")
    
    db = SessionLocal()
    users = db.query(User).all()
    bot.reply_to(message, f"📢 Syncing broadcast with {len(users)} nodes...")
    
    success = 0
    for u in users:
        try:
            bot.send_message(u.id, f"📢 **AURA Network Notice:**\n\n{msg_text}", parse_mode="Markdown")
            success += 1
            time.sleep(0.05)
        except:
            pass
    db.close()
    bot.reply_to(message, f"✅ Broadcast Complete! Sent to {success} active nodes.")

# --- CORE DOWNLOADER (FULLY OPTIMIZED) ---
@bot.message_handler(regexp=r'http[s]?://(?:[a-zA-Z]|[0-9]|[$-_@.&+]|[!*\(\),]|(?:%[0-9a-fA-F][0-9a-fA-F]))+')
def handle_link(message):
    user_id = message.from_user.id
    if MAINTENANCE and user_id != OWNER_ID:
        return bot.reply_to(message, MAINTENANCE_MSG, parse_mode="Markdown")
    
    if not check_force_sub(user_id):
        return bot.reply_to(message, "⚠️ Authenticate via channel first.")

    now = time.time()
    if user_id in user_cooldowns and (now - user_cooldowns[user_id]) < 3:
        return bot.reply_to(message, "🐢 **Overload Warning!** Please wait 3 seconds before next input.", parse_mode="Markdown")
    user_cooldowns[user_id] = now

    db = SessionLocal()
    user = get_user(db, user_id, message.from_user.first_name)
    db.close()
    
    if user.is_banned:
        return

    if user.role != 'owner' and user.daily_downloads >= LIMITS[user.role]:
        limit_msg = (
            f"❌ **Your Daily Bandwidth is completely exhausted! ({LIMITS[user.role]}/{LIMITS[user.role]})**\n\n"
            f"💎 **UPGRADE MATRIX:**\n"
            f"🥈 Silver (20 DL) - **{PRICING['silver']}**\n"
            f"🥇 Gold (50 DL) - **{PRICING['gold']}**\n"
            f"💎 Diamond (100 DL / 2GB File) - **{PRICING['diamond']}**\n\n"
            f"Click **'💎 Get Subscriptions'** to unlock higher extraction power!"
        )
        return bot.reply_to(message, limit_msg, parse_mode="Markdown")

    bot.send_chat_action(message.chat.id, 'typing')
    url = clean_url(message.text.strip())
    msg_id = message.message_id
    url_storage[msg_id] = url
    
    platform = get_platform_name(url)
    
    text = (
        f"🔗 *Signal Locked and Decoded!*\n\n"
        f"📡 **Source:** `{platform}`\n"
        f"📦 **Limit:** `50 MB Compression Enabled`\n\n"
        f"👇 *Select your extraction method:*"
    )
    bot.reply_to(message, text, reply_markup=get_inline_menu(msg_id), parse_mode="Markdown")

@bot.callback_query_handler(func=lambda call: call.data == 'cancel')
def cancel_action(call):
    bot.delete_message(call.message.chat.id, call.message.message_id)

@bot.callback_query_handler(func=lambda call: call.data.startswith('dl|'))
def process_dl(call):
    parts = call.data.split('|')
    dl_type = parts[1]
    msg_id = int(parts[2])
    url = url_storage.get(msg_id)
    
    if not url:
        return bot.answer_callback_query(call.id, "❌ Session expired!", show_alert=True)

    db = SessionLocal()
    user = get_user(db, call.from_user.id, call.from_user.first_name)
    
    if user.role != 'owner' and user.daily_downloads >= LIMITS[user.role]:
        db.close()
        return bot.answer_callback_query(call.id, "❌ Limit Exceeded! Upgrade your rank.", show_alert=True)

    msg = bot.edit_message_text("⏳ Requesting data...", call.message.chat.id, call.message.message_id)
    
    loading_animation(call.message.chat.id, msg.message_id)

    if user.role != 'owner': 
        user.daily_downloads += 1
    user.total_downloads += 1
    db.commit()

    ydl_opts = {
        'outtmpl': f'downloads/%(id)s_{user.id}.%(ext)s',
        'quiet': True,
        'nocheckcertificate': True,
        'no_warnings': True,
        'ignoreerrors': False, 
        'noplaylist': True,
        'http_headers': {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'en-us,en;q=0.5',
            'Sec-Fetch-Mode': 'navigate'
        }
    }
    
    if dl_type == 'vid':
        if 'youtube.com' in url or 'youtu.be' in url:
            ydl_opts['format'] = 'b/best/w'
        else:
            ydl_opts['format'] = 'bestvideo+bestaudio/best/b/worst'
    elif dl_type == 'aud':
        ydl_opts['format'] = 'm4a/bestaudio/best'

    if dl_type == 'thumb': 
        ydl_opts['skip_download'] = True
        ydl_opts['writethumbnail'] = True

    try:
        bot.edit_message_text("🚀 **Uploading payload to Telegram...**\nPlease maintain connection.", call.message.chat.id, msg.message_id, parse_mode="Markdown")
        
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            
            if not info:
                raise Exception("Failed to extract data. Media might be protected.")

            if dl_type == 'thumb':
                thumb_url = info.get('thumbnail')
                if thumb_url:
                    bot.send_photo(call.message.chat.id, thumb_url, caption="🖼 **AURA Visuals**", parse_mode="Markdown")
                bot.delete_message(call.message.chat.id, msg.message_id)
                return

            downloaded_files = glob.glob(f'downloads/*_{user.id}.*')
            if not downloaded_files:
                raise Exception("Data extraction failure in local memory.")
            path = downloaded_files[0]

            if os.path.exists(path):
                file_size = os.path.getsize(path) / (1024 * 1024)
                max_allowed_size = 49.5 if user.role not in ['diamond', 'owner'] else 1950.0
                
                if file_size > max_allowed_size and not USE_LOCAL_SERVER:
                    os.remove(path)
                    raise Exception(f"File size limit exceeded ({round(file_size, 1)}MB). Max allowed is {max_allowed_size}MB.")

                bot.send_chat_action(call.message.chat.id, 'upload_video' if dl_type == 'vid' else 'upload_document')
                
                with open(path, 'rb') as file:
                    if dl_type == 'aud': 
                        bot.send_audio(call.message.chat.id, file, title=info.get('title', 'AURA Audio'), caption="⚡ **AURA CORE**")
                    else: 
                        bot.send_video(call.message.chat.id, file, caption="⚡ **AURA DOWNLOADER**", supports_streaming=True)
                
                try:
                    os.remove(path)
                except:
                    pass
                
                bot.delete_message(call.message.chat.id, msg.message_id)
                
                if user.auto_delete:
                    try:
                        bot.delete_message(call.message.chat.id, msg_id)
                    except:
                        pass
                
    except Exception as e:
        logger.error(f"Extraction Error: {traceback.format_exc()}")
        
        if user.role != 'owner' and user.daily_downloads > 0:
            user.daily_downloads -= 1
        user.total_downloads -= 1
        db.commit()
        
        error_msg = f"❌ **Error:** {str(e)}"
        
        if "size limit exceeded" in str(e):
            error_msg = f"❌ {str(e)}\n\nPlease upgrade your AURA rank for heavier files."
        elif "Private video" in str(e) or "Status code 403" in str(e):
            error_msg = "❌ Target system is heavily encrypted (Private/Blocked)."
            
        try:
            bot.edit_message_text(f"{error_msg}\n\nBandwidth refunded to your node!", call.message.chat.id, msg.message_id)
        except:
            pass
            
    finally:
        db.close()
        if msg_id in url_storage:
            del url_storage[msg_id]

# --- AI CHAT HANDLER (DEEPSEEK) ---
@bot.message_handler(func=lambda m: m.text and m.from_user.id in chat_mode_users and not m.text.startswith('/'))
def handle_ai_chat(message):
    if not DEEPSEEK_API_KEY:
        return bot.reply_to(message, "⚠️ DeepSeek core is offline. (Admin: Initialize DEEPSEEK_API_KEY).")
        
    bot.send_chat_action(message.chat.id, 'typing')
    try:
        headers = {
            "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
            "Content-Type": "application/json"
        }
        payload = {
            "model": "deepseek-chat",
            "messages": [
                {
                    "role": "system", 
                    "content": "You are a helpful and friendly AI support assistant for the 'AURA Downloader Bot'. "
                               "The bot helps users download media from YouTube, Facebook, TikTok, Instagram, etc. "
                               "If the user asks a question in Bengali, reply in Bengali. If they ask in English, reply in English. Keep answers short and futuristic."
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
            logger.error(f"DeepSeek API Error: {response_data}")
            bot.reply_to(message, "❌ Core Error: Connection to DeepSeek failed.")
            
    except Exception as e:
        logger.error(f"DeepSeek Request Error: {e}")
        bot.reply_to(message, "❌ The AI matrix is currently overloaded. Retry later.")

if __name__ == "__main__":
    if not os.path.exists("downloads"):
        os.makedirs("downloads")
    logger.info("AURA Core System Online!")
    bot.infinity_polling()
