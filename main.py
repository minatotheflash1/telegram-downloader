import os
import secrets
import yt_dlp
import logging
import string
from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from database import SessionLocal, User, RedeemCode
from apscheduler.schedulers.background import BackgroundScheduler

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --- CONFIGURATION ---
# Railway Variable na pele ekhane manually apnar gulo likhun (Safe Side)
API_ID = int(os.getenv("API_ID", "37191396")) # Screenshot-er ID
API_HASH = os.getenv("API_HASH", "b0bd2eb8161cf5907e83f81c46454799") 
BOT_TOKEN = os.getenv("BOT_TOKEN", "8690383670:AAFfrdz1uD2jfrktnn2zSHelU6rzmzIGvnU")
ADMIN_ID = int(os.getenv("ADMIN_ID", "6198703244"))

# Download path setup
DOWNLOAD_DIR = "downloads"
if not os.path.exists(DOWNLOAD_DIR):
    os.makedirs(DOWNLOAD_DIR)

app = Client(
    "video_bot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN,
    in_memory=True # Crash komate session memory te rakhbe
)

# --- Database Helpers ---
def get_user(user_id):
    db = SessionLocal()
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        user = User(id=user_id)
        db.add(user)
        db.commit()
        db.refresh(user)
    db.close()
    return user

def reset_daily_credits():
    db = SessionLocal()
    db.query(User).update({User.credits: 100})
    db.commit()
    db.close()
    logger.info("Daily credits reset complete.")

scheduler = BackgroundScheduler()
scheduler.add_job(reset_daily_credits, 'cron', hour=0, minute=0)
scheduler.start()

# --- Handlers ---
@app.on_message(filters.command("start"))
async def start_cmd(client, message):
    user = get_user(message.from_user.id)
    await message.reply(f"👋 Welcome!\nCredit: **{user.credits}**\nLink pathan download korar jonno.")

@app.on_message(filters.regex(r'http|https'))
async def link_handler(client, message):
    url = message.text
    buttons = [
        [InlineKeyboardButton("Download Video (10 Credits)", callback_data=f"dl|best|{url}")]
    ]
    await message.reply("Click niche select korun:", reply_markup=InlineKeyboardMarkup(buttons))

@app.on_callback_query(filters.regex(r'^dl\|'))
async def process_download(client, callback):
    _, quality_pref, url = callback.data.split('|')
    user_id = callback.from_user.id
    
    db = SessionLocal()
    user = db.query(User).filter(User.id == user_id).first()
    
    if user.credits < 10:
        await callback.answer("Insufficient Credit!", show_alert=True)
        db.close()
        return

    user.credits -= 10
    db.commit()
    db.close()

    await callback.edit_message_text("⚡ Processing... Please wait.")

    ydl_opts = {
        'format': 'best',
        'outtmpl': f'{DOWNLOAD_DIR}/%(title)s_{user_id}.%(ext)s',
        'quiet': True,
        'no_warnings': True,
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            file_path = ydl.prepare_filename(info)
            
            await callback.message.reply_video(
                video=file_path, 
                caption=f"✅ Enjoy! Credits left: {user.credits}"
            )
            if os.path.exists(file_path):
                os.remove(file_path)
                
    except Exception as e:
        logger.error(f"Download Error: {e}")
        await callback.message.reply(f"❌ Error: Video download kora jabena ekhon.")

# --- Admin: /gencode format AURA-XXX ---
@app.on_message(filters.command("gencode") & filters.user(ADMIN_ID))
async def generate_aura_code(client, message):
    try:
        val = int(message.command[1])
        random_str = ''.join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(15))
        aura_code = f"AURA-{random_str}"
        
        db = SessionLocal()
        new_code = RedeemCode(code=aura_code, value=val)
        db.add(new_code)
        db.commit()
        db.close()
        
        await message.reply(f"🎁 Code: `{aura_code}`\nValue: {val}")
    except:
        await message.reply("Format: `/gencode 100`")

@app.on_message(filters.command("redeem"))
async def redeem_now(client, message):
    if len(message.command) < 2: return
    input_code = message.command[1].strip()
    db = SessionLocal()
    code_data = db.query(RedeemCode).filter(RedeemCode.code == input_code, RedeemCode.is_used == False).first()
    
    if code_data:
        user = db.query(User).filter(User.id == message.from_user.id).first()
        user.credits += code_data.value
        code_data.is_used = True
        db.commit()
        await message.reply(f"✅ Added {code_data.value} credits.")
    else:
        await message.reply("❌ Invalid Code.")
    db.close()

if __name__ == "__main__":
    app.run()
