import logging

import sqlalchemy as sa
from sqlalchemy import event, inspect
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from .config import Settings
from .models import metadata

log = logging.getLogger("espressolab.db")

_engine: AsyncEngine | None = None


def _add_missing_columns(sync_conn) -> None:
    """Poor-man's migration: metadata.create_all only creates missing
    *tables*, it never alters ones that already exist. So when a column is
    added to models.py, add it here too so already-deployed databases pick
    it up automatically — additive only (new nullable columns), never drops
    or changes anything, so existing data/rows are untouched."""
    inspector = inspect(sync_conn)
    for table in metadata.sorted_tables:
        existing_columns = {col["name"] for col in inspector.get_columns(table.name)}
        for column in table.columns:
            if column.name in existing_columns:
                continue
            col_type = column.type.compile(dialect=sync_conn.dialect)
            sync_conn.execute(sa.text(f'ALTER TABLE "{table.name}" ADD COLUMN "{column.name}" {col_type}'))
            log.info("Added missing column %s.%s", table.name, column.name)


def _normalize_url(raw_url: str) -> str:
    """Accept plain postgresql://... or sqlite:///... URLs and pick the async driver."""
    if raw_url.startswith("postgres://"):
        raw_url = "postgresql://" + raw_url[len("postgres://") :]
    if raw_url.startswith("postgresql://"):
        return "postgresql+asyncpg://" + raw_url[len("postgresql://") :]
    if raw_url.startswith("sqlite://"):
        return "sqlite+aiosqlite://" + raw_url[len("sqlite://") :]
    return raw_url


async def get_engine(settings: Settings) -> AsyncEngine:
    global _engine
    if _engine is None:
        url = _normalize_url(settings.database_url)
        # pool_pre_ping: check a pooled connection is still alive before handing
        # it out, instead of finding out mid-query ("connection is closed") —
        # matters over a network connection to a remote server, which can drop
        # idle connections without us knowing. pool_recycle: also proactively
        # replace connections before they get that old in the first place.
        engine = create_async_engine(url, pool_pre_ping=True, pool_recycle=1800)

        if engine.dialect.name == "sqlite":
            @event.listens_for(engine.sync_engine, "connect")
            def _enable_sqlite_foreign_keys(dbapi_connection, _record):
                cursor = dbapi_connection.cursor()
                cursor.execute("PRAGMA foreign_keys=ON")
                cursor.close()

        async with engine.begin() as conn:
            await conn.run_sync(metadata.create_all)
            await conn.run_sync(_add_missing_columns)

        _engine = engine
    return _engine


async def close_engine() -> None:
    global _engine
    if _engine is not None:
        await _engine.dispose()
        _engine = None
