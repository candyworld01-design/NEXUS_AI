import os
import asyncio
import logging
import yt_dlp

from pyrogram import Client, filters

from pytgcalls import PyTgCalls
from pytgcalls import filters as tg_filters

from pytgcalls.types import MediaStream, StreamEnded

from config import (
    API_ID,
    API_HASH,
    BOT_TOKEN,
    STRING_SESSION
)

from music_queue import queue


logging.basicConfig(level=logging.INFO)

logger = logging.getLogger(__name__)


# ==============================
# TELEGRAM CLIENTS
# ==============================

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


# ==============================
# PYTGCALLS
# ==============================

call_py = PyTgCalls(assistant_client)


# ==============================
# DOWNLOAD SETTINGS
# ==============================

DOWNLOAD_DIR = "/tmp/nexus_downloads"

os.makedirs(DOWNLOAD_DIR, exist_ok=True)


def get_ydl_options():

    options = {
        "format": "bestaudio/best",
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "retries": 3,
        "fragment_retries": 3,
        "extractor_retries": 3,
        "socket_timeout": 30,
        "outtmpl": os.path.join(
            DOWNLOAD_DIR,
            "%(id)s.%(ext)s"
        ),
    }

    cookies = os.getenv("YOUTUBE_COOKIES", "").strip()

    if cookies:

        cookie_path = "/tmp/nexus_cookies.txt"

        cookies = cookies.replace("\\n", "\n")

        with open(
            cookie_path,
            "w",
            encoding="utf-8"
        ) as file:

            file.write(cookies)

        options["cookiefile"] = cookie_path

    return options


# ==============================
# MUSIC PLAYER
# ==============================

class MusicPlayer:

    def __init__(self):

        self.is_playing = False

        self.current_chat_id = None

        self.current_file = None

        self.current_title = None

        self.generation_id = 0

        self.lock = asyncio.Lock()


    # --------------------------
    # SEARCH AND DOWNLOAD
    # --------------------------

    async def search_and_download(self, query):

        loop = asyncio.get_running_loop()

        def download():

            options = get_ydl_options()

            with yt_dlp.YoutubeDL(options) as ydl:

                if query.startswith("http://") or query.startswith("https://"):
                    search = query
                else:
                    search = f"ytsearch1:{query}"

                info = ydl.extract_info(
                    search,
                    download=True
                )

                if not info:
                    raise RuntimeError(
                        "No video information received from YouTube."
                    )

                if "entries" in info:

                    entries = info.get("entries") or []

                    if not entries:
                        raise RuntimeError(
                            "No search result found."
                        )

                    info = entries[0]

                file_path = ydl.prepare_filename(info)

                if not os.path.isfile(file_path):

                    raise RuntimeError(
                        "Downloaded audio file was not found."
                    )

                title = info.get(
                    "title",
                    "Unknown Title"
                )

                return file_path, title

        return await loop.run_in_executor(
            None,
            download
        )


    # --------------------------
    # PLAY FILE
    # --------------------------

    async def play_file(self, chat_id, file_path):

        stream = MediaStream(
            file_path,
            video_flags=MediaStream.Flags.IGNORE
        )

        await call_py.play(
            chat_id,
            stream
        )


    # --------------------------
    # ADD AND PLAY
    # --------------------------

    async def add_and_play(
        self,
        chat_id,
        file_path,
        title
    ):

        async with self.lock:

            if self.is_playing:

                if self.current_chat_id != chat_id:

                    raise RuntimeError(
                        "Music is already playing in another group."
                    )

                await queue.add(
                    (file_path, title)
                )

                position = await queue.get_size()

                return (
                    False,
                    f"➕ Added to queue. Position: {position}\n🎵 {title}"
                )

            self.is_playing = True

            self.current_chat_id = chat_id

            self.current_file = file_path

            self.current_title = title

            try:

                await self.play_file(
                    chat_id,
                    file_path
                )

                return (
                    True,
                    f"🎶 Now Playing: {title}"
                )

            except Exception:

                self.is_playing = False

                self.current_chat_id = None

                self.current_file = None

                self.current_title = None

                raise


    # --------------------------
    # NEXT SONG
    # --------------------------

    async def play_next(
        self,
        chat_id,
        expected_gen=None
    ):

        async with self.lock:

            if expected_gen is not None:

                if expected_gen != self.generation_id:
                    return

            old_file = self.current_file

            if old_file and os.path.exists(old_file):

                try:
                    os.remove(old_file)
                except OSError:
                    pass

            failures = 0

            while failures < 3:

                song = await queue.get_next()

                if not song:
                    break

                file_path, title = song

                try:

                    await self.play_file(
                        chat_id,
                        file_path
                    )

                    self.is_playing = True

                    self.current_chat_id = chat_id

                    self.current_file = file_path

                    self.current_title = title

                    logger.info(
                        "Now playing: %s",
                        title
                    )

                    return

                except Exception:

                    logger.exception(
                        "Playback failed for %s",
                        title
                    )

                    if os.path.exists(file_path):

                        try:
                            os.remove(file_path)
                        except OSError:
                            pass

                    failures += 1

            self.is_playing = False

            self.current_chat_id = None

            self.current_file = None

            self.current_title = None

            try:

                await call_py.leave_call(chat_id)

            except Exception:

                logger.exception(
                    "Could not leave voice chat"
                )


    # --------------------------
    # STOP MUSIC
    # --------------------------

    async def stop(self, chat_id):

        async with self.lock:

            self.generation_id += 1

            old_file = self.current_file

            old_queue = await queue.clear()

            self.is_playing = False

            self.current_chat_id = None

            self.current_file = None

            self.current_title = None

            try:

                await call_py.leave_call(chat_id)

            except Exception:

                logger.exception(
                    "Error while leaving voice chat"
                )

            files_to_delete = []

            if old_file:
                files_to_delete.append(old_file)

            for file_path, title in old_queue:
                files_to_delete.append(file_path)

            for file_path in files_to_delete:

                if os.path.exists(file_path):

                    try:
                        os.remove(file_path)
                    except OSError:
                        pass


player = MusicPlayer()


# ==============================
# PLAY COMMAND
# ==============================

@bot_client.on_message(
    filters.command("play") & filters.group
)
async def play_command(client, message):

    if len(message.command) < 2:

        return await message.reply(
            "❌ Song name likho.\nExample: /play falak tak"
        )

    query = " ".join(
        message.command[1:]
    )

    status = await message.reply(
        "🔍 Searching for your song..."
    )

    file_path = None

    try:

        file_path, title = await player.search_and_download(
            query
        )

        await status.edit(
            "🎵 Download complete. Starting playback..."
        )

        playing, response = await player.add_and_play(
            message.chat.id,
            file_path,
            title
        )

        await status.edit(response)

    except Exception as error:

        logger.exception(
            "PLAY COMMAND ERROR"
        )

        if file_path and os.path.exists(file_path):

            try:
                os.remove(file_path)
            except OSError:
                pass

        await status.edit(
            f"❌ Playback Error:\n{str(error)[:700]}"
        )


# ==============================
# STOP COMMAND
# ==============================

@bot_client.on_message(
    filters.command("stop") & filters.group
)
async def stop_command(client, message):

    if (
        player.current_chat_id is not None
        and player.current_chat_id != message.chat.id
    ):

        return await message.reply(
            "❌ Music is playing in another group."
        )

    await player.stop(
        message.chat.id
    )

    await message.reply(
        "⏹ Music stopped and queue cleared."
    )


# ==============================
# STREAM END HANDLER
# ==============================

@call_py.on_update(
    tg_filters.stream_end()
)
async def stream_end_handler(client, update: StreamEnded):

    chat_id = update.chat_id

    async with player.lock:

        if not player.is_playing:
            return

        if player.current_chat_id != chat_id:
            return

        current_gen = player.generation_id

    await player.play_next(
        chat_id,
        expected_gen=current_gen
    )


logger.info("Music module loaded successfully.")
