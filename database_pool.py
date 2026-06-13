"""
Enhanced database module with connection pooling, better error handling,
and optimized queries. Prevents SQL injection and improves performance.
"""
import aiosqlite
import logging
import time
from typing import Optional, List, Tuple, Dict, Any
from contextlib import asynccontextmanager
import asyncio
import config

logger = logging.getLogger(__name__)

class DatabasePool:
    """Database connection pool manager"""
    
    def __init__(self, database_file: str, pool_size: int = 10, min_size: int = 2):
        self.database_file = database_file
        self.pool_size = pool_size
        self.min_size = min_size
        self._pool: List[aiosqlite.Connection] = []
        self._lock = asyncio.Lock()
        self._initialized = False
        
    async def initialize(self):
        """Initialize the connection pool"""
        if self._initialized:
            return
            
        async with self._lock:
            if self._initialized:
                return
                
            # Create minimum connections
            for _ in range(self.min_size):
                conn = await self._create_connection()
                self._pool.append(conn)
                
            self._initialized = True
            logger.info(f"Database pool initialized with {self.min_size} connections")
    
    async def _create_connection(self) -> aiosqlite.Connection:
        """Create a new database connection with optimizations"""
        conn = await aiosqlite.connect(
            self.database_file,
            isolation_level=None  # Auto-commit mode for better performance
        )
        
        # Enable WAL mode for better concurrency
        await conn.execute("PRAGMA journal_mode=WAL")
        await conn.execute("PRAGMA synchronous=NORMAL")
        await conn.execute("PRAGMA cache_size=10000")
        await conn.execute("PRAGMA temp_store=MEMORY")
        
        return conn
    
    @asynccontextmanager
    async def acquire(self):
        """Acquire a connection from the pool"""
        if not self._initialized:
            await self.initialize()
            
        connection = None
        async with self._lock:
            if self._pool:
                connection = self._pool.pop()
            elif len(self._pool) < self.pool_size:
                connection = await self._create_connection()
                
        if connection is None:
            # Wait and retry if pool is exhausted
            await asyncio.sleep(0.1)
            async with self.acquire() as conn:
                yield conn
        else:
            try:
                yield connection
            finally:
                async with self._lock:
                    if len(self._pool) < self.pool_size:
                        self._pool.append(connection)
                    else:
                        await connection.close()
    
    async def close(self):
        """Close all connections in the pool"""
        async with self._lock:
            for conn in self._pool:
                await conn.close()
            self._pool.clear()
            self._initialized = False

# Global pool instance
db_pool = DatabasePool(
    config.DATABASE_FILE,
    config.DATABASE_POOL_SIZE,
    config.DATABASE_POOL_MIN_SIZE
)

# --- Async Database Setup with Indexes ---
async def setup_database():
    """Creates the necessary tables with proper indexes for performance"""
    try:
        async with db_pool.acquire() as db:
            # Users table with indexes
            await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                is_authorized BOOLEAN DEFAULT 0,
                is_admin BOOLEAN DEFAULT 0
            )""")
            
            # Create indexes for faster queries
            await db.execute("""
            CREATE INDEX IF NOT EXISTS idx_users_authorized 
            ON users(is_authorized) WHERE is_authorized = 1
            """)
            
            await db.execute("""
            CREATE INDEX IF NOT EXISTS idx_users_admin 
            ON users(is_admin) WHERE is_admin = 1
            """)
            
            # Settings table
            await db.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at INTEGER DEFAULT (strftime('%s', 'now'))
            )""")
            
            # Stats table
            await db.execute("""
            CREATE TABLE IF NOT EXISTS stats (
                key TEXT PRIMARY KEY,
                count INTEGER DEFAULT 0
            )""")
            
            # Managers table with index
            await db.execute("""
            CREATE TABLE IF NOT EXISTS managers (
                user_id INTEGER PRIMARY KEY,
                added_at INTEGER DEFAULT (strftime('%s', 'now'))
            )""")
            
            # User usage table with optimized structure
            await db.execute("""
            CREATE TABLE IF NOT EXISTS user_usage (
                user_id INTEGER PRIMARY KEY,
                cookie_count INTEGER DEFAULT 0,
                last_reset INTEGER DEFAULT (strftime('%s', 'now')),
                total_processed INTEGER DEFAULT 0
            )""")
            
            # Create index for rate limiting queries
            await db.execute("""
            CREATE INDEX IF NOT EXISTS idx_user_usage_last_reset 
            ON user_usage(last_reset)
            """)

            # Initialize default settings
            await db.execute("""
            INSERT OR IGNORE INTO settings (key, value) VALUES 
            ('maintenance_mode', 'off'),
            ('2fac_mode', 'off')
            """)
            
            await db.execute("""
            INSERT OR IGNORE INTO stats (key, count) VALUES 
            ('cookies_processed', 0),
            ('total_users', 0),
            ('successful_operations', 0),
            ('failed_operations', 0)
            """)

            await db.commit()
            
        logger.info("Database setup complete with optimized indexes")
    except aiosqlite.Error as e:
        logger.critical(f"Failed to set up database: {e}", exc_info=True)
        raise

# --- Settings Management with Caching ---
_settings_cache: Dict[str, Tuple[str, float]] = {}
CACHE_TTL = 60  # Cache settings for 60 seconds

async def get_setting(key: str, default: str = None) -> str:
    """Gets a setting value with caching"""
    # Check cache first
    if key in _settings_cache:
        value, timestamp = _settings_cache[key]
        if time.time() - timestamp < CACHE_TTL:
            return value
    
    try:
        async with db_pool.acquire() as db:
            async with db.execute(
                "SELECT value FROM settings WHERE key = ?", (key,)
            ) as cursor:
                row = await cursor.fetchone()
                value = row[0] if row else default
                
                # Update cache
                _settings_cache[key] = (value, time.time())
                return value
    except aiosqlite.Error as e:
        logger.error(f"Failed to get setting '{key}': {e}")
        return default

async def set_setting(key: str, value: str):
    """Sets a setting value and invalidates cache"""
    try:
        async with db_pool.acquire() as db:
            await db.execute("""
                INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET 
                    value = excluded.value,
                    updated_at = excluded.updated_at
            """, (key, str(value), int(time.time())))
            await db.commit()
            
            # Invalidate cache
            if key in _settings_cache:
                del _settings_cache[key]
    except aiosqlite.Error as e:
        logger.error(f"Failed to set setting '{key}': {e}")

# --- User & Authorization Management ---
async def is_user_authorized(user_id: int) -> bool:
    """Checks if a user is authorized or an admin"""
    try:
        async with db_pool.acquire() as db:
            async with db.execute(
                "SELECT 1 FROM users WHERE user_id = ? AND (is_authorized = 1 OR is_admin = 1)",
                (user_id,)
            ) as cursor:
                return await cursor.fetchone() is not None
    except aiosqlite.Error as e:
        logger.error(f"Failed to check authorization for user {user_id}: {e}")
        return False

async def is_user_admin(user_id: int) -> bool:
    """Checks if a user is an admin"""
    try:
        async with db_pool.acquire() as db:
            async with db.execute(
                "SELECT 1 FROM users WHERE user_id = ? AND is_admin = 1",
                (user_id,)
            ) as cursor:
                return await cursor.fetchone() is not None
    except aiosqlite.Error as e:
        logger.error(f"Failed to check admin status for user {user_id}: {e}")
        return False

async def is_user_manager(user_id: int) -> bool:
    """Checks if a user is a manager"""
    try:
        async with db_pool.acquire() as db:
            async with db.execute(
                "SELECT 1 FROM managers WHERE user_id = ?",
                (user_id,)
            ) as cursor:
                return await cursor.fetchone() is not None
    except aiosqlite.Error as e:
        logger.error(f"Failed to check manager status for user {user_id}: {e}")
        return False

async def add_manager(user_id: int) -> bool:
    """Adds a user as a manager"""
    try:
        async with db_pool.acquire() as db:
            await db.execute(
                "INSERT OR IGNORE INTO managers (user_id, added_at) VALUES (?, ?)",
                (user_id, int(time.time()))
            )
            await db.commit()
            return True
    except aiosqlite.Error as e:
        logger.error(f"Failed to add manager {user_id}: {e}")
        return False

async def remove_manager(user_id: int) -> bool:
    """Removes a user from managers"""
    try:
        async with db_pool.acquire() as db:
            cursor = await db.execute(
                "DELETE FROM managers WHERE user_id = ?",
                (user_id,)
            )
            await db.commit()
            return cursor.rowcount > 0
    except aiosqlite.Error as e:
        logger.error(f"Failed to remove manager {user_id}: {e}")
        return False

# --- Optimized Rate Limiting with Timestamps ---
async def get_user_cookie_usage(user_id: int) -> Tuple[int, Optional[int]]:
    """Returns (cookie_count, last_reset_timestamp) for rate limiting"""
    try:
        async with db_pool.acquire() as db:
            async with db.execute(
                "SELECT cookie_count, last_reset FROM user_usage WHERE user_id = ?",
                (user_id,)
            ) as cursor:
                row = await cursor.fetchone()
                if row:
                    return row[0], row[1]
                else:
                    # Initialize new user with timestamp
                    current_time = int(time.time())
                    await db.execute(
                        "INSERT INTO user_usage (user_id, cookie_count, last_reset) VALUES (?, 0, ?)",
                        (user_id, current_time)
                    )
                    await db.commit()
                    return 0, current_time
    except aiosqlite.Error as e:
        logger.error(f"Failed to get usage for user {user_id}: {e}")
        return 0, None

async def update_user_cookie_usage(user_id: int, cookie_count: int):
    """Updates user's cookie usage count"""
    try:
        async with db_pool.acquire() as db:
            await db.execute("""
                INSERT INTO user_usage (user_id, cookie_count, last_reset, total_processed) 
                VALUES (?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET 
                    cookie_count = excluded.cookie_count,
                    total_processed = total_processed + (excluded.cookie_count - cookie_count)
            """, (user_id, cookie_count, int(time.time()), cookie_count))
            await db.commit()
    except aiosqlite.Error as e:
        logger.error(f"Failed to update usage for user {user_id}: {e}")

async def reset_user_cookie_usage(user_id: int):
    """Resets user's cookie usage count"""
    try:
        async with db_pool.acquire() as db:
            await db.execute(
                "UPDATE user_usage SET cookie_count = 0, last_reset = ? WHERE user_id = ?",
                (int(time.time()), user_id)
            )
            await db.commit()
    except aiosqlite.Error as e:
        logger.error(f"Failed to reset usage for user {user_id}: {e}")

async def add_user(user_id: int, is_admin: bool = False):
    """Adds or updates a user"""
    try:
        async with db_pool.acquire() as db:
            await db.execute("""
                INSERT INTO users (user_id, is_authorized, is_admin) 
                VALUES (?, 1, ?)
                ON CONFLICT(user_id) DO UPDATE SET 
                    is_authorized = 1, 
                    is_admin = excluded.is_admin
            """, (user_id, is_admin))
            await db.commit()
            
            # Update user count stat
            await increment_stat('total_users')
    except aiosqlite.Error as e:
        logger.error(f"Failed to add or update user {user_id}: {e}")

async def revoke_user(user_id: int):
    """Revokes a user's authorization"""
    try:
        async with db_pool.acquire() as db:
            await db.execute(
                "UPDATE users SET is_authorized = 0 WHERE user_id = ?",
                (user_id,)
            )
            await db.commit()
    except aiosqlite.Error as e:
        logger.error(f"Failed to revoke user {user_id}: {e}")

# --- Stats & Counting with Batch Updates ---
async def get_total_users_count() -> int:
    """Counts the total number of users"""
    try:
        async with db_pool.acquire() as db:
            async with db.execute("SELECT COUNT(*) FROM users") as cursor:
                return (await cursor.fetchone())[0]
    except aiosqlite.Error:
        return 0

async def get_authorized_users_count() -> int:
    """Counts authorized users using index"""
    try:
        async with db_pool.acquire() as db:
            async with db.execute(
                "SELECT COUNT(*) FROM users WHERE is_authorized = 1 OR is_admin = 1"
            ) as cursor:
                return (await cursor.fetchone())[0]
    except aiosqlite.Error:
        return 0

async def get_admin_count() -> int:
    """Counts admin users using index"""
    try:
        async with db_pool.acquire() as db:
            async with db.execute(
                "SELECT COUNT(*) FROM users WHERE is_admin = 1"
            ) as cursor:
                return (await cursor.fetchone())[0]
    except aiosqlite.Error:
        return 0

async def get_all_authorized_users() -> List[int]:
    """Gets all authorized user IDs efficiently"""
    try:
        async with db_pool.acquire() as db:
            async with db.execute(
                "SELECT user_id FROM users WHERE is_authorized = 1 OR is_admin = 1"
            ) as cursor:
                rows = await cursor.fetchall()
                return [row[0] for row in rows]
    except aiosqlite.Error:
        return []

async def increment_stat(key: str, amount: int = 1):
    """Increments a statistic"""
    try:
        async with db_pool.acquire() as db:
            await db.execute("""
                INSERT INTO stats (key, count) VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET 
                    count = count + excluded.count
            """, (key, amount))
            await db.commit()
    except aiosqlite.Error as e:
        logger.error(f"Failed to increment stat '{key}': {e}")

async def get_stat(key: str) -> int:
    """Retrieves a statistic"""
    try:
        async with db_pool.acquire() as db:
            async with db.execute(
                "SELECT count FROM stats WHERE key = ?",
                (key,)
            ) as cursor:
                result = await cursor.fetchone()
                return result[0] if result else 0
    except aiosqlite.Error:
        return 0

async def check_access_allowed(user_id: int, mode: str = None) -> bool:
    """
    Check if user access should be allowed based on 2fac mode and user authorization.
    Optimized version with single query.
    """
    try:
        # Check admin status first (most privileged)
        if await is_user_admin(user_id):
            return True

        # For other modes, check 2fac setting
        twofac_mode = await get_setting('2fac_mode', 'off')
        
        if twofac_mode == 'on':
            # Public access allowed for non-privatization modes
            return True
        else:
            # Authorization required for all modes
            return await is_user_authorized(user_id)
            
    except Exception as e:
        logger.error(f"Failed to check access for user {user_id}: {e}")
        return False

# --- Cleanup function ---
async def cleanup():
    """Cleanup database connections"""
    await db_pool.close()