import asyncio
import threading
import logging

from flask import Flask
from pyrogram.errors import FloodWait

from config import PORT
from music import (
    bot_client,
    assistant_client,
    call_py
)

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
            logger.info(f"Starting {name}...")
            await client.start()
            logger.info(f"{name} started successfully!")
            return

        except FloodWait as e:
            wait_time = int(e.value) + 5

            logger.warning(
                f"{name}: Telegram FloodWait. "
                f"Waiting {wait_time} seconds."
            )

            await asyncio.sleep(wait_time)


async def main():

    threading.Thread(
        target=run_web_server,
        daemon=True
    ).start()

    await start_client(bot_client, "Telegram Bot")

    await start_client(
        assistant_client,
        "Assistant Account"
    )

    logger.info("Starting PyTgCalls...")

    await call_py.start()

    logger.info("PyTgCalls started successfully!")
    logger.info("NEXUS Music is Running!")

    await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(main())
