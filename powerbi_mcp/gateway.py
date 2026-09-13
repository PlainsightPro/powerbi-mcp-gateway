"""What the MCP tools do, without MCP in the way.

`Gateway` takes the signed-in user's key and Fabric token as plain values and owns the caches, the
lazy Foundry client and the generate -> execute -> repair loop. Keeping FastMCP's request context
and on-behalf-of token resolution out of this layer is what lets the tests drive every path with
`httpx.MockTransport` and a stub Responses client.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

import httpx
from fastmcp.exceptions import ToolError

from .catalog import Catalog
from .config import Settings
from .dax_generator import DaxGenerator, compact_schema
from .fabric import FabricAccessError, FabricClient
from .hosted_mcp import HostedMcpError, HostedPowerBIMcp
from .skills import Skills

FOUNDRY_SCOPE = "https://cognitiveservices.azure.com/.default"


class TtlCache:
    def __init__(self, ttl_seconds: float) -> None:
        self._ttl = ttl_seconds
        self._items: dict[Any, tuple[float, Any]] = {}

    def get(self, key: Any) -> Any | None:
        hit = self._items.get(key)
        if not hit:
            return None
        expires, value = hit
        if expires < time.monotonic():
            self._items.pop(key, None)
            return None
        return value

    def set(self, key: Any, value: Any) -> None:
        self._items[key] = (time.monotonic() + self._ttl, value)


def as_tool_error(exc: Exception) -> ToolError:
    """Every upstream failure becomes a readable error for the assistant and its user."""
    if isinstance(exc, FabricAccessError):
        return ToolError(
            "Power BI refused the request for the signed-in user. They need Build permission on the "
            "semantic model and a license appropriate to its workspace (Premium Per User for a PPU "
            f"workspace, or the applicable Power BI license on Fabric/Premium capacity). Details: {exc}"
        )
    if isinstance(exc, HostedMcpError):
        detail = f" (code {exc.code})" if exc.code else ""
        data = f" {exc.data}" if exc.data else ""
        return ToolError(f"Power BI MCP error{detail}: {exc}{data}")
    return ToolError(f"{type(exc).__name__}: {exc}")


class Gateway:
    """Per-deployment state (settings, skills, catalog, caches) and the behaviour behind each tool."""

    def __init__(
        self,
        settings: Settings,
        skills: Skills,
        catalog: Catalog,
        *,
        fabric_transport: httpx.AsyncBaseTransport | None = None,
        hosted_transport: httpx.AsyncBaseTransport | None = None,
        generator_factory: Callable[[], DaxGenerator] | None = None,
    ) -> None:
        self.settings = settings
        self.skills = skills
        self.catalog = catalog
        self._fabric_transport = fabric_transport
        self._hosted_transport = hosted_transport
        self._generator_factory = generator_factory or self._foundry_generator
        self._generator: DaxGenerator | None = None
        self._models_cache = TtlCache(settings.catalog_cache_seconds)
        self._schema_cache = TtlCache(settings.schema_cache_seconds)

    @classmethod
    def from_settings(cls, settings: Settings) -> Gateway:
        skills = Skills.load(settings.skills_dir)
        catalog = Catalog.load(settings.skills_dir / "catalog.yaml")
        return cls(settings, skills, catalog)

    # ------------------------------------------------------------ upstream clients

    def _fabric(self, token: str) -> FabricClient:
        return FabricClient(token, self.settings.fabric_api_url, transport=self._fabric_transport)

    def _hosted(self, token: str) -> HostedPowerBIMcp:
        return HostedPowerBIMcp(token, self.settings.hosted_mcp_url, transport=self._hosted_transport)

    def _foundry_generator(self) -> DaxGenerator:
        settings = self.settings
        if not settings.foundry_endpoint:
            raise ToolError("generate_dax is not configured: PBIMCP_FOUNDRY_ENDPOINT is empty on this server.")
        from openai import AsyncOpenAI

        if settings.foundry_api_key:
            api_key: Any = settings.foundry_api_key
        else:
            from azure.identity.aio import DefaultAzureCredential, get_bearer_token_provider

            api_key = get_bearer_token_provider(DefaultAzureCredential(process_timeout=60), FOUNDRY_SCOPE)
        client = AsyncOpenAI(base_url=settings.foundry_endpoint.rstrip("/") + "/openai/v1/", api_key=api_key)
        return DaxGenerator(
            client.responses,
            deployment=settings.foundry_deployment,
            rules=self.skills.dax_rules,
            reasoning_effort=settings.foundry_reasoning_effort,
        )

    def generator(self) -> DaxGenerator:
        if self._generator is None:
            self._generator = self._generator_factory()
        return self._generator

    # ----------------------------------------------------------------- skills

    def business_context(self) -> str:
        return self.skills.glossary.strip() + "\n\n## Recipes (use get_recipe)\n" + self.skills.recipe_index()

    def recipe(self, name: str) -> str:
        recipe = self.skills.recipes.get(name.strip().lower())
        if recipe is None:
            raise ToolError(f"Unknown recipe '{name}'. Available:\n{self.skills.recipe_index()}")
        return recipe

    def health(self) -> dict[str, Any]:
        return {"status": "ok", "recipes": sorted(self.skills.recipes), "curated_models": len(self.catalog.entries)}

    # ------------------------------------------------------------------ models

    async def list_models(self, user_key: str, token: str, include_uncurated: bool = True) -> list[dict]:
        rows = self._models_cache.get(user_key)
        if rows is None:
            client = self._fabric(token)
            try:
                accessible = await client.list_accessible_models()
            except Exception as exc:
                raise as_tool_error(exc) from exc
            finally:
                await client.aclose()
            rows = self.catalog.merge(accessible)
            self._models_cache.set(user_key, rows)
        return rows if include_uncurated else [r for r in rows if r["curated"]]

    async def fetch_schema(self, user_key: str, token: str, model_id: str) -> dict:
        key = (user_key, model_id.lower())
        cached = self._schema_cache.get(key)
        if cached is not None:
            return cached
        hosted = self._hosted(token)
        try:
            schema = await hosted.get_schema(model_id)
        except Exception as exc:
            raise as_tool_error(exc) from exc
        finally:
            await hosted.aclose()
        self._schema_cache.set(key, schema)
        return schema

    async def schema(self, user_key: str, token: str, model_id: str, compact: bool = True) -> Any:
        schema = await self.fetch_schema(user_key, token, model_id)
        notes = self.catalog.notes_for(model_id)
        if compact:
            return (f"## Notes\n{notes}\n\n" if notes else "") + "## Schema\n" + compact_schema(schema)
        return {"notes": notes, "schema": schema}

    async def execute(self, token: str, model_id: str, dax_queries: list[str], max_rows: int | None = None) -> dict:
        if not dax_queries or len(dax_queries) > 4:
            raise ToolError("Pass between 1 and 4 DAX queries.")
        hosted = self._hosted(token)
        try:
            return await hosted.execute_query(model_id, dax_queries, max_rows or self.settings.default_max_rows)
        except Exception as exc:
            raise as_tool_error(exc) from exc
        finally:
            await hosted.aclose()

    async def generate(
        self,
        user_key: str,
        token: str,
        model_id: str,
        question: str,
        execute: bool = True,
        max_rows: int | None = None,
        chat_history: list[dict[str, str]] | None = None,
    ) -> dict[str, Any]:
        schema = await self.fetch_schema(user_key, token, model_id)
        notes = self.catalog.notes_for(model_id)
        glossary = self.skills.glossary
        try:
            generated = await self.generator().generate(question, schema, notes, glossary, chat_history)
        except ToolError:
            raise
        except Exception as exc:
            raise ToolError(f"DAX generation failed: {type(exc).__name__}: {exc}") from exc

        result: dict[str, Any] = {
            "model_id": model_id,
            "dax": generated.dax,
            "explanation": generated.explanation,
            "assumptions": generated.assumptions,
        }
        if not execute:
            return result

        hosted = self._hosted(token)
        try:
            rows = max_rows or self.settings.default_max_rows
            try:
                run = await hosted.execute_query(model_id, [generated.dax], rows)
            except Exception as first_exc:
                first_error = str(as_tool_error(first_exc))
                try:
                    repaired = await self.generator().repair(
                        question, schema, notes, glossary, generated.dax, first_error
                    )
                    run = await hosted.execute_query(model_id, [repaired.dax], rows)
                except Exception as second_exc:
                    result["execution_error"] = first_error
                    result["repair_error"] = str(as_tool_error(second_exc))
                    return result
                result.update(
                    {
                        "dax": repaired.dax,
                        "explanation": repaired.explanation,
                        "assumptions": repaired.assumptions,
                        "repaired_after": first_error,
                    }
                )
            result["result"] = run.get("executionResult", run)
        finally:
            await hosted.aclose()
        return result

    async def report_metadata(self, token: str, report_id: str) -> Any:
        hosted = self._hosted(token)
        try:
            return await hosted.get_report_metadata(report_id)
        except Exception as exc:
            raise as_tool_error(exc) from exc
        finally:
            await hosted.aclose()
