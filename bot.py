"""NEXUS AI - multi-group Telegram community management foundation.
Python 3.11+, python-telegram-bot 22.x, Flask health endpoint, SQLite.
Each group has isolated settings, custom admin grants, member stats and audit trail.
"""
import os, time, sqlite3, logging, threading, html, asyncio
from datetime import datetime, timedelta, timezone
from flask import Flask, jsonify
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand
from telegram.constants import ChatType, ParseMode
from telegram.error import TelegramError, BadRequest
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters

TOKEN=os.getenv('BOT_TOKEN','').strip(); DB_PATH=os.getenv('DB_PATH','nexus.db'); PORT=int(os.getenv('PORT','10000'))
GLOBAL_OWNER_RAW=os.getenv('OWNER_ID','').strip(); GLOBAL_OWNER=int(GLOBAL_OWNER_RAW) if GLOBAL_OWNER_RAW.isdigit() else None
MAX_WARNINGS=int(os.getenv('MAX_WARNINGS','3')); FLOOD_COUNT=int(os.getenv('FLOOD_COUNT','8')); FLOOD_WINDOW=int(os.getenv('FLOOD_WINDOW','10'))
logging.basicConfig(level=os.getenv('LOG_LEVEL','INFO'),format='%(asctime)s %(levelname)s %(name)s: %(message)s')
log=logging.getLogger('nexus'); lock=threading.RLock(); started=time.monotonic(); web=Flask(__name__)

SCHEMA='''
CREATE TABLE IF NOT EXISTS groups(chat_id INTEGER PRIMARY KEY,title TEXT NOT NULL DEFAULT '',rules TEXT NOT NULL DEFAULT 'Be respectful. Follow Telegram rules.',welcome INTEGER NOT NULL DEFAULT 1,antiflood INTEGER NOT NULL DEFAULT 1,links INTEGER NOT NULL DEFAULT 0,autodelete INTEGER NOT NULL DEFAULT 8,created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS members(chat_id INTEGER NOT NULL,user_id INTEGER NOT NULL,name TEXT DEFAULT '',username TEXT DEFAULT '',messages INTEGER NOT NULL DEFAULT 0,warnings INTEGER NOT NULL DEFAULT 0,first_seen TEXT DEFAULT CURRENT_TIMESTAMP,last_seen TEXT DEFAULT CURRENT_TIMESTAMP,PRIMARY KEY(chat_id,user_id));
CREATE TABLE IF NOT EXISTS grants(chat_id INTEGER NOT NULL,user_id INTEGER NOT NULL,permission TEXT NOT NULL,granted_by INTEGER NOT NULL,PRIMARY KEY(chat_id,user_id,permission));
CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY AUTOINCREMENT,chat_id INTEGER NOT NULL,actor INTEGER,action TEXT NOT NULL,target INTEGER,details TEXT DEFAULT '',created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS reports(id INTEGER PRIMARY KEY AUTOINCREMENT,chat_id INTEGER NOT NULL,reporter INTEGER NOT NULL,target INTEGER,details TEXT DEFAULT '',status TEXT DEFAULT 'OPEN',created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS flood(chat_id INTEGER,user_id INTEGER,ts REAL);
'''
def conn():
    c=sqlite3.connect(DB_PATH,timeout=30,check_same_thread=False); c.row_factory=sqlite3.Row; return c
def init_db():
    with lock,conn() as c: c.executescript(SCHEMA); c.commit()
def group_ok(chat): return bool(chat and chat.type in (ChatType.GROUP,ChatType.SUPERGROUP))
def ensure_group(chat):
    with lock,conn() as c:
        c.execute('INSERT INTO groups(chat_id,title) VALUES(?,?) ON CONFLICT(chat_id) DO UPDATE SET title=excluded.title',(chat.id,chat.title or 'Telegram Group')); c.commit()
def get_group(cid):
    with lock,conn() as c:return c.execute('SELECT * FROM groups WHERE chat_id=?',(cid,)).fetchone()
def log_action(cid,actor,action,target=None,details=''):
    with lock,conn() as c:c.execute('INSERT INTO audit(chat_id,actor,action,target,details) VALUES(?,?,?,?,?)',(cid,actor,action,target,str(details)[:400]));c.commit()
def esc(s):return html.escape(str(s or ''))
def mention(uid,name):return f'<a href="tg://user?id={int(uid)}">{esc(name)}</a>'
def is_global(uid):return bool(GLOBAL_OWNER and uid==GLOBAL_OWNER)
async def tg_status(ctx,cid,uid):
    try:return (await ctx.bot.get_chat_member(cid,uid)).status
    except TelegramError:return 'unknown'
async def role(update,ctx):
    chat=update.effective_chat; user=update.effective_user
    if not group_ok(chat) or not user:return 'member'
    if is_global(user.id):return 'superowner'
    status=await tg_status(ctx,chat.id,user.id)
    if status=='creator':return 'owner'
    if status=='administrator':return 'admin'
    return 'member'
async def allowed(update,ctx,perm=None,owner_only=False):
    chat=update.effective_chat; user=update.effective_user
    if not group_ok(chat):
        await update.effective_message.reply_text('Ye feature Telegram group/supergroup mein use karein.');return False
    ensure_group(chat); r=await role(update,ctx)
    if r in ('superowner','owner'):return True
    if owner_only:
        await update.effective_message.reply_text('🔒 Sirf is group ka creator/owner ye setting badal sakta hai.');return False
    if r=='admin':
        if not perm:return True
        with lock,conn() as c: row=c.execute('SELECT 1 FROM grants WHERE chat_id=? AND user_id=? AND permission=?',(chat.id,user.id,perm)).fetchone()
        # Telegram admins receive routine operations by default; owner can explicitly deny only by not granting custom-only tools.
        if perm in {'ban','mute','warn','reports','stats','audit','welcome','rules','automation','ai'}: return True
        if row:return True
    await update.effective_message.reply_text('⛔ Is action ke liye permission nahi hai.');return False
async def admin_actor(update,ctx):
    return (await role(update,ctx)) in ('superowner','owner','admin')
def keyboard(kind):
    rows=[[InlineKeyboardButton('🛡 Guardian',callback_data='dept:guardian'),InlineKeyboardButton('👥 Members',callback_data='dept:members')],
    [InlineKeyboardButton('🧠 Intelligence',callback_data='dept:ai'),InlineKeyboardButton('📊 Analytics',callback_data='dept:analytics')],
    [InlineKeyboardButton('🏆 Hall of Fame',callback_data='dept:hall'),InlineKeyboardButton('📋 Audit',callback_data='dept:audit')],
    [InlineKeyboardButton('📚 Knowledge & Rules',callback_data='dept:knowledge'),InlineKeyboardButton('🗣 Community Voice',callback_data='dept:voice')]]
    if kind in ('superowner','owner','admin'):
        rows += [[InlineKeyboardButton('⚙️ Admin Operations',callback_data='dept:admin'),InlineKeyboardButton('🤖 Automation',callback_data='dept:auto')]]
    if kind in ('superowner','owner'):
        rows += [[InlineKeyboardButton('🎛 Owner Control',callback_data='dept:control')]]
    rows += [[InlineKeyboardButton('📖 Commands',callback_data='help')]]
    return InlineKeyboardMarkup(rows)
async def start(update,ctx):
    chat=update.effective_chat; u=update.effective_user
    if group_ok(chat):
        ensure_group(chat); await update.effective_message.reply_text(f'👑 <b>NEXUS AI</b>\n<i>Community Operating System</i>\n\nWelcome {mention(u.id,u.first_name)}.\nChoose a department below. Access is checked again when you use a control.',parse_mode=ParseMode.HTML,reply_markup=keyboard(await role(update,ctx)))
    else:
        await update.effective_message.reply_text('👑 <b>NEXUS AI</b>\nYour group. Your team. Your intelligence.\n\nAdd me to a group, promote me with only the permissions you need, then use /nexus in that group.\n\nEach group has isolated settings and records.',parse_mode=ParseMode.HTML)
async def nexus(update,ctx):await start(update,ctx)
async def help_cmd(update,ctx):
    text=('📚 <b>NEXUS COMMANDS</b>\n\n<b>Members</b>\n/start /nexus /help /profile /rank /top /stats /rules /report\n\n<b>Admins</b>\n/dashboard /warn /warnings /clearwarns /mute /unmute /ban /unban /audit /reports /welcome /setrules /antiflood /links /autodelete\n\n<b>Owner</b>\n/grant USER_ID PERMISSION — grant custom module permission\n/revoke USER_ID PERMISSION — revoke custom grant\n/permissions — list available permissions\n\n<b>Global Super Owner</b>\n/globalgroups /system\n\nReply to a member message for warn/mute/ban. Bot permissions and Telegram hierarchy still apply.')
    await update.effective_message.reply_text(text,parse_mode=ParseMode.HTML)
async def profile(update,ctx):
    if not group_ok(update.effective_chat):return await update.effective_message.reply_text('Use inside a group.')
    cid=update.effective_chat.id;u=update.effective_user;ensure_group(update.effective_chat)
    with lock,conn() as c:
        r=c.execute('SELECT * FROM members WHERE chat_id=? AND user_id=?',(cid,u.id)).fetchone()
        rank=c.execute('SELECT COUNT(*)+1 n FROM members WHERE chat_id=? AND messages>(SELECT COALESCE(messages,0) FROM members WHERE chat_id=? AND user_id=?)',(cid,cid,u.id)).fetchone()['n']
    await update.effective_message.reply_text(f'👤 <b>MEMBER PROFILE</b>\nName: {esc(u.full_name)}\nMessages: {r["messages"] if r else 0}\nWarnings: {r["warnings"] if r else 0}\nRank: #{rank}',parse_mode=ParseMode.HTML)
async def top(update,ctx):
    if not group_ok(update.effective_chat):return
    with lock,conn() as c:rows=c.execute('SELECT user_id,name,messages FROM members WHERE chat_id=? ORDER BY messages DESC LIMIT 10',(update.effective_chat.id,)).fetchall()
    body='\n'.join(f'{i}. {mention(r["user_id"],r["name"])} — {r["messages"]}' for i,r in enumerate(rows,1)) or 'No activity recorded yet.'
    await update.effective_message.reply_text('🏆 <b>HALL OF FAME</b>\n\n'+body,parse_mode=ParseMode.HTML)
async def stats(update,ctx):
    if not await allowed(update,ctx,'stats'):return
    with lock,conn() as c:
        m=c.execute('SELECT COUNT(*) n FROM members WHERE chat_id=?',(update.effective_chat.id,)).fetchone()['n'];n=c.execute('SELECT COALESCE(SUM(messages),0) n FROM members WHERE chat_id=?',(update.effective_chat.id,)).fetchone()['n']
    await update.effective_message.reply_text(f'📊 <b>COMMUNITY ANALYTICS</b>\nTracked members: {m}\nMessages seen since Nexus joined: {n}\nUptime: {int(time.monotonic()-started)} sec',parse_mode=ParseMode.HTML)
async def rules(update,ctx):
    if not group_ok(update.effective_chat):return
    r=get_group(update.effective_chat.id);await update.effective_message.reply_text('📜 <b>GROUP RULES</b>\n\n'+esc(r['rules'] if r else 'No rules configured.'),parse_mode=ParseMode.HTML)
def replied(update):return update.effective_message.reply_to_message.from_user if update.effective_message and update.effective_message.reply_to_message else None
async def warn(update,ctx):
    if not await allowed(update,ctx,'warn'):return
    t=replied(update)
    if not t or t.is_bot:return await update.effective_message.reply_text('Target member ke message par reply karke /warn use karein.')
    cid=update.effective_chat.id
    with lock,conn() as c:
        c.execute('INSERT INTO members(chat_id,user_id,name) VALUES(?,?,?) ON CONFLICT(chat_id,user_id) DO UPDATE SET warnings=warnings+1',(cid,t.id,t.full_name));c.commit()
        n=c.execute('SELECT warnings FROM members WHERE chat_id=? AND user_id=?',(cid,t.id)).fetchone()['warnings']
    log_action(cid,update.effective_user.id,'WARN',t.id,f'{n}/{MAX_WARNINGS}')
    await update.effective_message.reply_text(f'⚠️ {mention(t.id,t.first_name)} warning {n}/{MAX_WARNINGS}',parse_mode=ParseMode.HTML)
    if n>=MAX_WARNINGS:
        try: await ctx.bot.restrict_chat_member(cid,t.id,permissions={'can_send_messages':False},until_date=datetime.now(timezone.utc)+timedelta(hours=1));log_action(cid,update.effective_user.id,'AUTO_MUTE',t.id,'warning threshold')
        except TelegramError:pass
async def warnings(update,ctx):
    if not await allowed(update,ctx,'warn'):return
    t=replied(update)
    if not t:return await update.effective_message.reply_text('Reply to member message.')
    with lock,conn() as c:r=c.execute('SELECT warnings FROM members WHERE chat_id=? AND user_id=?',(update.effective_chat.id,t.id)).fetchone()
    await update.effective_message.reply_text(f'Warnings: {r[0] if r else 0}')
async def clearwarns(update,ctx):
    if not await allowed(update,ctx,'warn'):return
    t=replied(update)
    if not t:return await update.effective_message.reply_text('Reply to member message.')
    with lock,conn() as c:c.execute('UPDATE members SET warnings=0 WHERE chat_id=? AND user_id=?',(update.effective_chat.id,t.id));c.commit()
    log_action(update.effective_chat.id,update.effective_user.id,'CLEAR_WARNINGS',t.id);await update.effective_message.reply_text('✅ Warnings cleared.')
async def mod_action(update,ctx,action):
    if not await allowed(update,ctx,action):return
    t=replied(update)
    if not t or t.is_bot:return await update.effective_message.reply_text('Target member ke message par reply karein.')
    cid=update.effective_chat.id
    # Never let the bot act on the group creator or an administrator.
    st=await tg_status(ctx,cid,t.id)
    if st in ('creator','administrator'):return await update.effective_message.reply_text('Protected: Telegram owner/admin par action nahi liya ja sakta.')
    try:
        if action=='ban':await ctx.bot.ban_chat_member(cid,t.id)
        elif action=='mute':
            mins=10
            if ctx.args:
                try:mins=max(1,min(int(ctx.args[0]),10080))
                except ValueError:return await update.effective_message.reply_text('Use /mute 10 (reply).')
            await ctx.bot.restrict_chat_member(cid,t.id,permissions={'can_send_messages':False},until_date=datetime.now(timezone.utc)+timedelta(minutes=mins))
        elif action=='unmute':await ctx.bot.restrict_chat_member(cid,t.id,permissions={'can_send_messages':True,'can_send_audios':True,'can_send_documents':True,'can_send_photos':True,'can_send_videos':True,'can_send_video_notes':True,'can_send_voice_notes':True,'can_send_polls':True,'can_send_other_messages':True,'can_add_web_page_previews':True})
        log_action(cid,update.effective_user.id,action.upper(),t.id);await update.effective_message.reply_text(f'✅ {action.upper()} action completed for {mention(t.id,t.first_name)}.',parse_mode=ParseMode.HTML)
    except TelegramError as e:await update.effective_message.reply_text(f'Action failed: {type(e).__name__}. Check bot admin rights/hierarchy.')
async def ban(u,c):await mod_action(u,c,'ban')
async def mute(u,c):await mod_action(u,c,'mute')
async def unmute(u,c):await mod_action(u,c,'unmute')
async def unban(update,ctx):
    if not await allowed(update,ctx,'ban'):return
    if not ctx.args or not ctx.args[0].isdigit():return await update.effective_message.reply_text('Usage: /unban USER_ID')
    try:await ctx.bot.unban_chat_member(update.effective_chat.id,int(ctx.args[0]),only_if_banned=True);log_action(update.effective_chat.id,update.effective_user.id,'UNBAN',int(ctx.args[0]));await update.effective_message.reply_text('✅ Unbanned.')
    except TelegramError as e:await update.effective_message.reply_text(f'Failed: {type(e).__name__}')
async def setrules(update,ctx):
    if not await allowed(update,ctx,'rules',owner_only=True):return
    value=' '.join(ctx.args).strip()
    if not value:return await update.effective_message.reply_text('Usage: /setrules your rules')
    with lock,conn() as c:c.execute('UPDATE groups SET rules=? WHERE chat_id=?',(value[:3000],update.effective_chat.id));c.commit()
    log_action(update.effective_chat.id,update.effective_user.id,'SET_RULES');await update.effective_message.reply_text('✅ Rules updated.')
async def toggle(update,ctx,column,owner_only=False):
    if not await allowed(update,ctx,column,owner_only=owner_only):return
    if not ctx.args or ctx.args[0].lower() not in ('on','off'):return await update.effective_message.reply_text(f'Usage: /{column} on|off')
    val=int(ctx.args[0].lower()=='on')
    with lock,conn() as c:c.execute(f'UPDATE groups SET {column}=? WHERE chat_id=?',(val,update.effective_chat.id));c.commit()
    log_action(update.effective_chat.id,update.effective_user.id,'SET_'+column.upper(),details=ctx.args[0]);await update.effective_message.reply_text(f'✅ {column}: {ctx.args[0].upper()}')
async def antiflood(u,c):await toggle(u,c,'antiflood',True)
async def links(u,c):await toggle(u,c,'links',True)
async def welcome(u,c):await toggle(u,c,'welcome',True)
async def report(update,ctx):
    if not group_ok(update.effective_chat):return
    msg=update.effective_message;t=msg.reply_to_message.from_user if msg.reply_to_message else None;details=' '.join(ctx.args)[:500]
    if not t and not details:return await msg.reply_text('Reply to a message with /report [reason], or use /report reason.')
    with lock,conn() as c:c.execute('INSERT INTO reports(chat_id,reporter,target,details) VALUES(?,?,?,?)',(update.effective_chat.id,update.effective_user.id,t.id if t else None,details));c.commit()
    log_action(update.effective_chat.id,update.effective_user.id,'REPORT',t.id if t else None,details);await msg.reply_text('🚩 Report recorded for admin review.')
async def reports(update,ctx):
    if not await allowed(update,ctx,'reports'):return
    with lock,conn() as c:rs=c.execute("SELECT id,reporter,target,details FROM reports WHERE chat_id=? AND status='OPEN' ORDER BY id DESC LIMIT 10",(update.effective_chat.id,)).fetchall()
    body='\n'.join(f'#{r["id"]} reporter={r["reporter"]} target={r["target"] or "—"} {esc(r["details"])}' for r in rs) or 'No open reports.'
    await update.effective_message.reply_text('🚩 <b>OPEN REPORTS</b>\n'+body,parse_mode=ParseMode.HTML)
async def audit_cmd(update,ctx):
    if not await allowed(update,ctx,'audit'):return
    with lock,conn() as c:rs=c.execute('SELECT actor,action,target,details,created_at FROM audit WHERE chat_id=? ORDER BY id DESC LIMIT 12',(update.effective_chat.id,)).fetchall()
    body='\n'.join(f'• {r["action"]} | {r["actor"]} → {r["target"] or "—"} | {esc(r["details"])}' for r in rs) or 'No records yet.'
    await update.effective_message.reply_text('📋 <b>AUDIT LOG</b>\n'+body,parse_mode=ParseMode.HTML)
async def dashboard(update,ctx):
    if not await allowed(update,ctx):return
    await update.effective_message.reply_text('🎛 <b>NEXUS CONTROL SURFACE</b>\nChoose a module. Each action checks your current Telegram role.',parse_mode=ParseMode.HTML,reply_markup=keyboard(await role(update,ctx)))
async def grant(update,ctx,revoke=False):
    if not await allowed(update,ctx,owner_only=True):return
    if len(ctx.args)<2 or not ctx.args[0].isdigit():return await update.effective_message.reply_text('Usage: /grant USER_ID permission  | permissions: reports stats audit automation ai')
    uid=int(ctx.args[0]);p=ctx.args[1].lower()
    if p not in {'reports','stats','audit','automation','ai'}:return await update.effective_message.reply_text('Unknown permission.')
    with lock,conn() as c:
        if revoke:c.execute('DELETE FROM grants WHERE chat_id=? AND user_id=? AND permission=?',(update.effective_chat.id,uid,p))
        else:c.execute('INSERT OR IGNORE INTO grants(chat_id,user_id,permission,granted_by) VALUES(?,?,?,?)',(update.effective_chat.id,uid,p,update.effective_user.id))
        c.commit()
    log_action(update.effective_chat.id,update.effective_user.id,'REVOKE' if revoke else 'GRANT',uid,p);await update.effective_message.reply_text(('Revoked ' if revoke else 'Granted ')+p)
async def grant_cmd(u,c):await grant(u,c,False)
async def revoke_cmd(u,c):await grant(u,c,True)
async def globalgroups(update,ctx):
    if not is_global(update.effective_user.id):return await update.effective_message.reply_text('Global Super Owner only.')
    with lock,conn() as c:rs=c.execute('SELECT chat_id,title FROM groups ORDER BY title LIMIT 50').fetchall()
    await update.effective_message.reply_text('🌐 <b>REGISTERED GROUPS</b>\n'+'\n'.join(f'{esc(r["title"])} — <code>{r["chat_id"]}</code>' for r in rs) if rs else 'No groups registered.',parse_mode=ParseMode.HTML)
async def system(update,ctx):
    if not is_global(update.effective_user.id):return await update.effective_message.reply_text('Global Super Owner only.')
    await update.effective_message.reply_text(f'🧬 NEXUS runtime online\nPython process uptime: {int(time.monotonic()-started)} sec\nDatabase: SQLite\nAI provider: '+('configured' if os.getenv('AI_API_KEY') else 'not configured'))
async def callback(update,ctx):
    q=update.callback_query;await q.answer();chat=q.message.chat if q.message else None;u=q.from_user
    if not group_ok(chat):return await q.edit_message_text('Open this panel inside a group.')
    fake=type('Obj',(),{'effective_chat':chat,'effective_user':u,'effective_message':q.message})()
    r=await role(fake,ctx);d=q.data
    if d=='help':return await q.edit_message_text('Use /help for the complete command list.',reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('⬅️ Home',callback_data='home')]]))
    if d=='home':return await q.edit_message_text('👑 <b>NEXUS AI</b>\nSelect a department:',parse_mode=ParseMode.HTML,reply_markup=keyboard(r))
    name=d.split(':',1)[1] if ':' in d else ''
    if name in ('guardian','admin') and r not in ('superowner','owner','admin'):return await q.edit_message_text('⛔ Admin access required.')
    if name=='control' and r not in ('superowner','owner'):return await q.edit_message_text('⛔ Group owner access required.')
    descriptions={'guardian':'🛡 Guardian\nCommands: /warn /mute /unmute /ban /unban /antiflood /links','members':'👥 Members\nCommands: /profile /rank /top','ai':'🧠 Intelligence\nAI provider integration is not configured in this release; no fake AI replies are generated.','analytics':'📊 Analytics\nUse /stats for tracked member and message totals.','hall':'🏆 Hall of Fame\nUse /top for contribution ranking.','audit':'📋 Audit\nUse /audit for recent moderation and configuration events.','knowledge':'📚 Knowledge Base\nUse /rules to view group rules. Owner: /setrules text.','voice':'🗣 Community Voice\nMembers can submit /report; admins review /reports.','admin':'⚙️ Admin Operations\n/dashboard /reports /audit /warn /mute /ban','auto':'🤖 Automation\n/welcome on|off /antiflood on|off /links on|off','control':'🎛 Owner Control\n/setrules text /welcome on|off /antiflood on|off /links on|off /grant USER_ID permission /revoke USER_ID permission'}
    txt=descriptions.get(name,'Department information unavailable.')
    await q.edit_message_text(txt,reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('⬅️ Home',callback_data='home')]]))
async def on_join(update,ctx):
    chat=update.effective_chat;ensure_group(chat);g=get_group(chat.id)
    if g['welcome']:
        for u in update.effective_message.new_chat_members:
            if not u.is_bot:await update.effective_message.reply_text(f'Welcome {mention(u.id,u.first_name)}! Please read /rules and respect the community.',parse_mode=ParseMode.HTML)
async def on_message(update,ctx):
    m=update.effective_message;chat=update.effective_chat;u=update.effective_user
    if not m or not group_ok(chat) or not u or u.is_bot:return
    ensure_group(chat)
    with lock,conn() as c:
        c.execute('INSERT INTO members(chat_id,user_id,name,username,messages) VALUES(?,?,?,?,1) ON CONFLICT(chat_id,user_id) DO UPDATE SET name=excluded.name,username=excluded.username,messages=messages+1,last_seen=CURRENT_TIMESTAMP',(chat.id,u.id,u.full_name,u.username or ''))
        g=c.execute('SELECT antiflood,links FROM groups WHERE chat_id=?',(chat.id,)).fetchone()
        now=time.time();c.execute('DELETE FROM flood WHERE ts<?',(now-FLOOD_WINDOW,));c.execute('INSERT INTO flood(chat_id,user_id,ts) VALUES(?,?,?)',(chat.id,u.id,now));n=c.execute('SELECT COUNT(*) n FROM flood WHERE chat_id=? AND user_id=? AND ts>=?',(chat.id,u.id,now-FLOOD_WINDOW)).fetchone()['n'];c.commit()
    if g['antiflood'] and n==FLOOD_COUNT+1:log_action(chat.id,None,'FLOOD_SIGNAL',u.id,f'{n}/{FLOOD_WINDOW}s')
    # Link filtering is deliberately alert-only by default; no automatic deletion/punishment.
    if g['links'] and ('http://' in (m.text or '').lower() or 'https://' in (m.text or '').lower()):log_action(chat.id,None,'LINK_SIGNAL',u.id,'link observed')
@web.get('/')
def root():return jsonify({'service':'NEXUS AI','status':'online'})
@web.get('/health')
def health():return jsonify({'status':'ok','service':'nexus-ai'})
def run_web():web.run(host='0.0.0.0',port=PORT,use_reloader=False)
async def post_init(app):
    await app.bot.set_my_commands([BotCommand('start','Open NEXUS'),BotCommand('nexus','Open group control panel'),BotCommand('help','Commands'),BotCommand('dashboard','Group dashboard'),BotCommand('profile','Member profile'),BotCommand('top','Leaderboard'),BotCommand('stats','Group statistics'),BotCommand('rules','Group rules'),BotCommand('report','Report a message')])
    me=await app.bot.get_me();log.info('Connected as @%s (%s)',me.username,me.id)
def main():
    if not TOKEN:raise RuntimeError('BOT_TOKEN environment variable is missing in Render.')
    if GLOBAL_OWNER is None:log.warning('OWNER_ID not set: global Super Owner commands are disabled. Group owners/admins still work.')
    init_db();threading.Thread(target=run_web,daemon=True).start();app=Application.builder().token(TOKEN).post_init(post_init).build()
    cmds={'start':start,'nexus':nexus,'help':help_cmd,'profile':profile,'rank':profile,'top':top,'stats':stats,'rules':rules,'dashboard':dashboard,'warn':warn,'warnings':warnings,'clearwarns':clearwarns,'ban':ban,'mute':mute,'unmute':unmute,'unban':unban,'setrules':setrules,'welcome':welcome,'antiflood':antiflood,'links':links,'report':report,'reports':reports,'audit':audit_cmd,'grant':grant_cmd,'revoke':revoke_cmd,'globalgroups':globalgroups,'system':system}
    for name,fn in cmds.items():app.add_handler(CommandHandler(name,fn))
    app.add_handler(CallbackQueryHandler(callback));app.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS,on_join));app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND,on_message));app.run_polling(allowed_updates=Update.ALL_TYPES,drop_pending_updates=False)
if __name__=='__main__':main()
