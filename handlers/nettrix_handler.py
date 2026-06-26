"""
Nettrix Handler — Netflix Profile Email Adder
Adds a /nettrix conversation to the AetherX bot.

Flow:
  /nettrix  →  paste/upload cookies
           →  pick profile
           →  enter email
           →  confirm
           →  done ✅

Auth:    Uses AetherX's database (is_user_authorized).
Cookies: Parsed via AetherX's core/cookie_utils.py.
Health:  Checked via AetherX's core/health_checker.py.
Email:   Encrypted + submitted via core/netflix_profile_email.py.
"""

import asyncio
import re
import logging

from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import (
    ConversationHandler,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)
from telegram.error import TimedOut, NetworkError, RetryAfter

import database_pool as db
from core.cookie_utils import parse_cookie_content_to_dict, validate_netflix_cookies
from core.health_checker import check_cookie_health
from core.enums import PrivatizationStatus
from core.netflix_profile_email import (
    fetch_profiles,
    ale_provision,
    encrypt_email,
    update_profile_email as nf_update_profile_email,
)

logger = logging.getLogger(__name__)

# ── Conversation states ──────────────────────────────────────────────────────────
# Use values 200-203 to avoid collisions with any existing handler states.
NX_WAIT_COOKIES: int = 200
NX_WAIT_PROFILE: int = 201
NX_WAIT_EMAIL:   int = 202
NX_WAIT_CONFIRM: int = 203

# ── Netflix error-code → friendly message map ────────────────────────────────────
_NF_ERRORS = {
    "ACCOUNT_ALREADY_EXISTS":   "❌ That email is already linked to a Netflix account.\nTry a different address.",
    "INVALID_EMAIL":            "❌ Netflix rejected this email as invalid.\nCheck the address and try again.",
    "EMAIL_ALREADY_ON_PROFILE": "❌ This email is already on this profile.",
    "PROFILE_NOT_FOUND":        "❌ Profile not found — it may have been deleted.",
    "RATE_LIMIT":               "❌ Too many attempts. Wait a few minutes and try again.",
    "THROTTLING_FAILURE":       "⏱ *Netflix is throttling requests.*\n\nWait 5–10 minutes, then try again.",
    "INVALID_PERMISSIONS":      "🔒 *Profile is locked.*\n\nUnlock it in Netflix settings, or pick a different profile.",
    "UNAUTHORIZED":             "❌ Cookies expired or unauthorised. Export fresh cookies.",
}


# ── Helpers ──────────────────────────────────────────────────────────────────────

async def _safe_reply(message, text: str, retries: int = 3, **kwargs):
    """Reply with automatic back-off on Telegram errors."""
    for attempt in range(retries):
        try:
            return await message.reply_text(text, **kwargs)
        except RetryAfter as e:
            await asyncio.sleep(e.retry_after + 1)
        except (TimedOut, NetworkError):
            if attempt < retries - 1:
                await asyncio.sleep(2 ** attempt)
            else:
                logger.error("Nettrix: failed to send message after %d retries", retries)


async def _run_blocking(func, *args):
    """Run a synchronous function in the thread-pool executor."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, func, *args)


async def _is_authorized(update: Update) -> bool:
    """Return True if the user is authorised in the AetherX database."""
    return await db.is_user_authorized(update.effective_user.id)


# ── Conversation handlers ─────────────────────────────────────────────────────────

async def nettrix_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/nettrix — entry point."""
    if not await _is_authorized(update):
        await _safe_reply(update.message, "⛔ You are not authorised to use this feature.")
        return ConversationHandler.END

    context.user_data.clear()
    await _safe_reply(
        update.message,
        "🎬 *Nettrix — Netflix Profile Email Adder*\n\n"
        "Send me your Netflix cookies to get started.\n\n"
        "📋 *Accepted formats:*\n"
        "• Netscape cookie file (tab / space separated)\n"
        "• JSON array (EditThisCookie / Cookie-Editor)\n"
        "• Raw `Cookie:` header (semicolon-separated)\n\n"
        "📎 You can also upload a `.txt` file.\n\n"
        "Paste cookies or upload a file now:",
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardRemove(),
    )
    return NX_WAIT_COOKIES


async def nettrix_receive_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive cookies pasted as plain text."""
    if not await _is_authorized(update):
        return ConversationHandler.END
    return await _process_cookies(update, context, update.message.text.strip())


async def nettrix_receive_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive cookies uploaded as a .txt file."""
    if not await _is_authorized(update):
        return ConversationHandler.END

    doc = update.message.document
    if not doc:
        await _safe_reply(update.message, "❌ Please send a `.txt` file or paste cookies as text.")
        return NX_WAIT_COOKIES

    tg_file   = await doc.get_file()
    raw_bytes = await tg_file.download_as_bytearray()
    try:
        raw = raw_bytes.decode("utf-8", errors="ignore").strip()
    except Exception:
        await _safe_reply(update.message, "❌ Could not read the file. Please send a plain `.txt` file.")
        return NX_WAIT_COOKIES

    return await _process_cookies(update, context, raw)


async def _process_cookies(update: Update, context: ContextTypes.DEFAULT_TYPE, raw: str):
    """Parse cookies → health-check + profile fetch → show profile keyboard."""

    # 1. Parse (reuse AetherX's cookie_utils)
    cookies = parse_cookie_content_to_dict(raw)
    if not cookies or not validate_netflix_cookies(cookies):
        await _safe_reply(
            update.message,
            "❌ *Could not find `NetflixId` in your cookies.*\n\n"
            "Make sure you export directly from a Netflix page and try again.\n"
            "Send /nettrix to restart.",
            parse_mode="Markdown",
        )
        return NX_WAIT_COOKIES

    await _safe_reply(update.message, "⏳ Checking cookies…", reply_markup=ReplyKeyboardRemove())

    # 2. Health-check + profile fetch (parallel in thread pool)
    def _health_and_profiles():
        result   = check_cookie_health(raw)
        is_live  = result.status == PrivatizationStatus.SUCCESS
        profiles = fetch_profiles(cookies) if is_live else []
        return is_live, profiles

    try:
        live, profiles = await _run_blocking(_health_and_profiles)
    except Exception as e:
        await _safe_reply(update.message, f"❌ Error during check: `{e}`", parse_mode="Markdown")
        return ConversationHandler.END

    if not live:
        await _safe_reply(
            update.message,
            "🔴 *Cookie Dead*\n\n"
            "These cookies are expired or invalid.\n"
            "Export fresh cookies and send /nettrix.",
            parse_mode="Markdown",
        )
        return ConversationHandler.END

    if not profiles:
        await _safe_reply(update.message, "❌ Could not fetch profiles. Try again with /nettrix.")
        return ConversationHandler.END

    # Store state
    context.user_data["nx_cookies"]  = cookies
    context.user_data["nx_raw"]      = raw
    context.user_data["nx_profiles"] = profiles

    # 3. Show profile picker
    lines    = ["👤 *Profiles on this account:*\n"]
    keyboard = []
    for i, p in enumerate(profiles, 1):
        tag = " 👑" if p["owner"] else ""
        lines.append(f"  `{i}.` {p['name']}{tag}")
        keyboard.append([f"{i}. {p['name']}{tag}"])

    lines.append("\n*Reply with the number* of the profile to update:")
    await _safe_reply(
        update.message,
        "\n".join(lines),
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardMarkup(keyboard, one_time_keyboard=True, resize_keyboard=True),
    )
    return NX_WAIT_PROFILE


async def nettrix_receive_profile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """User picks a profile number."""
    if not await _is_authorized(update):
        return ConversationHandler.END

    text     = update.message.text.strip()
    profiles = context.user_data.get("nx_profiles", [])
    match    = re.match(r"^(\d+)", text)

    if not match:
        await _safe_reply(update.message, "❌ Reply with a number (e.g. `1`).", parse_mode="Markdown")
        return NX_WAIT_PROFILE

    idx = int(match.group(1)) - 1
    if idx < 0 or idx >= len(profiles):
        await _safe_reply(update.message, f"❌ Enter a number between 1 and {len(profiles)}.")
        return NX_WAIT_PROFILE

    context.user_data["nx_selected"] = profiles[idx]
    await _safe_reply(
        update.message,
        f"✅ Selected: *{profiles[idx]['name']}*\n\n"
        "📧 Enter the email address to add to this profile:",
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardRemove(),
    )
    return NX_WAIT_EMAIL


async def nettrix_receive_email(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """User enters an email address."""
    if not await _is_authorized(update):
        return ConversationHandler.END

    email = update.message.text.strip()
    if "@" not in email or "." not in email.split("@")[-1]:
        await _safe_reply(update.message, "❌ That doesn't look like a valid email. Try again:")
        return NX_WAIT_EMAIL

    context.user_data["nx_email"] = email
    cookies  = context.user_data["nx_cookies"]
    selected = context.user_data["nx_selected"]

    # Pre-warm AleProvision in the background while the user reads the confirm prompt.
    # By the time they tap "Yes", the token is usually already fetched.
    context.user_data["nx_prov_task"] = asyncio.create_task(
        _run_blocking(ale_provision, cookies, selected["guid"])
    )

    await _safe_reply(
        update.message,
        f"🔍 *Confirm:*\n\n"
        f"  Profile : *{selected['name']}*\n"
        f"  Email   : `{email}`\n\n"
        "Proceed?",
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardMarkup(
            [["✅ Yes, add it"], ["❌ Cancel"]],
            one_time_keyboard=True,
            resize_keyboard=True,
        ),
    )
    return NX_WAIT_CONFIRM


async def nettrix_receive_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """User confirms or cancels the email addition."""
    if not await _is_authorized(update):
        return ConversationHandler.END

    text = update.message.text.strip().lower()

    # ── Cancel path ──
    if "cancel" in text or "❌" in text:
        pt = context.user_data.get("nx_prov_task")
        if pt and not pt.done():
            pt.cancel()
        await _safe_reply(
            update.message,
            "❌ Cancelled. Send /nettrix to begin again.",
            reply_markup=ReplyKeyboardRemove(),
        )
        return ConversationHandler.END

    # ── Confirm path ──
    cookies  = context.user_data["nx_cookies"]
    selected = context.user_data["nx_selected"]
    email    = context.user_data["nx_email"]

    await _safe_reply(update.message, "⏳ Processing…", reply_markup=ReplyKeyboardRemove())

    try:
        prov_task = context.user_data.get("nx_prov_task")

        # Try to use the pre-warmed provision token first
        if prov_task is not None:
            try:
                prov = await prov_task
            except (asyncio.CancelledError, Exception):
                prov = await _run_blocking(ale_provision, cookies, selected["guid"])
        else:
            prov = await _run_blocking(ale_provision, cookies, selected["guid"])

        enc_email = await _run_blocking(
            encrypt_email, email, prov["kid"], prov["wrapped_key"], prov["private_key"]
        )
        result = await _run_blocking(
            nf_update_profile_email, cookies, selected["guid"], enc_email, prov["ale_token"]
        )

        # ── Parse Netflix response ──
        gql_errors = result.get("errors") or []
        data_block = result.get("data") or {}
        mutation   = (
            data_block.get("growthSetProfileEmail")
            or data_block.get("updateProfileEmail")
            or {}
        )
        typename   = mutation.get("__typename", "")
        error_code = mutation.get("errorCode", "")

        if gql_errors:
            err_msg = gql_errors[0].get("message", "Unknown GraphQL error")
            await _safe_reply(
                update.message,
                f"❌ Netflix error:\n`{err_msg}`\n\nSend /nettrix to try again.",
                parse_mode="Markdown",
            )
        elif "Error" in typename or error_code:
            friendly = _NF_ERRORS.get(error_code)
            if friendly:
                await _safe_reply(
                    update.message,
                    f"{friendly}\n\nSend /nettrix to try again.",
                    parse_mode="Markdown",
                )
            else:
                await _safe_reply(
                    update.message,
                    f"❌ Netflix returned an error:\n`{error_code or typename}`\n\nSend /nettrix to try again.",
                    parse_mode="Markdown",
                )
        else:
            await _safe_reply(
                update.message,
                f"✅ *Success!*\n\n"
                f"  Profile : *{selected['name']}*\n"
                f"  Email   : `{email}`\n\n"
                "Email added to the profile! 🎉\n\nSend /nettrix to do another.",
                parse_mode="Markdown",
            )

    except Exception as e:
        err = str(e)
        if "expired" in err.lower() or "401" in err:
            msg = "❌ Cookies expired. Export fresh cookies and send /nettrix."
        elif "AleProvision" in err:
            msg = f"❌ Netflix ALE error:\n`{err}`"
        else:
            msg = f"❌ Unexpected error:\n`{err}`"
        await _safe_reply(update.message, msg, parse_mode="Markdown")

    return ConversationHandler.END


async def nettrix_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/cancel fallback inside the nettrix conversation."""
    await _safe_reply(
        update.message,
        "❌ Cancelled. Send /nettrix to begin again.",
        reply_markup=ReplyKeyboardRemove(),
    )
    return ConversationHandler.END


# ── Factory ───────────────────────────────────────────────────────────────────────

def build_nettrix_handler() -> ConversationHandler:
    """
    Build and return the /nettrix ConversationHandler.
    Register it in main_optimized.py BEFORE the generic text MessageHandler.
    """
    return ConversationHandler(
        entry_points=[CommandHandler("nettrix", nettrix_start)],
        states={
            NX_WAIT_COOKIES: [
                MessageHandler(
                    filters.Document.MimeType("text/plain"), nettrix_receive_file
                ),
                MessageHandler(
                    filters.Document.FileExtension("txt"), nettrix_receive_file
                ),
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND, nettrix_receive_text
                ),
            ],
            NX_WAIT_PROFILE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, nettrix_receive_profile)
            ],
            NX_WAIT_EMAIL: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, nettrix_receive_email)
            ],
            NX_WAIT_CONFIRM: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, nettrix_receive_confirm)
            ],
        },
        fallbacks=[CommandHandler("cancel", nettrix_cancel)],
        allow_reentry=True,
        name="nettrix_conv",
    )
