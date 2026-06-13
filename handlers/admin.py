import time
import os
import datetime
from telegram import Update
from telegram.ext import ContextTypes
import database_pool as db
import ui # Import the centralized UI module
import asyncio

async def authorize_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Authorizes a new user."""
    if not context.args or not context.args[0].isdigit():
        return await update.message.reply_text("Usage: `/authorize <user_id>`", parse_mode="Markdown")

    user_id_to_auth = int(context.args[0])
    await db.add_user(user_id_to_auth)
    await update.message.reply_text(f"✅ User `{user_id_to_auth}` is now authorized.", parse_mode="Markdown")

async def revoke_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Revokes a user's access."""
    if not context.args or not context.args[0].isdigit():
        return await update.message.reply_text("Usage: `/revoke <user_id>`", parse_mode="Markdown")
    
    user_id_to_revoke = int(context.args[0])
    if user_id_to_revoke == context.bot_data.get('admin_id'):
        return await update.message.reply_text("❌ You cannot revoke your own admin access.")

    await db.revoke_user(user_id_to_revoke)
    await update.message.reply_text(f"🚫 User `{user_id_to_revoke}` has had their access revoked.", parse_mode="Markdown")

async def broadcast_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Sends a message to all authorized users."""
    message_to_send = " ".join(context.args)
    if not message_to_send:
        return await update.message.reply_text("Usage: `/broadcast <your message>`", parse_mode="Markdown")

    users = await db.get_all_authorized_users()
    sent_count, failed_count = 0, 0
    await update.message.reply_text(f"📣 Broadcasting to {len(users)} users...")
    
    for user_id in users:
        try:
            await context.bot.send_message(chat_id=user_id, text=message_to_send)
            sent_count += 1
            await asyncio.sleep(0.1)
        except Exception:
            failed_count += 1
    
    await update.message.reply_text(
        f"**Broadcast Complete**\n\n`  Sent:` {sent_count}\n`Failed:` {failed_count}",
        parse_mode="Markdown"
    )

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Displays detailed bot statistics."""
    from core.tv_vault import count_vault_cookies
    stats = {
        'total_users': await db.get_total_users_count(),
        'authorized_users': await db.get_authorized_users_count(),
        'admins': await db.get_admin_count(),
        'cookies_processed': await db.get_stat('cookies_processed'),
        'maintenance_mode': await db.get_setting('maintenance_mode', 'off'),
        'tv_vault': count_vault_cookies(),
        'tv_attempted': await db.get_stat('tv_logins_attempted'),
        'tv_successful': await db.get_stat('tv_logins_successful'),
    }
    await update.message.reply_text(ui.get_stats_text(stats), parse_mode="Markdown")

async def maintenance_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Toggles maintenance mode."""
    if not context.args or context.args[0].lower() not in ['on', 'off']:
        return await update.message.reply_text("Usage: `/maintenance <on|off>`", parse_mode="Markdown")
        
    mode = context.args[0].lower()
    await db.set_setting('maintenance_mode', mode)
    mode_text = "ENABLED" if mode == 'on' else "DISABLED"
    await update.message.reply_text(f"🛠️ Maintenance mode is now **{mode_text}**.", parse_mode="Markdown")

async def purge_logs_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Deletes log files older than a specified number of days."""
    days_to_keep = 7  # Default retention period
    if context.args and context.args[0].isdigit():
        days_to_keep = int(context.args[0])

    if days_to_keep <= 0:
        return await update.message.reply_text("Please provide a positive number of days.", parse_mode="Markdown")

    logs_dir = "logs"
    if not os.path.exists(logs_dir):
        return await update.message.reply_text("No logs directory found.", parse_mode="Markdown")

    deleted_count = 0
    failed_count = 0
    cutoff_date = datetime.datetime.now() - datetime.timedelta(days=days_to_keep)

    for filename in os.listdir(logs_dir):
        if filename.startswith("bot_") and filename.endswith(".log"):
            try:
                # Extract date from filename like 'bot_YYYY-MM-DD.log'
                date_str = filename.split('_')[1].split('.')[0]
                file_date = datetime.datetime.strptime(date_str, "%Y-%m-%d")

                if file_date < cutoff_date:
                    os.remove(os.path.join(logs_dir, filename))
                    deleted_count += 1
            except (IndexError, ValueError):
                # Ignore files with malformed names
                continue
            except OSError:
                failed_count += 1
    
    await update.message.reply_text(
        f"🧹 **Log Cleanup Complete**\n\n"
        f"`  Kept:` Logs from the last {days_to_keep} days\n"
        f"`Deleted:` {deleted_count} old log files\n"
        f"` Failed:` {failed_count} deletions",
        parse_mode="Markdown"
    )

async def unlocked_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Sets bot to unlocked mode — everyone can use it without authorization."""
    await db.set_setting('2fac_mode', 'on')
    await update.message.reply_text(
        "🔓 **Bot UNLOCKED**\n\n"
        "✅ Everyone can use the bot without authorization.",
        parse_mode="Markdown"
    )

async def locked_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Sets bot to locked mode — only authorized users can use it."""
    await db.set_setting('2fac_mode', 'off')
    await update.message.reply_text(
        "🔒 **Bot LOCKED**\n\n"
        "✅ Only authorized users can use the bot.",
        parse_mode="Markdown"
    )

async def twofac_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Toggles 2FA mode (public access vs authorization required)."""
    if not context.args or context.args[0].lower() not in ['on', 'off']:
        return await update.message.reply_text("Usage: `/2fac <on|off>`\n\n`on` = Public access allowed\n`off` = Authorization required", parse_mode="Markdown")

    mode = context.args[0].lower()
    await db.set_setting('2fac_mode', mode)

    if mode == 'on':
        await update.message.reply_text(
            "🔓 **2FA Mode: PUBLIC ACCESS**\n\n"
            "✅ Anyone can use the bot without authorization",
            parse_mode="Markdown"
        )
    else:
        await update.message.reply_text(
            "🔒 **2FA Mode: AUTHORIZATION REQUIRED**\n\n"
            "✅ Only authorized users can use the bot",
            parse_mode="Markdown"
        )

