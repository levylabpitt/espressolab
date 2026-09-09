from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from .config import Settings
from .models import metadata

_engine: AsyncEngine | None = None


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
        engine = create_async_engine(url)

        if engine.dialect.name == "sqlite":
            @event.listens_for(engine.sync_engine, "connect")
            def _enable_sqlite_foreign_keys(dbapi_connection, _record):
                cursor = dbapi_connection.cursor()
                cursor.execute("PRAGMA foreign_keys=ON")
                cursor.close()

        async with engine.begin() as conn:
            await conn.run_sync(metadata.create_all)

        _engine = engine
    return _engine


async def close_engine() -> None:
    global _engine
    if _engine is not None:
        await _engine.dispose()
        _engine = None
