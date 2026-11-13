import os

API_ID = int(os.getenv("API_ID", "0"))
API_HASH = os.getenv("API_HASH", "").strip()

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
STRING_SESSION = os.getenv("STRING_SESSION", "").strip()

OWNER_ID = int(os.getenv("OWNER_ID", "0"))

PORT = int(os.getenv("PORT", "8080"))


if not API_ID:
    raise RuntimeError("API_ID is missing!")

if not API_HASH:
    raise RuntimeError("API_HASH is missing!")

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is missing!")

if not STRING_SESSION:
    raise RuntimeError("STRING_SESSION is missing!")
