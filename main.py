import os
import asyncio
from flask import Flask
from pyrogram import Client, filters
from pytgcalls import PyTgCalls

from pytgcalls.types.input_stream import AudioPiped






import yt_dlp

# Flask app for UptimeRobot keep-alive
app = Flask(__name__)

@app.route('/')
def index():
    return "🌐 Flask keep-alive server started."

def run_flask():
    app.run(host="0.0.0.0", port=8080)

# Telegram API credentials (apni details yahan daal lena agarenv mein na ho)
API_ID = int(os.getenv("API_ID", "YOUR_API_ID"))
API_HASH = os.getenv("API_HASH", "YOUR_API_HASH")
BOT_TOKEN = os.getenv("BOT_TOKEN", "YOUR_BOT_TOKEN")

# Cookies ko temporary file mein likhne ka setup taaki yt-dlp use kar sake
cookies_content = os.getenv("YOUTUBE_COOKIES")
cookies_file_path = "cookies.txt"

if cookies_content:
    with open(cookies_file_path, "w", encoding="utf-8") as f:
        f.write(cookies_content)

# Pyrogram Bot Client
bot = Client(
    "music_bot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN
)

# PyTgCalls Group Call Client
call_py = PyTgCalls(bot)

@bot.on_message(filters.command("play") & filters.group)
async def play_music(client, message):
    if len(message.command) < 2:
        await message.reply("❌ Please provide a song name! Usage: `/play [song name]`")
        return

    query = " ".join(message.command[1:])
    m = await message.reply(f"🔎 Searching for **{query}**...")

    # yt-dlp options with cookies support
    ydl_opts = {
        'format': 'bestaudio/best',
        'noplaylist': True,
        'cookiefile': cookies_file_path if cookies_content else None,
        'quiet': True,
    }

    search_query = f"ytsearch:{query}"

    try:
        loop = asyncio.get_running_loop()
        data = await loop.run_in_executor(
            None, lambda: yt_dlp.YoutubeDL(ydl_opts).extract_info(search_query, download=False)
        )
        
        if 'entries' in data and data['entries']:
            song_info = data['entries'][0]
        else:
            song_info = data

        audio_url = song_info['url']
        title = song_info.get('title', 'Unknown Title')
        
        # Join voice chat and stream audio
        await call_py.join_group_call(
            message.chat.id,
            AudioPiped(audio_url)
        )
        await m.edit(f"🎵 Now playing: **{title}**")
        
    except Exception as e:
        await m.edit(f"❌ Error: {str(e)}")

async def main():
    # Start Flask in background thread
    import threading
    threading.Thread(target=run_flask, daemon=True).start()
    
    print("🎙️ Starting PyTgCalls (Assistant)...")
    await call_py.start()
    await bot.start()
    print("🤖 Bot is successfully running and online!")
    await asyncio.gather(asyncio.Event().wait())

if __name__ == '__main__':
    asyncio.run(main())
