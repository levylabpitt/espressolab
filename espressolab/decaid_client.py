"""Thin async client for Decaid's local REST API (see the `api/rest_v1.yml`
spec bundled with the Decaid app for the full contract)."""

import httpx

from .config import Settings


class DecaidClient:
    def __init__(self, settings: Settings):
        self._base = settings.decaid_rest_base
        self._webui_base = settings.decaid_webui_base

    async def get_shot(self, shot_id: str) -> dict:
        async with httpx.AsyncClient(base_url=self._base, timeout=10) as client:
            resp = await client.get(f"/api/v1/shots/{shot_id}")
            resp.raise_for_status()
            return resp.json()

    async def set_workflow_context(self, *, drinker_name: str, user_id: str) -> dict:
        """Tag the pending shot with who it's for, ahead of it being pulled.

        Uses PUT /api/v1/workflow with a partial `context`, which Decaid
        deep-merges into the current workflow rather than replacing it.
        """
        patch = {
            "context": {
                "drinkerName": drinker_name,
                "extras": {"espressolab_user_id": user_id},
            }
        }
        async with httpx.AsyncClient(base_url=self._base, timeout=10) as client:
            resp = await client.put("/api/v1/workflow", json=patch)
            resp.raise_for_status()
            return resp.json()

    async def get_webui_status(self) -> dict:
        async with httpx.AsyncClient(base_url=self._base, timeout=5) as client:
            resp = await client.get("/api/v1/webui/server/status")
            resp.raise_for_status()
            return resp.json()

    async def start_webui(self) -> dict:
        async with httpx.AsyncClient(base_url=self._base, timeout=5) as client:
            resp = await client.post("/api/v1/webui/server/start")
            resp.raise_for_status()
            return resp.json()

    @property
    def webui_url(self) -> str:
        return self._webui_base
