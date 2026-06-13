#!/usr/bin/env python3
"""
Production launcher for the optimized AetherX bot.
This properly initializes all components and handles the event loop.
"""

import os
import sys
import asyncio
import logging

# Fix for Windows console encoding
if sys.platform == 'win32':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

def check_environment():
    """Check that all requirements are met"""
    print("AetherX Bot - Optimized Version")
    print("=" * 50)
    
    # Check required files
    required_files = [
        'main_optimized.py',
        'database_pool.py', 
        'health_check.py',
        'core/session_pool.py',
        'handlers/files_optimized.py',
        'config.py'
    ]
    
    missing = []
    for file in required_files:
        if not os.path.exists(file):
            missing.append(file)
    
    if missing:
        print("ERROR: Missing required files:")
        for f in missing:
            print(f"  - {f}")
        sys.exit(1)
    
    print("[OK] All required files present")
    
    # Check configuration
    try:
        import config
        if not config.BOT_TOKEN:
            print("ERROR: BOT_TOKEN not configured")
            sys.exit(1)
        if not config.ADMIN_USER_ID:
            print("ERROR: ADMIN_USER_ID not configured")
            sys.exit(1)
        print(f"[OK] Configuration loaded")
        print(f"[OK] Admin ID: {config.ADMIN_USER_ID}")
    except Exception as e:
        print(f"ERROR: Failed to load config: {e}")
        sys.exit(1)
    
    return True

def run_bot():
    """Run the optimized bot with proper setup"""
    print("-" * 50)
    print("Starting bot services...")
    print("-" * 50)
    
    # Configure logging
    logging.basicConfig(
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        level=logging.INFO,
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler('bot_optimized.log', encoding='utf-8')
        ]
    )
    
    # Reduce noise from libraries
    logging.getLogger('httpx').setLevel(logging.WARNING)
    logging.getLogger('telegram').setLevel(logging.WARNING) 
    logging.getLogger('urllib3').setLevel(logging.WARNING)
    logging.getLogger('aiosqlite').setLevel(logging.WARNING)
    
    logger = logging.getLogger(__name__)
    
    try:
        # Apply nest_asyncio patch BEFORE importing main
        import nest_asyncio
        nest_asyncio.apply()
        
        # Now import and run the bot
        from main_optimized import main
        
        # Run the bot
        asyncio.run(main())
        
    except KeyboardInterrupt:
        print("\n[INFO] Bot stopped by user")
    except ImportError as e:
        print(f"\nERROR: Missing dependency: {e}")
        print("\nPlease install requirements:")
        print("  pip install aiosqlite nest_asyncio python-telegram-bot")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        print(f"\nERROR: {e}")
        print("\nCheck bot_optimized.log for details")
        sys.exit(1)

def main():
    """Main entry point"""
    # Check environment first
    if check_environment():
        run_bot()

if __name__ == "__main__":
    main()