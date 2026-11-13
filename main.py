import asyncio
import threading
import logging

from flask import Flask

from config import PORT

from music import (
    bot_client,
    assistant_client,
    call_py
)


logging.basicConfig(
    level=logging.INFO
)

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


async def main():

    threading.Thread(
        target=run_web_server,
        daemon=True
    ).start()

    logger.info("Starting Telegram Bot...")

    await bot_client.start()

    logger.info("Telegram Bot Started Successfully!")

    logger.info("Starting Assistant Account...")

    await assistant_client.start()

    logger.info("Assistant Account Started Successfully!")

    logger.info("Starting PyTgCalls...")

    await call_py.start()

    logger.info("PyTgCalls Started Successfully!")

    logger.info("NEXUS Music is Running!")

    await asyncio.Event().wait()


if __name__ == "__main__":

    asyncio.run(main())
