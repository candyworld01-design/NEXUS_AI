import asyncio
import logging
import os
import random
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

import yt_dlp
from pyrogram import Client, filters
from pyrogram.enums import ChatMemberStatus
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from pytgcalls import PyTgCalls, filters as tg_filters
from pytgcalls.types import MediaStream

from config import (
    API_ID, API_HASH, BOT_TOKEN, STRING_SESSION, OWNER_ID,
    MAX_QUEUE_SIZE, MAX_VOLUME, DEFAULT_VOLUME,
)
from music_queue import queue


logger = logging.getLogger(__name__)

bot_client = Client(
    "NexusBot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN,
)

assistant_client = Client(
    "NexusAssistant",
    api_id=API_ID,
    api_hash=API_HASH,
    session_string=STRING_SESSION,
)

call_py = None

DOWNLOAD_DIR = "/tmp/nexus_downloads"
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

executor = ThreadPoolExecutor(
    max_workers=4,
    thread_name_prefix="nexus-download",
)


# =========================================================
# YOUTUBE EXTRACTION
# =========================================================

def ydl_options():
    options = {
        "format": "bestaudio/best",
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "retries": 2,
        "fragment_retries": 2,
        "extractor_retries": 2,
        "socket_timeout": 15,
        "skip_download": True,
    }

    cookies = os.getenv("YOUTUBE_COOKIES", "").strip()

    if cookies:
        cookie_path = "/tmp/nexus_cookies.txt"

        with open(cookie_path, "w", encoding="utf-8") as fp:
            fp.write(cookies.replace("\\n", "\n"))

        options["cookiefile"] = cookie_path

    return options


def _first_entry(info):
    if not info:
        raise RuntimeError("No search result was returned.")

    if "entries" in info:
        entries = [
            item for item in (info.get("entries") or [])
            if item
        ]

        if not entries:
            raise RuntimeError("No song found for that search.")

        return entries[0]

    return info


def _get_audio_url(info):
    stream_url = info.get("url")

    if stream_url:
        return stream_url

    formats = [
        item for item in (info.get("formats") or [])
        if item.get("url") and item.get("acodec") != "none"
    ]

    if not formats:
        raise RuntimeError("No playable audio stream was found.")

    return formats[-1]["url"]


def _extract_info_sync(query):
    with yt_dlp.YoutubeDL(ydl_options()) as ydl:

        target = (
            query
            if query.startswith(("http://", "https://"))
            else f"ytsearch1:{query}"
        )

        info = _first_entry(
            ydl.extract_info(target, download=False)
        )

        stream_url = _get_audio_url(info)

        return {
            "file": None,
            "stream_url": stream_url,
            "stream_fetched_at": time.time(),
            "http_headers": info.get("http_headers") or {},
            "title": info.get("title") or "Unknown title",
            "duration": int(info.get("duration") or 0),
            "url": info.get("webpage_url") or query,
            "thumbnail": info.get("thumbnail"),
            "artist": (
                info.get("artist")
                or info.get("uploader")
                or info.get("channel")
                or "Unknown artist"
            ),
            "requester": None,
            "added_at": time.time(),
        }


def _refresh_stream_sync(webpage_url):
    with yt_dlp.YoutubeDL(ydl_options()) as ydl:

        info = _first_entry(
            ydl.extract_info(webpage_url, download=False)
        )

        return _get_audio_url(info)


async def search_and_download(query, requester):
    loop = asyncio.get_running_loop()

    song = await loop.run_in_executor(
        executor,
        _extract_info_sync,
        query,
    )

    song["requester"] = requester
    song["added_at"] = time.time()

    return song


# =========================================================
# THUMBNAIL
# =========================================================

def _download_thumbnail_sync(url, video_id):
    if not url:
        return None

    path = os.path.join(
        DOWNLOAD_DIR,
        f"{video_id or 'nexus'}_thumb.jpg",
    )

    if os.path.isfile(path) and os.path.getsize(path) > 1000:
        return path

    request = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0"},
    )

    try:
        with urllib.request.urlopen(request, timeout=8) as response:
            data = response.read(5 * 1024 * 1024)

        if len(data) < 1000:
            return None

        with open(path, "wb") as fp:
            fp.write(data)

        return path

    except Exception:
        logger.warning("Thumbnail download failed", exc_info=True)
        return None


async def get_thumbnail_file(song):
    existing = song.get("thumbnail_file")

    if existing and os.path.isfile(existing):
        return existing

    loop = asyncio.get_running_loop()

    video_url = song.get("url", "")
    video_id = video_url.rstrip("/").split("=")[-1][:80]

    path = await loop.run_in_executor(
        executor,
        _download_thumbnail_sync,
        song.get("thumbnail"),
        video_id,
    )

    song["thumbnail_file"] = path

    return path


# =========================================================
# HELPERS
# =========================================================

def fmt_time(seconds):
    seconds = max(0, int(seconds or 0))

    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    remaining = seconds % 60

    if hours:
        return f"{hours:02d}:{minutes:02d}:{remaining:02d}"

    return f"{minutes:02d}:{remaining:02d}"


def delete_song_file(song):
    if not song:
        return

    for key in ("file", "thumbnail_file"):
        path = song.get(key)

        if path and os.path.isfile(path):
            try:
                os.remove(path)
            except OSError:
                pass


# =========================================================
# PLAYER UI
# =========================================================

def player_card(state, chat_title="Group"):
    song = state.current

    if not song:
        return (
            "🎵 **NEXUS MUSIC**\n\n"
            "📭 Nothing is playing right now."
        )

    title = song.get("title") or "Unknown title"
    artist = song.get("artist") or "Unknown artist"
    duration = fmt_time(song.get("duration", 0))
    requester = song.get("requester") or "Member"

    status = "⏸ Paused" if state.paused else "▶️ Playing"

    return (
        f"🎵 **{title}**\n"
        f"🎤 {artist}\n"
        f"⏱ `{duration}`\n\n"
        f"{status} · Requested by **{requester}**"
    )


def controls_keyboard(chat_id):
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "⏮",
                callback_data=f"nx:back:{chat_id}",
            ),
            InlineKeyboardButton(
                "⏸",
                callback_data=f"nx:pause:{chat_id}",
            ),
            InlineKeyboardButton(
                "▶️",
                callback_data=f"nx:resume:{chat_id}",
            ),
            InlineKeyboardButton(
                "⏭",
                callback_data=f"nx:skip:{chat_id}",
            ),
        ],
        [
            InlineKeyboardButton(
                "📜 Queue",
                callback_data=f"nx:queue:{chat_id}",
            ),
            InlineKeyboardButton(
                "ℹ️ Details",
                callback_data=f"nx:details:{chat_id}",
            ),
            InlineKeyboardButton(
                "🔀 Shuffle",
                callback_data=f"nx:shuffle:{chat_id}",
            ),
            InlineKeyboardButton(
                "⏹ Stop",
                callback_data=f"nx:stop:{chat_id}",
            ),
        ],
    ])


def screen_keyboard(chat_id):
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "⬅️ Back to Player",
                callback_data=f"nx:player:{chat_id}",
            )
        ]
    ])


def details_card(state):
    song = state.current

    if not song:
        return "🎵 **No song is currently playing.**"

    url = song.get("url") or ""

    text = (
        "ℹ️ **SONG DETAILS**\n\n"
        f"🎵 **Title:** {song.get('title', 'Unknown')}\n"
        f"🎤 **Artist:** {song.get('artist') or 'Unknown artist'}\n"
        f"⏱ **Duration:** `{fmt_time(song.get('duration', 0))}`\n"
        f"🙋 **Requested by:** {song.get('requester') or 'Member'}\n"
        f"📡 **Status:** {'Paused' if state.paused else 'Playing'}"
    )

    if url.startswith(("http://", "https://")):
        text += f"\n\n🔗 {url}"

    return text


def queue_card(state, items):
    lines = ["📜 **NEXUS QUEUE**"]

    if state.current:
        lines.append(
            f"\n▶️ **Now Playing:** "
            f"{state.current.get('title', 'Unknown')}"
        )

    if not items:
        lines.append("\n\n📭 No upcoming songs.")

    for index, item in enumerate(items[:12], 1):
        lines.append(
            f"\n`{index}.` {item.get('title', 'Unknown')}"
            f"\n   👤 {item.get('requester', 'Member')}"
        )

    if len(items) > 12:
        lines.append(
            f"\n\n…and {len(items) - 12} more."
        )

    return "\n".join(lines)


# =========================================================
# GROUP PLAYER
# =========================================================

class GroupPlayer:
    def __init__(self, chat_id):
        self.chat_id = chat_id
        self.lock = asyncio.Lock()

        self.current = None
        self.previous = []

        self.paused = False
        self.loop_mode = "off"

        self.volume = DEFAULT_VOLUME
        self.generation = 0

        self.queue_preview = []
        self.vote_skip = set()

        self.last_message_id = None
        self.last_message_is_photo = False


# =========================================================
# PLAYER MANAGER
# =========================================================

class PlayerManager:
    def __init__(self):
        self.groups = {}
        self._groups_lock = asyncio.Lock()

    async def get(self, chat_id):
        async with self._groups_lock:
            if chat_id not in self.groups:
                self.groups[chat_id] = GroupPlayer(chat_id)

            return self.groups[chat_id]

    async def download(self, query, requester):
        return await search_and_download(query, requester)

    async def play_song(self, state, song):
        loop = asyncio.get_running_loop()

        stream_url = song.get("stream_url")
        fetched_at = song.get("stream_fetched_at", 0)

        # Reuse the URL obtained during search.
        # Refresh it only if missing or older than 3 minutes.
        if (
            not stream_url
            or time.time() - fetched_at > 180
        ):
            stream_url = await loop.run_in_executor(
                executor,
                _refresh_stream_sync,
                song["url"],
            )

            song["stream_url"] = stream_url
            song["stream_fetched_at"] = time.time()

        await call_py.play(
            state.chat_id,
            MediaStream(
                stream_url,
                video_flags=MediaStream.Flags.IGNORE,
            ),
        )

        state.current = song
        state.paused = False
        state.vote_skip.clear()
        state.generation += 1

    async def enqueue_or_start(self, chat_id, song):
        state = await self.get(chat_id)

        async with state.lock:
            if state.current:
                position = await queue.add(
                    chat_id,
                    song,
                    MAX_QUEUE_SIZE,
                )

                state.queue_preview = await queue.snapshot(chat_id)

                return (
                    False,
                    f"➕ **Added to queue**\n"
                    f"Position: `{position}`\n"
                    f"🎵 {song['title']}",
                )

            await self.play_song(state, song)

            state.queue_preview = await queue.snapshot(chat_id)

            return True, player_card(state)

    async def force_play(self, chat_id, song):
        state = await self.get(chat_id)

        async with state.lock:
            old = state.current

            if old:
                state.previous.append(old)

                if len(state.previous) > 10:
                    state.previous.pop(0)

            await self.play_song(state, song)

            state.queue_preview = await queue.snapshot(chat_id)

            return player_card(state)

    async def next_song(self, chat_id, expected_generation=None):
        state = await self.get(chat_id)

        async with state.lock:
            if (
                expected_generation is not None
                and expected_generation != state.generation
            ):
                return

            old = state.current

            if old and state.loop_mode == "song":
                next_item = old

            else:
                if old and state.loop_mode == "queue":
                    await queue.add(
                        chat_id,
                        old,
                        MAX_QUEUE_SIZE + 1,
                    )

                next_item = await queue.get_next(chat_id)

            if old and old is not next_item:
                state.previous.append(old)

                if len(state.previous) > 10:
                    state.previous.pop(0)

            while next_item:
                try:
                    await self.play_song(state, next_item)

                    state.queue_preview = await queue.snapshot(chat_id)

                    return

                except Exception:
                    logger.exception(
                        "Could not play queued song in %s",
                        chat_id,
                    )

                    delete_song_file(next_item)

                    next_item = await queue.get_next(chat_id)

            state.current = None
            state.paused = False
            state.queue_preview = []

            try:
                await call_py.leave_call(chat_id)

            except Exception:
                logger.info(
                    "No active call to leave for %s",
                    chat_id,
                )

    async def skip(self, chat_id):
        state = await self.get(chat_id)

        state.generation += 1

        await self.next_song(chat_id)

    async def stop(self, chat_id):
        state = await self.get(chat_id)

        async with state.lock:
            state.generation += 1

            old = state.current
            queued = await queue.clear(chat_id)

            state.current = None
            state.previous.clear()

            state.paused = False
            state.loop_mode = "off"

            state.queue_preview = []
            state.vote_skip.clear()

            try:
                await call_py.leave_call(chat_id)

            except Exception:
                logger.info(
                    "Leave call requested for %s",
                    chat_id,
                )

            delete_song_file(old)

            for item in queued:
                delete_song_file(item)

    async def back(self, chat_id):
        state = await self.get(chat_id)

        async with state.lock:
            if not state.previous:
                return False

            old = state.current
            previous = state.previous.pop()

            if old:
                await queue.add(
                    chat_id,
                    old,
                    MAX_QUEUE_SIZE + 1,
                )

            await self.play_song(state, previous)

            state.queue_preview = await queue.snapshot(chat_id)

            return True


manager = PlayerManager()


# =========================================================
# PLAYER MESSAGE MANAGEMENT
# =========================================================

async def update_player_message(
    state,
    chat_id,
    fallback_message=None,
):
    text = player_card(state)

    markup = (
        controls_keyboard(chat_id)
        if state.current
        else None
    )

    # Edit existing player message.
    if state.last_message_id:
        try:
            if state.last_message_is_photo:
                await bot_client.edit_message_caption(
                    chat_id,
                    state.last_message_id,
                    caption=text,
                    reply_markup=markup,
                )

            else:
                await bot_client.edit_message_text(
                    chat_id,
                    state.last_message_id,
                    text,
                    reply_markup=markup,
                    disable_web_page_preview=True,
                )

            return

        except Exception:
            logger.warning(
                "Existing player message could not be edited.",
                exc_info=True,
            )

            try:
                await bot_client.delete_messages(
                    chat_id,
                    state.last_message_id,
                )

            except Exception:
                pass

            state.last_message_id = None
            state.last_message_is_photo = False

    # Do not create an empty player card without a current song.
    if not state.current:
        return

    song = state.current
    thumbnail = None

    if song.get("thumbnail"):
        try:
            thumbnail = await get_thumbnail_file(song)

        except Exception:
            logger.warning(
                "Thumbnail retrieval failed.",
                exc_info=True,
            )

    if thumbnail:
        if fallback_message:
            sent = await fallback_message.reply_photo(
                photo=thumbnail,
                caption=text,
                reply_markup=markup,
            )

        else:
            sent = await bot_client.send_photo(
                chat_id,
                photo=thumbnail,
                caption=text,
                reply_markup=markup,
            )

        state.last_message_is_photo = True

    else:
        if fallback_message:
            sent = await fallback_message.reply_text(
                text,
                reply_markup=markup,
                disable_web_page_preview=True,
            )

        else:
            sent = await bot_client.send_message(
                chat_id,
                text,
                reply_markup=markup,
                disable_web_page_preview=True,
            )

        state.last_message_is_photo = False

    state.last_message_id = sent.id


async def reply_now_playing(message, state, text=None):
    # Update the existing player instead of sending duplicate cards.
    await update_player_message(
        state,
        message.chat.id,
        fallback_message=message,
    )


# =========================================================
# PERMISSIONS
# =========================================================

async def is_admin(message):
    if (
        message.from_user
        and OWNER_ID
        and message.from_user.id == OWNER_ID
    ):
        return True

    if not message.from_user:
        return False

    try:
        member = await bot_client.get_chat_member(
            message.chat.id,
            message.from_user.id,
        )

        return member.status in (
            ChatMemberStatus.OWNER,
            ChatMemberStatus.ADMINISTRATOR,
        )

    except Exception:
        return False


async def is_group_owner(message):
    if (
        message.from_user
        and OWNER_ID
        and message.from_user.id == OWNER_ID
    ):
        return True

    if not message.from_user:
        return False

    try:
        member = await bot_client.get_chat_member(
            message.chat.id,
            message.from_user.id,
        )

        return member.status == ChatMemberStatus.OWNER

    except Exception:
        return False


async def require_admin(message):
    if not await is_admin(message):
        await message.reply_text(
            "🔒 **Admin only**\n"
            "This control is reserved for group admins."
        )

        return False

    return True


# =========================================================
# HELP
# =========================================================

@bot_client.on_message(
    filters.command(["start", "help"]) & filters.group
)
async def group_help(_, message):
    text = (
        "🎧 **NEXUS MUSIC**\n\n"
        "👤 **Members**\n"
        "`/play <song>` · `/queue` · `/np`\n\n"
        "🛡️ **Group admins**\n"
        "`/skip` · `/pause` · `/resume` · `/back` · `/stop`\n"
        "`/playforce <song>` · `/shuffle` · `/loop`\n"
        "`/volume 100` · `/remove <position>` · `/clear`\n\n"
        "👑 **Group owner**\n"
        "All admin controls and group DJ configuration.\n\n"
        "NEXUS keeps each group's player and queue separate."
    )

    await message.reply_text(
        text,
        disable_web_page_preview=True,
    )


# =========================================================
# PLAY
# =========================================================

@bot_client.on_message(
    filters.command("play") & filters.group
)
async def play_command(_, message):
    if len(message.command) < 2:
        return await message.reply_text(
            "🎵 Usage: `/play song name`"
        )

    query = " ".join(message.command[1:])

    status = await message.reply_text(
        "🔎 **Searching NEXUS library...**"
    )

    song = None

    try:
        requester = (
            message.from_user.first_name
            if message.from_user
            else "Member"
        )

        song = await manager.download(query, requester)

        started, response = await manager.enqueue_or_start(
            message.chat.id,
            song,
        )

        if started:
            try:
                await status.delete()
            except Exception:
                pass

            state = await manager.get(message.chat.id)

            await reply_now_playing(message, state)

        else:
            await status.edit_text(
                response,
                reply_markup=None,
                disable_web_page_preview=True,
            )

    except Exception as exc:
        logger.exception("PLAY failed")

        if song:
            delete_song_file(song)

        await status.edit_text(
            f"⚠️ **Couldn't play this request.**\n"
            f"`{str(exc)[:350]}`"
        )


# =========================================================
# FORCE PLAY
# =========================================================

@bot_client.on_message(
    filters.command("playforce") & filters.group
)
async def playforce_command(_, message):
    if not await require_admin(message):
        return

    if len(message.command) < 2:
        return await message.reply_text(
            "⚡ Usage: `/playforce song name`"
        )

    query = " ".join(message.command[1:])

    status = await message.reply_text(
        "⚡ **Preparing priority request...**"
    )

    song = None

    try:
        requester = (
            message.from_user.first_name
            if message.from_user
            else "Admin"
        )

        song = await manager.download(query, requester)

        await manager.force_play(
            message.chat.id,
            song,
        )

        try:
            await status.delete()
        except Exception:
            pass

        state = await manager.get(message.chat.id)

        await reply_now_playing(message, state)

    except Exception as exc:
        logger.exception("PLAYFORCE failed")

        if song:
            delete_song_file(song)

        await status.edit_text(
            f"⚠️ **Force play failed:** `{str(exc)[:300]}`"
        )


# =========================================================
# QUEUE COMMAND
# =========================================================

@bot_client.on_message(
    filters.command("queue") & filters.group
)
async def queue_command(_, message):
    state = await manager.get(message.chat.id)

    items = await queue.snapshot(message.chat.id)

    if not state.current and not items:
        return await message.reply_text(
            "📭 **Queue is empty.** Use `/play song name`."
        )

    await message.reply_text(
        queue_card(state, items)
    )


# =========================================================
# NOW PLAYING
# =========================================================

@bot_client.on_message(
    filters.command(["np", "nowplaying"]) & filters.group
)
async def np_command(_, message):
    state = await manager.get(message.chat.id)

    if not state.current:
        return await message.reply_text(
            "📭 Nothing is playing right now."
        )

    await reply_now_playing(message, state)


# =========================================================
# SKIP
# =========================================================

@bot_client.on_message(
    filters.command("skip") & filters.group
)
async def skip_command(_, message):
    if not await require_admin(message):
        return

    await manager.skip(message.chat.id)

    state = await manager.get(message.chat.id)

    await reply_now_playing(message, state)


# =========================================================
# PAUSE
# =========================================================

@bot_client.on_message(
    filters.command("pause") & filters.group
)
async def pause_command(_, message):
    if not await require_admin(message):
        return

    state = await manager.get(message.chat.id)

    if not state.current:
        return await message.reply_text(
            "Nothing is playing."
        )

    try:
        await call_py.pause(message.chat.id)

        state.paused = True

        await update_player_message(
            state,
            message.chat.id,
            fallback_message=message,
        )

    except Exception as exc:
        await message.reply_text(
            f"Pause failed: `{str(exc)[:250]}`"
        )


# =========================================================
# RESUME
# =========================================================

@bot_client.on_message(
    filters.command("resume") & filters.group
)
async def resume_command(_, message):
    if not await require_admin(message):
        return

    state = await manager.get(message.chat.id)

    if not state.current:
        return await message.reply_text(
            "Nothing is playing."
        )

    try:
        await call_py.resume(message.chat.id)

        state.paused = False

        await update_player_message(
            state,
            message.chat.id,
            fallback_message=message,
        )

    except Exception as exc:
        await message.reply_text(
            f"Resume failed: `{str(exc)[:250]}`"
        )


# =========================================================
# PREVIOUS
# =========================================================

@bot_client.on_message(
    filters.command("back") & filters.group
)
async def back_command(_, message):
    if not await require_admin(message):
        return

    ok = await manager.back(message.chat.id)

    state = await manager.get(message.chat.id)

    if not ok:
        return await message.reply_text(
            "No previous track is available."
        )

    await reply_now_playing(message, state)


# =========================================================
# STOP
# =========================================================

@bot_client.on_message(
    filters.command("stop") & filters.group
)
async def stop_command(_, message):
    if not await require_admin(message):
        return

    await manager.stop(message.chat.id)

    state = await manager.get(message.chat.id)

    await update_player_message(
        state,
        message.chat.id,
    )

    await message.reply_text(
        "⏹️ **Playback stopped.** This group's queue was cleared."
    )


# =========================================================
# SHUFFLE
# =========================================================

@bot_client.on_message(
    filters.command("shuffle") & filters.group
)
async def shuffle_command(_, message):
    if not await require_admin(message):
        return

    count = await queue.shuffle(message.chat.id)

    state = await manager.get(message.chat.id)

    state.queue_preview = await queue.snapshot(message.chat.id)

    await message.reply_text(
        f"🔀 Queue shuffled. `{count}` upcoming tracks."
    )


# =========================================================
# LOOP
# =========================================================

@bot_client.on_message(
    filters.command("loop") & filters.group
)
async def loop_command(_, message):
    if not await require_admin(message):
        return

    state = await manager.get(message.chat.id)

    modes = ["off", "song", "queue"]

    state.loop_mode = modes[
        (modes.index(state.loop_mode) + 1) % len(modes)
    ]

    await message.reply_text(
        f"🔁 Loop mode: **{state.loop_mode.upper()}**"
    )


# =========================================================
# VOLUME
# =========================================================

@bot_client.on_message(
    filters.command("volume") & filters.group
)
async def volume_command(_, message):
    if not await require_admin(message):
        return

    if len(message.command) < 2:
        return await message.reply_text(
            "Usage: `/volume 100` (1–200)"
        )

    try:
        value = max(
            1,
            min(int(message.command[1]), MAX_VOLUME),
        )

        await call_py.change_volume_call(
            message.chat.id,
            value,
        )

        state = await manager.get(message.chat.id)

        state.volume = value

        await message.reply_text(
            f"🔊 Volume set to `{value}%`."
        )

    except Exception as exc:
        await message.reply_text(
            f"Volume change failed: `{str(exc)[:250]}`"
        )


# =========================================================
# REMOVE
# =========================================================

@bot_client.on_message(
    filters.command("remove") & filters.group
)
async def remove_command(_, message):
    if not await require_admin(message):
        return

    if (
        len(message.command) < 2
        or not message.command[1].isdigit()
    ):
        return await message.reply_text(
            "Usage: `/remove 2`"
        )

    item = await queue.remove(
        message.chat.id,
        int(message.command[1]),
    )

    if not item:
        return await message.reply_text(
            "That queue position does not exist."
        )

    delete_song_file(item)

    state = await manager.get(message.chat.id)

    state.queue_preview = await queue.snapshot(message.chat.id)

    await message.reply_text(
        f"🗑 Removed: **{item['title']}**"
    )


# =========================================================
# CLEAR
# =========================================================

@bot_client.on_message(
    filters.command("clear") & filters.group
)
async def clear_command(_, message):
    if not await require_admin(message):
        return

    items = await queue.clear(message.chat.id)

    for item in items:
        delete_song_file(item)

    state = await manager.get(message.chat.id)

    state.queue_preview = []

    await message.reply_text(
        f"🧹 Cleared `{len(items)}` upcoming songs. "
        "Current song continues."
    )


# =========================================================
# DJ MODE
# =========================================================

@bot_client.on_message(
    filters.command("djmode") & filters.group
)
async def djmode_command(_, message):
    if not await is_group_owner(message):
        return await message.reply_text(
            "🔒 Only the group owner can change DJ mode."
        )

    if (
        len(message.command) < 2
        or message.command[1].lower() not in ("on", "off")
    ):
        return await message.reply_text(
            "Usage: `/djmode on` or `/djmode off`"
        )

    await message.reply_text(
        "🎚️ DJ mode setting received. "
        "Current policy: Telegram group admins control playback; "
        "members can request songs and view the queue."
    )


# =========================================================
# CALLBACK HANDLER
# =========================================================

@bot_client.on_callback_query(
    filters.regex(r"^nx:")
)
async def control_callback(_, query):
    try:
        _, action, raw_chat = query.data.split(":", 2)

        chat_id = int(raw_chat)

        if query.message.chat.id != chat_id:
            return await query.answer(
                "This control belongs to another group.",
                show_alert=True,
            )

        state = await manager.get(chat_id)

        # -------------------------------------------------
        # READ-ONLY: QUEUE
        # -------------------------------------------------

        if action == "queue":
            items = await queue.snapshot(chat_id)

            text = queue_card(state, items)

            await query.answer()

            if query.message.photo:
                await query.message.edit_caption(
                    caption=text,
                    reply_markup=screen_keyboard(chat_id),
                )
            else:
                await query.message.edit_text(
                    text,
                    reply_markup=screen_keyboard(chat_id),
                    disable_web_page_preview=True,
                )

            return

        # -------------------------------------------------
        # READ-ONLY: DETAILS
        # -------------------------------------------------

        if action == "details":
            text = details_card(state)

            await query.answer()

            if query.message.photo:
                await query.message.edit_caption(
                    caption=text,
                    reply_markup=screen_keyboard(chat_id),
                )
            else:
                await query.message.edit_text(
                    text,
                    reply_markup=screen_keyboard(chat_id),
                    disable_web_page_preview=True,
                )

            return

        # -------------------------------------------------
        # BACK TO PLAYER
        # -------------------------------------------------

        if action == "player":
            text = player_card(state)

            markup = (
                controls_keyboard(chat_id)
                if state.current
                else None
            )

            await query.answer()

            if query.message.photo:
                await query.message.edit_caption(
                    caption=text,
                    reply_markup=markup,
                )
            else:
                await query.message.edit_text(
                    text,
                    reply_markup=markup,
                    disable_web_page_preview=True,
                )

            return

        # -------------------------------------------------
        # ADMIN PERMISSION
        # -------------------------------------------------

        try:
            member = await bot_client.get_chat_member(
                chat_id,
                query.from_user.id,
            )

            allowed = (
                (
                    OWNER_ID
                    and query.from_user.id == OWNER_ID
                )
                or member.status in (
                    ChatMemberStatus.OWNER,
                    ChatMemberStatus.ADMINISTRATOR,
                )
            )

        except Exception:
            allowed = bool(
                OWNER_ID
                and query.from_user.id == OWNER_ID
            )

        if not allowed:
            return await query.answer(
                "Admin permission required.",
                show_alert=True,
            )

        # -------------------------------------------------
        # PLAYBACK ACTIONS
        # -------------------------------------------------

        if action == "pause":

            if not state.current:
                return await query.answer(
                    "Nothing is playing.",
                    show_alert=True,
                )

            await call_py.pause(chat_id)

            state.paused = True

        elif action == "resume":

            if not state.current:
                return await query.answer(
                    "Nothing is playing.",
                    show_alert=True,
                )

            await call_py.resume(chat_id)

            state.paused = False

        elif action == "skip":

            await manager.skip(chat_id)

        elif action == "back":

            if not await manager.back(chat_id):
                return await query.answer(
                    "No previous track.",
                    show_alert=True,
                )

        elif action == "stop":

            await manager.stop(chat_id)

        elif action == "shuffle":

            await queue.shuffle(chat_id)

            state.queue_preview = await queue.snapshot(chat_id)

        else:
            return await query.answer(
                "Unknown control.",
                show_alert=True,
            )

        # Remember the message used by the player.
        state.last_message_id = query.message.id
        state.last_message_is_photo = bool(query.message.photo)

        await query.answer("NEXUS control applied.")

        await update_player_message(
            state,
            chat_id,
        )

    except Exception as exc:
        logger.exception("Callback control failed")

        try:
            await query.answer(
                f"Action failed: {str(exc)[:150]}",
                show_alert=True,
            )

        except Exception:
            pass


# =========================================================
# STREAM END HANDLER
# =========================================================

def register_stream_handler():

    @call_py.on_update(tg_filters.stream_end())
    async def stream_end_handler(_, update):

        state = await manager.get(update.chat_id)

        generation = state.generation

        await manager.next_song(
            update.chat_id,
            expected_generation=generation,
        )

        await update_player_message(
            state,
            update.chat_id,
        )


# =========================================================
# RUNTIME INITIALIZATION
# =========================================================

def initialize_runtime():
    global call_py

    call_py = PyTgCalls(assistant_client)

    register_stream_handler()

    logger.info(
        "NEXUS Music runtime initialized."
    )
