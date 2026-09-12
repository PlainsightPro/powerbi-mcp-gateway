"""Fabric REST client used with the signed-in user's token: it lists what *that user* can reach."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

import httpx


class FabricAccessError(RuntimeError):
    """The user's token was rejected by the Fabric API (expired, missing consent, no license)."""


@dataclass(frozen=True)
class SemanticModelRef:
    id: str
    name: str
    workspace_id: str
    workspace_name: str
    description: str = ""


class FabricClient:
    """Thin async wrapper over the Fabric core REST API (workspaces and semantic models)."""

    def __init__(
        self,
        user_token: str,
        base_url: str = "https://api.fabric.microsoft.com/v1",
        transport: httpx.AsyncBaseTransport | None = None,
        concurrency: int = 8,
    ) -> None:
        self._client = httpx.AsyncClient(
            base_url=base_url,
            headers={"Authorization": f"Bearer {user_token}"},
            timeout=30.0,
            transport=transport,
        )
        self._sem = asyncio.Semaphore(concurrency)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _get_all(self, path: str) -> list[dict]:
        """GET a Fabric list endpoint and follow `continuationToken` paging."""
        items: list[dict] = []
        params: dict[str, str] = {}
        while True:
            async with self._sem:
                resp = await self._client.get(path, params=params)
            if resp.status_code in (401, 403):
                raise FabricAccessError(f"Fabric API {resp.status_code} on {path}: {resp.text[:300]}")
            resp.raise_for_status()
            body = resp.json()
            items.extend(body.get("value", []))
            token = body.get("continuationToken")
            if not token:
                return items
            params = {"continuationToken": token}

    async def list_workspaces(self) -> list[dict]:
        return await self._get_all("/workspaces")

    async def list_semantic_models(self, workspace_id: str) -> list[dict]:
        return await self._get_all(f"/workspaces/{workspace_id}/semanticModels")

    async def list_accessible_models(self) -> list[SemanticModelRef]:
        """Every semantic model in every workspace the user can open, as one flat list."""
        workspaces = await self.list_workspaces()

        async def models_of(ws: dict) -> list[SemanticModelRef]:
            try:
                models = await self.list_semantic_models(ws["id"])
            except (FabricAccessError, httpx.HTTPStatusError):
                return []  # a workspace the user can see but whose items are not listable
            return [
                SemanticModelRef(
                    id=m["id"],
                    name=m.get("displayName", ""),
                    workspace_id=ws["id"],
                    workspace_name=ws.get("displayName", ""),
                    description=m.get("description", "") or "",
                )
                for m in models
            ]

        nested = await asyncio.gather(*(models_of(ws) for ws in workspaces))
        return [m for group in nested for m in group]
