"""Listens to Decaid's shotState WebSocket and logs each finished shot to
the database. Run as a long-lived background process on the same PC as
Decaid."""

import asyncio
import json
import logging
import os

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

# Frame types/states that plausibly mean "this shot just concluded". We don't
# rely on a single exact frame shape here — Decaid's shotId can go null again
# by the time a given conclusion-ish frame arrives, so instead we remember the
# last non-null shotId we've seen on this connection and act on it as soon as
# any of these show up.
_CONCLUSION_DECISION_KINDS = {"stop", "terminal", "abort"}
_CONCLUSION_STATES = {"finished", "idle"}


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


async def _ingest(engine, client: DecaidClient, shot_id: str) -> None:
    log.info("Shot %s appears finished, fetching full record", shot_id)
    shot = await _fetch_with_retry(client, shot_id)
    if shot is None:
        log.error("Giving up on shot %s: could not fetch record", shot_id)
        return
    try:
        await ingest_shot(engine, shot)
        log.info("Logged shot %s", shot_id)
    except Exception:
        log.exception("Failed to write shot %s to the database", shot_id)


class _ConnectionState:
    """Tracks the last shotId seen on the current WebSocket connection."""

    def __init__(self):
        self.pending_shot_id: str | None = None
        self.handled_shot_id: str | None = None


async def _handle_event(engine, client: DecaidClient, raw_message: str, conn_state: _ConnectionState) -> None:
    try:
        event = json.loads(raw_message)
    except json.JSONDecodeError:
        log.warning("Ignoring non-JSON shotState frame: %r", raw_message[:200])
        return

    shot_id = event.get("shotId")
    decision = event.get("decision") or {}
    state = event.get("state")

    log.debug(
        "shotState frame: event=%s state=%s decision.kind=%s decision.reason=%s shotId=%s",
        event.get("event"),
        state,
        decision.get("kind"),
        decision.get("reason"),
        shot_id,
    )

    if shot_id:
        conn_state.pending_shot_id = shot_id

    is_conclusion = (
        event.get("event") == "terminal"
        or decision.get("kind") in _CONCLUSION_DECISION_KINDS
        or state in _CONCLUSION_STATES
    )
    if not is_conclusion:
        return

    target_id = conn_state.pending_shot_id
    if not target_id or target_id == conn_state.handled_shot_id:
        return

    conn_state.handled_shot_id = target_id
    await _ingest(engine, client, target_id)


async def run_logger(settings: Settings) -> None:
    engine = await get_engine(settings)
    client = DecaidClient(settings)

    while True:
        try:
            log.info("Connecting to %s", settings.decaid_ws_url)
            async with websockets.connect(settings.decaid_ws_url, ping_interval=20) as ws:
                log.info("Connected. Waiting for shots to finish...")
                conn_state = _ConnectionState()
                async for raw_message in ws:
                    await _handle_event(engine, client, raw_message, conn_state)
        except (websockets.ConnectionClosed, OSError) as exc:
            log.warning("Lost connection to Decaid (%s), retrying in %ss", exc, RECONNECT_DELAY_SECONDS)
            await asyncio.sleep(RECONNECT_DELAY_SECONDS)


def main() -> None:
    level = os.getenv("LOG_LEVEL", "INFO").upper()
    logging.basicConfig(level=level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    settings = get_settings()
    asyncio.run(run_logger(settings))


if __name__ == "__main__":
    main()
