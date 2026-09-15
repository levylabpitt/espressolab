"""Touchscreen-facing web portal.

Flow: the picker screen ("who's brewing?") is shown first. Picking a person
tags Decaid's in-progress workflow with their name (so the shot it's about to
record already carries the attribution), then embeds Decaid's own web portal
below a small "brewing as ..." bar so the person can pull their shot without
leaving this page.
"""

import logging
import secrets
from pathlib import Path
from urllib.parse import quote

import httpx
import sqlalchemy as sa
from fastapi import Cookie, Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from ..asana_sync import sync_from_asana
from ..config import Settings, get_settings
from ..db import get_engine
from ..decaid_client import DecaidClient
from ..heartbeat import get_heartbeat_status
from ..models import shot_feedback, shots, users
from ..session import cookie_seconds_remaining, make_user_cookie, read_user_cookie

log = logging.getLogger("espressolab.portal")

BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

app = FastAPI(title="espressolab portal")
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")

settings: Settings = get_settings()
basic_auth = HTTPBasic()

USER_COOKIE = "espressolab_user"

FLAVOR_TAGS = [
    "Fruity", "Floral", "Chocolatey", "Nutty", "Caramel", "Sweet",
    "Sour", "Bitter", "Balanced", "Syrupy", "Watery", "Burnt",
]

# Decaid's WebUI sends X-Frame-Options: SAMEORIGIN, which blocks framing it
# from our own origin (different port = different origin). So instead of
# pointing the iframe straight at it, we proxy it through this origin — same
# trick browsers extensions use — and drop the header on the way through.
DECAID_PROXY_ENTRY = "dw"
_STRIPPED_PROXY_HEADERS = {
    "x-frame-options",
    "content-security-policy",
    "content-length",
    "content-encoding",
    "transfer-encoding",
    "connection",
}

# A Flutter web build is dozens of files (JS, wasm, fonts, assets). Reuse one
# connection-pooled client across all of them instead of opening a fresh TCP
# connection per file — that was the main source of the slow first load.
_proxy_client: httpx.AsyncClient | None = None


@app.on_event("startup")
async def on_startup() -> None:
    global _proxy_client
    _proxy_client = httpx.AsyncClient(follow_redirects=True, timeout=15)

    await get_engine(settings)
    client = DecaidClient(settings)
    try:
        status = await client.get_webui_status()
        if not status.get("serving"):
            log.info("Decaid WebUI server not running yet, starting it")
            await client.start_webui()
    except httpx.HTTPError:
        log.warning("Could not reach Decaid at %s yet (is it running?)", settings.decaid_rest_base)


@app.on_event("shutdown")
async def on_shutdown() -> None:
    if _proxy_client is not None:
        await _proxy_client.aclose()


async def get_active_users():
    engine = await get_engine(settings)
    async with engine.connect() as conn:
        result = await conn.execute(sa.select(users).where(users.c.active).order_by(users.c.display_name))
        return result.mappings().all()


async def get_user(user_id: str):
    engine = await get_engine(settings)
    async with engine.connect() as conn:
        result = await conn.execute(sa.select(users).where(users.c.id == user_id))
        return result.mappings().first()


async def get_user_stats(user_id: str) -> dict:
    engine = await get_engine(settings)
    async with engine.connect() as conn:
        shot_count = (
            await conn.execute(sa.select(sa.func.count()).select_from(shots).where(shots.c.user_id == user_id))
        ).scalar_one()
        last_profile = (
            await conn.execute(
                sa.select(shots.c.profile_title)
                .where(shots.c.user_id == user_id)
                .order_by(shots.c.started_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
    return {"shot_count": shot_count, "last_profile": last_profile}


@app.get("/", response_class=HTMLResponse)
async def home(request: Request, espressolab_user: str | None = Cookie(default=None)):
    user_id = read_user_cookie(espressolab_user, settings.portal_session_idle_minutes)
    if user_id:
        user = await get_user(user_id)
        if user:
            return templates.TemplateResponse(
                "brew.html",
                {
                    "request": request,
                    "user": user,
                    "stats": await get_user_stats(user_id),
                    "decaid_proxy_url": f"/{DECAID_PROXY_ENTRY}",
                    "session_seconds_remaining": cookie_seconds_remaining(
                        espressolab_user, settings.portal_session_idle_minutes
                    ),
                },
            )

    active_users = await get_active_users()
    return templates.TemplateResponse(
        "picker.html",
        {
            "request": request,
            "users": active_users,
            "screensaver_idle_minutes": settings.screensaver_idle_minutes,
            "screensaver_url": "/static/screensaver/espresso_10.html",
        },
    )


@app.post("/select/{user_id}")
async def select_user(user_id: str):
    user = await get_user(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="Unknown user")

    client = DecaidClient(settings)
    try:
        await client.set_workflow_context(drinker_name=user["display_name"], user_id=str(user["id"]))
    except httpx.HTTPError:
        log.exception("Could not tag Decaid workflow for user %s", user["display_name"])
        # Still let them through — the shot will just land unattributed.

    try:
        if await client.wake_if_sleeping():
            log.info("Woke the machine for %s", user["display_name"])
    except httpx.HTTPError:
        log.exception("Could not check/wake machine state for %s", user["display_name"])

    response = RedirectResponse(url="/", status_code=303)
    response.set_cookie(USER_COOKIE, make_user_cookie(str(user["id"])), max_age=60 * 60 * 12, httponly=True)
    return response


@app.post("/switch")
async def switch_user():
    response = RedirectResponse(url="/", status_code=303)
    response.delete_cookie(USER_COOKIE)
    return response


# --- Feedback: rate a past shot from a flavor tag wheel ---------------------


async def get_recent_shots_for_user(user_id: str, limit: int = 10):
    engine = await get_engine(settings)
    async with engine.connect() as conn:
        result = await conn.execute(
            sa.select(
                shots.c.id,
                shots.c.started_at,
                shots.c.profile_title,
                shots.c.actual_dose_weight,
                shots.c.actual_yield,
                shot_feedback.c.overall_rating,
            )
            .select_from(
                shots.outerjoin(
                    shot_feedback,
                    sa.and_(shot_feedback.c.shot_id == shots.c.id, shot_feedback.c.user_id == user_id),
                )
            )
            .where(shots.c.user_id == user_id)
            .order_by(shots.c.started_at.desc())
            .limit(limit)
        )
        return result.mappings().all()


async def get_shot_for_user(shot_id: str, user_id: str):
    engine = await get_engine(settings)
    async with engine.connect() as conn:
        shot = (
            await conn.execute(sa.select(shots).where(shots.c.id == shot_id, shots.c.user_id == user_id))
        ).mappings().first()
        if not shot:
            return None, None
        feedback = (
            await conn.execute(
                sa.select(shot_feedback).where(
                    shot_feedback.c.shot_id == shot_id, shot_feedback.c.user_id == user_id
                )
            )
        ).mappings().first()
        return shot, feedback


@app.get("/feedback", response_class=HTMLResponse)
async def feedback_home(request: Request, espressolab_user: str | None = Cookie(default=None)):
    user_id = read_user_cookie(espressolab_user, settings.portal_session_idle_minutes)
    if not user_id:
        active_users = await get_active_users()
        return templates.TemplateResponse(
            "picker.html",
            {
                "request": request,
                "users": active_users,
                "screensaver_idle_minutes": settings.screensaver_idle_minutes,
                "screensaver_url": "/static/screensaver/espresso_10.html",
                "feedback_mode": True,
            },
        )

    user = await get_user(user_id)
    if not user:
        response = RedirectResponse(url="/feedback", status_code=303)
        response.delete_cookie(USER_COOKIE)
        return response

    return templates.TemplateResponse(
        "feedback_list.html",
        {"request": request, "user": user, "shots": await get_recent_shots_for_user(user_id)},
    )


@app.post("/feedback/identify/{user_id}")
async def feedback_identify(user_id: str):
    user = await get_user(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="Unknown user")
    response = RedirectResponse(url="/feedback", status_code=303)
    response.set_cookie(USER_COOKIE, make_user_cookie(str(user["id"])), max_age=60 * 60 * 12, httponly=True)
    return response


@app.get("/feedback/{shot_id}", response_class=HTMLResponse)
async def feedback_form(request: Request, shot_id: str, espressolab_user: str | None = Cookie(default=None)):
    user_id = read_user_cookie(espressolab_user, settings.portal_session_idle_minutes)
    if not user_id:
        return RedirectResponse(url="/feedback", status_code=303)

    shot, feedback = await get_shot_for_user(shot_id, user_id)
    if not shot:
        raise HTTPException(status_code=404, detail="Shot not found")

    return templates.TemplateResponse(
        "feedback_form.html",
        {
            "request": request,
            "shot": shot,
            "feedback": feedback,
            "flavor_tags": FLAVOR_TAGS,
            "selected_tags": set((feedback or {}).get("flavor_tags") or []),
        },
    )


@app.post("/feedback/{shot_id}")
async def feedback_submit(
    shot_id: str,
    espressolab_user: str | None = Cookie(default=None),
    overall_rating: int = Form(...),
    flavor_tags: list[str] = Form([]),
    notes: str = Form(""),
):
    user_id = read_user_cookie(espressolab_user, settings.portal_session_idle_minutes)
    if not user_id:
        return RedirectResponse(url="/feedback", status_code=303)

    shot, _ = await get_shot_for_user(shot_id, user_id)
    if not shot:
        raise HTTPException(status_code=404, detail="Shot not found")

    engine = await get_engine(settings)
    async with engine.begin() as conn:
        await conn.execute(
            sa.delete(shot_feedback).where(shot_feedback.c.shot_id == shot_id, shot_feedback.c.user_id == user_id)
        )
        await conn.execute(
            sa.insert(shot_feedback).values(
                shot_id=shot_id,
                user_id=user_id,
                overall_rating=overall_rating,
                flavor_tags=flavor_tags,
                notes=notes.strip() or None,
            )
        )
    return RedirectResponse(url="/feedback", status_code=303)


# --- Status: quick on-screen health check, since a kiosk browser hides the --
# --- console — no other way to tell what's actually running.               -


@app.get("/status", response_class=HTMLResponse)
async def status_page(request: Request):
    engine = await get_engine(settings)
    client = DecaidClient(settings)

    decaid_online = False
    webui_serving = False
    try:
        webui_status = await client.get_webui_status()
        decaid_online = True
        webui_serving = bool(webui_status.get("serving"))
    except httpx.HTTPError:
        pass

    logger = await get_heartbeat_status(engine, "logger")

    return templates.TemplateResponse(
        "status.html",
        {
            "request": request,
            "decaid_online": decaid_online,
            "webui_serving": webui_serving,
            "logger": logger,
        },
    )


# --- Admin: manage the roster of lab members -------------------------------


def require_admin(credentials: HTTPBasicCredentials = Depends(basic_auth)) -> None:
    correct = secrets.compare_digest(credentials.password, settings.admin_password)
    if not (credentials.username == "admin" and correct):
        raise HTTPException(status_code=401, detail="Unauthorized", headers={"WWW-Authenticate": "Basic"})


@app.get("/admin", response_class=HTMLResponse, dependencies=[Depends(require_admin)])
async def admin_home(request: Request):
    engine = await get_engine(settings)
    async with engine.connect() as conn:
        result = await conn.execute(sa.select(users).order_by(users.c.display_name))
        all_users = result.mappings().all()
    return templates.TemplateResponse(
        "admin.html",
        {
            "request": request,
            "users": all_users,
            "asana_configured": bool(settings.asana_token and settings.asana_team_gid),
            "synced": request.query_params.get("synced"),
            "asana_error": request.query_params.get("asana_error"),
        },
    )


@app.post("/admin/sync-asana", dependencies=[Depends(require_admin)])
async def admin_sync_asana():
    engine = await get_engine(settings)
    try:
        count = await sync_from_asana(engine, settings)
    except Exception as exc:
        log.exception("Asana sync failed")
        return RedirectResponse(url=f"/admin?asana_error={quote(str(exc))}", status_code=303)
    return RedirectResponse(url=f"/admin?synced={count}", status_code=303)


@app.post("/admin/users", dependencies=[Depends(require_admin)])
async def admin_create_user(
    display_name: str = Form(...),
    avatar_emoji: str = Form("☕"),
    avatar_color: str = Form("#6f4e37"),
):
    engine = await get_engine(settings)
    async with engine.begin() as conn:
        await conn.execute(
            sa.insert(users).values(
                display_name=display_name.strip(),
                avatar_emoji=avatar_emoji.strip() or "☕",
                avatar_color=avatar_color.strip() or "#6f4e37",
            )
        )
    return RedirectResponse(url="/admin", status_code=303)


@app.post("/admin/users/{user_id}/toggle", dependencies=[Depends(require_admin)])
async def admin_toggle_user(user_id: str):
    engine = await get_engine(settings)
    async with engine.begin() as conn:
        await conn.execute(sa.update(users).where(users.c.id == user_id).values(active=sa.not_(users.c.active)))
    return RedirectResponse(url="/admin", status_code=303)


@app.post("/admin/users/{user_id}/delete", dependencies=[Depends(require_admin)])
async def admin_delete_user(user_id: str):
    engine = await get_engine(settings)
    async with engine.begin() as conn:
        await conn.execute(sa.delete(users).where(users.c.id == user_id))
    return RedirectResponse(url="/admin", status_code=303)


# --- Decaid WebUI reverse proxy ---------------------------------------------
# Registered last so it only catches requests no route above already claimed.


async def _proxy_to_decaid(upstream_path: str, query: str = "") -> Response:
    target = f"{settings.decaid_webui_base}/{upstream_path}"
    if query:
        target += f"?{query}"
    try:
        upstream = await _proxy_client.get(target)
    except httpx.HTTPError:
        raise HTTPException(status_code=502, detail="Could not reach Decaid's WebUI server")

    headers = {k: v for k, v in upstream.headers.items() if k.lower() not in _STRIPPED_PROXY_HEADERS}
    return Response(
        content=upstream.content,
        status_code=upstream.status_code,
        headers=headers,
        media_type=upstream.headers.get("content-type"),
    )


@app.get(f"/{DECAID_PROXY_ENTRY}")
async def decaid_webui_entry():
    return await _proxy_to_decaid("")


@app.get("/{path:path}")
async def decaid_webui_asset_proxy(path: str, request: Request):
    return await _proxy_to_decaid(path, str(request.url.query))
