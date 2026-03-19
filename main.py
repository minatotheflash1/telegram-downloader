import os
import telebot
from yt_dlp import YoutubeDL

# আপনার টোকেন এবং এডমিন আইডি
API_TOKEN = 'YOUR_BOT_TOKEN'
ADMIN_ID = 8037371175 # আপনার নতুন আপডেট করা এডমিন আইডি
bot = telebot.TeleBot(API_TOKEN)

def download_video(url, user_id):
    # ভিডিও সেভ করার জন্য ডিরেক্টরি চেক
    if not os.path.exists('downloads'):
        os.makedirs('downloads')

    ydl_opts = {
        'outtmpl': f'downloads/%(id)s_{user_id}.%(ext)s',
        'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
        'merge_output_format': 'mp4',
        'noplaylist': True,
        'quiet': True,
        'http_headers': {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }
    }

    with YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        file_path = ydl.prepare_filename(info)
        return file_path

@bot.message_handler(func=lambda message: True)
def handle_message(message):
    url = message.text
    if "youtube.com" in url or "facebook.com" in url or "tiktok.com" in url or "instagram.com" in url:
        msg = bot.reply_to(message, "Downloading... Please wait.")
        try:
            file_path = download_video(url, message.from_user.id)
            
            # ফাইলের সাইজ চেক (Railway/Public API এর জন্য ৫০এমবি লিমিট)
            file_size = os.path.getsize(file_path) / (1024 * 1024)
            
            if file_size > 50:
                bot.edit_message_text("File is larger than 50MB. Telegram Public API limitation.", message.chat.id, msg.message_id)
            else:
                with open(file_path, 'rb') as video:
                    bot.send_video(message.chat.id, video, caption="Downloaded by Aura Bot")
            
            # ডাউনলোড শেষ হলে ফাইল ডিলিট করে দেওয়া (সার্ভার স্পেস বাঁচাতে)
            os.remove(file_path)
            
        except Exception as e:
            bot.edit_message_text(f"Error: {str(e)}", message.chat.id, msg.message_id)

bot.infinity_polling()
