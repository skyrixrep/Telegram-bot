from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
import ui # Import the whole module for cleaner access

# --- UI Definitions for Callbacks ---

# A simple "Back to Main Menu" button to be reused
BACK_BUTTON = [InlineKeyboardButton("« Back to Main Menu", callback_data="main_menu")]
BACK_KEYBOARD = InlineKeyboardMarkup([BACK_BUTTON])

# Texts for each mode, now centralized
MODE_TEXTS = {
    "nf_check": "🩺 *Netflix Check*\n\nSend a Netflix cookie file or a ZIP of files. I will perform a quick, non-intrusive check to see if it's active.",
    "nf_token": "🎟️ *NF Token Generator*\n\nSend a Netflix cookie file or ZIP. I will generate login token links (phone, desktop, TV).",
    "tv_info": "📺 *Netflix TV Login*\n\nUse the `/tv` command to activate Netflix on your TV.\n\n*Usage:* `/tv 12345678`\n\nJust enter the 8-digit code shown on your TV screen.",
    "claude_check": "🤖 *Claude AI Check*\n\nSend your Claude AI cookie file or a ZIP of files. I will check if the cookies are valid and show plan information.",
    "chatgpt_check": "💬 *ChatGPT Check*\n\nSend your ChatGPT cookie file or a ZIP of files. I will check if the cookies are valid and show plan information.",
    "spotify_check": "🎵 *Spotify Check*\n\nSend your Spotify cookie file or a ZIP of files. I will check if the cookies are valid and show plan, country, and account details.",
    "hotstar_check": "🔥 *Hotstar Check*\n\nSend your Hotstar cookie file or a ZIP of files. I will check if the cookies are valid and show subscription plan details.",
    "converter": "⇄ *Convert Cookie*\n\nSelect the format you want to convert your cookie *to*."
}

# Keyboards for each mode
MODE_KEYBOARDS = {
    "nf_check": BACK_KEYBOARD,
    "nf_token": BACK_KEYBOARD,
    "tv_info": BACK_KEYBOARD,
    "claude_check": BACK_KEYBOARD,
    "chatgpt_check": BACK_KEYBOARD,
    "spotify_check": BACK_KEYBOARD,
    "hotstar_check": BACK_KEYBOARD,
    "converter": InlineKeyboardMarkup([
        [
            InlineKeyboardButton("To JSON", callback_data="convert_json"),
            InlineKeyboardButton("To Netscape", callback_data="convert_netscape")
        ],
        BACK_BUTTON
    ])
}

async def button_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles all inline button presses from menus."""
    query = update.callback_query
    await query.answer()
    
    # Unified handler for going back to the main menu
    if query.data == "main_menu":
        context.user_data.clear()
        
        # Reconstruct the main menu to avoid circular imports and keep logic self-contained
        menu_text = ui.get_main_menu_text(query.from_user.first_name)
        reply_markup = InlineKeyboardMarkup(ui.MAIN_MENU_KEYBOARD)
        
        await query.edit_message_text(
            text=menu_text + ui.FOOTER_TEXT,
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
        return

    # Mode selection handler
    if query.data.startswith("mode_"):
        mode = query.data.split("mode_")[1]
        context.user_data['mode'] = mode
        
        text = MODE_TEXTS.get(mode, "Please select an option.")
        keyboard = MODE_KEYBOARDS.get(mode, BACK_KEYBOARD)
        
        await query.edit_message_text(text, reply_markup=keyboard, parse_mode="Markdown")

    # Converter format selection
    elif query.data.startswith("convert_"):
        target_format = query.data.split("convert_")[1]
        context.user_data['target_format'] = target_format
        
        format_name_map = {"json": "JSON", "netscape": "Netscape"}
        text = f"✅ Format set to **{format_name_map.get(target_format, 'N/A')}**.\n\nPlease send your cookie file to be converted."
        
        await query.edit_message_text(text, reply_markup=BACK_KEYBOARD, parse_mode="Markdown")