"""
Startup utilities for PR Guardian AI
"""
import asyncio
import logging
from app.core.database import init_db, close_db
from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


async def migrate_database():
    """
    Run Alembic migrations programmatically.
    Call this on startup to ensure database is up to date.
    """
    try:
        from alembic.config import Config
        from alembic import command

        # Create Alembic config
        alembic_cfg = Config()
        alemby/versions_dir = "alembic"

        # Upgrade to latest migration
        command.upgrade(alembic_cfg, "head")
        logger.info("Database migrations completed successfully")
        return True
    except Exception as e:
        logger.error(f"Failed to run migrations: {e}")
        return False


async def startup_tasks():
    """
    Run all startup tasks.
    """
    logger.info("Running startup tasks...")

    # Initialize database tables
    await init_db()
    logger.info("Database initialized")

    # Run migrations (optional - can be disabled via env var)
    if settings.log_level == "debug":
        logger.info("Auto-migration enabled (debug mode)")
        await migrate_database()

    logger.info("Startup tasks completed")


async def shutdown_tasks():
    """
    Run all shutdown tasks.
    """
    logger.info("Running shutdown tasks...")
    await close_db()
    logger.info("Shutdown tasks completed")


if __name__ == "__main__":
    # Run startup tasks directly
    asyncio.run(startup_tasks())
