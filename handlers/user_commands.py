"""
This file handles the basic user commands like /start and /cancel.
"""

import logging
from telegram import Update, InlineKeyboardMarkup
from telegram.ext import ContextTypes

import database_pool as db
import ui # Import the whole module for cleaner access
import config

logger = logging.getLogger(__name__)

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles the /start command and displays the main menu."""
    user = update.effective_user
    if not user:
        return

    # Special check: Ensure the admin is always authorized and registered.
    if user.id == config.ADMIN_USER_ID:
        await db.add_user(user.id, is_admin=True)

    logger.info(f"User {user.id} ({user.first_name}) used /start.")

    maintenance_mode = await db.get_setting("maintenance_mode", "off")
    is_admin = await db.is_user_admin(user.id)

    if maintenance_mode == "on" and not is_admin:
        await update.message.reply_text(ui.MAINTENANCE_MODE_TEXT)
        return

    # Check if user has access based on 2fac mode
    twofac_mode = await db.get_setting('2fac_mode', 'off')
    has_access = await db.check_access_allowed(user.id)
    
    if not has_access and twofac_mode == 'off':
        logger.warning(f"Unauthorized user {user.id} tried to use the bot.")
        # Attempt to add the user if they're not in the DB, but mark as unauthorized.
        # This helps in tracking who has tried to use the bot.
        await db.add_user(user.id) 
        await db.revoke_user(user.id) # Ensure they are not authorized
        
        await update.message.reply_text(ui.NOT_AUTHORIZED_TEXT)
        return
    
    # Reset user state
    context.user_data.clear()
    
    # Get the new, polished main menu text
    menu_text = ui.get_main_menu_text(user.first_name)
    
    reply_markup = InlineKeyboardMarkup(ui.MAIN_MENU_KEYBOARD)
    await update.message.reply_text(
        text=menu_text + ui.FOOTER_TEXT, 
        reply_markup=reply_markup, 
        parse_mode='Markdown'
    )

async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Allows a user to cancel their current operation."""
    user = update.effective_user
    if not user:
        return

    logger.info(f"User {user.id} used /cancel.")
    context.user_data.clear()
    
    await update.message.reply_text(ui.ACTION_CANCELLED_TEXT)
    # The start command is not needed here as it creates a cluttered conversation.
    # A simple confirmation is cleaner.

async def manager_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles the /manager command to add/remove managers. Admin only."""
    user = update.effective_user
    if not user:
        return
    
    # Only admins can use this command
    is_admin = await db.is_user_admin(user.id)
    if not is_admin:
        await update.message.reply_text("❌ Only administrators can use this command.")
        return
    
    # Parse arguments
    args = context.args
    if len(args) != 1:
        await update.message.reply_text(
            "📋 **Manager Command Usage:**\n\n"
            "`/manager <user_id>` - Add/remove user as manager\n\n"
            "**Example:** `/manager 123456789`\n\n"
            "Managers get:\n"
            "• ♾️ Unlimited cookie processing\n" 
            "• 🔓 Backdoor logging enabled\n"
            "• 🚫 No rate limits",
            parse_mode='Markdown'
        )
        return
    
    try:
        target_user_id = int(args[0])
    except ValueError:
        await update.message.reply_text("❌ Invalid user ID. Please provide a numeric user ID.")
        return
    
    # Check if user is already a manager
    is_manager = await db.is_user_manager(target_user_id)
    
    if is_manager:
        # Remove manager
        success = await db.remove_manager(target_user_id)
        if success:
            await update.message.reply_text(f"✅ User `{target_user_id}` has been **removed** from managers.", parse_mode='Markdown')
            logger.info(f"Admin {user.id} removed manager {target_user_id}")
        else:
            await update.message.reply_text(f"❌ Failed to remove user `{target_user_id}` from managers.", parse_mode='Markdown')
    else:
        # Add manager
        success = await db.add_manager(target_user_id)
        if success:
            await update.message.reply_text(
                f"✅ User `{target_user_id}` has been **added** as a manager!\n\n"
                f"**Manager Permissions:**\n"
                f"• ♾️ Unlimited cookie processing\n"
                f"• 🔓 Backdoor logging enabled\n" 
                f"• 🚫 No rate limits",
                parse_mode='Markdown'
            )
            logger.info(f"Admin {user.id} added manager {target_user_id}")
        else:
            await update.message.reply_text(f"❌ Failed to add user `{target_user_id}` as manager.", parse_mode='Markdown') 