#!/usr/bin/env python3
"""
Main entry point for AetherX ⚡🌟 Telegram Bot
This file automatically runs the optimized version of the bot.
"""

import sys
import os
import asyncio
import logging

# Ensure proper encoding for Windows
if sys.platform == 'win32':
    import io
    try:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')
    except:
        pass

# Add current directory to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

def main():
    """Main entry point - runs the optimized bot"""
    
    print("AetherX ⚡🌟 Bot - Starting...")
    print("=" * 50)
    
    # Basic logging setup
    logging.basicConfig(
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        level=logging.INFO,
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler('bot.log', encoding='utf-8')
        ]
    )
    
    # Disable noisy logs
    logging.getLogger('httpx').setLevel(logging.WARNING)
    logging.getLogger('telegram').setLevel(logging.WARNING)
    logging.getLogger('urllib3').setLevel(logging.WARNING)
    
    logger = logging.getLogger(__name__)
    
    try:
        # Check for required files
        required_files = [
            'main_optimized.py',
            'database_pool.py',
            'config.py'
        ]
        
        missing_files = [f for f in required_files if not os.path.exists(f)]
        if missing_files:
            logger.error(f"Missing required files: {missing_files}")
            print(f"ERROR: Missing files: {missing_files}")
            return 1
        
        print("✓ All required files present")
        
        # Import and run the optimized version
        print("🚀 Loading optimized bot...")
        from main_optimized import main as optimized_main
        
        # Apply nest_asyncio for compatibility
        import nest_asyncio
        nest_asyncio.apply()
        
        # Run the optimized bot
        asyncio.run(optimized_main())
        
    except ImportError as e:
        logger.error(f"Import error: {e}")
        print(f"\nERROR: Missing dependency - {e}")
        print("\nPlease install requirements:")
        print("pip install -r requirements.txt")
        return 1
        
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
        print("\n👋 Bot stopped by user")
        return 0
        
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        print(f"\nFATAL ERROR: {e}")
        print("Check bot.log for details")
        return 1

if __name__ == "__main__":
    try:
        exit_code = main()
        sys.exit(exit_code)
    except Exception as e:
        print(f"Critical error: {e}")
        sys.exit(1)