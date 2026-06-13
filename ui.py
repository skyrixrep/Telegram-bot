"""
This file contains all user-facing strings and UI components for the bot.
The design philosophy is clean, modern, and trustworthy.
"""

from telegram import InlineKeyboardButton

# A subtle footer to be used on key messages
FOOTER_TEXT = "\n_⚡ Powered by AetherX 🌟 | Created by SkyriX_"

# === 𝗠𝗮𝗶𝗻 𝗠𝗲𝗻𝘂 ===

def get_main_menu_text(user_name: str) -> str:
    """Returns the personalized welcome/main menu text."""
    return (
        f"Welcome to AetherX ⚡🌟, {user_name}!\n\n"
        "I'm your premium assistant for Netflix, Claude AI, ChatGPT, Spotify & Hotstar cookie checking, token generation, and TV login.\n\n"
        "To begin, please select a tool below."
    )

MAIN_MENU_KEYBOARD = [
    [
        InlineKeyboardButton("🩺 NF Check", callback_data="mode_nf_check"),
        InlineKeyboardButton("🎟️ NF Token", callback_data="mode_nf_token")
    ],
    [
        InlineKeyboardButton("🤖 Claude AI", callback_data="mode_claude_check"),
        InlineKeyboardButton("💬 ChatGPT", callback_data="mode_chatgpt_check")
    ],
    [
        InlineKeyboardButton("🎵 Spotify", callback_data="mode_spotify_check"),
        InlineKeyboardButton("🔥 Hotstar", callback_data="mode_hotstar_check")
    ],
    [
        InlineKeyboardButton("📺 TV Login", callback_data="mode_tv_info"),
        InlineKeyboardButton("⇄ Convert", callback_data="mode_converter")
    ]
]

# === File Processing Messages ===

def get_file_received_text(filename: str) -> str:
    return f"Processing `{filename}`..."

def get_nf_check_result_text(filename: str, message: str, duration: str) -> str:
     return (
        f"**Netflix Check Result for `{filename}`**\n\n"
        f"`• Status: {message}`\n"
        f"`• Time: {duration}`"
     )

def get_claude_check_result_text(filename: str, message: str, duration: str) -> str:
    return (
        f"**Claude AI Check Result for `{filename}`**\n\n"
        f"`• Status: {message}`\n"
        f"`• Time: {duration}`"
    )

def get_nf_token_result_text(filename: str, expiry_date: str, phone_link: str, desktop_link: str, tv_link: str) -> str:
    return (
        f"**NF Token Generated for `{filename}`**\n\n"
        f"**Valid Until:** `{expiry_date}`\n\n"
        f"**Login Links:**\n"
        f"📱 Phone: `{phone_link}`\n\n"
        f"🖥️ Desktop: `{desktop_link}`\n\n"
        f"📺 TV: `{tv_link}`"
    )

def get_chatgpt_check_result_text(filename: str, message: str, duration: str) -> str:
    return (
        f"**ChatGPT Check Result for `{filename}`**\n\n"
        f"`• Status: {message}`\n"
        f"`• Time: {duration}`"
    )

def get_chatgpt_batch_result_text(filename: str, valid_count: int, total_count: int, plan_breakdown: dict) -> str:
    """
    Generates a summary message for ChatGPT batch processing with plan breakdown.
    """
    result_lines = [
        f"**ChatGPT Batch Check Complete for `{filename}`**\n",
        f"✅ **Valid**: {valid_count}/{total_count}",
        f"❌ **Invalid**: {total_count - valid_count}/{total_count}\n"
    ]
    
    if plan_breakdown:
        result_lines.append("**Plan Breakdown:**")
        for plan, count in sorted(plan_breakdown.items()):
            # Add emojis for different plan types
            if plan.lower() == 'free':
                emoji = "🆓"
            elif 'plus' in plan.lower():
                emoji = "⭐"
            elif 'team' in plan.lower():
                emoji = "👥"
            elif 'pro' in plan.lower():
                emoji = "💼"
            else:
                emoji = "📊"
            
            result_lines.append(f"`• {emoji} {plan}: {count}`")
    
    return "\n".join(result_lines)

def get_converter_batch_result_text(filename: str, valid_count: int, total_count: int, target_format: str) -> str:
    """
    Generates a summary message for converter batch processing.
    """
    format_names = {'netscape': 'Netscape', 'json': 'JSON'}
    format_name = format_names.get(target_format, target_format.title())
    
    result_lines = [
        f"**Conversion Complete for `{filename}`**\n",
        f"✅ **Converted**: {valid_count}/{total_count} to **{format_name}** format",
        f"❌ **Failed**: {total_count - valid_count}/{total_count}\n",
        f"🔄 All supported cookie formats (JSON, Netscape, Headers) have been converted to {format_name}."
    ]
    
    return "\n".join(result_lines)


# === General & Error Messages ===

ACTION_CANCELLED_TEXT = "Action cancelled."
NOT_AUTHORIZED_TEXT = "🔒 You are not authorized to perform this action."
ERROR_FALLBACK_TEXT = "An unexpected error occurred. Please try again in a few moments."
MAINTENANCE_TEXT = (
    "**🛠️ System Maintenance**\n\n"
    "AetherX ⚡🌟 is currently undergoing scheduled maintenance to improve performance and security.\n\n"
    "We'll be back online shortly. Thank you for your patience."
)
UNKNOWN_COMMAND_TEXT = (
    "I'm sorry, I didn't understand that.\n\n"
    "Please select an option from the main menu or send a cookie file to get started. "
    "If you need help, you can use the /start command at any time."
)
INVALID_MODE_TEXT = "🤔 Please select a mode from the /start menu first."


# === Admin Panel ===

def get_stats_text(stats: dict) -> str:
    """Formats bot statistics into a clean, readable block."""
    # This implementation avoids triple-quotes to prevent syntax errors.
    lines = [
        "*📊 Bot Statistics*",
        "```",
        f"      Users ┆ {stats.get('total_users', 'N/A')} Total",
        f"            ┆ {stats.get('authorized_users', 'N/A')} Authorized",
        f"            ┆ {stats.get('admins', 'N/A')} Admins",
        "----------------------------",
        f"  Processed ┆ {stats.get('cookies_processed', 'N/A')} Cookies",
        f"Maintenance ┆ {stats.get('maintenance_mode', 'N/A').title()}",
        "----------------------------",
        f"   TV Vault ┆ {stats.get('tv_vault', 'N/A')} Cookies",
        f"  TV Logins ┆ {stats.get('tv_attempted', 0)} Attempted",
        f"            ┆ {stats.get('tv_successful', 0)} Successful",
        "```"
    ]
    return "\n".join(lines)