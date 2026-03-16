import os
import secrets
import yt_dlp
import logging
import string
from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from sqlalchemy.orm import Session
from database import SessionLocal, User, RedeemCode
from apscheduler.schedulers.background import BackgroundScheduler

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Config
API_ID = int(os.getenv("API_ID", "0"))
API_HASH = os.getenv("API_HASH", "")
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))

app = Client("video_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# --- Helpers ---
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
        [InlineKeyboardButton("High Quality (10 Credits)", callback_data=f"dl|best|{url}")],
        [InlineKeyboardButton("Normal Quality (10 Credits)", callback_data=f"dl|worst|{url}")]
    ]
    await message.reply("Select Quality:", reply_markup=InlineKeyboardMarkup(buttons))

@app.on_callback_query(filters.regex(r'^dl\|'))
async def process_download(client, callback):
    _, quality_pref, url = callback.data.split('|')
    user_id = callback.from_user.id
    
    db = SessionLocal()
    user = db.query(User).filter(User.id == user_id).first()
    
    if user.credits < 10:
        await callback.answer("Credit nai!", show_alert=True)
        db.close()
        return

    user.credits -= 10
    db.commit()
    db.close()

    await callback.edit_message_text("⚡ Processing... please wait.")

    # Format Fix: Specific quality na peye jeno crash na kore
    # 'best' ba 'worst' use kora Facebook/TikTok-er jonno beshi safe
    ydl_opts = {
        'format': 'bestvideo+bestaudio/best', # Best available format nibe
        'outtmpl': f'downloads/%(title)s_{user_id}.%(ext)s',
        'quiet': True,
        'merge_output_format': 'mp4'
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            file_path = ydl.prepare_filename(info)
            
            # Extension change hote pare tai real file path check
            if not os.path.exists(file_path):
                file_path = file_path.rsplit('.', 1)[0] + ".mp4"

            await callback.message.reply_video(
                video=file_path, 
                caption=f"✅ Done!\nRemaining: {user.credits}"
            )
            if os.path.exists(file_path):
                os.remove(file_path)
                
    except Exception as e:
        await callback.message.reply(f"❌ Error: {str(e)}")

# --- Admin: Generate Code (AURA-XXXXXXXXXXXXXXX) ---
@app.on_message(filters.command("gencode") & filters.user(ADMIN_ID))
async def generate_aura_code(client, message):
    if len(message.command) < 2:
        return await message.reply("Format: `/gencode 100`")
    
    try:
        val = int(message.command[1])
        # AURA- format-e 15 ta random character
        random_str = ''.join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(15))
        aura_code = f"AURA-{random_str}"
        
        db = SessionLocal()
        new_code = RedeemCode(code=aura_code, value=val)
        db.add(new_code)
        db.commit()
        db.close()
        
        await message.reply(f"🎁 Generated Code:\n`{aura_code}`\nValue: {val} Credits")
    except Exception as e:
        await message.reply(f"Error: {str(e)}")

@app.on_message(filters.command("redeem"))
async def redeem_now(client, message):
    if len(message.command) < 2:
        return await message.reply("Use: `/redeem AURA-CODE`")
    
    input_code = message.command[1].strip()
    db = SessionLocal()
    code_data = db.query(RedeemCode).filter(RedeemCode.code == input_code, RedeemCode.is_used == False).first()
    
    if code_data:
        user = db.query(User).filter(User.id == message.from_user.id).first()
        user.credits += code_data.value
        code_data.is_used = True
        db.commit()
        await message.reply(f"✅ Success! {code_data.value} credits added.")
    else:
        await message.reply("❌ Invalid or Expired Code.")
    db.close()

if __name__ == "__main__":
    if not os.path.exists("downloads"):
        os.makedirs("downloads")
    app.run()
