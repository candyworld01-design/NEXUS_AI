# NEXUS AI — Telegram Group Operating System

This release is a tested starting foundation, not a claim that all 15 proposed departments are fully implemented. It includes multi-group isolation, group-owner/admin/member role detection, basic moderation, reports, rules, welcome toggle, group analytics, audit records, permission grant storage, dashboard navigation, and a Render health endpoint.

## Files
- `bot.py` — Telegram bot and handlers
- `requirements.txt` — Python dependencies
- `render.yaml` — Render Web Service blueprint
- `.env.example` — environment variable template

## Render setup
- Runtime: Python
- Build: `pip install -r requirements.txt`
- Start: `python bot.py`
- Required env: `BOT_TOKEN`
- Recommended env: `OWNER_ID` (your numeric Telegram ID; optional, only enables global Super Owner commands)
- `DB_PATH=nexus.db`

## Telegram setup
1. Create the bot with BotFather and set `BOT_TOKEN` in Render.
2. Add it to a test supergroup and promote it with only the permissions required for moderation (delete messages, restrict members, ban users; promote admins is not required in this release).
3. Disable BotFather privacy mode only if you need broad message counting; otherwise Telegram privacy mode limits which group messages the bot receives.
4. Test `/nexus`, `/help`, `/warn` (reply), `/mute` (reply), `/ban` (reply), `/rules`, `/stats`, `/audit`.

## Role model
- Telegram group creator is the group Owner.
- Telegram administrators are operational Admins.
- Members use public commands.
- `OWNER_ID` is the optional global Super Owner identity. It does not replace group-owner detection and does not make other groups private.
- The current permission-grant commands store grants for selected advanced modules; ordinary Telegram admins can use core moderation commands. Full per-admin deny/allow policy management is not yet implemented.

## Important limitations before public launch
- SQLite on Render Free's ephemeral filesystem can be lost on redeploy/replacement. Use a persistent disk or managed PostgreSQL for durable production data; availability/pricing depend on your Render plan.
- Render free services may sleep/suspend and have usage limits; this cannot be guaranteed to run 24/7 for free.
- AI conversations, semantic knowledge search, advanced analytics, scheduled broadcasts, anti-raid automation, report-resolution workflow, full custom role editor, and all 15 department features are planned but not implemented in this foundation. No fake AI responses are generated.
- Telegram only exposes messages the bot receives after it joins; it cannot fetch arbitrary historical group messages.
- Bot admin permissions and Telegram hierarchy determine whether moderation actions succeed. The bot cannot moderate the group creator or admins above its own authority.

## Local syntax check
`python -m py_compile bot.py`
