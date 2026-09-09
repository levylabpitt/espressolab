"""Pages through Decaid's shot history and ingests it. `ingest_shot` is
idempotent (delete-then-insert), so re-running this over shots we already
have is always safe — that's what makes it useful both as a one-off initial
load and as a periodic catch-up sweep (see logger.py) for whatever the live
WebSocket listener missed, e.g. while the database was unreachable.

Usage:
    python -m espressolab.backfill
"""

import asyncio
import logging

import httpx

from .config import get_settings
from .db import close_engine, get_engine
from .decaid_client import DecaidClient
from .ingest import ingest_shot

log = logging.getLogger("espressolab.backfill")

PAGE_SIZE = 50


async def catch_up(engine, client: DecaidClient, decaid_rest_base: str, *, page_size: int = PAGE_SIZE, max_pages: int | None = None) -> int:
    """Ingests shots from Decaid's history, most recent first. With
    max_pages=None, walks the entire history (initial backfill); with
    max_pages=1, just the most recent page (periodic catch-up sweep)."""
    ingested = 0
    offset = 0
    pages = 0
    async with httpx.AsyncClient(base_url=decaid_rest_base, timeout=15) as http:
        while max_pages is None or pages < max_pages:
            resp = await http.get("/api/v1/shots", params={"limit": page_size, "offset": offset})
            resp.raise_for_status()
            page = resp.json()
            items = page["items"]
            if not items:
                break

            for summary in items:
                shot_id = summary["id"]
                shot = await client.get_shot(shot_id)
                await ingest_shot(engine, shot)
                ingested += 1

            offset += page_size
            pages += 1

    return ingested


async def run_backfill() -> None:
    settings = get_settings()
    engine = await get_engine(settings)
    client = DecaidClient(settings)

    ingested = await catch_up(engine, client, settings.decaid_rest_base)

    log.info("Backfill complete: %d shots ingested", ingested)
    await close_engine()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    asyncio.run(run_backfill())


if __name__ == "__main__":
    main()
