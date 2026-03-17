import os
import secrets
import string
import time
import logging
import glob
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

if DATABASE_URL and DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

bot = telebot.TeleBot(BOT_TOKEN)
url_storage = {}
MAINTENANCE = False 

# --- SUBSCRIPTION LIMITS & PRICING ---
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
    total_downloads = Column(Integer, default=0)
    is_banned = Column(Boolean, default=False)
    referred_by = Column(BigInteger, nullable=True)
    referral_count = Column(Integer, default=0)

class RedeemCode(Base):
    __tablename__ = "redeem_codes"
    id = Column(Integer, primary_key=True)
    code = Column(String, unique=True)
    role_granted = Column(String) 
    expires_at = Column(DateTime, nullable=True) 
    is_used = Column(Boolean, default=False)

# ⚠️ Deploy korar por ei nicher line ta delete ba comment kore diben
Base.metadata.drop_all(engine)
Base.metadata.create_all(engine)

def get_user(db, user_id, user_name="User", referrer_id=None):
    user = db.query(User).filter(User.id == user_id).first()
    is_new = False
    
    if not user:
        is_new = True
        role = 'owner' if user_id == OWNER_ID else 'free'
        user = User(id=user_id, name=user_name, role=role, referred_by=referrer_id)
        db.add(user)
        db.commit()
        db.refresh(user)
        
        # Referral Bonus (Referrer gets +2 limit today)
        if referrer_id:
            referrer = db.query(User).filter(User.id == referrer_id).first()
            if referrer:
                referrer.referral_count += 1
                referrer.daily_downloads = max(0, referrer.daily_downloads - 2) # Free up 2 limits
                db.commit()
                try:
                    bot.send_message(referrer.id, f"🎉 Kew apnar invite link diye join koreche! Apni ajker jonno +2 extra download limit peyechen.")
                except: pass
                
        # New User Notification to Admin
        try:
            bot.send_message(OWNER_ID, f"🔔 **New User Joined!**\nName: {user_name}\nID: `{user_id}`\nTotal Users: {db.query(User).count()}", parse_mode="Markdown")
        except: pass

    # Expiry Check
    if user.role not in ['free', 'owner'] and user.role_expires_at:
        if datetime.now() > user.role_expires_at:
            user.role = 'free'
            user.role_expires_at = None
            db.commit()
            try: bot.send_message(user.id, "⚠️ Apnar Premium plan er meyad shesh hoye geche. Apni ekhon Free plan e achen.")
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

# --- UI MENUS ---
def get_bottom_keyboard():
    markup = ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    markup.add(
        KeyboardButton("👤 Profile"), KeyboardButton("💎 Upgrade Plan"),
        KeyboardButton("🏆 Leaderboard"), KeyboardButton("🎁 Invite & Earn")
    )
    return markup

def get_inline_menu(msg_id):
    markup = InlineKeyboardMarkup()
    markup.row(
        InlineKeyboardButton("🎬 1080p", callback_data=f"dl|1080|{msg_id}"),
        InlineKeyboardButton("🎬 720p", callback_data=f"dl|720|{msg_id}")
    )
    markup.row(
        InlineKeyboardButton("🎵 Audio (m4a)", callback_data=f"dl|aud|{msg_id}"),
        InlineKeyboardButton("🖼 Thumb", callback_data=f"dl|thumb|{msg_id}")
    )
    markup.row(InlineKeyboardButton("❌ Cancel", callback_data="cancel"))
    return markup

def get_admin_menu():
    markup = InlineKeyboardMarkup()
    markup.row(
        InlineKeyboardButton("📊 System Stats", callback_data="admin_stats"),
        InlineKeyboardButton("🛠 Maint. Mode", callback_data="admin_maint")
    )
    return markup

# --- USER HANDLERS ---
@bot.message_handler(commands=['start'])
def start_cmd(message):
    if MAINTENANCE and message.from_user.id != OWNER_ID:
        return bot.reply_to(message, "🛠 **Bot under maintenance.**", parse_mode="Markdown")

    parts = message.text.split()
    referrer_id = None
    if len(parts) > 1 and parts[1].startswith('ref_'):
        try: referrer_id = int(parts[1].split('_')[1])
        except: pass

    db = SessionLocal()
    user = get_user(db, message.from_user.id, message.from_user.first_name, referrer_id)
    
    if user.is_banned:
        db.close()
        return bot.reply_to(message, "❌ You are banned from using this bot.")
        
    db.close()
    
    img_url = "https://images.unsplash.com/photo-1614680376593-902f74cf0d41?q=80&w=800&auto=format&fit=crop"
    text = f"🚀 **Hello {message.from_user.first_name}, Welcome to AURA Downloader!**\n\nDrop any video link to start downloading instantly.\n\n👑 **Your Role:** `{user.role.capitalize()}`\n📥 **Today's Usage:** `{user.daily_downloads} / {LIMITS[user.role]}`"
    bot.send_photo(message.chat.id, img_url, caption=text, reply_markup=get_bottom_keyboard(), parse_mode="Markdown")

@bot.message_handler(func=lambda m: m.text in ["👤 Profile", "💎 Upgrade Plan", "🏆 Leaderboard", "🎁 Invite & Earn"])
def bottom_menu_handler(message):
    if MAINTENANCE and message.from_user.id != OWNER_ID: return
    db = SessionLocal()
    user = get_user(db, message.from_user.id, message.from_user.first_name)
    
    if user.is_banned:
        db.close()
        return
    
    if message.text == "👤 Profile":
        expiry = user.role_expires_at.strftime("%Y-%m-%d %H:%M") if user.role_expires_at else "Lifetime"
        text = f"👤 **AURA Profile**\n\n🆔 ID: `{user.id}`\n👤 Name: {user.name}\n👑 Role: `{user.role.capitalize()}`\n⏳ Expiry: `{expiry}`\n\n📊 **Today's Downloads:** `{user.daily_downloads} / {LIMITS[user.role]}`\n📥 **Total Downloaded:** `{user.total_downloads}`\n👥 **Total Invites:** `{user.referral_count}`"
        bot.reply_to(message, text, parse_mode="Markdown")
        
    elif message.text == "💎 Upgrade Plan":
        text = "💎 **GET SUBSCRIPTIONS (AURA PREMIUM)** 💎\n\n"
        text += f"🥈 **Silver:** 20 Downloads/Day ➡️ **{PRICING['silver']}**\n"
        text += f"🥇 **Gold:** 50 Downloads/Day ➡️ **{PRICING['gold']}**\n"
        text += f"💎 **Diamond:** 100 Downloads/Day ➡️ **{PRICING['diamond']}**\n\n"
        text += "💳 **Payment Methods:**\n"
        text += "Bkash / Nagad: `01846849460` (Send Money)\n\n"
        text += "⚠️ Payment korar por nicher button e click kore screenshot ar TrxID din."
        
        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton("✅ Verify Payment", callback_data="verify_payment"))
        bot.reply_to(message, text, reply_markup=markup, parse_mode="Markdown")
            
    elif message.text == "🏆 Leaderboard":
        top = db.query(User).order_by(User.total_downloads.desc()).limit(5).all()
        text = "🏆 **Top AURA Users**\n\n"
        for i, u in enumerate(top): text += f"{i+1}. {u.name} - 📥 {u.total_downloads}\n"
        bot.reply_to(message, text, parse_mode="Markdown")
        
    elif message.text == "🎁 Invite & Earn":
        bot_username = bot.get_me().username
        ref_link = f"https://t.me/{bot_username}?start=ref_{user.id}"
        text = f"🎁 **Invite & Earn!**\n\nApnar invite link diye kew join korlei apni paben **+2 Extra Download Limit** ajker jonno!\n\n🔗 **Your Invite Link:**\n`{ref_link}`\n\nTotal Invited: `{user.referral_count}`"
        bot.reply_to(message, text, parse_mode="Markdown")
    db.close()

# --- PAYMENT VERIFICATION SYSTEM ---
@bot.callback_query_handler(func=lambda call: call.data == 'verify_payment')
def verify_payment_start(call):
    msg = bot.send_message(call.message.chat.id, "📸 Doya kore apnar Payment er **Screenshot** ti ekhane send korun.")
    bot.register_next_step_handler(msg, process_payment_ss)

def process_payment_ss(message):
    if not message.photo:
        bot.reply_to(message, "❌ Eita screenshot noy. Doya kore abar 'Upgrade Plan' theke 'Verify Payment' e click kore thikvabe chobi din.")
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

# --- MEGA ADMIN COMMANDS (NEW!) ---
@bot.message_handler(commands=['ping'])
def ping_cmd(message):
    start_time = time.time()
    msg = bot.reply_to(message, "Pinging...")
    end_time = time.time()
    bot.edit_message_text(f"🏓 **Pong!**\nLatency: `{round((end_time - start_time) * 1000)}ms`\nServer: Online ✅", msg.chat.id, msg.message_id, parse_mode="Markdown")

@bot.message_handler(commands=['admin'])
def admin_panel(message):
    if message.from_user.id != OWNER_ID: return
    bot.reply_to(message, "👑 **AURA Admin Control Panel**", reply_markup=get_admin_menu(), parse_mode="Markdown")

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
        bot.edit_message_text(text, call.message.chat.id, call.message.message_id, reply_markup=get_admin_menu(), parse_mode="Markdown")
        
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
        if u:
            bot.reply_to(message, f"🔍 **User Details:**\nName: {u.name}\nID: `{u.id}`\nRole: {u.role}\nTotal DLs: {u.total_downloads}\nBanned: {u.is_banned}", parse_mode="Markdown")
        else:
            bot.reply_to(message, "❌ User not found.")
    except:
        bot.reply_to(message, "Use: `/search [ID]`", parse_mode="Markdown")

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
        else:
            bot.reply_to(message, "❌ User not found.")
        db.close()
    except:
        bot.reply_to(message, "Use: `/ban [ID]` or `/unban [ID]`")

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
        else:
            bot.reply_to(message, "❌ User not found.")
        db.close()
    except:
        bot.reply_to(message, "Use: `/setrole [ID] [role]` (free/silver/gold/diamond)", parse_mode="Markdown")

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
            bot.reply_to(message, f"✅ Added {amount} extra downloads to {user_id} for today.")
            bot.send_message(user_id, f"🎁 Admin has given you {amount} extra downloads for today!")
        else:
            bot.reply_to(message, "❌ User not found.")
        db.close()
    except:
        bot.reply_to(message, "Use: `/addlimit [ID] [amount]`", parse_mode="Markdown")

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
            with open(file_name, "w") as f:
                f.write("\n".join(generated_codes))
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
    
    if not c:
        bot.reply_to(message, "❌ Invalid Code. Code ti sothik noy.")
    elif c.is_used:
        bot.reply_to(message, "❌ Ei code ti already onno kew use kore feleche.")
    elif c.expires_at and datetime.now() > c.expires_at:
        bot.reply_to(message, "❌ Ei code ti expire hoye geche!")
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

# --- LINK PROCESSOR ---
@bot.message_handler(regexp=r'http[s]?://(?:[a-zA-Z]|[0-9]|[$-_@.&+]|[!*\(\),]|(?:%[0-9a-fA-F][0-9a-fA-F]))+')
def handle_link(message):
    if MAINTENANCE and message.from_user.id != OWNER_ID: return
    
    db = SessionLocal()
    user = get_user(db, message.from_user.id, message.from_user.first_name)
    if user.is_banned:
        db.close()
        return bot.reply_to(message, "❌ You are banned from using this bot.")
    db.close()

    if user.daily_downloads >= LIMITS[user.role]:
        limit_msg = (
            f"❌ **Apnar ajker Download Limit shesh! ({LIMITS[user.role]}/{LIMITS[user.role]})**\n\n"
            f"💎 **GET SUBSCRIPTIONS:**\n"
            f"🥈 Silver (20 DL) - **{PRICING['silver']}**\n"
            f"🥇 Gold (50 DL) - **{PRICING['gold']}**\n"
            f"💎 Diamond (100 DL) - **{PRICING['diamond']}**\n\n"
            f"Nicher **'💎 Upgrade Plan'** button a click kore payment korun, naki kalke abar try korun!"
        )
        return bot.reply_to(message, limit_msg, parse_mode="Markdown")

    url = message.text.strip()
    msg_id = message.message_id
    url_storage[msg_id] = url
    bot.reply_to(message, "🔗 **Link Analyzed!**\nChoose format & quality:", reply_markup=get_inline_menu(msg_id), parse_mode="Markdown")

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
    
    if user.daily_downloads >= LIMITS[user.role]:
        db.close()
        return bot.answer_callback_query(call.id, "❌ Limit Exceeded! Upgrade your plan.", show_alert=True)

    msg = bot.edit_message_text("⏳ Processing request...", call.message.chat.id, call.message.message_id)

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
                    user.daily_downloads += 1
                    user.total_downloads += 1
                    db.commit()
                return

            downloaded_files = glob.glob(f'downloads/{info["id"]}_{user.id}.*')
            if not downloaded_files: raise Exception("File save hoyni")
            path = downloaded_files[0]

            if os.path.exists(path):
                if os.path.getsize(path) / (1024 * 1024) > 49.5:
                    bot.send_message(call.message.chat.id, "❌ Video 50MB er theke boro!")
                    os.remove(path)
                    return

                user.daily_downloads += 1
                user.total_downloads += 1
                db.commit()

                bot.send_chat_action(call.message.chat.id, 'upload_video' if dl_type in ['1080', '720'] else 'upload_document')
                with open(path, 'rb') as file:
                    if dl_type == 'aud': 
                        bot.send_audio(call.message.chat.id, file, title=info.get('title', 'AURA Audio'), caption="⚡ **AURA Downloader**", parse_mode="Markdown")
                    else: 
                        bot.send_video(call.message.chat.id, file, caption="⚡ **AURA Downloader**", parse_mode="Markdown")
                os.remove(path)
                
    except Exception as e:
        logger.error(f"DL Error: {e}")
        bot.send_message(call.message.chat.id, "❌ Download failed. Link invalid ba private.")
    finally:
        db.close()
        if msg_id in url_storage: del url_storage[msg_id]

if __name__ == "__main__":
    if not os.path.exists("downloads"): os.makedirs("downloads")
    logger.info("AURA Ultra-Premium Bot Started!")
    bot.infinity_polling()
