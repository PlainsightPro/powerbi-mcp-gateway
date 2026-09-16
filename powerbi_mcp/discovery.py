"""Per-user discovery, schema caching and model selection."""

from __future__ import annotations

import asyncio
import copy
import time
from collections import OrderedDict
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from .catalog import Catalog
from .config import Settings
from .contracts import DiscoveryResult
from .errors import GatewayError, error_info
from .fabric import FabricClient, SemanticModelRef
from .hosted_mcp import HostedPowerBIMcp
from .schema import schema_body


class TtlCache:
    def __init__(self, ttl_seconds: float, max_entries: int = 256):
        self.ttl, self.max_entries = ttl_seconds, max_entries
        self.items = OrderedDict()

    def get(self, key):
        hit = self.items.get(key)
        if hit is None:
            return None
        expires, value = hit
        if expires <= time.monotonic():
            self.items.pop(key, None)
            return None
        self.items.move_to_end(key)
        return copy.deepcopy(value)

    def set(self, key, value):
        now = time.monotonic()
        for expired in [k for k, (expires, _) in self.items.items() if expires <= now]:
            self.items.pop(expired, None)
        self.items[key] = (now + self.ttl, copy.deepcopy(value))
        self.items.move_to_end(key)
        while len(self.items) > self.max_entries:
            self.items.popitem(last=False)

    def remove(self, key):
        self.items.pop(key, None)


def timestamp() -> str:
    return datetime.now(UTC).isoformat()


class Discovery:
    def __init__(
        self,
        settings: Settings,
        catalog: Catalog,
        *,
        fabric_factory: Callable[..., Any] = FabricClient,
        hosted_factory: Callable[..., Any] = HostedPowerBIMcp,
    ):
        self.settings, self.catalog = settings, catalog
        self.fabric_factory, self.hosted_factory = fabric_factory, hosted_factory
        self.models = TtlCache(settings.catalog_cache_seconds, settings.cache_max_entries)
        self.schemas = TtlCache(settings.schema_cache_seconds, settings.cache_max_entries)

    def hosted(self, token: str):
        return self.hosted_factory(token, self.settings.hosted_mcp_url)

    async def schema(self, user: str, token: str, model_id: str, refresh: bool = False) -> dict:
        key = (user, model_id.lower())
        if not refresh:
            cached = self.schemas.get(key)
            if cached is not None:
                return cached
        self.schemas.remove(key)
        client = self.hosted(token)
        try:
            schema = await client.get_schema(model_id)
            try:
                schema_body(schema)
            except (ValueError, TypeError, AttributeError) as exc:
                raise GatewayError("The hosted model schema has an unsupported structure.", kind="protocol") from exc
        finally:
            await client.aclose()
        self.schemas.set(key, schema)
        return schema

    async def discover(self, user: str, token: str, refresh: bool = False) -> DiscoveryResult:
        if not refresh:
            cached = self.models.get(user)
            if cached is not None:
                return cached.model_copy(update={"cached": True})
        self.models.remove(user)
        client = self.fabric_factory(token, self.settings.fabric_api_url)
        try:
            refs = await client.list_accessible_models(allow_partial=True)
            warnings = client.warnings
        finally:
            await client.aclose()
        result = DiscoveryResult(
            models=self.catalog.merge(refs),
            warnings=warnings,
            checked_at=timestamp(),
            status="partial" if warnings else "complete",
            total_matches=len(refs),
        )
        # A transiently missing workspace must never poison the user's catalogue cache.
        if not warnings:
            self.models.set(user, result)
        return result

    async def search(
        self,
        user: str,
        token: str,
        query: str = "",
        workspace: str = "",
        refresh: bool = False,
        verify_access: bool = False,
        offset: int = 0,
        limit: int = 25,
    ) -> DiscoveryResult:
        if not 1 <= limit <= 100 or offset < 0:
            raise ValueError("Use limit 1..100 and offset >= 0.")
        result = await self.discover(user, token, refresh)
        rows = result.models
        # Directly shared curated models need not appear in the workspace API. Check only matching
        # candidates and only on an explicit access check, bounded to 20 per request.
        if verify_access:
            known = {r["id"].lower() for r in rows}
            missing = [
                SemanticModelRef(e.id, e.name, e.workspace_id, e.workspace, e.description)
                for e in self.catalog.entries
                if e.id.lower() not in known
            ]
            candidates = self.catalog.search(self.catalog.merge(missing), query, workspace)
            if len(candidates) > 20:
                result.warnings.append(
                    {"message": "Only 20 directly shared catalog candidates were checked. Narrow your search."}
                )
                result.status = "partial"
            for candidate in candidates[:20]:
                try:
                    await self.schema(user, token, candidate["id"], refresh=True)
                except Exception as exc:
                    info = error_info(exc)
                    if info.kind != "permission":
                        result.warnings.append(
                            {"message": "A directly shared candidate could not be checked.", "error": info.model_dump()}
                        )
                        result.status = "partial"
                    continue
                rows.append(candidate)
        matches = self.catalog.search(rows, query, workspace)
        page = matches[offset : offset + limit]
        if verify_access:
            sem = asyncio.Semaphore(4)

            async def check(row):
                async with sem:
                    client = self.hosted(token)
                    try:
                        await client.execute_query(row["id"], ['EVALUATE ROW("probe", 1)'], 1)
                        row.update(query_access="verified", access_checked_at=timestamp())
                    except Exception as exc:
                        info = error_info(exc)
                        row.update(
                            query_access="unavailable"
                            if info.kind in ("permission", "authentication")
                            else "unchecked",
                            access_error=info.model_dump(),
                        )
                    finally:
                        await client.aclose()

            await asyncio.gather(*(check(row) for row in page))
        return result.model_copy(
            update={
                "models": page,
                "total_matches": len(matches),
                "next_offset": offset + limit if offset + limit < len(matches) else None,
            }
        )
