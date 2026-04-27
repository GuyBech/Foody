"""
Alembic environment — configured for async SQLAlchemy (psycopg3 driver).
"""

import asyncio
import os
import sys
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

# Make src/ importable when running alembic from the project root
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from foody.db.models import Base  # noqa: E402 — must come after sys.path patch

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _get_url() -> str:
    # Prefer the DATABASE_URL env var (set by Vercel / .env); fall back to alembic.ini
    url = os.getenv("DATABASE_URL") or config.get_main_option("sqlalchemy.url", "")
    # Alembic needs a sync driver; psycopg3 works with both postgresql:// and postgresql+psycopg://
    return url.replace("postgresql+psycopg://", "postgresql://", 1)


def run_migrations_offline() -> None:
    context.configure(
        url=_get_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    cfg = config.get_section(config.config_ini_section, {})
    cfg["sqlalchemy.url"] = _get_url()
    connectable = async_engine_from_config(
        cfg,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
