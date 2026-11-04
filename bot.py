"""
NEXUS AI — Telegram community management starter
Python 3.11+ | python-telegram-bot 22.x | SQLite | Flask health endpoint

Important:
- Set BOT_TOKEN and OWNER_ID as environment variables.
- Add the bot to your group and grant only the admin permissions it needs.
- For message analytics, disable BotFather Privacy Mode (Bot Settings > Group Privacy)
  and ensure the bot can receive group messages. Telegram still does not provide
  arbitrary access to old messages.
"""
import os
import re
import asyncio
import time
import sqlite3
import logging
import threading
from datetime import datetime, timezone
from functools import wraps

from flask import Flask, jsonify
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup,
    BotCommand, BotCommandScopeDefault, BotCommandScopeChat,
)
from telegram.constants import ChatType, ParseMode
from telegram.error import TelegramError, BadRequest, Forbidden
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    ContextTypes, filters,
)

# ----------------------------- Configuration -----------------------------

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
OWNER_ID_RAW = os.getenv("OWNER_ID", "").strip()
DB_PATH = os.getenv("DB_PATH", "nexus.db")
PORT = int(os.getenv("PORT", "10000"))
WELCOME_ENABLED_DEFAULT = True
MAX_WARNINGS = 3
RATE_WINDOW_SECONDS = 8
RATE_MAX_MESSAGES = 7

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
)
log = logging.getLogger("nexus")

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN environment variable is missing.")
if not OWNER_ID_RAW.isdigit():
    raise RuntimeError("Set OWNER_ID to your numeric Telegram user ID.")
OWNER_ID = int(OWNER_ID_RAW)

# ----------------------------- Database ----------------------------------

db_lock = threading.RLock()

def db():
    conn = sqlite3.connect(DB_PATH, timeout=20, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with db_lock, db() as con:
        con.executescript("""
        CREATE TABLE IF NOT EXISTS groups (
            chat_id INTEGER PRIMARY KEY,
            title TEXT DEFAULT '',
            welcome_enabled INTEGER DEFAULT 1,
            rules TEXT DEFAULT 'Please be respectful and follow the group rules.',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS members (
            chat_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            username TEXT DEFAULT '',
            full_name TEXT DEFAULT '',
            messages INTEGER DEFAULT 0,
            warnings INTEGER DEFAULT 0,
            joined_at TEXT DEFAULT CURRENT_TIMESTAMP,
            last_seen TEXT DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY(chat_id, user_id)
        );
        CREATE TABLE IF NOT EXISTS audit (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER NOT NULL,
            actor_id INTEGER,
            action TEXT NOT NULL,
            target_id INTEGER,
            details TEXT DEFAULT '',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS rate_events (
            chat_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            event_ts REAL NOT NULL
        );
        """)
        # Safe schema migration for groups created by earlier NEXUS versions.
        cols = [r["name"] for r in con.execute("PRAGMA table_info(groups)").fetchall()]
        if "auto_delete_seconds" not in cols:
            con.execute("ALTER TABLE groups ADD COLUMN auto_delete_seconds INTEGER DEFAULT 8")
        con.commit()

def ensure_group(chat):
    with db_lock, db() as con:
        con.execute(
            "INSERT OR IGNORE INTO groups(chat_id,title) VALUES(?,?)",
            (chat.id, chat.title or "Telegram Group"),
        )
        con.execute("UPDATE groups SET title=? WHERE chat_id=?", (chat.title or "", chat.id))
        con.commit()

def touch_member(chat_id, user):
    if not user or user.is_bot:
        return
    with db_lock, db() as con:
        con.execute("""
            INSERT INTO members(chat_id,user_id,username,full_name,messages,last_seen)
            VALUES(?,?,?,?,0,CURRENT_TIMESTAMP)
            ON CONFLICT(chat_id,user_id) DO UPDATE SET
              username=excluded.username, full_name=excluded.full_name,
              last_seen=CURRENT_TIMESTAMP
        """, (chat_id, user.id, user.username or "", user.full_name or ""))
        con.commit()

def add_message(chat_id, user):
    touch_member(chat_id, user)
    with db_lock, db() as con:
        con.execute(
            "UPDATE members SET messages=messages+1,last_seen=CURRENT_TIMESTAMP WHERE chat_id=? AND user_id=?",
            (chat_id, user.id),
        )
        con.commit()

def audit(chat_id, actor_id, action, target_id=None, details=""):
    with db_lock, db() as con:
        con.execute(
            "INSERT INTO audit(chat_id,actor_id,action,target_id,details) VALUES(?,?,?,?,?)",
            (chat_id, actor_id, action, target_id, str(details)[:500]),
        )
        con.commit()

def get_group(chat_id):
    with db_lock, db() as con:
        return con.execute("SELECT * FROM groups WHERE chat_id=?", (chat_id,)).fetchone()

def get_member(chat_id, user_id):
    with db_lock, db() as con:
        return con.execute(
            "SELECT * FROM members WHERE chat_id=? AND user_id=?", (chat_id, user_id)
        ).fetchone()

def get_top(chat_id, limit=10):
    with db_lock, db() as con:
        return con.execute(
            "SELECT * FROM members WHERE chat_id=? ORDER BY messages DESC LIMIT ?",
            (chat_id, limit),
        ).fetchall()

def get_counts(chat_id):
    with db_lock, db() as con:
        total = con.execute("SELECT COUNT(*) n FROM members WHERE chat_id=?", (chat_id,)).fetchone()["n"]
        msgs = con.execute("SELECT COALESCE(SUM(messages),0) n FROM members WHERE chat_id=?", (chat_id,)).fetchone()["n"]
        return total, msgs

def is_owner(user_id):
    return bool(user_id and user_id == OWNER_ID)

async def is_group_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    chat = update.effective_chat
    user = update.effective_user
    if not chat or not user:
        return False
    if is_owner(user.id):
        return True
    if chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
        return False
    try:
        member = await context.bot.get_chat_member(chat.id, user.id)
        return member.status in ("administrator", "creator")
    except TelegramError:
        return False

async def require_group(update, context):
    chat = update.effective_chat
    if not chat or chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
        await update.effective_message.reply_text("Ye command group mein use karein.")
        return False
    ensure_group(chat)
    return True

async def require_admin(update, context):
    if not await require_group(update, context):
        return False
    if not await is_group_admin(update, context):
        await update.effective_message.reply_text("⛔ Ye feature sirf group admins ke liye hai.")
        return False
    return True

async def require_owner(update, context):
    if not update.effective_user or not is_owner(update.effective_user.id):
        await update.effective_message.reply_text("⛔ Ye control sirf NEXUS owner ke liye hai.")
        return False
    return True

def mention(user_id, name="Member"):
    safe = (name or "Member").replace("<", "").replace(">", "")
    return f'<a href="tg://user?id={user_id}">{safe}</a>'

def clean_text(value, limit=500):
    return (value or "").strip()[:limit]

async def send_temporary(reply_to, text, context, **kwargs):
    """Send a short-lived bot notice. The underlying database record remains."""
    sent = await reply_to.reply_text(text, **kwargs)
    chat = reply_to.chat
    seconds = 8
    if chat and chat.type in (ChatType.GROUP, ChatType.SUPERGROUP):
        row = get_group(chat.id)
        if row and row["auto_delete_seconds"] is not None:
            seconds = int(row["auto_delete_seconds"])
    if seconds > 0:
        async def delayed_delete():
            await asyncio.sleep(seconds)
            try:
                await context.bot.delete_message(chat_id=sent.chat_id, message_id=sent.message_id)
            except TelegramError:
                pass
        asyncio.create_task(delayed_delete())
    return sent

# ----------------------------- UI ----------------------------------------

def main_keyboard(is_admin=False, owner=False):
    rows = [
        [InlineKeyboardButton("👤 My Profile", callback_data="profile"),
         InlineKeyboardButton("🏆 Leaderboard", callback_data="top")],
        [InlineKeyboardButton("📊 Group Stats", callback_data="stats"),
         InlineKeyboardButton("📜 Rules", callback_data="rules")],
        [InlineKeyboardButton("📚 Help & Commands", callback_data="help")],
    ]
    if is_admin:
        rows.insert(2, [InlineKeyboardButton("🛡 Admin Center", callback_data="admin")])
    if owner:
        rows.append([InlineKeyboardButton("👑 Owner Center", callback_data="owner")])
    return InlineKeyboardMarkup(rows)

def back_keyboard():
    return InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Home", callback_data="home")]])

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat = update.effective_chat
    if chat and chat.type in (ChatType.GROUP, ChatType.SUPERGROUP):
        ensure_group(chat)
        touch_member(chat.id, user)
        admin = await is_group_admin(update, context)
        await update.effective_message.reply_text(
            "🌐 <b>NEXUS AI</b>\n"
            "<i>Community intelligence • Security • Management</i>\n\n"
            f"Welcome, {mention(user.id, user.first_name)}.\n"
            "Use the buttons below to explore available tools.",
            parse_mode=ParseMode.HTML,
            reply_markup=main_keyboard(admin, is_owner(user.id)),
        )
        return
    await update.effective_message.reply_text(
        "🌐 <b>NEXUS AI</b>\n"
        "<i>One group. One intelligence. Complete control.</i>\n\n"
        "Add me to a group and grant the permissions needed for the features you want. "
        "Use /help to see the command guide.",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("📚 Help & Commands", callback_data="help")]
        ]),
    )

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "📚 <b>NEXUS COMMAND GUIDE</b>\n\n"
        "<b>Members</b>\n"
        "/start — Open NEXUS home\n"
        "/help — Command guide\n"
        "/profile — Your group profile\n"
        "/rank — Your current rank\n"
        "/top — Top contributors\n"
        "/stats — Basic group statistics\n"
        "/rules — View group rules\n"
        "/report — Privately notify admins (reply to a message)\n\n"
        "<b>Group admins</b>\n"
        "/dashboard — Admin dashboard\n"
        "/warn — Warn a member (reply to their message)\n"
        "/warnings — Check warnings (reply to member)\n"
        "/clearwarns — Clear warnings (reply to member)\n"
        "/mute [minutes] — Restrict (reply to member; default 10)\n"
        "/unmute — Remove restriction (reply to member)\n"
        "/ban — Ban (reply to member)\n"
        "/unban USER_ID — Unban by numeric ID\n"
        "/setrules TEXT — Set group rules\n"
        "/welcome on|off — Toggle welcome messages\n"
        "/autodelete 8|off — Temporary notice cleanup\n"
        "/audit — Recent NEXUS actions\n\n"
        "<b>NEXUS owner</b>\n"
        "/owner — Owner controls\n"
        "/system — Runtime status\n\n"
        "Tip: For moderation commands, reply to the target member's message. "
        "NEXUS only tracks messages it receives after it is added and configured."
    )
    await update.effective_message.reply_text(text, parse_mode=ParseMode.HTML)

async def profile_cmd(update, context):
    if not await require_group(update, context): return
    user = update.effective_user
    touch_member(update.effective_chat.id, user)
    row = get_member(update.effective_chat.id, user.id)
    rank = "—"
    top = get_top(update.effective_chat.id, 100000)
    for i, item in enumerate(top, 1):
        if item["user_id"] == user.id:
            rank = str(i); break
    await update.effective_message.reply_text(
        f"👤 <b>MEMBER PROFILE</b>\n\n"
        f"Name: {mention(user.id, user.full_name)}\n"
        f"Messages counted: <b>{row['messages'] if row else 0}</b>\n"
        f"Group rank: <b>#{rank}</b>\n"
        f"Warnings: <b>{row['warnings'] if row else 0}</b>",
        parse_mode=ParseMode.HTML,
    )

async def rank_cmd(update, context):
    await profile_cmd(update, context)

async def top_cmd(update, context):
    if not await require_group(update, context): return
    rows = get_top(update.effective_chat.id, 10)
    lines = ["🏆 <b>NEXUS HALL OF FAME</b>", ""]
    if not rows:
        lines.append("Abhi ranking data available nahi hai.")
    else:
        medals = ["🥇", "🥈", "🥉"]
        for i, row in enumerate(rows, 1):
            badge = medals[i-1] if i <= 3 else f"{i}."
            name = row["full_name"] or row["username"] or "Member"
            lines.append(f"{badge} {mention(row['user_id'], name)} — <b>{row['messages']}</b>")
    await update.effective_message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)

async def stats_cmd(update, context):
    if not await require_group(update, context): return
    members, messages = get_counts(update.effective_chat.id)
    await update.effective_message.reply_text(
        "📊 <b>COMMUNITY SNAPSHOT</b>\n\n"
        f"Tracked members: <b>{members}</b>\n"
        f"Messages counted: <b>{messages}</b>\n"
        "Status: 🟢 Monitoring\n\n"
        "<i>Counts start when NEXUS can receive group messages.</i>",
        parse_mode=ParseMode.HTML,
    )

async def rules_cmd(update, context):
    if not await require_group(update, context): return
    row = get_group(update.effective_chat.id)
    await update.effective_message.reply_text("📜 <b>GROUP RULES</b>\n\n" + (row["rules"] if row else "Rules not set."),
                                              parse_mode=ParseMode.HTML)

async def dashboard_cmd(update, context):
    if not await require_admin(update, context): return
    members, messages = get_counts(update.effective_chat.id)
    await update.effective_message.reply_text(
        "🛡 <b>NEXUS ADMIN CENTER</b>\n\n"
        f"Tracked members: <b>{members}</b>\n"
        f"Messages counted: <b>{messages}</b>\n"
        "Use /help for moderation commands.\n"
        "Use the buttons for a compact dashboard.",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("📊 Statistics", callback_data="stats"),
             InlineKeyboardButton("🏆 Leaderboard", callback_data="top")],
            [InlineKeyboardButton("🧾 Audit Log", callback_data="audit")],
            [InlineKeyboardButton("⬅️ Home", callback_data="home")],
        ]),
    )

async def owner_cmd(update, context):
    if not await require_owner(update, context): return
    await update.effective_message.reply_text(
        "👑 <b>NEXUS OWNER CENTER</b>\n\n"
        "Owner-only controls:\n"
        "/system — runtime status\n"
        "/groups — connected groups (basic registry)\n"
        "Owner ID is configured through the OWNER_ID environment variable.\n\n"
        "For safety, ownership cannot be transferred through a public chat command.",
        parse_mode=ParseMode.HTML,
    )

async def system_cmd(update, context):
    if not await require_owner(update, context): return
    await update.effective_message.reply_text(
        f"🧬 <b>NEXUS SYSTEM</b>\n\n"
        f"Status: 🟢 Online\n"
        f"Python: {os.sys.version.split()[0]}\n"
        f"Database: SQLite\n"
        f"Uptime: {int(time.monotonic() - START_TIME)} seconds",
        parse_mode=ParseMode.HTML,
    )

async def groups_cmd(update, context):
    if not await require_owner(update, context): return
    with db_lock, db() as con:
        rows = con.execute("SELECT chat_id,title FROM groups ORDER BY title LIMIT 30").fetchall()
    if not rows:
        await update.effective_message.reply_text("Abhi koi group registry mein nahi hai.")
        return
    body = "\n".join(f"• {r['title'][:50]} — <code>{r['chat_id']}</code>" for r in rows)
    await update.effective_message.reply_text("🌍 <b>CONNECTED GROUPS</b>\n\n"+body, parse_mode=ParseMode.HTML)

# ----------------------------- Moderation --------------------------------

def replied_user(update):
    msg = update.effective_message
    return msg.reply_to_message.from_user if msg and msg.reply_to_message else None

async def warn_cmd(update, context):
    if not await require_admin(update, context): return
    target = replied_user(update)
    if not target or target.is_bot:
        await update.effective_message.reply_text("Kisi member ke message par reply karke /warn use karein.")
        return
    chat_id = update.effective_chat.id
    with db_lock, db() as con:
        con.execute("INSERT OR IGNORE INTO members(chat_id,user_id,full_name) VALUES(?,?,?)",
                    (chat_id, target.id, target.full_name))
        con.execute("UPDATE members SET warnings=warnings+1 WHERE chat_id=? AND user_id=?",
                    (chat_id, target.id))
        row = con.execute("SELECT warnings FROM members WHERE chat_id=? AND user_id=?",
                          (chat_id, target.id)).fetchone()
        con.commit()
    audit(chat_id, update.effective_user.id, "WARN", target.id, f"warnings={row['warnings']}")
    await update.effective_message.reply_text(
        f"⚠️ {mention(target.id, target.first_name)} ko warning di gayi.\n"
        f"Total warnings: <b>{row['warnings']}/{MAX_WARNINGS}</b>",
        parse_mode=ParseMode.HTML,
    )

async def warnings_cmd(update, context):
    if not await require_admin(update, context): return
    target = replied_user(update)
    if not target:
        await update.effective_message.reply_text("Member ke message par reply karke /warnings use karein.")
        return
    row = get_member(update.effective_chat.id, target.id)
    await update.effective_message.reply_text(
        f"⚠️ {mention(target.id, target.first_name)} ke warnings: <b>{row['warnings'] if row else 0}</b>",
        parse_mode=ParseMode.HTML,
    )

async def clearwarns_cmd(update, context):
    if not await require_admin(update, context): return
    target = replied_user(update)
    if not target:
        await update.effective_message.reply_text("Member ke message par reply karke /clearwarns use karein.")
        return
    with db_lock, db() as con:
        con.execute("UPDATE members SET warnings=0 WHERE chat_id=? AND user_id=?",
                    (update.effective_chat.id, target.id))
        con.commit()
    audit(update.effective_chat.id, update.effective_user.id, "CLEAR_WARNINGS", target.id)
    await update.effective_message.reply_text("✅ Warnings reset kar di gayi hain.")

async def mute_cmd(update, context):
    if not await require_admin(update, context): return
    target = replied_user(update)
    if not target or target.is_bot:
        await update.effective_message.reply_text("Member ke message par reply karke /mute [minutes] use karein.")
        return
    minutes = 10
    if context.args:
        try: minutes = max(1, min(int(context.args[0]), 10080))
        except ValueError:
            await update.effective_message.reply_text("Usage: /mute 10 (reply to member)")
            return
    try:
        from datetime import timedelta
        until = datetime.now(timezone.utc) + timedelta(minutes=minutes)
        await context.bot.restrict_chat_member(
            update.effective_chat.id, target.id,
            permissions={"can_send_messages": False},
            until_date=until,
        )
        audit(update.effective_chat.id, update.effective_user.id, "MUTE", target.id, f"{minutes}m")
        await send_temporary(update.effective_message,
                             f"🔇 {mention(target.id, target.first_name)} restricted for {minutes} minutes.",
                             context, parse_mode=ParseMode.HTML)
    except TelegramError as e:
        await update.effective_message.reply_text(f"Action failed. Check bot admin permissions. ({type(e).__name__})")

async def unmute_cmd(update, context):
    if not await require_admin(update, context): return
    target = replied_user(update)
    if not target:
        await update.effective_message.reply_text("Member ke message par reply karke /unmute use karein.")
        return
    try:
        await context.bot.restrict_chat_member(
            update.effective_chat.id, target.id,
            permissions={"can_send_messages": True, "can_send_audios": True,
                         "can_send_documents": True, "can_send_photos": True,
                         "can_send_videos": True, "can_send_video_notes": True,
                         "can_send_voice_notes": True, "can_send_polls": True,
                         "can_send_other_messages": True, "can_add_web_page_previews": True},
        )
        audit(update.effective_chat.id, update.effective_user.id, "UNMUTE", target.id)
        await send_temporary(update.effective_message, "🔊 Restriction removed.", context)
    except TelegramError as e:
        await update.effective_message.reply_text(f"Action failed. ({type(e).__name__})")

async def ban_cmd(update, context):
    if not await require_admin(update, context): return
    target = replied_user(update)
    if not target or target.is_bot:
        await update.effective_message.reply_text("Member ke message par reply karke /ban use karein.")
        return
    try:
        await context.bot.ban_chat_member(update.effective_chat.id, target.id)
        audit(update.effective_chat.id, update.effective_user.id, "BAN", target.id)
        await send_temporary(update.effective_message,
                             f"⛔ {mention(target.id, target.first_name)} removed from group.",
                             context, parse_mode=ParseMode.HTML)
    except TelegramError as e:
        await update.effective_message.reply_text(f"Action failed. Check permissions. ({type(e).__name__})")

async def unban_cmd(update, context):
    if not await require_admin(update, context): return
    if not context.args or not context.args[0].isdigit():
        await update.effective_message.reply_text("Usage: /unban USER_ID")
        return
    target_id = int(context.args[0])
    try:
        await context.bot.unban_chat_member(update.effective_chat.id, target_id, only_if_banned=True)
        audit(update.effective_chat.id, update.effective_user.id, "UNBAN", target_id)
        await update.effective_message.reply_text("✅ User unbanned.")
    except TelegramError as e:
        await update.effective_message.reply_text(f"Action failed. ({type(e).__name__})")

async def setrules_cmd(update, context):
    if not await require_admin(update, context): return
    rules = clean_text(" ".join(context.args), 1000)
    if not rules:
        await update.effective_message.reply_text("Usage: /setrules Be respectful...")
        return
    with db_lock, db() as con:
        con.execute("UPDATE groups SET rules=? WHERE chat_id=?", (rules, update.effective_chat.id))
        con.commit()
    audit(update.effective_chat.id, update.effective_user.id, "SET_RULES")
    await update.effective_message.reply_text("✅ Group rules updated.")

async def welcome_cmd(update, context):
    if not await require_admin(update, context): return
    if not context.args or context.args[0].lower() not in ("on", "off"):
        await update.effective_message.reply_text("Usage: /welcome on  OR  /welcome off")
        return
    enabled = 1 if context.args[0].lower() == "on" else 0
    with db_lock, db() as con:
        con.execute("UPDATE groups SET welcome_enabled=? WHERE chat_id=?", (enabled, update.effective_chat.id))
        con.commit()
    audit(update.effective_chat.id, update.effective_user.id, "WELCOME", details=context.args[0].lower())
    await update.effective_message.reply_text(f"✅ Welcome messages {context.args[0].lower()}.")

async def audit_cmd(update, context):
    if not await require_admin(update, context): return
    with db_lock, db() as con:
        rows = con.execute(
            "SELECT actor_id,action,target_id,details,created_at FROM audit WHERE chat_id=? ORDER BY id DESC LIMIT 10",
            (update.effective_chat.id,),
        ).fetchall()
    if not rows:
        await update.effective_message.reply_text("Abhi koi NEXUS audit record nahi hai.")
        return
    lines = ["🧾 <b>RECENT NEXUS ACTIONS</b>", ""]
    for r in rows:
        lines.append(f"• <b>{r['action']}</b> | actor <code>{r['actor_id']}</code> | target <code>{r['target_id'] or '—'}</code>")
    await update.effective_message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)

async def autodelete_cmd(update, context):
    if not await require_admin(update, context): return
    if not context.args:
        row = get_group(update.effective_chat.id)
        current = row["auto_delete_seconds"] if row else 8
        state = "OFF (messages stay)" if not current else f"{current} seconds"
        await update.effective_message.reply_text(
            f"🧹 Temporary notice auto-delete: <b>{state}</b>\\n"
            "Usage: /autodelete 8  |  /autodelete 15  |  /autodelete off",
            parse_mode=ParseMode.HTML,
        )
        return
    raw = context.args[0].lower()
    if raw == "off":
        seconds = 0
    else:
        try:
            seconds = int(raw)
            if seconds < 3 or seconds > 300:
                raise ValueError
        except ValueError:
            await update.effective_message.reply_text("Use /autodelete 3-300 or /autodelete off.")
            return
    with db_lock, db() as con:
        con.execute("UPDATE groups SET auto_delete_seconds=? WHERE chat_id=?",
                    (seconds, update.effective_chat.id))
        con.commit()
    audit(update.effective_chat.id, update.effective_user.id, "AUTO_DELETE", details=str(seconds))
    label = "OFF" if seconds == 0 else f"{seconds} seconds"
    await update.effective_message.reply_text(
        f"✅ Temporary notice auto-delete set to <b>{label}</b>.\\n"
        "Important dashboards and reports are designed to remain; short moderation confirmations disappear.",
        parse_mode=ParseMode.HTML,
    )

async def report_cmd(update, context):
    if not await require_group(update, context): return
    msg = update.effective_message
    if not msg.reply_to_message:
        await msg.reply_text("Jis message ki report karni hai, us par reply karke /report likhein.")
        return
    target = msg.reply_to_message.from_user
    audit(update.effective_chat.id, update.effective_user.id, "MEMBER_REPORT", target.id if target else None,
          clean_text(msg.reply_to_message.text or msg.reply_to_message.caption or "media", 200))
    # Notify group admins through a quiet, compact message in the group.
    await msg.reply_text("🚩 Report admins ke review ke liye record kar di gayi.")

# ----------------------------- Callbacks ---------------------------------

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    chat = q.message.chat if q.message else None
    user = q.from_user
    if q.data == "home":
        admin = False
        if chat and chat.type in (ChatType.GROUP, ChatType.SUPERGROUP):
            try:
                cm = await context.bot.get_chat_member(chat.id, user.id)
                admin = cm.status in ("administrator", "creator") or is_owner(user.id)
            except TelegramError:
                pass
        await q.edit_message_text(
            "🌐 <b>NEXUS AI</b>\n<i>Community intelligence • Security • Management</i>\n\nChoose a department:",
            parse_mode=ParseMode.HTML,
            reply_markup=main_keyboard(admin, is_owner(user.id)),
        )
    elif q.data == "help":
        await q.edit_message_text(
            "📚 <b>HELP CENTER</b>\n\n"
            "Members: /profile /rank /top /stats /rules /report\n"
            "Admins: /dashboard /warn /mute /unmute /ban /unban /audit /setrules /welcome\n"
            "Owner: /owner /system /groups\n\n"
            "Use /help in a chat to see full usage notes.",
            parse_mode=ParseMode.HTML, reply_markup=back_keyboard(),
        )
    elif q.data == "profile":
        if not chat or chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
            await q.edit_message_text("Profile is available inside a group.", reply_markup=back_keyboard()); return
        touch_member(chat.id, user)
        row = get_member(chat.id, user.id)
        await q.edit_message_text(
            f"👤 <b>YOUR PROFILE</b>\n\nName: {user.full_name}\nMessages counted: <b>{row['messages'] if row else 0}</b>\nWarnings: <b>{row['warnings'] if row else 0}</b>",
            parse_mode=ParseMode.HTML, reply_markup=back_keyboard(),
        )
    elif q.data in ("top", "stats", "rules"):
        if not chat or chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
            await q.edit_message_text("Open this panel from a group.", reply_markup=back_keyboard()); return
        if q.data == "top":
            rows = get_top(chat.id, 10)
            body = "\n".join(f"{i}. {r['full_name'] or 'Member'} — {r['messages']}" for i, r in enumerate(rows, 1)) or "No ranking data yet."
            title = "🏆 <b>HALL OF FAME</b>"
        elif q.data == "stats":
            members, messages = get_counts(chat.id)
            body = f"Tracked members: <b>{members}</b>\nMessages counted: <b>{messages}</b>"
            title = "📊 <b>COMMUNITY SNAPSHOT</b>"
        else:
            row = get_group(chat.id)
            body = row["rules"] if row else "Rules not set."
            title = "📜 <b>GROUP RULES</b>"
        await q.edit_message_text(title + "\n\n" + body, parse_mode=ParseMode.HTML, reply_markup=back_keyboard())
    elif q.data == "admin":
        if not chat or not chat.type in (ChatType.GROUP, ChatType.SUPERGROUP):
            await q.edit_message_text("Admin center is available in a group.", reply_markup=back_keyboard()); return
        try:
            cm = await context.bot.get_chat_member(chat.id, user.id)
            allowed = cm.status in ("administrator", "creator") or is_owner(user.id)
        except TelegramError:
            allowed = False
        if not allowed:
            await q.edit_message_text("⛔ Admin access required.", reply_markup=back_keyboard()); return
        await q.edit_message_text(
            "🛡 <b>ADMIN CENTER</b>\n\n/dashboard — Overview\n/audit — Recent NEXUS actions\n/warn, /mute, /unmute, /ban — Moderation\n/setrules — Update rules\n/welcome on|off — Welcome control",
            parse_mode=ParseMode.HTML, reply_markup=back_keyboard(),
        )
    elif q.data == "owner":
        if not is_owner(user.id):
            await q.edit_message_text("⛔ Owner access required.", reply_markup=back_keyboard()); return
        await q.edit_message_text(
            "👑 <b>OWNER CENTER</b>\n\n/system — Runtime status\n/groups — Registered groups\nOwner ID is protected by the OWNER_ID environment variable.",
            parse_mode=ParseMode.HTML, reply_markup=back_keyboard(),
        )
    elif q.data == "audit":
        if not chat or chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
            await q.edit_message_text("Open this panel from a group.", reply_markup=back_keyboard()); return
        try:
            cm = await context.bot.get_chat_member(chat.id, user.id)
            allowed = cm.status in ("administrator", "creator") or is_owner(user.id)
        except TelegramError:
            allowed = False
        if not allowed:
            await q.edit_message_text("⛔ Admin access required.", reply_markup=back_keyboard()); return
        with db_lock, db() as con:
            rows = con.execute("SELECT action,actor_id,target_id FROM audit WHERE chat_id=? ORDER BY id DESC LIMIT 8", (chat.id,)).fetchall()
        body = "\n".join(f"• {r['action']} | {r['actor_id']} → {r['target_id'] or '—'}" for r in rows) or "No records yet."
        await q.edit_message_text("🧾 <b>AUDIT CENTER</b>\n\n"+body, parse_mode=ParseMode.HTML, reply_markup=back_keyboard())

# ----------------------------- Events ------------------------------------

async def on_text_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    chat = update.effective_chat
    user = update.effective_user
    if not msg or not chat or chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP) or not user or user.is_bot:
        return
    ensure_group(chat)
    add_message(chat.id, user)

    # Basic flood signal only; no automatic punishment to avoid false positives.
    now = time.time()
    with db_lock, db() as con:
        con.execute("DELETE FROM rate_events WHERE event_ts < ?", (now - RATE_WINDOW_SECONDS,))
        con.execute("INSERT INTO rate_events(chat_id,user_id,event_ts) VALUES(?,?,?)", (chat.id, user.id, now))
        count = con.execute(
            "SELECT COUNT(*) n FROM rate_events WHERE chat_id=? AND user_id=? AND event_ts>=?",
            (chat.id, user.id, now - RATE_WINDOW_SECONDS),
        ).fetchone()["n"]
        con.commit()
    if count == RATE_MAX_MESSAGES + 1:
        audit(chat.id, None, "FLOOD_SIGNAL", user.id, f"{count} messages/{RATE_WINDOW_SECONDS}s")

async def new_members(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    if not chat or chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP): return
    ensure_group(chat)
    row = get_group(chat.id)
    for user in update.effective_message.new_chat_members:
        touch_member(chat.id, user)
        if row and row["welcome_enabled"]:
            await update.effective_message.reply_text(
                f"Welcome to <b>{clean_text(chat.title, 80)}</b>, {mention(user.id, user.first_name)}! 🌟\n"
                "Please read /rules and keep the community respectful.",
                parse_mode=ParseMode.HTML,
            )

# ----------------------------- App setup ---------------------------------

START_TIME = time.monotonic()

async def post_init(application: Application):
    # A compact command menu; command access is still enforced in handlers.
    commands = [
        BotCommand("start", "Open NEXUS home"),
        BotCommand("help", "Help and command guide"),
        BotCommand("profile", "Your member profile"),
        BotCommand("rank", "Your current rank"),
        BotCommand("top", "Community leaderboard"),
        BotCommand("stats", "Group statistics"),
        BotCommand("rules", "View group rules"),
        BotCommand("report", "Report a message"),
        BotCommand("dashboard", "Admin dashboard"),
        BotCommand("owner", "Owner center"),
    ]
    await application.bot.set_my_commands(commands, scope=BotCommandScopeDefault())

def build_app():
    application = Application.builder().token(BOT_TOKEN).post_init(post_init).build()

    handlers = [
        CommandHandler("start", start),
        CommandHandler("help", help_cmd),
        CommandHandler("profile", profile_cmd),
        CommandHandler("rank", rank_cmd),
        CommandHandler("top", top_cmd),
        CommandHandler("stats", stats_cmd),
        CommandHandler("rules", rules_cmd),
        CommandHandler("dashboard", dashboard_cmd),
        CommandHandler("owner", owner_cmd),
        CommandHandler("system", system_cmd),
        CommandHandler("groups", groups_cmd),
        CommandHandler("warn", warn_cmd),
        CommandHandler("warnings", warnings_cmd),
        CommandHandler("clearwarns", clearwarns_cmd),
        CommandHandler("mute", mute_cmd),
        CommandHandler("unmute", unmute_cmd),
        CommandHandler("ban", ban_cmd),
        CommandHandler("unban", unban_cmd),
        CommandHandler("setrules", setrules_cmd),
        CommandHandler("welcome", welcome_cmd),
        CommandHandler("autodelete", autodelete_cmd),
        CommandHandler("audit", audit_cmd),
        CommandHandler("report", report_cmd),
        CallbackQueryHandler(callback_handler),
        MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, new_members),
        MessageHandler(filters.ALL & ~filters.COMMAND, on_text_message),
    ]
    for handler in handlers:
        application.add_handler(handler)
    return application

# Minimal HTTP endpoint for Render Web Service health checks.
web = Flask(__name__)

@web.get("/")
def root():
    return jsonify({"service": "NEXUS AI", "status": "online"})

@web.get("/health")
def health():
    return jsonify({"status": "ok", "service": "nexus-ai"})

def run_web():
    web.run(host="0.0.0.0", port=PORT, use_reloader=False)

def main():
    init_db()
    threading.Thread(target=run_web, daemon=True).start()
    app = build_app()
    log.info("NEXUS AI starting...")
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)

if __name__ == "__main__":
    main()
