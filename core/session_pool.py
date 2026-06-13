"""
Optimized session management with connection pooling for API requests.
Replaces the simple session creation with a robust pool system.
"""
import asyncio
import logging
from typing import Optional, Dict, Any
from contextlib import asynccontextmanager
import time
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import cloudscraper
import config

logger = logging.getLogger(__name__)

class SessionPool:
    """Connection pool for HTTP sessions with retry logic and optimization"""
    
    def __init__(self, pool_size: int = 10, max_retries: int = 3):
        self.pool_size = pool_size
        self.max_retries = max_retries
        self._sessions = []
        self._cloudscraper_sessions = []
        self._lock = asyncio.Lock()
        self._initialized = False
        
        # Retry configuration
        self.retry_strategy = Retry(
            total=max_retries,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["HEAD", "GET", "OPTIONS", "POST"],
            backoff_factor=1
        )
        
    def _create_session(self) -> requests.Session:
        """Create an optimized requests session"""
        session = requests.Session()
        
        # Set common headers
        session.headers.update({
            'User-Agent': config.USER_AGENT,
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
            'Accept-Encoding': 'gzip, deflate',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1'
        })
        
        # Add retry adapter
        adapter = HTTPAdapter(
            max_retries=self.retry_strategy,
            pool_connections=20,
            pool_maxsize=20
        )
        session.mount("http://", adapter)
        session.mount("https://", adapter)
        
        # Set timeout
        session.request = self._wrap_request_with_timeout(session.request)
        
        return session
    
    def _create_cloudscraper_session(self) -> cloudscraper.CloudScraper:
        """Create a CloudScraper session for Cloudflare bypass"""
        session = cloudscraper.create_scraper(
            browser={
                'browser': 'chrome',
                'platform': 'windows',
                'desktop': True
            }
        )
        
        # Set common headers
        session.headers.update({
            'User-Agent': config.USER_AGENT,
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5'
        })
        
        return session
    
    def _wrap_request_with_timeout(self, original_request):
        """Wrap request method with default timeout"""
        def wrapped_request(*args, **kwargs):
            if 'timeout' not in kwargs:
                kwargs['timeout'] = config.REQUEST_TIMEOUT
            return original_request(*args, **kwargs)
        return wrapped_request
    
    async def initialize(self):
        """Initialize the session pool"""
        if self._initialized:
            return
            
        async with self._lock:
            if self._initialized:
                return
                
            # Create initial sessions
            for _ in range(min(3, self.pool_size)):
                self._sessions.append(self._create_session())
                self._cloudscraper_sessions.append(self._create_cloudscraper_session())
                
            self._initialized = True
            logger.info(f"Session pool initialized with {len(self._sessions)} sessions")
    
    @asynccontextmanager
    async def acquire_session(self, use_cloudscraper: bool = False):
        """Acquire a session from the pool"""
        if not self._initialized:
            await self.initialize()
            
        session = None
        async with self._lock:
            if use_cloudscraper:
                if self._cloudscraper_sessions:
                    session = self._cloudscraper_sessions.pop()
                elif len(self._cloudscraper_sessions) < self.pool_size:
                    session = self._create_cloudscraper_session()
            else:
                if self._sessions:
                    session = self._sessions.pop()
                elif len(self._sessions) < self.pool_size:
                    session = self._create_session()
        
        if session is None:
            # Pool exhausted, create temporary session
            session = (
                self._create_cloudscraper_session() 
                if use_cloudscraper 
                else self._create_session()
            )
            
        try:
            yield session
        finally:
            # Return session to pool
            async with self._lock:
                if use_cloudscraper:
                    if len(self._cloudscraper_sessions) < self.pool_size:
                        self._cloudscraper_sessions.append(session)
                else:
                    if len(self._sessions) < self.pool_size:
                        self._sessions.append(session)
    
    async def close(self):
        """Close all sessions in the pool"""
        async with self._lock:
            for session in self._sessions:
                try:
                    session.close()
                except Exception as e:
                    logger.error(f"Error closing session: {e}")
                    
            for session in self._cloudscraper_sessions:
                try:
                    session.close()
                except Exception as e:
                    logger.error(f"Error closing cloudscraper session: {e}")
                    
            self._sessions.clear()
            self._cloudscraper_sessions.clear()
            self._initialized = False

# Global session pool instance
session_pool = SessionPool(
    pool_size=config.MAX_CONCURRENT_WORKERS,
    max_retries=config.MAX_RETRIES
)

# Helper functions for backward compatibility
async def get_new_session(use_cloudscraper: bool = True) -> Any:
    """
    Get a session from the pool.
    For backward compatibility with existing code.
    """
    # Return a context manager that can be used with 'with' statement
    return session_pool.acquire_session(use_cloudscraper)

def get_new_session_sync(use_cloudscraper: bool = True) -> Any:
    """
    Synchronous version for non-async code.
    Creates a standalone session (not from pool).
    """
    if use_cloudscraper:
        session = cloudscraper.create_scraper(
            browser={
                'browser': 'chrome',
                'platform': 'windows',
                'desktop': True
            }
        )
    else:
        session = requests.Session()
        
    session.headers.update({
        'User-Agent': config.USER_AGENT,
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.5'
    })
    
    if not use_cloudscraper:
        retry_strategy = Retry(
            total=config.MAX_RETRIES,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["HEAD", "GET", "OPTIONS", "POST"],
            backoff_factor=1
        )
        adapter = HTTPAdapter(
            max_retries=retry_strategy,
            pool_connections=10,
            pool_maxsize=10
        )
        session.mount("http://", adapter)
        session.mount("https://", adapter)
    
    return session

# Response cache for reducing API calls
class ResponseCache:
    """Simple response cache with TTL"""
    
    def __init__(self, ttl: int = 300, max_size: int = 1000):
        self.ttl = ttl
        self.max_size = max_size
        self._cache: Dict[str, Tuple[Any, float]] = {}
        self._lock = asyncio.Lock()
        
    async def get(self, key: str) -> Optional[Any]:
        """Get cached response if not expired"""
        async with self._lock:
            if key in self._cache:
                value, timestamp = self._cache[key]
                if time.time() - timestamp < self.ttl:
                    logger.debug(f"Cache hit for {key}")
                    return value
                else:
                    del self._cache[key]
        return None
        
    async def set(self, key: str, value: Any):
        """Cache a response"""
        async with self._lock:
            # Clean old entries if cache is full
            if len(self._cache) >= self.max_size:
                current_time = time.time()
                expired_keys = [
                    k for k, (_, ts) in self._cache.items()
                    if current_time - ts >= self.ttl
                ]
                for k in expired_keys:
                    del self._cache[k]
                    
                # If still full, remove oldest
                if len(self._cache) >= self.max_size:
                    oldest_key = min(
                        self._cache.keys(),
                        key=lambda k: self._cache[k][1]
                    )
                    del self._cache[oldest_key]
                    
            self._cache[key] = (value, time.time())
            logger.debug(f"Cached response for {key}")
            
    async def clear(self):
        """Clear the cache"""
        async with self._lock:
            self._cache.clear()

# Global response cache
response_cache = ResponseCache(
    ttl=config.CACHE_TTL_SECONDS,
    max_size=config.CACHE_MAX_SIZE
)

# Cleanup function
async def cleanup():
    """Cleanup all resources"""
    await session_pool.close()
    await response_cache.clear()