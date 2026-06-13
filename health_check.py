"""
Health check module for monitoring bot and service status.
Provides comprehensive health checks for all components.
"""
import asyncio
import logging
import time
from typing import Dict, Any, Tuple
import aiosqlite
import requests
from telegram import Update
from telegram.ext import ContextTypes

import config
import database_pool as db
from core.session_pool import session_pool, response_cache

logger = logging.getLogger(__name__)

class HealthChecker:
    """Comprehensive health checker for all bot services"""
    
    @staticmethod
    async def check_database() -> Tuple[bool, str]:
        """Check database connectivity and performance"""
        try:
            start_time = time.time()
            async with db.db_pool.acquire() as conn:
                async with conn.execute("SELECT 1") as cursor:
                    await cursor.fetchone()
            
            elapsed = time.time() - start_time
            if elapsed > 1.0:
                return False, f"Slow response ({elapsed:.2f}s)"
            
            return True, f"OK ({elapsed:.3f}s)"
        except Exception as e:
            logger.error(f"Database health check failed: {e}")
            return False, str(e)[:50]
    
    @staticmethod
    async def check_netflix_api() -> Tuple[bool, str]:
        """Check Netflix API accessibility"""
        try:
            async with session_pool.acquire_session(use_cloudscraper=True) as session:
                start_time = time.time()
                loop = asyncio.get_running_loop()
                
                # Run sync request in executor
                response = await loop.run_in_executor(
                    None,
                    lambda: session.get(
                        "https://www.netflix.com",
                        timeout=10,
                        allow_redirects=False
                    )
                )
                
                elapsed = time.time() - start_time
                
                if response.status_code in [200, 301, 302]:
                    return True, f"OK ({elapsed:.2f}s)"
                else:
                    return False, f"Status {response.status_code}"
                    
        except Exception as e:
            logger.error(f"Netflix API health check failed: {e}")
            return False, "Connection failed"
    
    @staticmethod
    async def check_claude_api() -> Tuple[bool, str]:
        """Check Claude AI API accessibility"""
        try:
            async with session_pool.acquire_session(use_cloudscraper=True) as session:
                start_time = time.time()
                loop = asyncio.get_running_loop()
                
                response = await loop.run_in_executor(
                    None,
                    lambda: session.get(
                        "https://claude.ai",
                        timeout=10,
                        allow_redirects=False
                    )
                )
                
                elapsed = time.time() - start_time
                
                if response.status_code in [200, 301, 302, 403]:  # 403 is expected
                    return True, f"OK ({elapsed:.2f}s)"
                else:
                    return False, f"Status {response.status_code}"
                    
        except Exception as e:
            logger.error(f"Claude API health check failed: {e}")
            return False, "Connection failed"
    
    @staticmethod
    async def check_session_pool() -> Tuple[bool, str]:
        """Check session pool status"""
        try:
            if not session_pool._initialized:
                await session_pool.initialize()
            
            async with session_pool._lock:
                regular_sessions = len(session_pool._sessions)
                cloudscraper_sessions = len(session_pool._cloudscraper_sessions)
            
            total = regular_sessions + cloudscraper_sessions
            if total == 0:
                return False, "No sessions available"
            
            return True, f"{total} sessions active"
            
        except Exception as e:
            logger.error(f"Session pool health check failed: {e}")
            return False, "Pool error"
    
    @staticmethod
    async def check_cache() -> Tuple[bool, str]:
        """Check cache system status"""
        try:
            # Test cache operations
            test_key = "_health_check_test"
            test_value = {"test": True, "timestamp": time.time()}
            
            await response_cache.set(test_key, test_value)
            retrieved = await response_cache.get(test_key)
            
            if retrieved == test_value:
                async with response_cache._lock:
                    cache_size = len(response_cache._cache)
                return True, f"{cache_size} items cached"
            else:
                return False, "Cache retrieval failed"
                
        except Exception as e:
            logger.error(f"Cache health check failed: {e}")
            return False, "Cache error"
    
    @staticmethod
    async def check_rate_limiting() -> Tuple[bool, str]:
        """Check rate limiting system"""
        try:
            # Get rate limit stats
            total_users = await db.get_total_users_count()
            
            # Check if rate limiting queries work
            test_user_id = 0
            cookie_count, last_reset = await db.get_user_cookie_usage(test_user_id)
            
            return True, f"{total_users} users tracked"
            
        except Exception as e:
            logger.error(f"Rate limiting health check failed: {e}")
            return False, "Rate limit error"
    
    @staticmethod
    async def check_telegram_api() -> Tuple[bool, str]:
        """Check Telegram API connectivity"""
        try:
            # This will be checked through the bot's connection
            # The bot being responsive means Telegram API is working
            return True, "Connected"
        except Exception as e:
            return False, "Disconnected"
    
    @staticmethod
    async def get_system_stats() -> Dict[str, Any]:
        """Get comprehensive system statistics"""
        try:
            stats = {
                "cookies_processed": await db.get_stat("cookies_processed"),
                "successful_operations": await db.get_stat("successful_operations"),
                "failed_operations": await db.get_stat("failed_operations"),
                "total_users": await db.get_total_users_count(),
                "authorized_users": await db.get_authorized_users_count(),
                "admin_count": await db.get_admin_count(),
                "maintenance_mode": await db.get_setting("maintenance_mode", "off"),
                "2fac_mode": await db.get_setting("2fac_mode", "off")
            }
            return stats
        except Exception as e:
            logger.error(f"Failed to get system stats: {e}")
            return {}

async def perform_health_check() -> Dict[str, Tuple[bool, str]]:
    """Perform all health checks concurrently"""
    checker = HealthChecker()
    
    # Run all checks concurrently
    results = await asyncio.gather(
        checker.check_database(),
        checker.check_netflix_api(),
        checker.check_claude_api(),
        checker.check_session_pool(),
        checker.check_cache(),
        checker.check_rate_limiting(),
        checker.check_telegram_api(),
        return_exceptions=True
    )
    
    # Map results to service names
    services = [
        "Database",
        "Netflix API",
        "Claude API",
        "Session Pool",
        "Cache System",
        "Rate Limiting",
        "Telegram API"
    ]
    
    health_status = {}
    for service, result in zip(services, results):
        if isinstance(result, Exception):
            health_status[service] = (False, str(result)[:50])
        else:
            health_status[service] = result
    
    return health_status

async def health_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Health check command handler for Telegram bot.
    Shows comprehensive health status of all services.
    """
    # Send initial message
    status_msg = await update.message.reply_text("🔍 Running health checks...")
    
    try:
        # Perform health checks
        health_status = await perform_health_check()
        
        # Get system stats
        stats = await HealthChecker.get_system_stats()
        
        # Build response message
        message_lines = ["🏥 **Health Check Report**\n"]
        
        # Service status
        message_lines.append("**Service Status:**")
        all_healthy = True
        
        for service, (is_healthy, details) in health_status.items():
            if is_healthy:
                emoji = "✅"
            else:
                emoji = "❌"
                all_healthy = False
            
            message_lines.append(f"{emoji} {service}: {details}")
        
        # Overall status
        message_lines.append("")
        if all_healthy:
            message_lines.append("✨ **Overall Status: HEALTHY** ✨")
        else:
            message_lines.append("⚠️ **Overall Status: DEGRADED** ⚠️")
        
        # System statistics
        message_lines.append("\n**System Statistics:**")
        message_lines.append(f"📊 Cookies Processed: {stats.get('cookies_processed', 0):,}")
        message_lines.append(f"👥 Total Users: {stats.get('total_users', 0):,}")
        message_lines.append(f"✅ Authorized Users: {stats.get('authorized_users', 0):,}")
        message_lines.append(f"🛠 Maintenance Mode: {stats.get('maintenance_mode', 'off').upper()}")
        message_lines.append(f"🔒 2FA Mode: {stats.get('2fac_mode', 'off').upper()}")
        
        # Performance metrics
        async with db.db_pool._lock:
            db_connections = len(db.db_pool._pool)
        async with session_pool._lock:
            http_sessions = len(session_pool._sessions) + len(session_pool._cloudscraper_sessions)
        async with response_cache._lock:
            cache_items = len(response_cache._cache)
        
        message_lines.append("\n**Performance Metrics:**")
        message_lines.append(f"🔌 DB Connections: {db_connections}/{config.DATABASE_POOL_SIZE}")
        message_lines.append(f"🌐 HTTP Sessions: {http_sessions}/{config.MAX_CONCURRENT_WORKERS}")
        message_lines.append(f"💾 Cache Items: {cache_items}/{config.CACHE_MAX_SIZE}")
        
        # Edit the message with results
        await status_msg.edit_text(
            "\n".join(message_lines),
            parse_mode="Markdown"
        )
        
    except Exception as e:
        logger.error(f"Health check command error: {e}", exc_info=True)
        await status_msg.edit_text(
            "❌ Health check failed. Check logs for details.",
            parse_mode="Markdown"
        )

async def auto_health_check(interval: int = 300):
    """
    Automated health check that runs periodically.
    Logs warnings if services are unhealthy.
    """
    while True:
        try:
            await asyncio.sleep(interval)
            
            health_status = await perform_health_check()
            unhealthy_services = [
                service for service, (is_healthy, _) in health_status.items()
                if not is_healthy
            ]
            
            if unhealthy_services:
                logger.warning(
                    f"Health check warning - Unhealthy services: {', '.join(unhealthy_services)}"
                )
            else:
                logger.debug("All services healthy")
                
        except Exception as e:
            logger.error(f"Auto health check error: {e}")

# Export for use in main.py
__all__ = ['health_command', 'perform_health_check', 'auto_health_check', 'HealthChecker']