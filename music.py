# Add this import with the other imports:
# from pyrogram.errors import MessageNotModified


def _extract_info_sync(query):
    with yt_dlp.YoutubeDL(ydl_options()) as ydl:
        target = (
            query if query.startswith(("http://", "https://"))
            else f"ytsearch1:{query}"
        )

        info = _first_entry(ydl.extract_info(target, download=False))
        stream_url = info.get("url")

        if not stream_url:
            formats = [
                f for f in (info.get("formats") or [])
                if f.get("url") and f.get("acodec") != "none"
            ]
            if not formats:
                raise RuntimeError("No playable audio stream was found.")
            stream_url = formats[-1]["url"]

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
        }


async def play_song(self, state, song):
    loop = asyncio.get_running_loop()

    # Reuse a fresh URL for the first playback.
    # Refresh only if it is missing or old.
    fetched_at = song.get("stream_fetched_at", 0)
    stream_url = song.get("stream_url")

    if not stream_url or time.time() - fetched_at > 240:
        stream_url = await loop.run_in_executor(
            executor,
            _refresh_stream_sync,
            song["url"]
        )
        song["stream_url"] = stream_url
        song["stream_fetched_at"] = time.time()

    await call_py.play(
        state.chat_id,
        MediaStream(
            stream_url,
            video_flags=MediaStream.Flags.IGNORE
        )
    )

    state.current = song
    state.paused = False
    state.vote_skip.clear()
    state.generation += 1


def player_card(state, chat_title="Group"):
    song = state.current

    if not song:
        return "🎵 **NEXUS MUSIC**\n\n📭 Nothing is playing."

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
            InlineKeyboardButton("⏮", callback_data=f"nx:back:{chat_id}"),
            InlineKeyboardButton("⏸", callback_data=f"nx:pause:{chat_id}"),
            InlineKeyboardButton("▶️", callback_data=f"nx:resume:{chat_id}"),
            InlineKeyboardButton("⏭", callback_data=f"nx:skip:{chat_id}"),
        ],
        [
            InlineKeyboardButton("📜 Queue", callback_data=f"nx:queue:{chat_id}"),
            InlineKeyboardButton("ℹ️ Details", callback_data=f"nx:details:{chat_id}"),
            InlineKeyboardButton("⏹ Stop", callback_data=f"nx:stop:{chat_id}"),
        ],
    ])


def details_keyboard(chat_id):
    return InlineKeyboardMarkup([[
        InlineKeyboardButton(
            "⬅️ Back to Player",
            callback_data=f"nx:player:{chat_id}"
        )
    ]])


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
        text += f"\n\n🔗 [Open Song Source]({url})"

    return text


def queue_card(state, items):
    lines = ["📜 **UP NEXT**"]

    if state.current:
        lines.append(
            f"\n▶️ **Now Playing:** {state.current.get('title', 'Unknown')}"
        )

    if not items:
        lines.append("\n\n📭 No upcoming songs.")

    for index, item in enumerate(items[:12], 1):
        lines.append(
            f"\n`{index}.` {item.get('title', 'Unknown')}"
            f"\n   👤 {item.get('requester', 'Member')}"
        )

    if len(items) > 12:
        lines.append(f"\n\n…and {len(items) - 12} more.")

    return "\n".join(lines)


def queue_keyboard(chat_id):
    return InlineKeyboardMarkup([[
        InlineKeyboardButton(
            "⬅️ Back to Player",
            callback_data=f"nx:player:{chat_id}"
        )
    ]])


async def update_player_message(state, chat_id, fallback_message=None):
    text = player_card(state)
    markup = controls_keyboard(chat_id) if state.current else None

    # Edit existing player message first.
    if state.last_message_id:
        try:
            if state.last_message_is_photo:
                await bot_client.edit_message_caption(
                    chat_id,
                    state.last_message_id,
                    caption=text,
                    reply_markup=markup
                )
            else:
                await bot_client.edit_message_text(
                    chat_id,
                    state.last_message_id,
                    text,
                    reply_markup=markup,
                    disable_web_page_preview=True
                )
            return
        except Exception:
            logger.warning("Player edit failed; replacing message.", exc_info=True)

            try:
                await bot_client.delete_messages(
                    chat_id,
                    state.last_message_id
                )
            except Exception:
                pass

            state.last_message_id = None
            state.last_message_is_photo = False

    # Send a new player only when no editable player exists.
    song = state.current or {}
    thumbnail = None

    if song.get("thumbnail"):
        try:
            thumbnail = await get_thumbnail_file(song)
        except Exception:
            logger.warning("Thumbnail retrieval failed.", exc_info=True)

    if fallback_message:
        if thumbnail:
            sent = await fallback_message.reply_photo(
                photo=thumbnail,
                caption=text,
                reply_markup=markup
            )
            state.last_message_is_photo = True
        else:
            sent = await fallback_message.reply_text(
                text,
                reply_markup=markup,
                disable_web_page_preview=True
            )
            state.last_message_is_photo = False
    else:
        if thumbnail:
            sent = await bot_client.send_photo(
                chat_id,
                photo=thumbnail,
                caption=text,
                reply_markup=markup
            )
            state.last_message_is_photo = True
        else:
            sent = await bot_client.send_message(
                chat_id,
                text,
                reply_markup=markup,
                disable_web_page_preview=True
            )
            state.last_message_is_photo = False

    state.last_message_id = sent.id


async def reply_now_playing(message, state, text=None):
    # Kept for compatibility with existing command handlers.
    await update_player_message(
        state,
        message.chat.id,
        fallback_message=message
    )


@bot_client.on_callback_query(filters.regex(r"^nx:"))
async def control_callback(_, query):
    try:
        _, action, raw_chat = query.data.split(":", 2)
        chat_id = int(raw_chat)

        if query.message.chat.id != chat_id:
            return await query.answer(
                "This control belongs to another group.",
                show_alert=True
            )

        state = await manager.get(chat_id)

        # Read-only screens are available to all members.
        if action == "details":
            await query.answer()
            text = details_card(state)
            if query.message.photo:
                await query.message.edit_caption(
                    caption=text,
                    reply_markup=details_keyboard(chat_id)
                )
            else:
                await query.message.edit_text(
                    text,
                    reply_markup=details_keyboard(chat_id),
                    disable_web_page_preview=False
                )
            return

        if action == "queue":
            await query.answer()
            items = await queue.snapshot(chat_id)
            text = queue_card(state, items)
            if query.message.photo:
                await query.message.edit_caption(
                    caption=text,
                    reply_markup=queue_keyboard(chat_id)
                )
            else:
                await query.message.edit_text(
                    text,
                    reply_markup=queue_keyboard(chat_id)
                )
            return

        if action == "player":
            await query.answer()
            text = player_card(state)
            markup = controls_keyboard(chat_id) if state.current else None

            if query.message.photo:
                await query.message.edit_caption(
                    caption=text,
                    reply_markup=markup
                )
            else:
                await query.message.edit_text(
                    text,
                    reply_markup=markup,
                    disable_web_page_preview=True
                )
            return

        # Playback controls require admin permission.
        try:
            member = await bot_client.get_chat_member(
                chat_id,
                query.from_user.id
            )
            allowed = (
                (OWNER_ID and query.from_user.id == OWNER_ID)
                or member.status in (
                    ChatMemberStatus.OWNER,
                    ChatMemberStatus.ADMINISTRATOR
                )
            )
        except Exception:
            allowed = bool(OWNER_ID and query.from_user.id == OWNER_ID)

        if not allowed:
            return await query.answer(
                "Admin permission required.",
                show_alert=True
            )

        if action == "pause":
            if not state.current:
                return await query.answer("Nothing is playing.", show_alert=True)
            await call_py.pause(chat_id)
            state.paused = True

        elif action == "resume":
            if not state.current:
                return await query.answer("Nothing is playing.", show_alert=True)
            await call_py.resume(chat_id)
            state.paused = False

        elif action == "skip":
            await manager.skip(chat_id)

        elif action == "back":
            if not await manager.back(chat_id):
                return await query.answer(
                    "No previous track.",
                    show_alert=True
                )

        elif action == "stop":
            await manager.stop(chat_id)

        else:
            return await query.answer("Unknown control.", show_alert=True)

        await query.answer("Done.")

        state.last_message_id = query.message.id
        state.last_message_is_photo = bool(query.message.photo)

        await update_player_message(state, chat_id)

    except Exception as exc:
        logger.exception("Callback control failed")
        try:
            await query.answer(
                f"Action failed: {str(exc)[:120]}",
                show_alert=True
            )
        except Exception:
            pass
