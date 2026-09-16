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
from .fabric import FabricClient
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


# A query that touches no data: it tells whether the user may query a model at all.
ACCESS_PROBE = 'EVALUATE ROW("probe", 1)'
PROBE_CONCURRENCY = 4


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
        rows = self.catalog.merge(refs)
        rows = self.catalog.sort(rows + await self._directly_shared(token, {r["id"].lower() for r in rows}))
        result = DiscoveryResult(
            models=rows,
            warnings=warnings,
            checked_at=timestamp(),
            status="partial" if warnings else "complete",
            total_matches=len(refs),
        )
        # A transiently missing workspace must never poison the user's catalogue cache.
        if not warnings:
            self.models.set(user, result)
        return result

    async def _directly_shared(self, token: str, listed: set[str]) -> list[dict]:
        """Curated models the workspace API did not return but the user can still query.

        Fabric only lists workspaces the user is a member of, so a model shared with them directly
        (an item share, the usual way a report consumer gets Build) is invisible there. Each curated
        model missing from the listing is probed with a data-free query as the user; the ones that
        answer are listed, the rest (no access, or an upstream hiccup) are left out. Bounded by the
        catalog size and cached with the listing.
        """
        missing = [e for e in self.catalog.entries if e.id.lower() not in listed]
        if not missing:
            return []
        gate = asyncio.Semaphore(PROBE_CONCURRENCY)

        async def probe(entry) -> dict | None:
            async with gate:
                client = self.hosted(token)
                try:
                    await client.execute_query(entry.id, [ACCESS_PROBE], 1)
                except Exception:
                    return None
                finally:
                    await client.aclose()
            return self.catalog.shared_row(entry)

        found = await asyncio.gather(*(probe(entry) for entry in missing))
        return [row for row in found if row is not None]

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
        # Curated models shared directly with the user are already part of discover().
        matches = self.catalog.search(result.models, query, workspace)
        page = matches[offset : offset + limit]
        if verify_access:
            sem = asyncio.Semaphore(4)

            async def check(row):
                async with sem:
                    client = self.hosted(token)
                    try:
                        await client.execute_query(row["id"], [ACCESS_PROBE], 1)
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
