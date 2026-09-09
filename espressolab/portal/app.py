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

import httpx
import sqlalchemy as sa
from fastapi import Cookie, Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from ..config import Settings, get_settings
from ..db import get_engine
from ..decaid_client import DecaidClient
from ..models import users
from ..session import make_user_cookie, read_user_cookie

log = logging.getLogger("espressolab.portal")

BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

app = FastAPI(title="espressolab portal")
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")

settings: Settings = get_settings()
basic_auth = HTTPBasic()

USER_COOKIE = "espressolab_user"


@app.on_event("startup")
async def on_startup() -> None:
    await get_engine(settings)
    client = DecaidClient(settings)
    try:
        status = await client.get_webui_status()
        if not status.get("serving"):
            log.info("Decaid WebUI server not running yet, starting it")
            await client.start_webui()
    except httpx.HTTPError:
        log.warning("Could not reach Decaid at %s yet (is it running?)", settings.decaid_rest_base)


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
                    "decaid_webui_url": DecaidClient(settings).webui_url,
                },
            )

    active_users = await get_active_users()
    return templates.TemplateResponse("picker.html", {"request": request, "users": active_users})


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

    response = RedirectResponse(url="/", status_code=303)
    response.set_cookie(USER_COOKIE, make_user_cookie(str(user["id"])), max_age=60 * 60 * 12, httponly=True)
    return response


@app.post("/switch")
async def switch_user():
    response = RedirectResponse(url="/", status_code=303)
    response.delete_cookie(USER_COOKIE)
    return response


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
    return templates.TemplateResponse("admin.html", {"request": request, "users": all_users})


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
