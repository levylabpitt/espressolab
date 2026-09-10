"""Pulls a team's members from Asana and adds/updates lab members from them:
name, email, and profile photo (downloaded locally so page loads don't
depend on Asana being reachable, and don't rely on Asana's photo URLs staying
valid forever).

Matching an Asana member to a users row, in order:
  1. asana_gid — set on a previous sync, the stable link.
  2. display_name (case-insensitive) — "adopts" an existing manually-added
     user instead of creating a duplicate.
  3. otherwise, create a new user.

Never deactivates or deletes anyone who's no longer on the Asana team — that
stays a manual /admin decision, so nobody unexpectedly vanishes from the
picker mid-lab-meeting.
"""

import logging
import uuid
from pathlib import Path

import httpx
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncEngine

from .config import Settings
from .models import users

log = logging.getLogger("espressolab.asana_sync")

ASANA_API_BASE = "https://app.asana.com/api/1.0"
AVATARS_DIR = Path(__file__).resolve().parent / "portal" / "static" / "avatars"


async def sync_from_asana(engine: AsyncEngine, settings: Settings) -> int:
    if not settings.asana_token or not settings.asana_team_gid:
        raise RuntimeError("Set ASANA_TOKEN and ASANA_TEAM_GID in .env before syncing")

    async with httpx.AsyncClient(
        base_url=ASANA_API_BASE,
        headers={"Authorization": f"Bearer {settings.asana_token}"},
        timeout=20,
    ) as asana:
        members = await _fetch_team_members(asana, settings.asana_team_gid)

    synced = 0
    async with httpx.AsyncClient(timeout=20) as plain_http:  # no Asana auth header — photo URLs are public
        for member in members:
            await _sync_one_member(engine, plain_http, member)
            synced += 1

    return synced


async def _fetch_team_members(asana: httpx.AsyncClient, team_gid: str) -> list[dict]:
    members = []
    url: str | None = f"/teams/{team_gid}/users"
    params = {"opt_fields": "gid,name,email,photo.image_128x128", "limit": 100}
    while url:
        resp = await asana.get(url, params=params)
        resp.raise_for_status()
        body = resp.json()
        members.extend(body["data"])
        next_page = body.get("next_page")
        url = next_page["uri"] if next_page and next_page.get("uri") else None
        params = None  # next_page.uri already carries the query params
    return members


async def _sync_one_member(engine: AsyncEngine, plain_http: httpx.AsyncClient, member: dict) -> None:
    gid = member["gid"]
    name = (member.get("name") or "").strip()
    if not name:
        log.warning("Skipping Asana member %s: no name", gid)
        return
    email = member.get("email")
    photo_url = ((member.get("photo") or {}).get("image_128x128"))

    async with engine.begin() as conn:
        row = (await conn.execute(sa.select(users).where(users.c.asana_gid == gid))).mappings().first()
        if not row:
            row = (
                await conn.execute(sa.select(users).where(sa.func.lower(users.c.display_name) == name.lower()))
            ).mappings().first()

        user_id = row["id"] if row else str(uuid.uuid4())
        avatar_path = await _download_avatar(plain_http, user_id, photo_url) if photo_url else None

        values = {"asana_gid": gid, "email": email}
        if avatar_path:
            values["avatar_image_path"] = avatar_path

        if row:
            await conn.execute(sa.update(users).where(users.c.id == row["id"]).values(**values))
        else:
            await conn.execute(sa.insert(users).values(id=user_id, display_name=name, **values))


async def _download_avatar(plain_http: httpx.AsyncClient, user_id: str, photo_url: str) -> str | None:
    try:
        resp = await plain_http.get(photo_url)
        resp.raise_for_status()
    except httpx.HTTPError:
        log.warning("Could not download Asana photo for user %s", user_id)
        return None

    AVATARS_DIR.mkdir(parents=True, exist_ok=True)
    file_path = AVATARS_DIR / f"{user_id}.jpg"
    file_path.write_bytes(resp.content)
    return f"/static/avatars/{user_id}.jpg"
