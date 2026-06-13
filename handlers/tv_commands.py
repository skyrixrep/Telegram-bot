"""
Handlers for Netflix TV Login commands: /tv, /upload, /vault
"""

import re
import io
import asyncio
import logging

from telegram import Update
from telegram.ext import ContextTypes
from telegram.constants import ParseMode

import database_pool as db
import config
from core.tv_login import process_tv_login
from core.tv_vault import count_vault_cookies, add_cookies_to_vault

logger = logging.getLogger(__name__)

BRAILLE_FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
DOTS_FRAMES = ["", ".", "..", "..."]


async def animate_message(context: ContextTypes.DEFAULT_TYPE, chat_id: int, message_id: int, stop_event: asyncio.Event):
    frame_idx = 0
    while not stop_event.is_set():
        frame = BRAILLE_FRAMES[frame_idx % len(BRAILLE_FRAMES)]
        dots = DOTS_FRAMES[(frame_idx // len(BRAILLE_FRAMES)) % len(DOTS_FRAMES)]
        try:
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=f"{frame} Checking cookies{dots}\n\nPlease wait...",
            )
        except Exception:
            pass
        frame_idx += 1
        await asyncio.sleep(0.3)


async def tv_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /tv <8-digit-code> command."""
    user = update.effective_user
    chat_id = update.effective_chat.id
    message_id = update.message.message_id

    args = context.args
    if not args:
        await update.message.reply_text(
            "❌ <b>Usage:</b> <code>/tv 12345678</code>",
            parse_mode=ParseMode.HTML,
            reply_to_message_id=message_id,
        )
        return

    tv_code = re.sub(r'\D', '', args[0])
    if len(tv_code) != 8:
        await update.message.reply_text(
            "❌ TV code must be exactly <b>8 digits</b>",
            parse_mode=ParseMode.HTML,
            reply_to_message_id=message_id,
        )
        return

    if count_vault_cookies() == 0:
        await update.message.reply_text(
            "😔 <b>No cookies left in vault!</b>\n\nWait for admin to upload more.",
            parse_mode=ParseMode.HTML,
            reply_to_message_id=message_id,
        )
        return

    status_msg = await update.message.reply_text(
        f"🔍 <b>Starting TV login...</b>\n\n"
        f"📺 Code: <code>{tv_code}</code>\n"
        f"🍪 Searching vault for a working cookie...",
        parse_mode=ParseMode.HTML,
        reply_to_message_id=message_id,
    )

    # Start animation
    stop_anim = asyncio.Event()
    anim_task = asyncio.create_task(animate_message(context, chat_id, status_msg.message_id, stop_anim))

    result = await asyncio.to_thread(process_tv_login, tv_code)

    stop_anim.set()
    await asyncio.sleep(0.5)

    if result["success"]:
        await db.increment_stat('tv_logins_successful')
        response = (
            f"✅ <b>TV ACTIVATED SUCCESSFULLY!</b>\n\n"
            f"📺 Your Code: <code>{tv_code}</code>\n"
            f"🌍 Account Country: <b>{result.get('country', 'N/A')}</b>\n"
            f"📦 Plan: <b>{result.get('plan', 'N/A')}</b>\n\n"
            f"<i>Your TV is now ready to watch Netflix!</i> 🍿"
        )
    elif result.get("error") == "no_cookies":
        await db.increment_stat('tv_logins_failed')
        response = "😔 <b>All cookies exhausted!</b>\n\nNo working cookies left in vault.\nWait for admin to upload more."
    elif result.get("error") == "all_dead":
        await db.increment_stat('tv_logins_failed')
        response = "❌ <b>No working cookies found!</b>\n\nAll available cookies are dead.\nVault is now empty."
    elif result.get("error") == "Invalid or expired TV code":
        await db.increment_stat('tv_codes_rejected')
        response = (
            f"❌ <b>Invalid or Expired TV Code</b>\n\n"
            f"📺 Code: <code>{tv_code}</code>\n"
            f"🌍 Cookie: <b>{result.get('country', 'N/A')}</b>\n\n"
            f"<i>The code you entered is wrong or expired.\n"
            f"Please check your TV screen and try again with a fresh code.</i>"
        )
    else:
        await db.increment_stat('tv_codes_rejected')
        response = (
            f"❌ <b>Activation Failed</b>\n\n"
            f"📺 Code: <code>{tv_code}</code>\n"
            f"🌍 Cookie: <b>{result.get('country', 'N/A')}</b>\n"
            f"⚠️ Error: {result.get('error', 'Unknown')}\n\n"
            f"<i>Please try again with a fresh code.</i>"
        )

    await db.increment_stat('tv_logins_attempted')
    await status_msg.edit_text(response, parse_mode=ParseMode.HTML)


async def upload_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /upload command - admin uploads cookies to TV vault."""
    user_id = update.effective_user.id
    message_id = update.message.message_id

    is_admin = await db.is_user_admin(user_id)
    if not is_admin:
        await update.message.reply_text(
            "🚫 <b>Admin only!</b>",
            parse_mode=ParseMode.HTML,
            reply_to_message_id=message_id,
        )
        return

    if not update.message.reply_to_message or not update.message.reply_to_message.document:
        await update.message.reply_text(
            "📎 <b>Usage:</b> Reply to a ZIP file with <code>/upload</code>\n\n"
            "ZIP should contain .txt or .json cookie files.",
            parse_mode=ParseMode.HTML,
            reply_to_message_id=message_id,
        )
        return

    doc = update.message.reply_to_message.document
    if not doc.file_name.lower().endswith('.zip'):
        await update.message.reply_text(
            "❌ Only <b>.zip</b> files are accepted!",
            parse_mode=ParseMode.HTML,
            reply_to_message_id=message_id,
        )
        return

    status_msg = await update.message.reply_text(
        "📥 <b>Downloading...</b>",
        parse_mode=ParseMode.HTML,
        reply_to_message_id=message_id,
    )

    try:
        file = await context.bot.get_file(doc.file_id)
        zip_bytes = await file.download_as_bytearray()

        await status_msg.edit_text("📂 <b>Extracting...</b>", parse_mode=ParseMode.HTML)

        added, skipped = await asyncio.to_thread(add_cookies_to_vault, bytes(zip_bytes))

        vault_count = count_vault_cookies()
        await status_msg.edit_text(
            f"✅ <b>Upload complete!</b>\n\n"
            f"📥 Added: <b>{added}</b> cookies\n"
            f"⏭️ Skipped: <b>{skipped}</b>\n"
            f"🍪 Total in vault: <b>{vault_count}</b>",
            parse_mode=ParseMode.HTML,
        )

    except Exception as e:
        logger.error(f"Upload error: {e}", exc_info=True)
        await status_msg.edit_text(f"❌ <b>Error:</b> {str(e)}", parse_mode=ParseMode.HTML)


async def vault_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /vault command - show vault stats (admin only)."""
    user_id = update.effective_user.id
    message_id = update.message.message_id

    is_admin = await db.is_user_admin(user_id)
    if not is_admin:
        await update.message.reply_text(
            "🚫 <b>Admin only!</b>",
            parse_mode=ParseMode.HTML,
            reply_to_message_id=message_id,
        )
        return

    vault_count = count_vault_cookies()
    tv_attempted = await db.get_stat('tv_logins_attempted')
    tv_successful = await db.get_stat('tv_logins_successful')
    tv_failed = await db.get_stat('tv_logins_failed')
    tv_rejected = await db.get_stat('tv_codes_rejected')

    msg = (
        f"🗄️ <b>TV Vault Statistics</b>\n\n"
        f"🍪 <b>Cookies in vault:</b> {vault_count}\n\n"
        f"📊 <b>TV Login Stats:</b>\n"
        f"🎬 Attempted: <b>{tv_attempted}</b>\n"
        f"✅ Successful: <b>{tv_successful}</b>\n"
        f"❌ Failed (dead cookies): <b>{tv_failed}</b>\n"
        f"🚫 Codes rejected: <b>{tv_rejected}</b>"
    )

    await update.message.reply_text(
        msg,
        parse_mode=ParseMode.HTML,
        reply_to_message_id=message_id,
    )
