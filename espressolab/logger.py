"""Listens to Decaid's shotState WebSocket and logs each finished shot to
Postgres. Run as a long-lived background process on the same PC as Decaid."""

import asyncio
import json
import logging

import httpx
import websockets

from .config import Settings, get_settings
from .db import get_engine
from .decaid_client import DecaidClient
from .ingest import ingest_shot

log = logging.getLogger("espressolab.logger")

RECONNECT_DELAY_SECONDS = 5
FETCH_RETRY_ATTEMPTS = 5
FETCH_RETRY_DELAY_SECONDS = 1


async def _fetch_with_retry(client: DecaidClient, shot_id: str) -> dict | None:
    for attempt in range(1, FETCH_RETRY_ATTEMPTS + 1):
        try:
            return await client.get_shot(shot_id)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404 and attempt < FETCH_RETRY_ATTEMPTS:
                await asyncio.sleep(FETCH_RETRY_DELAY_SECONDS)
                continue
            log.exception("Failed to fetch shot %s from Decaid", shot_id)
            return None
        except httpx.HTTPError:
            log.exception("Network error fetching shot %s from Decaid", shot_id)
            return None
    return None


async def _handle_event(engine, client: DecaidClient, raw_message: str) -> None:
    try:
        event = json.loads(raw_message)
    except json.JSONDecodeError:
        log.warning("Ignoring non-JSON shotState frame: %r", raw_message[:200])
        return

    if event.get("event") != "terminal":
        return

    shot_id = event.get("shotId")
    if not shot_id:
        return

    log.info("Shot %s finished, fetching full record", shot_id)
    shot = await _fetch_with_retry(client, shot_id)
    if shot is None:
        log.error("Giving up on shot %s: could not fetch record", shot_id)
        return

    try:
        await ingest_shot(engine, shot)
        log.info("Logged shot %s", shot_id)
    except Exception:
        log.exception("Failed to write shot %s to the database", shot_id)


async def run_logger(settings: Settings) -> None:
    engine = await get_engine(settings)
    client = DecaidClient(settings)

    while True:
        try:
            log.info("Connecting to %s", settings.decaid_ws_url)
            async with websockets.connect(settings.decaid_ws_url, ping_interval=20) as ws:
                log.info("Connected. Waiting for shots to finish...")
                async for raw_message in ws:
                    await _handle_event(engine, client, raw_message)
        except (websockets.ConnectionClosed, OSError) as exc:
            log.warning("Lost connection to Decaid (%s), retrying in %ss", exc, RECONNECT_DELAY_SECONDS)
            await asyncio.sleep(RECONNECT_DELAY_SECONDS)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    settings = get_settings()
    asyncio.run(run_logger(settings))


if __name__ == "__main__":
    main()
