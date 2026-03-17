import os
import secrets
import string
import time
import logging
import glob
import csv
from io import StringIO
from datetime import datetime, timedelta
import yt_dlp
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton
from sqlalchemy import create_engine, Column, Integer, String, Boolean, BigInteger, DateTime
from sqlalchemy.orm import declarative_base, sessionmaker
from apscheduler.schedulers.background import BackgroundScheduler

try:
    import psutil
except ImportError:
    os.system("pip install psutil")
    import psutil

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --- CONFIGURATIONS ---
BOT_TOKEN = os.getenv("BOT_TOKEN")
OWNER_ID = 8651895707 # Main Owner ID
DATABASE_URL = os.getenv("DATABASE_URL")

# Multi-Channel Force Sub (Array)
FORCE_CHANNELS = [] 

if DATABASE_URL and DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

bot = telebot.TeleBot(BOT_TOKEN)
url_storage = {}
user_cooldowns = {} 
MAINTENANCE = False 

# --- SUBSCRIPTION LIMITS & PRICING ---
LIMITS = {
    'free': 5,
    'silver': 20,
    'gold': 50,
    'diamond': 100,
    'owner': 999999
}

PRICING = {'silver': '10 TK', 'gold': '50 TK', 'diamond': '100 TK'}

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
    total_downloads = Column(Integer, default=0)
    is_banned = Column(Boolean, default=False)
    referred_by = Column(BigInteger, nullable=True)
    referral_count = Column(Integer, default=0)
    join_date = Column(DateTime, default=datetime.now)

class RedeemCode(Base):
    __tablename__ = "redeem_codes"
    id = Column(Integer, primary_key=True)
    code = Column(String, unique=True)
    role_granted = Column(String) 
    expires_at = Column(DateTime, nullable=True) 
    is_used = Column(Boolean, default=False)

# DATABASE SAFETY: Drop all bondho kora holo jate data na haray
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
            referrer = db.query(User).filter(User.id == referrer_id).first()
            if referrer:
                referrer.referral_count += 1
                referrer.daily_downloads = max(0, referrer.daily_downloads - 2)
                db.commit()
                try: bot.send_message(referrer.id, f"🎉 Kew apnar invite link diye join koreche! +2 Extra Download limit added.")
                except: pass

    # Smart Role Expiry Reset
    if user.role not in ['free', 'owner'] and user.role_expires_at:
        if datetime.now() > user.role_expires_at:
            user.role = 'free'
            user.role_expires_at = None
            db.commit()
            try: bot.send_message(user.id, "⚠️ Apnar Premium plan er meyad shesh! Apni ekhon Free plan e achen.")
            except: pass
            
    return user

# --- SCHEDULERS ---
def reset_daily_limits():
    db = SessionLocal()
    try:
        db.query(User).update({User.daily_downloads: 0})
        db.commit()
    finally:
        db.close()

def clean_storage():
    files = glob.glob('downloads/*')
    for f in files:
        if os.path.isfile(f): os.remove(f)

scheduler = BackgroundScheduler()
scheduler.add_job(reset_daily_limits, 'cron', hour=0, minute=0)
scheduler.add_job(clean_storage, 'interval', hours=12) 
scheduler.start()

# --- UTILS ---
def check_force_sub(user_id):
    if not FORCE_CHANNELS: return True
    if user_id == OWNER_ID: return True
    for ch in FORCE_CHANNELS:
        try:
            status = bot.get_chat_member(ch, user_id).status
            if status not in ['creator', 'administrator', 'member']: return False
        except: return False
    return True

def clean_url(url):
    if '?' in url and ('instagram.com' in url or 'tiktok.com' in url):
        return url.split('?')[0]
    return url

# --- UI MENUS ---
def get_bottom_keyboard():
    markup = ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    markup.add(
        KeyboardButton("👤 Profile"), KeyboardButton("💎 Get Subscriptions"),
        KeyboardButton("🏆 Leaderboard"), KeyboardButton("🎁 Invite & Earn"),
        KeyboardButton("🎁 Daily Claim"), KeyboardButton("ℹ️ Help & Rules")
    )
    return markup

def get_inline_menu(msg_id):
    markup = InlineKeyboardMarkup()
    markup.row(InlineKeyboardButton("🎬 1080p", callback_data=f"dl|1080|{msg_id}"), InlineKeyboardButton("🎬 720p", callback_data=f"dl|720|{msg_id}"))
    markup.row(InlineKeyboardButton("🎵 Audio", callback_data=f"dl|aud|{msg_id}"), InlineKeyboardButton("🖼 Thumb", callback_data=f"dl|thumb|{msg_id}"))
    markup.row(InlineKeyboardButton("❌ Cancel", callback_data="cancel"))
    return markup

# --- USER COMMANDS ---
@bot.message_handler(commands=['start'])
def start_cmd(message):
    if MAINTENANCE and message.from_user.id != OWNER_ID:
        return bot.reply_to(message, "🛠 **Bot under maintenance.**", parse_mode="Markdown")

    if not check_force_sub(message.from_user.id):
        btn = InlineKeyboardMarkup()
        for ch in FORCE_CHANNELS: btn.add(InlineKeyboardButton(f"📢 Join {ch}", url=f"https://t.me/{ch.replace('@', '')}"))
        return bot.reply_to(message, "⚠️ **Please join our channels to use the bot!**", reply_markup=btn, parse_mode="Markdown")

    parts = message.text.split()
    referrer_id = None
    if len(parts) > 1 and parts[1].startswith('ref_'):
        try: referrer_id = int(parts[1].split('_')[1])
        except: pass

    db = SessionLocal()
    user = get_user(db, message.from_user.id, message.from_user.first_name, referrer_id)
    total_users = db.query(User).count()
    db.close()
    
    if user.is_banned: return bot.reply_to(message, "❌ You are banned.")
    
    text = f"🚀 **Hello {message.from_user.first_name}, Welcome to AURA!**\n\nDrop any video link to start downloading instantly.\n\n👑 **Role:** `{user.role.capitalize()}`\n📥 **Usage:** `{user.daily_downloads} / {LIMITS[user.role]}`\n👥 **Community:** `{total_users} Users`"
    bot.send_message(message.chat.id, text, reply_markup=get_bottom_keyboard(), parse_mode="Markdown")

@bot.message_handler(func=lambda m: m.text in ["👤 Profile", "💎 Get Subscriptions", "🏆 Leaderboard", "🎁 Invite & Earn", "🎁 Daily Claim", "ℹ️ Help & Rules"])
def bottom_menu_handler(message):
    if MAINTENANCE and message.from_user.id != OWNER_ID: return
    db = SessionLocal()
    user = get_user(db, message.from_user.id, message.from_user.first_name)
    
    if user.is_banned:
        db.close()
        return
    
    if message.text == "👤 Profile":
        expiry = user.role_expires_at.strftime("%Y-%m-%d %H:%M") if user.role_expires_at else "Lifetime"
        text = f"👤 **AURA Profile**\n\n🆔 ID: `{user.id}`\n👑 Role: `{user.role.capitalize()}`\n⏳ Expiry: `{expiry}`\n📊 **Usage:** `{user.daily_downloads} / {LIMITS[user.role]}`\n📥 **Total:** `{user.total_downloads}`\n👥 **Invites:** `{user.referral_count}`"
        bot.reply_to(message, text, parse_mode="Markdown")
        
    elif message.text == "💎 Get Subscriptions":
        text = f"💎 **AURA PREMIUM SUBSCRIPTIONS** 💎\n\n🥈 **Silver:** 20 DL/Day ➡️ **{PRICING['silver']}**\n🥇 **Gold:** 50 DL/Day ➡️ **{PRICING['gold']}**\n💎 **Diamond:** 100 DL/Day ➡️ **{PRICING['diamond']}**\n\n💳 **Bkash/Nagad:** `01846849460` (Send Money)\n\n⚠️ Payment korar por nicher button theke verify korun:"
        markup = InlineKeyboardMarkup().add(InlineKeyboardButton("✅ Verify Payment", callback_data="verify_payment"))
        bot.reply_to(message, text, reply_markup=markup, parse_mode="Markdown")
            
    elif message.text == "🏆 Leaderboard":
        top = db.query(User).order_by(User.total_downloads.desc()).limit(5).all()
        text = "🏆 **Top AURA Users**\n\n"
        for i, u in enumerate(top): 
            text += f"{i+1}. {u.name} (`{u.id}`) - **{u.role.upper()}** - 📥 {u.total_downloads}\n"
        bot.reply_to(message, text, parse_mode="Markdown")
        
    elif message.text == "🎁 Invite & Earn":
        bot_username = bot.get_me().username
        text = f"🎁 **Invite & Earn!**\nGet +2 limit for each invite.\n\n🔗 Link: `https://t.me/{bot_username}?start=ref_{user.id}`"
        bot.reply_to(message, text, parse_mode="Markdown")

    elif message.text == "🎁 Daily Claim":
        if user.last_daily_claim and user.last_daily_claim.date() == datetime.now().date():
            bot.reply_to(message, "⚠️ Ajker daily claim apni already niye niyechhen! Kal abar ashben.")
        else:
            user.daily_downloads = max(0, user.daily_downloads - 2)
            user.last_daily_claim = datetime.now()
            db.commit()
            bot.reply_to(message, "🎉 **+2 Extra Downloads Added!**\nEnjoy your daily bonus.", parse_mode="Markdown")

    elif message.text == "ℹ️ Help & Rules":
        text = "🛠 **Commands & Rules:**\n- `/redeem CODE` - Upgrade plan.\n- `/feedback MSG` - Send msg to Admin.\n- Free users get 5 DL/Day.\n- Max 50MB per video."
        bot.reply_to(message, text, parse_mode="Markdown")
        
    db.close()

# --- PAYMENT VERIFICATION SYSTEM ---
@bot.callback_query_handler(func=lambda call: call.data == 'verify_payment')
def verify_payment_start(call):
    msg = bot.send_message(call.message.chat.id, "📸 Doya kore apnar Payment er **Screenshot** ti ekhane send korun.")
    bot.register_next_step_handler(msg, process_payment_ss)

def process_payment_ss(message):
    if not message.photo:
        bot.reply_to(message, "❌ Eita screenshot noy. Abar 'Get Subscriptions' theke 'Verify Payment' e click kore chobi din.")
        return
    file_id = message.photo[-1].file_id
    msg = bot.reply_to(message, "✅ Screenshot peyechi. Ebar apnar **TrxID (Transaction ID)** ba je number theke taka pathiyechen seta likhe send korun.")
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
    markup.row(InlineKeyboardButton("Approve 💎 Diamond", callback_data=f"apv|diamond|{user_id}"))
    markup.row(InlineKeyboardButton("❌ Reject Payment", callback_data=f"apv|reject|{user_id}"))

    caption = f"💳 **New Payment Request!**\n\n👤 User: {name} (`{user_id}`)\n🔢 TrxID / Number: `{trxid}`"
    bot.send_photo(OWNER_ID, file_id, caption=caption, reply_markup=markup, parse_mode="Markdown")
    bot.reply_to(message, "✅ Apnar request Admin er kache pathano hoyeche. Khub taratari apnar plan upgrade hoye jabe!")

@bot.callback_query_handler(func=lambda call: call.data.startswith('apv|'))
def admin_approve_payment(call):
    if call.from_user.id != OWNER_ID: return
    parts = call.data.split('|')
    action = parts[1]
    target_id = int(parts[2])
    
    db = SessionLocal()
    target_user = get_user(db, target_id)
    
    if action == 'reject':
        bot.send_message(target_id, "❌ Sorry, apnar payment verify kora jayni. Admin apnar request reject koreche.")
        bot.answer_callback_query(call.id, "Rejected!")
        bot.edit_message_caption(f"{call.message.caption}\n\n❌ **STATUS: REJECTED**", call.message.chat.id, call.message.message_id)
    else:
        target_user.role = action
        target_user.role_expires_at = datetime.now() + timedelta(days=30) 
        db.commit()
        bot.send_message(target_id, f"🎉 **Congratulations!** Apnar payment verify hoyeche.\nApni ekhon **{action.capitalize()}** plan e achen. Enjoy!")
        bot.answer_callback_query(call.id, f"Approved as {action}!")
        bot.edit_message_caption(f"{call.message.caption}\n\n✅ **STATUS: APPROVED ({action.upper()})**", call.message.chat.id, call.message.message_id)
    db.close()

# --- A TO Z ADMIN COMMANDS ---
@bot.message_handler(commands=['cmds'])
def a_to_z_commands(message):
    if message.from_user.id != OWNER_ID: return
    text = """👑 **AURA Admin Commands (A to Z)** 👑

**🔧 System & Management:**
`/admin` - Open Visual Admin Panel
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
`/feedback [Msg]` - Message Admin
"""
    bot.reply_to(message, text, parse_mode="Markdown")

@bot.message_handler(commands=['gift'])
def gift_cmd(message):
    if message.from_user.id != OWNER_ID: return
    try:
        parts = message.text.split()
        user_id = int(parts[1])
        role = parts[2].lower()
        days = int(parts[3])
        if role not in LIMITS.keys(): raise ValueError
        
        db = SessionLocal()
        u = db.query(User).filter(User.id == user_id).first()
        if u:
            u.role = role
            u.role_expires_at = datetime.now() + timedelta(days=days)
            u.daily_downloads = 0
            db.commit()
            bot.reply_to(message, f"✅ Gifted **{role.capitalize()}** to {user_id} for {days} days.")
            bot.send_message(user_id, f"🎁 **Gift Received!**\nAdmin has gifted you **{role.capitalize()}** for {days} days. Enjoy!")
        else: bot.reply_to(message, "❌ User not found.")
        db.close()
    except: bot.reply_to(message, "Use: `/gift [ID] [role] [days]`", parse_mode="Markdown")

@bot.message_handler(commands=['ping'])
def ping_cmd(message):
    start_time = time.time()
    msg = bot.reply_to(message, "Pinging...")
    end_time = time.time()
    bot.edit_message_text(f"🏓 **Pong!**\nLatency: `{round((end_time - start_time) * 1000)}ms`\nServer: Online ✅", msg.chat.id, msg.message_id, parse_mode="Markdown")

@bot.message_handler(commands=['admin'])
def admin_panel(message):
    if message.from_user.id != OWNER_ID: return
    markup = InlineKeyboardMarkup()
    markup.row(InlineKeyboardButton("📊 System Stats", callback_data="admin_stats"), InlineKeyboardButton("🛠 Maint. Mode", callback_data="admin_maint"))
    bot.reply_to(message, "👑 **AURA Admin Control Panel**", reply_markup=markup, parse_mode="Markdown")

@bot.callback_query_handler(func=lambda call: call.data.startswith('admin_'))
def admin_callbacks(call):
    if call.from_user.id != OWNER_ID: return
    action = call.data.split('_')[1]
    
    if action == "stats":
        db = SessionLocal()
        users = db.query(User).count()
        dls = sum([u.total_downloads for u in db.query(User).all()])
        db.close()
        cpu, ram = psutil.cpu_percent(), psutil.virtual_memory().percent
        text = f"📊 **System Stats**\n👥 Users: {users}\n📥 Downloads: {dls}\n🖥 CPU: {cpu}% | RAM: {ram}%"
        bot.answer_callback_query(call.id)
        
        markup = InlineKeyboardMarkup()
        markup.row(InlineKeyboardButton("📊 System Stats", callback_data="admin_stats"), InlineKeyboardButton("🛠 Maint. Mode", callback_data="admin_maint"))
        bot.edit_message_text(text, call.message.chat.id, call.message.message_id, reply_markup=markup, parse_mode="Markdown")
        
    elif action == "maint":
        global MAINTENANCE
        MAINTENANCE = not MAINTENANCE
        status = "ON 🔴" if MAINTENANCE else "OFF 🟢"
        bot.answer_callback_query(call.id, f"Maintenance is now {status}", show_alert=True)

@bot.message_handler(commands=['search'])
def search_user(message):
    if message.from_user.id != OWNER_ID: return
    try:
        user_id = int(message.text.split()[1])
        db = SessionLocal()
        u = db.query(User).filter(User.id == user_id).first()
        db.close()
        if u: bot.reply_to(message, f"🔍 **User Details:**\nName: {u.name}\nID: `{u.id}`\nRole: {u.role}\nTotal DLs: {u.total_downloads}\nBanned: {u.is_banned}", parse_mode="Markdown")
        else: bot.reply_to(message, "❌ User not found.")
    except: bot.reply_to(message, "Use: `/search [ID]`", parse_mode="Markdown")

@bot.message_handler(commands=['ban', 'unban'])
def ban_unban_user(message):
    if message.from_user.id != OWNER_ID: return
    try:
        cmd = message.text.split()[0].replace('/', '')
        user_id = int(message.text.split()[1])
        db = SessionLocal()
        u = db.query(User).filter(User.id == user_id).first()
        if u:
            u.is_banned = (cmd == 'ban')
            db.commit()
            bot.reply_to(message, f"✅ User {user_id} is now {cmd}ned.")
        else: bot.reply_to(message, "❌ User not found.")
        db.close()
    except: bot.reply_to(message, "Use: `/ban [ID]` or `/unban [ID]`")

@bot.message_handler(commands=['setrole'])
def set_role_cmd(message):
    if message.from_user.id != OWNER_ID: return
    try:
        parts = message.text.split()
        user_id = int(parts[1])
        role = parts[2].lower()
        if role not in LIMITS.keys(): raise ValueError
        
        db = SessionLocal()
        u = db.query(User).filter(User.id == user_id).first()
        if u:
            u.role = role
            u.role_expires_at = datetime.now() + timedelta(days=30)
            u.daily_downloads = 0
            db.commit()
            bot.reply_to(message, f"✅ User {user_id} role updated to {role}.")
            bot.send_message(user_id, f"🎉 Admin has upgraded your account to **{role.capitalize()}**!")
        else: bot.reply_to(message, "❌ User not found.")
        db.close()
    except: bot.reply_to(message, "Use: `/setrole [ID] [role]`", parse_mode="Markdown")

@bot.message_handler(commands=['addlimit'])
def add_limit_cmd(message):
    if message.from_user.id != OWNER_ID: return
    try:
        parts = message.text.split()
        user_id = int(parts[1])
        amount = int(parts[2])
        db = SessionLocal()
        u = db.query(User).filter(User.id == user_id).first()
        if u:
            u.daily_downloads = max(0, u.daily_downloads - amount)
            db.commit()
            bot.reply_to(message, f"✅ Added {amount} extra downloads to {user_id}.")
            bot.send_message(user_id, f"🎁 Admin has given you {amount} extra downloads for today!")
        else: bot.reply_to(message, "❌ User not found.")
        db.close()
    except: bot.reply_to(message, "Use: `/addlimit [ID] [amount]`", parse_mode="Markdown")

@bot.message_handler(commands=['export'])
def export_db_cmd(message):
    if message.from_user.id != OWNER_ID: return
    db = SessionLocal()
    users = db.query(User).all()
    
    csv_data = StringIO()
    writer = csv.writer(csv_data)
    writer.writerow(['ID', 'Name', 'Role', 'Total DLs', 'Join Date'])
    for u in users: writer.writerow([u.id, u.name, u.role, u.total_downloads, u.join_date.strftime("%Y-%m-%d")])
    
    csv_data.seek(0)
    bot.send_document(message.chat.id, ('aura_users.csv', csv_data.getvalue()), caption="📊 **Database Export**", parse_mode="Markdown")
    db.close()

@bot.message_handler(commands=['msg'])
def direct_msg_cmd(message):
    if message.from_user.id != OWNER_ID: return
    try:
        parts = message.text.split(' ', 2)
        target_id = int(parts[1])
        text = parts[2]
        bot.send_message(target_id, f"📩 **Message from Admin:**\n\n{text}", parse_mode="Markdown")
        bot.reply_to(message, "✅ Message sent.")
    except: bot.reply_to(message, "Use: `/msg [ID] [Text]`")

@bot.message_handler(commands=['sendad'])
def send_ad_cmd(message):
    if message.from_user.id != OWNER_ID: return
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
                bot.send_message(u.id, f"📢 **Sponsor/Promo:**\n\n{text}", reply_markup=markup, parse_mode="Markdown")
                success += 1
                time.sleep(0.05)
            except: pass
        db.close()
        bot.reply_to(message, f"✅ Ad sent to {success} users.")
    except: bot.reply_to(message, "Use: `/sendad Ad Text | Button Text | Button URL`")

# 🎁 GENCODE SYSTEM
@bot.message_handler(func=lambda m: m.text and m.text.startswith('/gencode'))
def generate_code_cmd(message):
    if message.from_user.id != OWNER_ID: return
    
    parts = message.text.split()
    cmd = parts[0].lower()
    
    num_str = cmd.replace('/gencode', '')
    count = int(num_str) if num_str.isdigit() else 1
    
    if len(parts) != 3:
        return bot.reply_to(message, "⚠️ **Vul Format!**\nUse: `/gencode[count] [role] [hours]`\nExample: `/gencode10 silver 24`", parse_mode="Markdown")
    
    try:
        role = parts[1].lower()
        if role not in ['silver', 'gold', 'diamond']: raise ValueError
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
            bot.reply_to(message, f"🎁 **{count} Codes Generated!**\n\n👑 Role: `{role.capitalize()}`\n⏳ Valid To Redeem: `{hours} Hours`\n\n{codes_str}", parse_mode="Markdown")
        else:
            file_name = f"AURA_Codes_{role.upper()}_{count}.txt"
            with open(file_name, "w") as f: f.write("\n".join(generated_codes))
            with open(file_name, "rb") as f:
                bot.send_document(message.chat.id, f, caption=f"🎁 **{count} Codes Generated!**\n👑 Role: `{role.capitalize()}`\n⏳ Valid To Redeem: `{hours} Hours`", parse_mode="Markdown")
            os.remove(file_name)
            
    except Exception as e:
        bot.reply_to(message, f"❌ Error: Role ba format vul. Example: `/gencode100 diamond 48`", parse_mode="Markdown")

# 💳 REDEEM SYSTEM
@bot.message_handler(commands=['redeem'])
def redeem_cmd(message):
    if MAINTENANCE and message.from_user.id != OWNER_ID: return
    parts = message.text.split()
    if len(parts) < 2: return bot.reply_to(message, "Use: `/redeem AURA-CODE`")
    
    db = SessionLocal()
    user = get_user(db, message.from_user.id, message.from_user.first_name)
    
    if user.is_banned:
        db.close()
        return
    
    if user.last_code_used and user.last_code_used.date() == datetime.now().date():
        db.close()
        return bot.reply_to(message, "❌ Apni ajke already ekta redeem code use korechen. Kal abar try korun.")

    code_in = parts[1].strip()
    c = db.query(RedeemCode).filter(RedeemCode.code == code_in).first()
    
    if not c: bot.reply_to(message, "❌ Invalid Code. Code ti sothik noy.")
    elif c.is_used: bot.reply_to(message, "❌ Ei code ti already onno kew use kore feleche.")
    elif c.expires_at and datetime.now() > c.expires_at: bot.reply_to(message, "❌ Ei code ti expire hoye geche!")
    else:
        user.role = c.role_granted
        user.role_expires_at = datetime.now() + timedelta(days=1)
        user.last_code_used = datetime.now()
        user.daily_downloads = 0
        c.is_used = True
        db.commit()
        bot.reply_to(message, f"✅ **Success!**\nApni ekhon **{c.role_granted.capitalize()}** plan e achen 24 ghontar jonno.")
    db.close()

@bot.message_handler(commands=['broadcast'])
def broadcast_cmd(message):
    if message.from_user.id != OWNER_ID: return
    msg_text = message.text.replace('/broadcast', '').strip()
    if not msg_text: return bot.reply_to(message, "⚠️ Eivabe likhun: `/broadcast Hello!`", parse_mode="Markdown")
    
    db = SessionLocal()
    users = db.query(User).all()
    bot.reply_to(message, f"📢 Broadcasting to {len(users)} users...")
    
    success = 0
    for u in users:
        try:
            bot.send_message(u.id, f"📢 **AURA Update:**\n\n{msg_text}", parse_mode="Markdown")
            success += 1
            time.sleep(0.05)
        except: pass
    db.close()
    bot.reply_to(message, f"✅ Broadcast Complete! Sent to {success} users.")

# --- ANTI-SPAM & LINK PROCESSOR ---
@bot.message_handler(regexp=r'http[s]?://(?:[a-zA-Z]|[0-9]|[$-_@.&+]|[!*\(\),]|(?:%[0-9a-fA-F][0-9a-fA-F]))+')
def handle_link(message):
    user_id = message.from_user.id
    if MAINTENANCE and user_id != OWNER_ID: return
    if not check_force_sub(user_id): return bot.reply_to(message, "⚠️ Join channel first.")

    now = time.time()
    if user_id in user_cooldowns and (now - user_cooldowns[user_id]) < 3:
        return bot.reply_to(message, "🐢 **Too fast!** Please wait 3 seconds between links.", parse_mode="Markdown")
    user_cooldowns[user_id] = now

    db = SessionLocal()
    user = get_user(db, user_id, message.from_user.first_name)
    db.close()
    if user.is_banned: return

    if user.role != 'owner' and user.daily_downloads >= LIMITS[user.role]:
        limit_msg = (
            f"❌ **Apnar ajker Download Limit shesh! ({LIMITS[user.role]}/{LIMITS[user.role]})**\n\n"
            f"💎 **GET SUBSCRIPTIONS:**\n"
            f"🥈 Silver (20 DL) - **{PRICING['silver']}**\n"
            f"🥇 Gold (50 DL) - **{PRICING['gold']}**\n"
            f"💎 Diamond (100 DL) - **{PRICING['diamond']}**\n\n"
            f"Nicher **'💎 Get Subscriptions'** button a click kore payment korun, naki kalke abar try korun!"
        )
        return bot.reply_to(message, limit_msg, parse_mode="Markdown")

    bot.send_chat_action(message.chat.id, 'typing')
    url = clean_url(message.text.strip())
    msg_id = message.message_id
    url_storage[msg_id] = url
    
    # Platform Detector
    platform = "Video"
    if "youtube.com" in url or "youtu.be" in url: platform = "YouTube"
    elif "facebook.com" in url or "fb.watch" in url: platform = "Facebook"
    elif "tiktok.com" in url: platform = "TikTok"
    elif "instagram.com" in url: platform = "Instagram"

    bot.reply_to(message, f"🔗 **{platform} Link Analyzed!**\nChoose format & quality:", reply_markup=get_inline_menu(msg_id), parse_mode="Markdown")

@bot.callback_query_handler(func=lambda call: call.data == 'cancel')
def cancel_action(call):
    bot.delete_message(call.message.chat.id, call.message.message_id)

@bot.callback_query_handler(func=lambda call: call.data.startswith('dl|'))
def process_dl(call):
    parts = call.data.split('|')
    dl_type = parts[1]
    msg_id = int(parts[2])
    url = url_storage.get(msg_id)
    
    if not url: return bot.answer_callback_query(call.id, "❌ Link expired!", show_alert=True)

    db = SessionLocal()
    user = get_user(db, call.from_user.id, call.from_user.first_name)
    
    if user.role != 'owner' and user.daily_downloads >= LIMITS[user.role]:
        db.close()
        return bot.answer_callback_query(call.id, "❌ Limit Exceeded! Get subscriptions.", show_alert=True)

    msg = bot.edit_message_text("⏳ Extracting video data...", call.message.chat.id, call.message.message_id)

    # Pre-deduct limit (will refund if failed)
    if user.role != 'owner': 
        user.daily_downloads += 1
    user.total_downloads += 1
    db.commit()

    ydl_opts = {
        'outtmpl': f'downloads/%(id)s_{user.id}.%(ext)s',
        'max_filesize': 50 * 1024 * 1024,
        'quiet': True, 'noplaylist': True,
        'http_headers': {'User-Agent': 'Mozilla/5.0'}
    }
    
    if dl_type == '1080': ydl_opts['format'] = 'bestvideo[height<=1080]+bestaudio/best'
    elif dl_type == '720': ydl_opts['format'] = 'bestvideo[height<=720]+bestaudio/best'
    elif dl_type == 'aud': ydl_opts['format'] = 'bestaudio[ext=m4a]/bestaudio/best'
    elif dl_type == 'thumb': ydl_opts['skip_download'] = True; ydl_opts['writethumbnail'] = True

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            bot.delete_message(call.message.chat.id, msg.message_id)

            if dl_type == 'thumb':
                thumb_url = info.get('thumbnail')
                if thumb_url:
                    bot.send_photo(call.message.chat.id, thumb_url, caption="🖼 **AURA Thumbnail**", parse_mode="Markdown")
                return

            downloaded_files = glob.glob(f'downloads/{info["id"]}_{user.id}.*')
            if not downloaded_files: raise Exception("File save hoyni")
            path = downloaded_files[0]

            if os.path.exists(path):
                if os.path.getsize(path) / (1024 * 1024) > 49.5:
                    raise Exception("Video is larger than 50MB Telegram limit!")

                bot.send_chat_action(call.message.chat.id, 'upload_video' if dl_type in ['1080', '720'] else 'upload_document')
                with open(path, 'rb') as file:
                    if dl_type == 'aud': bot.send_audio(call.message.chat.id, file, title=info.get('title', 'AURA Audio'), caption="⚡ **AURA**")
                    else: bot.send_video(call.message.chat.id, file, caption="⚡ **AURA Downloader**")
                os.remove(path)
                
    except Exception as e:
        logger.error(f"DL Error: {e}")
        # Auto-Refund Logic
        if user.role != 'owner' and user.daily_downloads > 0:
            user.daily_downloads -= 1
        user.total_downloads -= 1
        db.commit()
        
        bot.send_message(call.message.chat.id, "❌ Download failed or file too large. Apnar limit refund deya hoyeche!")
    finally:
        db.close()
        if msg_id in url_storage: del url_storage[msg_id]

if __name__ == "__main__":
    if not os.path.exists("downloads"): os.makedirs("downloads")
    logger.info("AURA Enterprise Bot Started!")
    bot.infinity_polling()
