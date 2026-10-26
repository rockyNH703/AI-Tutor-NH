"""Alembic environment (async, asyncpg).

The URL is taken, in order, from `alembic -x url=...`, `MIGRATION_DATABASE_URL`, then the
application settings (`DATABASE_URL` / `.env`). Production uses the dedicated migration owner;
development and tests remain compatible with `DATABASE_URL` alone.
"""

import asyncio
import os

from alembic import context
from sqlalchemy.ext.asyncio import create_async_engine

from app.db import models  # noqa: F401 - registers every table on Base.metadata
from app.db.base import Base
from app.settings import get_settings

config = context.config
target_metadata = Base.metadata


def _url() -> str:
    return (
        context.get_x_argument(as_dictionary=True).get("url")
        or os.environ.get("MIGRATION_DATABASE_URL")
        or get_settings().database_url
    )


def run_migrations_offline() -> None:
    context.configure(url=_url(), target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def _run(connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    engine = create_async_engine(_url())
    async with engine.connect() as connection:
        await connection.run_sync(_run)
    await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
