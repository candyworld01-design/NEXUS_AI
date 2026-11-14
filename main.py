import asyncio
import logging

import music
import health
from pyrogram.errors import FloodWait

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("nexus")


async def start_client(client, name):
    while True:
        try:
            logger.info("Starting %s...", name)
            await client.start()
            logger.info("%s started.", name)
            return
        except FloodWait as exc:
            delay = int(exc.value) + 5
            logger.warning("%s FloodWait: sleeping %s seconds", name, delay)
            await asyncio.sleep(delay)


async def main():
    health.start_health_server()

    # Start both Telegram sessions concurrently instead of waiting for one
    # connection/login cycle to finish before beginning the other.
    await asyncio.gather(
        start_client(music.bot_client, "Telegram bot"),
        start_client(music.assistant_client, "Assistant account"),
    )
    health.set_component("bot_active", True)
    health.set_component("assistant_active", True)

    music.initialize_runtime()

    logger.info("Starting PyTgCalls...")
    await music.call_py.start()
    health.set_component("pytgcalls_active", True)

    logger.info("NEXUS Music 2.1 is online.")
    await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(main())
