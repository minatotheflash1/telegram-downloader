import os
import secrets
import string
import time
import logging
import glob
from datetime import datetime
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
ADMIN_ID = int(os.getenv("ADMIN_ID", "8651895707"))
DATABASE_URL = os.getenv("DATABASE_URL")

if DATABASE_URL and DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

bot = telebot.TeleBot(BOT_TOKEN)
url_storage = {}
cooldown = {}
MAINTENANCE = False # Global maintenance flag

# --- DATABASE SETUP ---
Base = declarative_base()
engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(bind=engine)

class User(Base):
    __tablename__ = "users"
    id = Column(BigInteger, primary_key=True)
    credits = Column(Integer, default=100)
    total_downloads = Column(Integer, default=0)
    is_banned = Column(Boolean, default=False)
    is_vip = Column(Boolean, default=False)
    last_daily_claim = Column(DateTime, nullable=True)

class RedeemCode(Base):
    __tablename__ = "redeem_codes"
    id = Column(Integer, primary_key=True)
    code = Column(String, unique=True)
    value = Column(Integer)
    is_used = Column(Boolean, default=False)

Base.metadata.create_all(engine)

def get_user(db, user_id):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        user = User(id=user_id, credits=100)
        db.add(user)
        db.commit()
        db.refresh(user)
    return user

# --- SCHEDULERS ---
def reset_daily_credits():
    db = SessionLocal()
    try:
        db.query(User).filter(User.is_vip == False).update({User.credits: 100})
        db.commit()
    finally:
        db.close()

def clean_storage():
    files = glob.glob('downloads/*')
    for f in files:
        if os.path.isfile(f): os.remove(f)

scheduler = BackgroundScheduler()
scheduler.add_job(reset_daily_credits, 'cron', hour=0, minute=0)
scheduler.add_job(clean_storage, 'interval', hours=12) 
scheduler.start()

# --- UI MENUS ---
def get_bottom_keyboard():
    markup = ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    markup.add(
        KeyboardButton("👤 Profile"), KeyboardButton("🎁 Daily Bonus"),
        KeyboardButton("🏆 Leaderboard"), KeyboardButton("ℹ️ Help & Rules")
    )
    return markup

def get_inline_menu(msg_id):
    markup = InlineKeyboardMarkup()
    markup.row(InlineKeyboardButton("🎬 Video (10 Cr)", callback_data=f"dl|vid|{msg_id}"),
               InlineKeyboardButton("🎵 Audio (5 Cr)", callback_data=f"dl|aud|{msg_id}"))
    markup.row(InlineKeyboardButton("❌ Cancel", callback_data="cancel"))
    return markup

def loading_animation(chat_id, msg_id, text):
    stages = [
        "[■□□□□□□□□□] 10%",
        "[■■■□□□□□□□] 30%",
        "[■■■■■□□□□□] 50%",
        "[■■■■■■■□□□] 70%",
        "[■■■■■■■■■□] 90%",
        "[■■■■■■■■■■] 100% Extracting..."
    ]
    for stage in stages:
        try:
            bot.edit_message_text(f"⚡ {text}\n\n{stage}", chat_id, msg_id)
            time.sleep(0.5)
        except: pass

# --- USER COMMANDS ---
@bot.message_handler(commands=['start'])
def start_cmd(message):
    if MAINTENANCE and message.from_user.id != ADMIN_ID:
        return bot.reply_to(message, "🛠 **Bot under maintenance.**\nServer update cholche, doya kore ektu por try korun.", parse_mode="Markdown")

    db = SessionLocal()
    user = get_user(db, message.from_user.id)
    db.close()
    
    img_url = "https://images.unsplash.com/photo-1611162617474-5b21e879e113?q=80&w=800&auto=format&fit=crop"
    text = f"🚀 **Welcome to AURA Premium V2** 🚀\n\nDrop any video link to start downloading instantly.\n\n💰 Credits: `{user.credits}`"
    bot.send_photo(message.chat.id, img_url, caption=text, reply_markup=get_bottom_keyboard(), parse_mode="Markdown")

@bot.message_handler(commands=['feedback'])
def feedback_cmd(message):
    if MAINTENANCE and message.from_user.id != ADMIN_ID:
        return bot.reply_to(message, "🛠 **Bot under maintenance.**", parse_mode="Markdown")

    msg_text = message.text.replace('/feedback', '').strip()
    if not msg_text:
        return bot.reply_to(message, "⚠️ Eivabe likhun: `/feedback Amar ei problem hochche...`", parse_mode="Markdown")
    
    feedback_msg = f"📩 **New Feedback!**\n\n👤 **User ID:** `{message.from_user.id}`\n🗣 **Name:** {message.from_user.first_name}\n\n📝 **Message:** {msg_text}"
    try:
        bot.send_message(ADMIN_ID, feedback_msg, parse_mode="Markdown")
        bot.reply_to(message, "✅ Apnar feedback successfully Admin er kache pathano hoyeche! Dhonnobad.")
    except Exception as e:
        bot.reply_to(message, "❌ Admin ke message pathate somoshsha hochche.")

@bot.message_handler(func=lambda m: m.text in ["👤 Profile", "🎁 Daily Bonus", "🏆 Leaderboard", "ℹ️ Help & Rules"])
def bottom_menu_handler(message):
    if MAINTENANCE and message.from_user.id != ADMIN_ID:
        return bot.reply_to(message, "🛠 **Bot under maintenance.**", parse_mode="Markdown")

    db = SessionLocal()
    user = get_user(db, message.from_user.id)
    
    if message.text == "👤 Profile":
        status = "VIP 🌟" if user.is_vip else "Regular 👤"
        bot.reply_to(message, f"👤 **AURA Profile**\n\n🆔 ID: `{user.id}`\n👑 Status: {status}\n💰 Credits: `{user.credits}`\n📥 Downloaded: `{user.total_downloads}`", parse_mode="Markdown")
        
    elif message.text == "🎁 Daily Bonus":
        now = datetime.now()
        if user.last_daily_claim and (now - user.last_daily_claim).days < 1:
            bot.reply_to(message, "⚠️ Ajker bonus niye neya hoyeche! Kal abar ashben.")
        else:
            user.credits += 20
            user.last_daily_claim = now
            db.commit()
            bot.reply_to(message, f"🎉 **+20 Credits Added!**\nNew Balance: `{user.credits}`", parse_mode="Markdown")
            
    elif message.text == "🏆 Leaderboard":
        top = db.query(User).order_by(User.total_downloads.desc()).limit(5).all()
        text = "🏆 **Top AURA Users**\n\n"
        for i, u in enumerate(top): text += f"{i+1}. `{u.id}` - 📥 {u.total_downloads}\n"
        bot.reply_to(message, text, parse_mode="Markdown")
        
    elif message.text == "ℹ️ Help & Rules":
        text = "🛠 **Commands & Rules:**\n- `/transfer ID AMOUNT` - Send credits.\n- `/redeem CODE` - Add credits.\n- `/feedback MESSAGE` - Send msg to Admin.\n- Max 50MB per video."
        bot.reply_to(message, text, parse_mode="Markdown")
    db.close()

@bot.message_handler(commands=['transfer'])
def transfer_credits(message):
    if MAINTENANCE and message.from_user.id != ADMIN_ID: return
    parts = message.text.split()
    if len(parts) != 3: return bot.reply_to(message, "Use: `/transfer 12345678 50`", parse_mode="Markdown")
    
    try:
        target_id = int(parts[1])
        amount = int(parts[2])
        if amount <= 0: raise ValueError
        
        db = SessionLocal()
        sender = get_user(db, message.from_user.id)
        if sender.credits < amount:
            db.close()
            return bot.reply_to(message, "❌ Insufficient credits.")
            
        receiver = db.query(User).filter(User.id == target_id).first()
        if not receiver:
            db.close()
            return bot.reply_to(message, "❌ Target user bot e register koreni.")
            
        sender.credits -= amount
        receiver.credits += amount
        db.commit()
        db.close()
        bot.reply_to(message, f"✅ Successfully transferred {amount} credits to {target_id}!")
        bot.send_message(target_id, f"🎁 You received {amount} credits from `{message.from_user.id}`!", parse_mode="Markdown")
    except:
        bot.reply_to(message, "❌ Invalid format.")

# --- ADMIN COMMANDS ---
@bot.message_handler(commands=['maintenance', 'offmaintenance'])
def toggle_maintenance(message):
    if message.from_user.id != ADMIN_ID: return
    global MAINTENANCE
    
    cmd = message.text.split()[0].replace('/', '')
    if cmd == 'maintenance':
        MAINTENANCE = True
        bot.reply_to(message, "🛠 **Maintenance Mode is ON.**\nUser-ra ekhon bot bebohar korte parbe na.", parse_mode="Markdown")
    else:
        MAINTENANCE = False
        bot.reply_to(message, "✅ **Maintenance Mode is OFF.**\nBot is live for everyone.", parse_mode="Markdown")

@bot.message_handler(commands=['broadcast'])
def broadcast_cmd(message):
    if message.from_user.id != ADMIN_ID: return
    msg_text = message.text.replace('/broadcast', '').strip()
    
    if not msg_text:
        return bot.reply_to(message, "⚠️ Eivabe likhun: `/broadcast Hello everyone!`", parse_mode="Markdown")
    
    db = SessionLocal()
    users = db.query(User).all()
    bot.reply_to(message, f"📢 Broadcasting message to {len(users)} users. Please wait...")
    
    success_count = 0
    for u in users:
        try:
            bot.send_message(u.id, f"📢 **AURA Update:**\n\n{msg_text}", parse_mode="Markdown")
            success_count += 1
            time.sleep(0.05)
        except: pass
    db.close()
    bot.reply_to(message, f"✅ Broadcast Complete! Sent to {success_count} active users.")

@bot.message_handler(commands=['sysinfo'])
def sys_info(message):
    if message.from_user.id != ADMIN_ID: return
    cpu = psutil.cpu_percent()
    ram = psutil.virtual_memory().percent
    disk = psutil.disk_usage('/').percent
    text = f"🖥 **Server Status:**\n\n⚙️ CPU Usage: `{cpu}%`\n💽 RAM Usage: `{ram}%`\n💾 Disk Usage: `{disk}%`"
    bot.reply_to(message, text, parse_mode="Markdown")

@bot.message_handler(commands=['gencode', 'stats', 'vip', 'ban'])
def admin_commands(message):
    if message.from_user.id != ADMIN_ID: return
    
    cmd = message.text.split()[0].replace('/', '')
    parts = message.text.split()
    db = SessionLocal()
    
    try:
        if cmd == 'gencode' and len(parts) == 2:
            val = int(parts[1])
            code = f"AURA-{''.join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(12))}"
            db.add(RedeemCode(code=code, value=val))
            db.commit()
            bot.reply_to(message, f"🎁 **Code:** `{code}`\nValue: {val}", parse_mode="Markdown")
            
        elif cmd == 'stats':
            users = db.query(User).count()
            dls = sum([u.total_downloads for u in db.query(User).all()])
            m_status = "ON 🛠" if MAINTENANCE else "OFF ✅"
            bot.reply_to(message, f"📊 **Stats:**\nUsers: {users}\nTotal Downloads: {dls}\nMaintenance: {m_status}")
            
        elif cmd == 'vip' and len(parts) == 2:
            u = get_user(db, int(parts[1]))
            u.is_vip = not u.is_vip
            db.commit()
            bot.reply_to(message, f"✅ User {parts[1]} VIP: {u.is_vip}")
            
        elif cmd == 'ban' and len(parts) == 2:
            u = get_user(db, int(parts[1]))
            u.is_banned = not u.is_banned
            db.commit()
            status = "Banned" if u.is_banned else "Unbanned"
            bot.reply_to(message, f"✅ User {parts[1]} is now {status}.")
            
    except Exception as e:
        bot.reply_to(message, f"❌ Error: {e}")
    finally:
        db.close()

@bot.message_handler(commands=['redeem'])
def redeem_cmd(message):
    if MAINTENANCE and message.from_user.id != ADMIN_ID: return
    parts = message.text.split()
    if len(parts) < 2: return bot.reply_to(message, "Use: `/redeem AURA-CODE`")
    
    db = SessionLocal()
    code_in = parts[1].strip()
    c = db.query(RedeemCode).filter(RedeemCode.code == code_in, RedeemCode.is_used == False).first()
    if c:
        u = get_user(db, message.from_user.id)
        u.credits += c.value
        c.is_used = True
        db.commit()
        bot.reply_to(message, f"✅ Success! {c.value} Credits Added.")
    else:
        bot.reply_to(message, "❌ Invalid or Expired Code.")
    db.close()

# --- LINK PROCESSOR ---
@bot.message_handler(regexp=r'http[s]?://(?:[a-zA-Z]|[0-9]|[$-_@.&+]|[!*\(\),]|(?:%[0-9a-fA-F][0-9a-fA-F]))+')
def handle_link(message):
    if MAINTENANCE and message.from_user.id != ADMIN_ID:
        return bot.reply_to(message, "🛠 **Bot under maintenance.**\nServer update cholche, doya kore ektu por try korun.", parse_mode="Markdown")

    url = message.text.strip()
    msg_id = message.message_id
    url_storage[msg_id] = url
    bot.reply_to(message, "🔗 **Link Analyzed!**\nChoose format:", reply_markup=get_inline_menu(msg_id), parse_mode="Markdown")

@bot.message_handler(func=lambda m: True)
def unknown_text(message):
    if not message.text.startswith('/'):
        bot.reply_to(message, "🤔 Eita to kono video link na bhai. Doya kore valid link din!")

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
    user = get_user(db, call.from_user.id)
    cost = 10 if dl_type == 'vid' else 5
    
    if not user.is_vip and user.credits < cost:
        db.close()
        return bot.answer_callback_query(call.id, "❌ Insufficient Credits!", show_alert=True)

    msg = bot.edit_message_text("⏳ Processing request...", call.message.chat.id, call.message.message_id)
    loading_animation(call.message.chat.id, msg.message_id, "Fetching data...")

    ydl_opts = {
        'outtmpl': f'downloads/%(id)s_{user.id}.%(ext)s',
        'max_filesize': 50 * 1024 * 1024,
        'quiet': True, 'noplaylist': True,
        'http_headers': {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
    }
    
    # 🎵 Direct Audio Fix (FFmpeg chara m4a download)
    if dl_type == 'vid': 
        ydl_opts['format'] = 'best'
    else: 
        ydl_opts['format'] = 'bestaudio[ext=m4a]/bestaudio/best'

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            
            # File location dynamic bhabe khuje ber kora
            downloaded_files = glob.glob(f'downloads/{info["id"]}_{user.id}.*')
            if not downloaded_files:
                raise Exception("File save hoyni")
                
            path = downloaded_files[0]

            if os.path.exists(path):
                if os.path.getsize(path) / (1024 * 1024) > 49.5:
                    bot.edit_message_text("❌ Video 50MB er theke boro!", call.message.chat.id, msg.message_id)
                    os.remove(path)
                    return

                if not user.is_vip: user.credits -= cost
                user.total_downloads += 1
                db.commit()

                bot.edit_message_text("🚀 Uploading to Telegram...", call.message.chat.id, msg.message_id)
                with open(path, 'rb') as file:
                    if dl_type == 'aud': 
                        bot.send_audio(call.message.chat.id, file, title=info.get('title', 'AURA Audio'), caption="⚡ **AURA Downloader**", parse_mode="Markdown")
                    else: 
                        bot.send_video(call.message.chat.id, file, caption="⚡ **AURA Downloader**", parse_mode="Markdown")
                
                os.remove(path)
                bot.delete_message(call.message.chat.id, msg.message_id)
                
    except Exception as e:
        logger.error(f"DL Error: {e}")
        bot.edit_message_text("❌ Download failed. Link invalid ba private.", call.message.chat.id, msg.message_id)
    finally:
        db.close()
        if msg_id in url_storage: del url_storage[msg_id]

if __name__ == "__main__":
    if not os.path.exists("downloads"): os.makedirs("downloads")
    logger.info("AURA Premium Bot Started!")
    bot.infinity_polling()
