"""
Netflix TV cookie vault management.
Stores cookies on disk, provides random cookie retrieval for TV login.
"""

import os
import re
import random
import string
import logging
import zipfile
import io
import threading

import config
from .netflix_cookie_extractor import extract_cookie_dict

logger = logging.getLogger(__name__)

cookie_lock = threading.Lock()


def get_vault_cookies():
    vault_dir = config.TV_VAULT_DIR
    if not os.path.exists(vault_dir):
        return []
    return [f for f in os.listdir(vault_dir) if f.lower().endswith((".txt", ".json"))]


def count_vault_cookies():
    return len(get_vault_cookies())


def get_random_cookie_file():
    """
    Pick a random cookie file from the vault, read its content, and delete it.
    Returns (filename, content) or (None, None) if vault is empty.
    """
    with cookie_lock:
        files = get_vault_cookies()
        if not files:
            return None, None
        filename = random.choice(files)
        filepath = os.path.join(config.TV_VAULT_DIR, filename)
        try:
            with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
            os.remove(filepath)
            return filename, content
        except Exception:
            return None, None


def add_cookies_to_vault(zip_bytes):
    """
    Extract cookie files from a ZIP and save valid ones to the vault.
    Returns (added_count, skipped_count).
    """
    os.makedirs(config.TV_VAULT_DIR, exist_ok=True)
    added = 0
    skipped = 0

    try:
        with zipfile.ZipFile(io.BytesIO(zip_bytes), 'r') as zf:
            for name in zf.namelist():
                if name.endswith('/') or name.startswith('__MACOSX') or name.startswith('.'):
                    continue
                if not name.lower().endswith(('.txt', '.json')):
                    skipped += 1
                    continue
                try:
                    content = zf.read(name).decode('utf-8', errors='ignore')
                    cookies = extract_cookie_dict(content)
                    if not cookies:
                        skipped += 1
                        continue
                    base = os.path.basename(name)
                    safe_name = re.sub(r'[<>:"/\\|?*]', '_', base)
                    dest = os.path.join(config.TV_VAULT_DIR, safe_name)
                    if os.path.exists(dest):
                        suffix = ''.join(random.choices(string.ascii_uppercase + string.digits, k=5))
                        name_part, ext = os.path.splitext(safe_name)
                        dest = os.path.join(config.TV_VAULT_DIR, f"{name_part}_{suffix}{ext}")
                    with open(dest, 'w', encoding='utf-8') as f:
                        f.write(content)
                    added += 1
                except Exception:
                    skipped += 1
    except Exception as e:
        logger.error(f"Error processing vault ZIP: {e}")

    return added, skipped
