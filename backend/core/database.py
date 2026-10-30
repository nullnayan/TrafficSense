# @file: backend/core/database.py
# @exports: create_database, get_session, verify_postgis_connection
# @depends_on: backend/core/config.py
# @purpose: Async SQLAlchemy session factory and live PostGIS/seed/heartbeat health probe.
from collections.abc import AsyncIterator

from fastapi import Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from backend.core.config import Settings


def create_database(settings: Settings) -> tuple[AsyncEngine, async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(
        settings.database_url.get_secret_value(), pool_pre_ping=True,
        pool_size=5, max_overflow=5, pool_timeout=5,
        connect_args={'timeout': 10, 'command_timeout': 5},
    )
    return engine, async_sessionmaker(engine, expire_on_commit=False)


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    async with request.app.state.session_factory() as session:
        yield session


async def verify_postgis_connection(engine: AsyncEngine) -> dict:
    async with engine.connect() as connection:
        row = (await connection.execute(text('''
            SELECT PostGIS_Full_Version() AS postgis_version,
                (SELECT count(*) FROM gazetteer_places) AS gazetteer_count,
                (SELECT last_heartbeat_utc FROM system_heartbeat
                 WHERE service_name = 'national_macro_sentry') AS heartbeat_timestamp
        '''))).mappings().one()
    return dict(row)
