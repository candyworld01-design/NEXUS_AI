import os
import asyncio
import threading
import pkgutil
from flask import Flask
from pyrogram import Client, filters
from pytgcalls import PyTgCalls
import pytgcalls
import yt_dlp

# --- FOOLPROOF DYNAMIC IMPORT AUTO-DISCOVERER ---
AudioPiped = None
target_names = ['AudioPiped', 'InputStream', 'AudioStream', 'AudioVideoPiped']

candidate_modules = [
    'pytgcalls.types.input.audio_piped',
    'pytgcalls.types.input',
    'pytgcalls.types.stream',
    'pytgcalls.types.input_stream',
    'pytgcalls.types',
    'pytgcalls'
]

for mod_name in candidate_modules:
    try:
        mod = __import__(mod_name, fromlist=target_names)
        for t in target_names:
            if hasattr(mod, t):
                AudioPiped = getattr(mod, t)
                print(f"✅ Loaded {t} from {mod_name}")
                break
        if AudioPiped:
            break
    except Exception:
        pass

if not AudioPiped:
    try:
        import pytgcalls.types
        for _, name, _ in pkgutil.walk_packages(pytgcalls.types.__path__, 'pytgcalls.types.'):
            try:
                m = __import__(name, fromlist=target_names)
                for t in target_names:
                    if hasattr(m, t):
                        AudioPiped = getattr(m, t)
                        print(f"✅ Loaded {t} via walk from {name}")
                        break
                if AudioPiped:
                    break
            except Exception:
                pass
    except Exception:
        pass

if not AudioPiped:
    raise ImportError("Could not automatically locate AudioPiped in installed pytgcalls version.")

# Flask app for UptimeRobot keep-alive
app = Flask(__name__)

@app.route('/')
def index():
    return "🌐 Flask keep-alive server started."

def run_flask():
    app.run(host="0.0.0.0", port=8080)

API_ID = int(os.getenv("API_ID", "0"))
API_HASH = os.getenv("API_HASH", "")
BOT_TOKEN = os.getenv("BOT_TOKEN", "")

# Cookies setup for yt-dlp
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
        
        audio_stream = AudioPiped(audio_url)
        
        # Version-agnostic call method handler
        try:
            if hasattr(call_py, 'play'):
                await call_py.play(message.chat.id, audio_stream)
            else:
                await call_py.join_group_call(message.chat.id, audio_stream)
        except Exception:
            await call_py.join_group_call(message.chat.id, audio_stream)

        await m.edit(f"🎵 Now playing: **{title}**")
        
    except Exception as e:
        await m.edit(f"❌ Error: {str(e)}")

async def main():
    threading.Thread(target=run_flask, daemon=True).start()
    
    print("🤖 Starting Telegram Bot...")
    await bot.start()
    
    print("🎙️ Starting PyTgCalls...")
    await call_py.start()
    
    print("🤖 Bot is successfully running and online!")
    await asyncio.gather(asyncio.Event().wait())

if __name__ == '__main__':
    asyncio.run(main())
