import os
import secrets
import yt_dlp
import logging
import string
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
from database import SessionLocal, User, RedeemCode
from apscheduler.schedulers.background import BackgroundScheduler

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --- CONFIG (NO API_ID REQUIRED) ---
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "6198703244"))

if not BOT_TOKEN:
    logger.error("BOT_TOKEN is missing! Railway Variables e check korun.")
    exit()

bot = telebot.TeleBot(BOT_TOKEN)

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
        logger.info("Daily credits reset successfully.")
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
        bot.reply_to(message, f"👋 **Hello!**\n\nCredit: `{user.credits}`\nJust link pathan download korar jonno.", parse_mode="Markdown")

@bot.message_handler(regexp=r'http[s]?://(?:[a-zA-Z]|[0-9]|[$-_@.&+]|[!*\(\),]|(?:%[0-9a-fA-F][0-9a-fA-F]))+')
def link_handler(message):
    url = message.text
    btn = InlineKeyboardMarkup()
    btn.add(InlineKeyboardButton("Download Video (10 Credits)", callback_data=f"dl|{url}"))
    bot.reply_to(message, "⚡ Select action:", reply_markup=btn)

@bot.callback_query_handler(func=lambda call: call.data.startswith('dl|'))
def download_logic(call):
    url = call.data.split('|', 1)[1]
    user_id = call.from_user.id
    
    db = SessionLocal()
    user = db.query(User).filter(User.id == user_id).first()
    
    if not user or user.credits < 10:
        bot.answer_callback_query(call.id, "Credits shesh! Kal abar reset hobe.", show_alert=True)
        db.close()
        return

    user.credits -= 10
    db.commit()
    db.close()

    bot.edit_message_text("📥 Downloading... Wait koro.", chat_id=call.message.chat.id, message_id=call.message.message_id)

    ydl_opts = {
        'format': 'best',
        'outtmpl': f'downloads/%(title)s_{user_id}.%(ext)s',
        'quiet': True,
        'noplaylist': True
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            path = ydl.prepare_filename(info)
            
            with open(path, 'rb') as video:
                bot.send_video(call.message.chat.id, video, caption=f"✅ Enjoy! Credits: {user.credits}")
            
            if os.path.exists(path): 
                os.remove(path)
    except Exception as e:
        bot.send_message(call.message.chat.id, f"❌ Error: Video download kora jacche na.")
        logger.error(f"Download Error: {e}")

# --- ADMIN SECTION ---
@bot.message_handler(commands=['gencode'])
def gencode(message):
    if message.from_user.id != ADMIN_ID: return
    parts = message.text.split()
    if len(parts) < 2: return
    
    try:
        val = int(parts[1])
        code = f"AURA-{''.join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(12))}"
        
        db = SessionLocal()
        db.add(RedeemCode(code=code, value=val))
        db.commit()
        db.close()
        bot.reply_to(message, f"🎁 Code: `{code}`\nValue: {val}", parse_mode="Markdown")
    except ValueError:
        bot.reply_to(message, "Format: `/gencode 100`")

@bot.message_handler(commands=['redeem'])
def redeem(message):
    parts = message.text.split()
    if len(parts) < 2: return
    
    code_in = parts[1].strip()
    db = SessionLocal()
    c = db.query(RedeemCode).filter(RedeemCode.code == code_in, RedeemCode.is_used == False).first()
    
    if c:
        u = db.query(User).filter(User.id == message.from_user.id).first()
        u.credits += c.value
        c.is_used = True
        db.commit()
        bot.reply_to(message, f"✅ Success! {c.value} Credits Added.")
    else:
        bot.reply_to(message, "❌ Invalid or Expired Code.")
    db.close()

if __name__ == "__main__":
    if not os.path.exists("downloads"): 
        os.makedirs("downloads")
    logger.info("Bot is starting without API ID/Hash...")
    bot.infinity_polling()
