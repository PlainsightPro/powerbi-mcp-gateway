"""Fabric REST client used with the signed-in user's token: it lists what *that user* can reach."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

import httpx

from .hosted_mcp import retry_after_seconds

DEFAULT_BASE_URL = "https://api.fabric.microsoft.com/v1"
TIMEOUT_SECONDS = 30.0
MAX_RETRY_WAIT_SECONDS = 10.0


class FabricError(RuntimeError):
    """The Fabric API did not answer a listing request."""


class FabricAccessError(FabricError):
    """The user's token was rejected by the Fabric API (expired, missing consent, no license)."""


class FabricThrottledError(FabricError):
    """The Fabric API is rate-limiting this user (429) and one retry did not help."""

    def __init__(self, message: str, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


@dataclass(frozen=True)
class SemanticModelRef:
    id: str
    name: str
    workspace_id: str
    workspace_name: str
    description: str = ""


class FabricClient:
    """Thin async wrapper over the Fabric core REST API (workspaces and semantic models).

    Pass a shared `client` to reuse connections across users and calls; the token then travels as
    a per-request header and `aclose()` leaves the shared client open.
    """

    def __init__(
        self,
        user_token: str,
        base_url: str = DEFAULT_BASE_URL,
        transport: httpx.AsyncBaseTransport | None = None,
        concurrency: int = 8,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._headers = {"Authorization": f"Bearer {user_token}"}
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(timeout=TIMEOUT_SECONDS, transport=transport)
        self._sem = asyncio.Semaphore(concurrency)

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def _get(self, path: str, params: dict[str, str]) -> httpx.Response:
        """One GET with a single retry on 429, waiting what Retry-After asks for (capped)."""
        url = self._base_url + path
        async with self._sem:
            resp = await self._client.get(url, params=params, headers=self._headers)
            if resp.status_code != 429:
                return resp
            wait = retry_after_seconds(resp)
            await asyncio.sleep(min(wait if wait is not None else 1.0, MAX_RETRY_WAIT_SECONDS))
            return await self._client.get(url, params=params, headers=self._headers)

    async def _get_all(self, path: str) -> list[dict]:
        """GET a Fabric list endpoint and follow `continuationToken` paging."""
        items: list[dict] = []
        params: dict[str, str] = {}
        while True:
            resp = await self._get(path, params)
            if resp.status_code in (401, 403):
                raise FabricAccessError(f"Fabric API {resp.status_code} on {path}: {resp.text[:300]}")
            if resp.status_code == 429:
                wait = retry_after_seconds(resp)
                raise FabricThrottledError(f"Fabric API is throttling this user (429 on {path})", retry_after=wait)
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
        """Every semantic model in every workspace the user can open, as one flat list.

        A workspace the user can see but whose items they may not list (401/403) contributes
        nothing. Any other failure (throttling, a 5xx) is raised, so a partial list is never
        returned as if it were complete.
        """
        workspaces = await self.list_workspaces()

        async def models_of(ws: dict) -> list[SemanticModelRef]:
            try:
                models = await self.list_semantic_models(ws["id"])
            except FabricAccessError:
                return []
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

        nested = await asyncio.gather(*(models_of(ws) for ws in workspaces), return_exceptions=True)
        for outcome in nested:
            if isinstance(outcome, BaseException):
                raise outcome
        return [m for group in nested if isinstance(group, list) for m in group]
