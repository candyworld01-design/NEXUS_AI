import asyncio
import threading
import logging

from flask import Flask
from pyrogram.errors import FloodWait

import music
from config import PORT

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)


@app.route("/")
def home():
    return "NEXUS Music Bot is Online!", 200


@app.route("/health")
def health():
    return {
        "status": "healthy",
        "bot": "running"
    }, 200


def run_web_server():
    app.run(
        host="0.0.0.0",
        port=PORT,
        debug=False,
        use_reloader=False
    )


async def start_client(client, name):

    while True:
        try:
            logger.info("Starting %s...", name)

            await client.start()

            logger.info("%s started successfully!", name)
            return

        except FloodWait as e:

            wait_time = int(e.value) + 5

            logger.warning(
                "%s: FloodWait. Waiting %s seconds.",
                name,
                wait_time
            )

            await asyncio.sleep(wait_time)


async def main():

    threading.Thread(
        target=run_web_server,
        daemon=True
    ).start()

    # Start Telegram bot
    await start_client(
        music.bot_client,
        "Telegram Bot"
    )

    # Start assistant account
    await start_client(
        music.assistant_client,
        "Assistant Account"
    )

    # Initialize everything inside the SAME event loop
    logger.info("Initializing music runtime...")

    music.initialize_runtime()

    logger.info("Starting PyTgCalls...")

    await music.call_py.start()

    logger.info("PyTgCalls started successfully!")
    logger.info("NEXUS Music is Running!")

    await asyncio.Event().wait()


if __name__ == "__main__":

    asyncio.run(main())
