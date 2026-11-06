import os
import asyncio
from pyrogram import Client
from pytgcalls import PyTgCalls, filters
from pytgcalls.types import StreamEnded
from config import API_ID, API_HASH, STRING_SESSION, BOT_TOKEN
from music_queue import queue
import yt_dlp

bot_client = Client(
    "NexusBot",
    api_id=int(API_ID),
    api_hash=API_HASH,
    bot_token=BOT_TOKEN
)

assistant_client = Client(
    "NexusAssistant",
    api_id=int(API_ID),
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

    async def search_and_download(self, query: str):
        ydl_opts = {
            'format': 'bestaudio/best',
            'noplaylist': True,
            'quiet': True,
            'outtmpl': 'downloads/%(id)s.%(ext)s',
        }
        os.makedirs("downloads", exist_ok=True)
        
        loop = asyncio.get_running_loop()
        def _download():
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(query, download=True)
                if 'entries' in info:
                    info = info['entries'][0]
                filename = ydl.prepare_filename(info)
                return filename, info.get('title', 'Unknown Title')

        return await loop.run_in_executor(None, _download)

    async def add_and_play(self, chat_id: int, file_path: str, title: str):
        async with self.lock:
            if not self.is_playing:
                self.is_playing = True
                self.current_chat_id = chat_id
                try:
                    await call_py.play(chat_id, file_path)
                    return True, f"🎶 Starting playback: **{title}**"
                except Exception as e:
                    self.is_playing = False
                    self.current_chat_id = None
                    print(f"Immediate playback error for {title}: {e}")
                    raise e
            else:
                await queue.add((file_path, title))
                q_size = await queue.get_size()
                return False, f"➕ Added to queue (Position {q_size}): **{title}**"

    async def play_next(self, chat_id: int, expected_gen: int = None):
        async with self.lock:
            if expected_gen is not None and expected_gen != self.generation_id:
                return None

            consecutive_failures = 0
            MAX_FAILURES = 3

            while consecutive_failures < MAX_FAILURES:
                song = await queue.get_next()
                if song:
                    file_path, title = song
                    self.is_playing = True
                    self.current_chat_id = chat_id
                    track_gen = self.generation_id
                    try:
                        await call_py.play(chat_id, file_path)
                        return title
                    except Exception as e:
                        print(f"Playback execution error for {title}: {e}")
                        consecutive_failures += 1
                        if track_gen != self.generation_id:
                            break
                        continue
                else:
                    self.is_playing = False
                    self.current_chat_id = None
                    try:
                        await call_py.leave_call(chat_id)
                    except Exception:
                        pass
                    return None

            self.is_playing = False
            self.current_chat_id = None
            try:
                await call_py.leave_call(chat_id)
            except Exception:
                pass
            return None

    async def stop(self, chat_id: int):
        async with self.lock:
            self.generation_id += 1
            await queue.clear()
            self.is_playing = False
            self.current_chat_id = None
            try:
                await call_py.leave_call(chat_id)
            except Exception:
                pass

player = MusicPlayer()

@call_py.on_update(filters.stream_end())
async def on_stream_end_handler(client, update: StreamEnded):
    chat_id = update.chat_id
    async with player.lock:
        if not player.is_playing or player.current_chat_id != chat_id:
            return
        current_gen = player.generation_id
    
    await player.play_next(chat_id, expected_gen=current_gen)
