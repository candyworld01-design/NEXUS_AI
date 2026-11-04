NEXUS AI — COMPLETE STARTER PACKAGE
===================================

This package contains:
- bot.py
- requirements.txt
- README.txt

This is a feature-rich, single-file Telegram group-management foundation. It is designed
to be quiet in the group: important dashboards stay visible, while selected short
moderation confirmations (warning, mute, unmute, ban, report receipt) auto-delete.
The underlying warning/audit records remain in the database after the visible notice
disappears.

IMPORTANT SCOPE NOTE
--------------------
This is a complete first deployable foundation, not a claim that every proposed future
feature (full AI reasoning, web control panel, advanced anti-scam classifier, multi-group
visual management, or 100+ commands) is already implemented. Those require additional
services, API keys, privacy decisions, and testing. Do not advertise unimplemented
features to your group as active.

1. TELEGRAM BOT CREATION
------------------------
1. Open @BotFather and run /newbot.
2. Save the token privately. Never upload it to GitHub or share it in a group.
3. Get your numeric Telegram user ID from a trusted ID utility.
4. In BotFather > Bot Settings > Group Privacy, turn Privacy Mode OFF if you want
   NEXUS to count ordinary group messages. Remove and re-add the bot if Telegram asks.
5. Add NEXUS to your test group as an administrator. Grant only the rights it needs:
   - Delete messages (for temporary notices / moderation)
   - Restrict members (mute)
   - Ban users (ban/unban)
6. Test in a private group before adding it to your main community.

Telegram limits apply: a bot cannot read arbitrary old chat history. It can only process
updates it receives. Privacy Mode and admin rights affect what it can see and do.

2. GITHUB
---------
Create a repository and upload only these three files:
- bot.py
- requirements.txt
- README.txt

Never upload BOT_TOKEN, OWNER_ID secrets, .env files, or the live SQLite database.

3. RENDER WEB SERVICE
---------------------
Create a Web Service connected to the repository.

Build Command:
    pip install -r requirements.txt

Start Command:
    python bot.py

Health Check Path:
    /health

Environment Variables:
    BOT_TOKEN = your BotFather token
    OWNER_ID  = your numeric Telegram user ID
    DB_PATH   = nexus.db       (optional)
    LOG_LEVEL = INFO           (optional)

The app exposes:
    /
    /health

Hosting note: free hosting can have monthly usage caps, sleeping/suspension, and ephemeral
storage. A SQLite database on an ephemeral filesystem may be lost on redeploy/restart.
For reliable persistence, attach a persistent disk or migrate to PostgreSQL and back it
up. Free 24/7 operation is not guaranteed.

4. COMMANDS
-----------

MEMBERS:
    /start       Open NEXUS home
    /help        Full command guide
    /profile     Your member profile
    /rank        Your profile/rank
    /top         Top contributors
    /stats       Tracked group statistics
    /rules       View group rules
    /report      Reply to a message to report it

GROUP ADMINS (use reply-to target where indicated):
    /dashboard       Admin overview
    /warn            Reply to a member message
    /warnings        Reply to a member message
    /clearwarns      Reply to a member message
    /mute            Reply to a member message; default 10 minutes
    /mute 15         Restrict for 15 minutes
    /unmute          Reply to a member message
    /ban             Reply to a member message
    /unban USER_ID   Unban by numeric Telegram user ID
    /setrules TEXT   Set group rules
    /welcome on      Enable welcome messages
    /welcome off     Disable welcome messages
    /autodelete      Show current temporary-notice deletion setting
    /autodelete 8    Delete supported short notices after 8 seconds
    /autodelete 15   Delete supported short notices after 15 seconds
    /autodelete off  Keep supported short notices
    /audit           Recent NEXUS audit records

NEXUS OWNER:
    /owner       Owner controls guide
    /system      Runtime status
    /groups      Registered groups

5. MESSAGE CLEANUP — HOW IT WORKS
---------------------------------
Default temporary-notice cleanup is 8 seconds.

The following short confirmations are auto-deleted:
- Warning confirmation
- Mute/unmute confirmation
- Ban confirmation
- Report receipt

The actual warning count and audit record remain saved in SQLite. Deleting the visible
warning notice does NOT cancel the warning.

Persistent by design:
- /start dashboard
- Help and command guides
- Group rules
- Main statistics / leaderboard responses
- Owner and admin dashboards

Set a different duration as a group admin:
    /autodelete 5    (minimum accepted: 3 seconds)
    /autodelete 30
    /autodelete off

The bot needs Telegram's Delete Messages administrator permission to delete its own
group messages reliably. If it lacks permission, the deletion attempt is silently
ignored and the notice may remain. Telegram may also reject deletion in some situations.

6. PERMISSION MODEL
-------------------
Members:
- Basic profile, rank, top, stats, rules, help, report.

Telegram group admins:
- Moderation, group rules, welcome setting, auto-delete setting, audit and dashboard.

NEXUS owner:
- Owner controls, system status and connected group list.
- Owner identity is fixed by OWNER_ID environment variable.
- Being a Telegram group admin does not automatically make someone NEXUS owner.
- There is deliberately no public /setowner command.

7. TEST BEFORE LAUNCH
---------------------
Use a private test group with consenting test accounts:
1. Verify /start and /help.
2. Verify member commands as a normal member.
3. Verify admin commands are denied to normal members.
4. Verify owner commands are denied to non-owner accounts.
5. Test /warn by replying to a test account's message.
6. Confirm the warning notice disappears after the configured delay while the warning
   count remains stored.
7. Test /mute 1 and /unmute.
8. Test /ban and /unban USER_ID.
9. Test /autodelete 15 and /autodelete off.
10. Check Render /health and confirm database persistence after restart.

Never test moderation against real community members. Do not give the bot unnecessary
permissions. Keep a backup before production deployment.

8. KNOWN LIMITATIONS
--------------------
- Message analytics start only after the bot can receive messages; no historical backfill.
- The leaderboard is based on raw message count, not message quality.
- The flood detector records a signal only; it does not automatically punish anyone.
- Warning counts are stored but there is no automatic ban threshold.
- /report currently records a report in the audit database and confirms it in the group;
  it does not yet create a private admin ticket.
- AI summaries, smart scam detection, full web dashboard, custom role editor, scheduled
  reports, advanced multi-group owner controls and 15 complete departments are not part
  of this package.
- SQLite is suitable for a small starter. For scale/multiple instances, use PostgreSQL.
- Bot API permissions, privacy mode, rate limits and Telegram platform rules apply.

9. SECURITY
-----------
- Never share the bot token.
- If exposed, revoke it immediately using @BotFather.
- Keep OWNER_ID and BOT_TOKEN in Render Environment Variables only.
- Never commit database files or private group exports to GitHub.
- Review audit logs and keep backups.
