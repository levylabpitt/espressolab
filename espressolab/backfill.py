"""One-off / catch-up script: pages through every shot Decaid knows about and
ingests it. Useful for the initial load of shot history that predates this
system, or to recover from the logger service being down for a while.

Usage:
    python -m espressolab.backfill
"""

import asyncio
import logging

import httpx

from .config import get_settings
from .db import close_pool, get_pool
from .decaid_client import DecaidClient
from .ingest import ingest_shot

log = logging.getLogger("espressolab.backfill")

PAGE_SIZE = 50


async def run_backfill() -> None:
    settings = get_settings()
    pool = await get_pool(settings)
    client = DecaidClient(settings)

    ingested = 0
    offset = 0
    async with httpx.AsyncClient(base_url=settings.decaid_rest_base, timeout=15) as http:
        while True:
            resp = await http.get("/api/v1/shots", params={"limit": PAGE_SIZE, "offset": offset})
            resp.raise_for_status()
            page = resp.json()
            items = page["items"]
            if not items:
                break

            for summary in items:
                shot_id = summary["id"]
                shot = await client.get_shot(shot_id)
                await ingest_shot(pool, shot)
                ingested += 1
                log.info("Backfilled shot %s (%d so far)", shot_id, ingested)

            offset += PAGE_SIZE

    log.info("Backfill complete: %d shots ingested", ingested)
    await close_pool()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    asyncio.run(run_backfill())


if __name__ == "__main__":
    main()
