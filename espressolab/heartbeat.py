"""Lets the logger record "I'm alive" so the portal's /status page can show
whether it's actually running — they're separate OS processes, so the portal
has no other way to know."""

from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncEngine

from .models import service_heartbeats

STALE_AFTER_SECONDS = 90


async def write_heartbeat(engine: AsyncEngine, service: str) -> None:
    async with engine.begin() as conn:
        await conn.execute(sa.delete(service_heartbeats).where(service_heartbeats.c.service == service))
        await conn.execute(sa.insert(service_heartbeats).values(service=service, last_seen=datetime.utcnow()))


async def get_heartbeat_status(engine: AsyncEngine, service: str) -> dict:
    async with engine.connect() as conn:
        last_seen = (
            await conn.execute(
                sa.select(service_heartbeats.c.last_seen).where(service_heartbeats.c.service == service)
            )
        ).scalar_one_or_none()

    if last_seen is None:
        return {"seen": False, "healthy": False, "seconds_ago": None}

    seconds_ago = (datetime.utcnow() - last_seen).total_seconds()
    return {"seen": True, "healthy": seconds_ago < STALE_AFTER_SECONDS, "seconds_ago": seconds_ago}
