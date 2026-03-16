import os
import secrets
import yt_dlp
import logging
import string
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
from database import SessionLocal, User, RedeemCode
from apscheduler.schedulers.background import BackgroundScheduler

# Logging Setup
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --- CONFIG ---
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "8037371175"))

if not BOT_TOKEN:
    logger.error("BOT_TOKEN is missing! Railway Variables check korun.")
    exit()

bot = telebot.TeleBot(BOT_TOKEN)

# Telegram 64-byte limit bypass korar jonno temporary storage
url_storage = {}

# --- DATABASE LOGIC ---
def get_user(user_id):
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.id == user_id).first()
        if not user:
            user = User(id=user_id, credits=100)
            db.add(user)
            db.commit()
            db.refresh(user)
        return user
    finally:
        db.close()

def reset_daily_credits():
    db = SessionLocal()
    try:
        db.query(User).update({User.credits: 100})
        db.commit()
        logger.info("Daily credits reset complete.")
    finally:
        db.close()

scheduler = BackgroundScheduler()
scheduler.add_job(reset_daily_credits, 'cron', hour=0, minute=0)
scheduler.start()

# --- HANDLERS ---
@bot.message_handler(commands=['start'])
def start_cmd(message):
    user = get_user(message.from_user.id)
    if user:
        bot.reply_to(message, f"👋 **Hello Ononto!**\n\nCredit: `{user.credits}`\nJust link pathan download korar jonno.", parse_mode="Markdown")

# Ekhane regex er bodole simple HTTP check kora hoyeche jate kono link miss na jay
@bot.message_handler(func=lambda m: m.text and ('http://' in m.text or 'https://' in m.text))
def link_handler(message):
    url = message.text.strip()
    msg_id = message.message_id
    
    # Link ta storage e save korlam
    url_storage[msg_id] = url
    
    btn = InlineKeyboardMarkup()
    # Button e sudhu message ID pathacchi jate 64 byte cross na kore
    btn.add(InlineKeyboardButton("Download Video (10 Credits)", callback_data=f"dl|{msg_id}"))
    bot.reply_to(message, "⚡ Select action:", reply_markup=btn)

@bot.callback_query_handler(func=lambda call: call.data.startswith('dl|'))
def download_logic(call):
    msg_id = int(call.data.split('|')[1])
    url = url_storage.get(msg_id)
    
    if not url:
        bot.answer_callback_query(call.id, "❌ Link expired! Please link ta abar send korun.", show_alert=True)
        return

    user_id = call.from_user.id
    chat_id = call.message.chat.id
    
    db = SessionLocal()
    user = db.query(User).filter(User.id == user_id).first()
    
    if not user or user.credits < 10:
        bot.answer_callback_query(call.id, "Credits shesh! Kal abar reset hobe.", show_alert=True)
        db.close()
        return

    bot.edit_message_text("📥 Downloading... please wait.", chat_id=chat_id, message_id=call.message.message_id)

    # YT, FB, TikTok - Universal Settings
    ydl_opts = {
        'format': 'best',
        'outtmpl': f'downloads/%(id)s_{user_id}.%(ext)s',
        'max_filesize': 50 * 1024 * 1024, # 50 MB limit
        'socket_timeout': 30,
        'quiet': True,
        'noplaylist': True,
        'no_warnings': True,
        'http_headers': {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        }
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            path = ydl.prepare_filename(info)
            
            # Extension check
            if not os.path.exists(path):
                path = path.rsplit('.', 1)[0] + ".mp4"

            if os.path.exists(path):
                file_size_mb = os.path.getsize(path) / (1024 * 1024)
                
                if file_size_mb > 49.5:
                    bot.send_message(chat_id, f"❌ Error: Video size ({file_size_mb:.1f} MB) onek boro! Telegram maximum 50MB support kore. \n\nApnar credit kata hoyni.")
                    os.remove(path)
                    db.close()
                    return

                # Download complete hole tobei credit katbe
                user.credits -= 10
                db.commit()

                with open(path, 'rb') as video:
                    bot.send_video(chat_id, video, caption=f"✅ Enjoy! Credits Left: {user.credits}")
                
                os.remove(path)
                
                # Kaaj sheshe storage theke link delete kore dibe jate memory free thake
                if msg_id in url_storage:
                    del url_storage[msg_id]
                
    except Exception as e:
        error_msg = str(e).lower()
        if "max-filesize" in error_msg:
            bot.send_message(chat_id, "❌ Error: Video 50MB er theke boro! Telegram e pathano somvob na. (Credit kata hoyni)")
        else:
            bot.send_message(chat_id, f"❌ Download fail hoyeche. Private video ba invalid link hote pare.")
            logger.error(f"Download Error: {e}")
    finally:
        db.close()

# --- ADMIN SECTION ---
@bot.message_handler(commands=['gencode'])
def generate_code_cmd(message):
    if message.from_user.id != ADMIN_ID: 
        bot.reply_to(message, f"❌ Sorry, apni Admin na! (ID: {message.from_user.id})")
        return
        
    parts = message.text.split()
    if len(parts) < 2: 
        bot.reply_to(message, "⚠️ Vul format! Eivabe likhun: `/gencode 100`", parse_mode="Markdown")
        return
    
    try:
        val = int(parts[1])
        code = f"AURA-{''.join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(12))}"
        
        db = SessionLocal()
        db.add(RedeemCode(code=code, value=val))
        db.commit()
        db.close()
        
        bot.reply_to(message, f"🎁 **Code Generated!**\n\nCode: `{code}`\nValue: {val} Credits", parse_mode="Markdown")
    except ValueError:
        bot.reply_to(message, "⚠️ Amount oboshshoi number hote hobe. Jemon: `/gencode 50`", parse_mode="Markdown")

@bot.message_handler(commands=['redeem'])
def redeem_cmd(message):
    parts = message.text.split()
    if len(parts) < 2: 
        bot.reply_to(message, "⚠️ Eivabe likhun: `/redeem AURA-XXXXX`", parse_mode="Markdown")
        return
    
    code_in = parts[1].strip()
    db = SessionLocal()
    c = db.query(RedeemCode).filter(RedeemCode.code == code_in, RedeemCode.is_used == False).first()
    
    if c:
        u = db.query(User).filter(User.id == message.from_user.id).first()
        u.credits += c.value
        c.is_used = True
        db.commit()
        bot.reply_to(message, f"✅ Success! Apnar account-e {c.value} Credits add hoyeche.")
    else:
        bot.reply_to(message, "❌ Invalid ba Expired Code.")
    db.close()

if __name__ == "__main__":
    if not os.path.exists("downloads"): 
        os.makedirs("downloads")
    logger.info("Bot started successfully!")
    bot.infinity_polling()
