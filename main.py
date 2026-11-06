import asyncio
import sys
import signal
from pyrogram import filters
from config import API_ID, API_HASH
from music import bot_client, assistant_client, call_py, player
from health import start_health_server, health_status

@bot_client.on_message(filters.command("play") & filters.group)
async def play_command(client, message):
    if len(message.command) < 2:
        await message.reply_text("⚠️ Please provide a song name or URL! Example: `/play Faded`")
        return

    query = message.text.split(None, 1)[1]
    status_msg = await message.reply_text("🔍 Searching and downloading audio...")

    try:
        file_path, title = await player.search_and_download(query)
        chat_id = message.chat.id

        _, msg_text = await player.add_and_play(chat_id, file_path, title)
        await status_msg.edit_text(msg_text)

    except Exception as e:
        await status_msg.edit_text(f"❌ Error processing request: {str(e)}")

@bot_client.on_message(filters.command("stop") & filters.group)
async def stop_command(client, message):
    chat_id = message.chat.id
    try:
        await player.stop(chat_id)
        await message.reply_text("⏹️ Stopped playback, cleared queue, and left the voice chat.")
    except Exception as e:
        await message.reply_text(f"❌ Error stopping: {e}")

async def main():
    print("🚀 Starting NEXUS Music Prototype...")
    
    start_health_server()
    print("🌐 Health check server started on port 8080.")

    bot_started = False
    assistant_started = False
    call_started = False

    try:
        print("🤖 Starting Pyrogram Bot Client...")
        await bot_client.start()
        bot_started = True
        health_status["bot_active"] = True

        print("👤 Starting Assistant User Session...")
        await assistant_client.start()
        assistant_started = True
        health_status["assistant_active"] = True

        print("🎙️ Starting Py-TgCalls Client...")
        await call_py.start()
        call_started = True
        health_status["pytgcalls_active"] = True

        print("✨ NEXUS Music Prototype is fully active and listening for commands!")

        stop_event = asyncio.Event()
        
        def signal_handler():
            stop_event.set()

        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, signal_handler)

        await stop_event.wait()

    except Exception as e:
        print(f"❌ Critical error during execution: {e}")
    finally:
        print("🛑 Initiating graceful shutdown...")
        health_status["bot_active"] = False
        health_status["assistant_active"] = False
        health_status["pytgcalls_active"] = False

        if call_started:
            try:
                await call_py.stop()
            except Exception:
                pass
        if assistant_started:
            try:
                await assistant_client.stop()
            except Exception:
                pass
        if bot_started:
            try:
                await bot_client.stop()
            except Exception:
                pass
        print("👋 Shutdown complete. Goodbye!")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(0)
