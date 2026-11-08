import os
import asyncio
import threading
import aiohttp
from flask import Flask
from pyrogram import Client, filters, idle
from pytgcalls import PyTgCalls, filters as tg_filters
from pytgcalls.types import StreamEnded

# Environment variables
API_ID = int(os.getenv("API_ID", "0"))
API_HASH = os.getenv("API_HASH", "")
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
STRING_SESSION = os.getenv("STRING_SESSION", "")

# Flask keep-alive server for Render (Port binding mandatory)
app = Flask(__name__)

@app.route('/')
def index():
    return "Nexus Music Bot is Live!"

def run_flask():
    port = int(os.getenv("PORT", 10000))
    app.run(host="0.0.0.0", port=port)

# Inline Song Queue Manager
class SongQueue:
    def __init__(self):
        self.queue = []

    async def add(self, item):
        self.queue.append(item)

    async def get_next(self):
        if self.queue:
            return self.queue.pop(0)
        return None

    async def get_size(self):
        return len(self.queue)

    async def clear(self):
        self.queue.clear()

queue = SongQueue()

bot_client = Client(
    "NexusBot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN
)

assistant_client = Client(
    "NexusAssistant",
    api_id=API_ID,
    api_hash=API_HASH,
    session_string=STRING_SESSION
)

call_py = PyTgCalls(assistant_client)

class MusicPlayer:
    def __init__(self):
        self.is_playing = False
        self.current_chat_id = None
        self.generation_id = 0
        self.lock = asyncio.Lock()

    async def search_and_get_stream(self, query: str):
        async with aiohttp.ClientSession() as session:
            url = f"https://saavn.dev/api/search/songs?query={query}"
            async with session.get(url) as resp:
                if resp.status != 200:
                    raise Exception("Failed to fetch song from JioSaavn")
                data = await resp.json()
                results = data.get("data", {}).get("results", [])
                if not results:
                    raise Exception("Song not found on JioSaavn!")
                
                song = results[0]
                title = song.get("name", "Unknown Title")
                download_url = None
                for d in song.get("downloadUrl", []):
                    if d.get("quality") == "320kbps":
                        download_url = d.get("url")
                if not download_url and song.get("downloadUrl"):
                    download_url = song.get("downloadUrl")[-1].get("url")
                
                if not download_url:
                    raise Exception("Audio stream link not available!")
                
                return download_url, title

    async def add_and_play(self, chat_id: int, stream_url: str, title: str):
        async with self.lock:
            if not self.is_playing:
                self.is_playing = True
                self.current_chat_id = chat_id
                try:
                    await call_py.play(chat_id, stream_url)
                    return True, f"🎶 Playing from JioSaavn: **{title}**"
                except Exception as e:
                    self.is_playing = False
                    self.current_chat_id = None
                    raise e
            else:
                await queue.add((stream_url, title))
                q_size = await queue.get_size()
                return False, f"➕ Added to queue (Position {q_size}): **{title}**"

    async def play_next(self, chat_id: int, expected_gen: int = None):
        async with self.lock:
            if expected_gen is not None and expected_gen != self.generation_id:
                return None

            song = await queue.get_next()
            if song:
                stream_url, title = song
                self.is_playing = True
                self.current_chat_id = chat_id
                try:
                    await call_py.play(chat_id, stream_url)
                    return title
                except Exception:
                    return await self.play_next(chat_id, expected_gen)
            else:
                self.is_playing = False
                self.current_chat_id = None
                try:
                    await call_py.leave_call(chat_id)
                except Exception:
                    pass
                return None

player = MusicPlayer()

@bot_client.on_message(filters.command("play") & filters.group)
async def play_command(client, message):
    if len(message.command) < 2:
        return await message.reply("❌ Gaane ka naam likh bhai! Example: `/play falak tak`")
    
    query = " ".join(message.command[1:])
    m = await message.reply("🔍 Searching on JioSaavn...")
    
    try:
        stream_url, title = await player.search_and_get_stream(query)
        _, msg = await player.add_and_play(message.chat.id, stream_url, title)
        await m.edit(msg)
    except Exception as e:
        await m.edit(f"❌ Error: `{e}`")

@call_py.on_update(tg_filters.stream_end())
async def on_stream_end_handler(client, update: StreamEnded):
    chat_id = update.chat_id
    async with player.lock:
        if not player.is_playing or player.current_chat_id != chat_id:
            return
        current_gen = player.generation_id
    
    await player.play_next(chat_id, expected_gen=current_gen)

async def main():
    # Start Flask web server in background thread so Render keeps the service alive
    threading.Thread(target=run_flask, daemon=True).start()
    print("🌐 Flask keep-alive server started.")

    await call_py.start()
    print("🎙️ PyTgCalls started.")
    
    await bot_client.start()
    print("🤖 Telegram Bot started successfully!")
    
    await idle()

if __name__ == "__main__":
    asyncio.get_event_loop().run_until_complete(main())
