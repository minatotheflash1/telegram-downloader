import os
import secrets
import yt_dlp
from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from sqlalchemy.orm import Session
from database import SessionLocal, User, RedeemCode
from apscheduler.schedulers.background import BackgroundScheduler

# Environment Variables (Railway Dashboard e set korben)
API_ID = int(os.getenv("API_ID"))
API_HASH = os.getenv("API_HASH")
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID"))

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
    print("Credits reset to 100 for all users.")

# Daily Credit Reset Scheduler (Rat 12-tay)
scheduler = BackgroundScheduler()
scheduler.add_job(reset_daily_credits, 'cron', hour=0, minute=0)
scheduler.start()

# --- Handlers ---
@app.on_message(filters.command("start"))
async def start_cmd(client, message):
    user = get_user(message.from_user.id)
    await message.reply(
        f"👋 Hello!\n\n"
        f"Apnar Current Credit: **{user.credits}**\n"
        f"Protidin 100 credit free paben.\n"
        f"Just video link pathan download korar jonno."
    )

@app.on_message(filters.regex(r'http|https'))
async def link_handler(client, message):
    url = message.text
    buttons = [
        [InlineKeyboardButton("720p (10 Credits)", callback_data=f"dl|720|{url}")],
        [InlineKeyboardButton("360p (10 Credits)", callback_data=f"dl|360|{url}")]
    ]
    await message.reply("Select Video Quality:", reply_markup=InlineKeyboardMarkup(buttons))

@app.on_callback_query(filters.regex(r'^dl\|'))
async def process_download(client, callback):
    _, quality, url = callback.data.split('|')
    user_id = callback.from_user.id
    
    db = SessionLocal()
    user = db.query(User).filter(User.id == user_id).first()
    
    if user.credits < 10:
        await callback.answer("Sorry! Apnar credit shesh. Kal abar reset hobe.", show_alert=True)
        db.close()
        return

    user.credits -= 10
    db.commit()
    db.close()

    await callback.edit_message_text("⚡ Downloading... Please wait.")

    # yt-dlp Options
    ydl_opts = {
        'format': f'bestvideo[height<={quality}]+bestaudio/best[height<={quality}]',
        'outtmpl': f'downloads/%(title)s_{user_id}.%(ext)s',
        'quiet': True
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            file_path = ydl.prepare_filename(info)
            
            await callback.message.reply_video(
                video=file_path, 
                caption=f"✅ Downloaded Successfully!\nRemaining Credits: {user.credits}"
            )
            if os.path.exists(file_path):
                os.remove(file_path)
                
    except Exception as e:
        await callback.message.reply(f"❌ Error: {str(e)}")

# --- Admin Section ---
@app.on_message(filters.command("gen") & filters.user(ADMIN_ID))
async def generate_redeem(client, message):
    try:
        val = int(message.command[1])
        code = secrets.token_hex(4).upper()
        db = SessionLocal()
        new_code = RedeemCode(code=code, value=val)
        db.add(new_code)
        db.commit()
        db.close()
        await message.reply(f"🎁 New Code: `{code}`\nValue: {val} Credits")
    except:
        await message.reply("Format: `/gen 50`")

@app.on_message(filters.command("redeem"))
async def redeem_now(client, message):
    if len(message.command) < 2:
        return await message.reply("Use: `/redeem CODE`")
    
    input_code = message.command[1].upper()
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