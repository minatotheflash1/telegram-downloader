import os
import secrets
import string
import time
import logging
import glob
from datetime import datetime
import yt_dlp
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
from sqlalchemy import create_engine, Column, Integer, String, Boolean, BigInteger, DateTime
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from apscheduler.schedulers.background import BackgroundScheduler

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --- CONFIGURATIONS ---
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "8037371175"))

DATABASE_URL = os.getenv("DATABASE_URL")
if DATABASE_URL and DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

bot = telebot.TeleBot(BOT_TOKEN)
url_storage = {}
cooldown = {}

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

# --- MAIN MENU UI ---
def get_main_menu():
    markup = InlineKeyboardMarkup()
    markup.row(
        InlineKeyboardButton("👤 My Profile", callback_data="menu_profile"),
        InlineKeyboardButton("🎁 Daily Bonus", callback_data="menu_daily")
    )
    markup.row(
        InlineKeyboardButton("🏆 Leaderboard", callback_data="menu_leaderboard"),
        InlineKeyboardButton("ℹ️ Help", callback_data="menu_help")
    )
    return markup

@bot.message_handler(commands=['start'])
def start_cmd(message):
    db = SessionLocal()
    user = get_user(db, message.from_user.id)
    
    # Referral System
    parts = message.text.split()
    if len(parts) > 1 and parts[1].startswith('ref_'):
        ref_id = int(parts[1].split('_')[1])
        if ref_id != user.id:
            referrer = db.query(User).filter(User.id == ref_id).first()
            if referrer:
                referrer.credits += 50
                bot.send_message(ref_id, "🎉 Kew apnar invite link diye join koreche! +50 Credits!")
                db.commit()
    db.close()

    text = (
        f"⚡ **Welcome to AURA Downloader** ⚡\n\n"
        f"Apni jekono YouTube, Facebook, TikTok ba Instagram video-r link ekhane send korte paren.\n\n"
        f"👇 Nicher menu theke apnar account manage korun:"
    )
    bot.reply_to(message, text, reply_markup=get_main_menu(), parse_mode="Markdown")

# --- MENU CALLBACKS ---
@bot.callback_query_handler(func=lambda call: call.data.startswith('menu_'))
def menu_callbacks(call):
    action = call.data.split('_')[1]
    db = SessionLocal()
    user = get_user(db, call.from_user.id)
    
    if action == "profile":
        status = "VIP 🌟" if user.is_vip else "Regular 👤"
        text = f"👤 **AURA Profile**\n\n🆔 ID: `{user.id}`\n👑 Status: {status}\n💰 Credits: `{user.credits}`\n📥 Total Downloaded: `{user.total_downloads}`\n\n🔗 Invite link: `https://t.me/{bot.get_me().username}?start=ref_{user.id}`"
        bot.edit_message_text(text, call.message.chat.id, call.message.message_id, reply_markup=get_main_menu(), parse_mode="Markdown")
        
    elif action == "daily":
        now = datetime.now()
        if user.last_daily_claim and (now - user.last_daily_claim).days < 1:
            bot.answer_callback_query(call.id, "⚠️ Apni ajker daily bonus niye niyechen! Kal try korun.", show_alert=True)
        else:
            user.credits += 20
            user.last_daily_claim = now
            db.commit()
            bot.answer_callback_query(call.id, "🎉 +20 Credits Added Successfully!", show_alert=True)
            bot.edit_message_text(f"🎁 **Daily Bonus Claimed!**\nNew Balance: `{user.credits}`", call.message.chat.id, call.message.message_id, reply_markup=get_main_menu(), parse_mode="Markdown")
            
    elif action == "leaderboard":
        top_users = db.query(User).order_by(User.total_downloads.desc()).limit(5).all()
        text = "🏆 **Top 5 Downloaders**\n\n"
        for i, u in enumerate(top_users):
            text += f"{i+1}. ID: `{u.id}` - 📥 {u.total_downloads} vids\n"
        bot.edit_message_text(text, call.message.chat.id, call.message.message_id, reply_markup=get_main_menu(), parse_mode="Markdown")
        
    elif action == "help":
        text = "🛠 **AURA Help Center**\n\n- Ekta video download korle `10 credit` katbe.\n- Audio download korle `5 credit` katbe.\n- Protidin auto `100 credit` paben.\n- Video max `50 MB` hote hobe.\n- Credit nite `/redeem CODE` use korun."
        bot.edit_message_text(text, call.message.chat.id, call.message.message_id, reply_markup=get_main_menu(), parse_mode="Markdown")
        
    db.close()

@bot.callback_query_handler(func=lambda call: call.data == 'cancel')
def cancel_action(call):
    bot.delete_message(call.message.chat.id, call.message.message_id)

# --- DOWNLOAD LOGIC ---
@bot.message_handler(regexp=r'http[s]?://(?:[a-zA-Z]|[0-9]|[$-_@.&+]|[!*\(\),]|(?:%[0-9a-fA-F][0-9a-fA-F]))+')
def link_handler(message):
    db = SessionLocal()
    user = get_user(db, message.from_user.id)
    if user.is_banned:
        db.close()
        return bot.reply_to(message, "❌ You are banned from AURA Downloader.")
    db.close()

    url = message.text.strip()
    msg_id = message.message_id
    url_storage[msg_id] = url
    
    btn = InlineKeyboardMarkup()
    btn.add(InlineKeyboardButton("🎬 Download Video (10 Cr)", callback_data=f"dl|vid|{msg_id}"))
    btn.add(InlineKeyboardButton("🎵 Download MP3 (5 Cr)", callback_data=f"dl|aud|{msg_id}"))
    btn.add(InlineKeyboardButton("❌ Cancel", callback_data="cancel"))
    
    bot.reply_to(message, "⚡ **Link Detected!** Ki format e chan?", reply_markup=btn, parse_mode="Markdown")

@bot.callback_query_handler(func=lambda call: call.data.startswith('dl|'))
def process_download(call):
    parts = call.data.split('|')
    dl_type = parts[1]
    msg_id = int(parts[2])
    url = url_storage.get(msg_id)
    
    if not url: return bot.answer_callback_query(call.id, "❌ Link expired! Abar send korun.", show_alert=True)

    user_id = call.from_user.id
    chat_id = call.message.chat.id
    cost = 10 if dl_type == 'vid' else 5
    
    db = SessionLocal()
    user = get_user(db, user_id)
    
    if not user.is_vip and user.credits < cost:
        bot.answer_callback_query(call.id, "❌ Credits shesh!", show_alert=True)
        db.close()
        return

    bot.edit_message_text("📥 Extracting data... please wait.", chat_id=chat_id, message_id=call.message.message_id)

    ydl_opts = {
        'outtmpl': f'downloads/%(id)s_{user_id}.%(ext)s',
        'max_filesize': 50 * 1024 * 1024,
        'quiet': True,
        'noplaylist': True,
        'http_headers': {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
    }
    
    if dl_type == 'vid':
        ydl_opts['format'] = 'best'
    else:
        ydl_opts['format'] = 'bestaudio/best'
        ydl_opts['postprocessors'] = [{'key': 'FFmpegExtractAudio', 'preferredcodec': 'mp3', 'preferredquality': '192'}]

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            path = ydl.prepare_filename(info)
            if dl_type == 'aud': path = path.rsplit('.', 1)[0] + ".mp3"
            elif not os.path.exists(path): path = path.rsplit('.', 1)[0] + ".mp4"

            if os.path.exists(path):
                if os.path.getsize(path) / (1024 * 1024) > 49.5:
                    bot.send_message(chat_id, "❌ Error: Video 50MB er theke boro!")
                    os.remove(path)
                    return

                if not user.is_vip: user.credits -= cost
                user.total_downloads += 1
                db.commit()

                bot.edit_message_text("🚀 Uploading to Telegram...", chat_id=chat_id, message_id=call.message.message_id)
                caption = f"✅ Downloaded Successfully!\n⚡ Bot by **AURA MINATO**" 
                
                with open(path, 'rb') as file:
                    if dl_type == 'aud': bot.send_audio(chat_id, file, caption=caption, parse_mode="Markdown")
                    else: bot.send_video(chat_id, file, caption=caption, parse_mode="Markdown")
                
                os.remove(path)
                bot.delete_message(chat_id, call.message.message_id)
                
    except Exception as e:
        bot.edit_message_text("❌ Download fail hoyeche. Server block ba link invalid.", chat_id=chat_id, message_id=call.message.message_id)
    finally:
        db.close()
        if msg_id in url_storage: del url_storage[msg_id]

# --- ADMIN COMMANDS ---
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
            bot.reply_to(message, f"📊 **Stats:**\nUsers: {users}\nTotal Downloads: {dls}")
            
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

if __name__ == "__main__":
    if not os.path.exists("downloads"): os.makedirs("downloads")
    logger.info("AURA Bot Started!")
    bot.infinity_polling()
