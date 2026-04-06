# -*- coding: utf-8 -*-
"""Bot entry point — run with: python -m bot"""

import asyncio
import logging
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties

from bot.config import settings
from bot.handlers import start, create

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
    datefmt="%H:%M:%S",
)
# Quieten noisy libs
logging.getLogger("aiohttp").setLevel(logging.WARNING)
logging.getLogger("aiogram").setLevel(logging.WARNING)

logger = logging.getLogger("skazka_bot")


async def main():
    logger.info("Starting Skazka Bot...")

    # Ensure media dir exists
    settings.media_dir.mkdir(parents=True, exist_ok=True)

    # Initialize database
    from db.database import init_db
    await init_db()

    # Initialize config manager
    import db.database as db_mod
    from db.config_manager import cfg
    cfg.set_pool(db_mod._pool)
    await cfg.seed_defaults()

    bot = Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode="HTML"),
    )
    dp = Dispatcher()

    # Register routers
    dp.include_router(start.router)
    dp.include_router(create.router)

    logger.info("Bot started. Polling...")
    try:
        await dp.start_polling(bot)
    finally:
        from db.database import close_db
        from engine.http_session import close_session
        await close_session()
        await close_db()
        logger.info("Shutdown complete.")


if __name__ == "__main__":
    asyncio.run(main())
