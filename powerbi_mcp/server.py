"""Power BI MCP Gateway.

Auth: FastMCP's Azure OAuth proxy. MCP clients (Claude Code, Claude Desktop, claude.ai, VS Code,
Cursor, ChatGPT, ...) register dynamically against this server; the server signs users in with
Microsoft Entra and exchanges their token on-behalf-of for a Power BI / Fabric token per call.
Nothing runs under a service identity, so Power BI's permissions and row-level security apply to
every query.

Execution: Microsoft's hosted Power BI MCP server (schema, DAX execution, report metadata) and the
Fabric REST API (which models the user can open). DAX generation: a Foundry (Azure OpenAI)
deployment, grounded in the model schema plus the deployment's skills folder.

The engine carries no domain vocabulary: tool names are generic, and every prompt, glossary entry,
recipe and catalog row comes from the skills folder the deployment was built with.
"""

from __future__ import annotations

import time
from typing import Any, Callable

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.server.auth.providers.azure import AzureProvider, EntraOBOToken
from fastmcp.server.dependencies import get_access_token
from starlette.requests import Request
from starlette.responses import JSONResponse

from .catalog import Catalog
from .config import Settings, load_settings
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


def _current_user_key() -> str:
    token = get_access_token()
    claims = getattr(token, "claims", None) or {}
    return claims.get("oid") or claims.get("sub") or claims.get("preferred_username") or "anonymous"


def _as_tool_error(exc: Exception) -> ToolError:
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


def _recipe_prompt_factory(skills: Skills, name: str) -> Callable[..., str]:
    """One MCP prompt per recipe file. Name, title and body all come from the skills folder."""
    title = skills.recipe_title(name)

    def recipe_prompt(model_id: str = "", period: str = "") -> str:
        model_hint = model_id or "the model this recipe names (confirm its id with list_semantic_models)"
        period_hint = period or "the period the user asked for"
        return (
            f"Follow the recipe '{title}' on semantic model {model_hint} for {period_hint}. "
            "Run its steps in order with execute_dax, adapting filters and period bounds to the model; "
            "when a step fails, fix the query rather than skipping it. End with the answer structure "
            "the recipe describes.\n\n" + skills.recipes[name]
        )

    recipe_prompt.__name__ = name.replace("-", "_")
    recipe_prompt.__doc__ = title
    return recipe_prompt


def build_server(settings: Settings | None = None) -> FastMCP:
    settings = settings or load_settings()
    skills = Skills.load(settings.skills_dir)
    catalog = Catalog.load(settings.skills_dir / "catalog.yaml")

    auth = AzureProvider(
        client_id=settings.client_id,
        client_secret=settings.client_secret,
        tenant_id=settings.tenant_id,
        base_url=settings.base_url,
        required_scopes=[settings.api_scope_name],
        identifier_uri=settings.identifier_uri,
        jwt_signing_key=settings.jwt_signing_key,
        # Entra's v2 endpoint rejects an RFC 8707 `resource` parameter next to `scope`
        # (AADSTS9010010), so never forward the client's resource indicator upstream.
        forward_resource=False,
    )

    mcp = FastMCP(name=settings.server_name, instructions=skills.instructions, auth=auth)

    fabric_scopes = [settings.fabric_scope]
    models_cache = TtlCache(settings.catalog_cache_seconds)
    schema_cache = TtlCache(settings.schema_cache_seconds)
    generator_box: dict[str, DaxGenerator] = {}

    def generator() -> DaxGenerator:
        if "gen" in generator_box:
            return generator_box["gen"]
        if not settings.foundry_endpoint:
            raise ToolError("generate_dax is not configured: PBIMCP_FOUNDRY_ENDPOINT is empty on this server.")
        from openai import AsyncOpenAI

        if settings.foundry_api_key:
            api_key: Any = settings.foundry_api_key
        else:
            from azure.identity.aio import DefaultAzureCredential, get_bearer_token_provider

            api_key = get_bearer_token_provider(DefaultAzureCredential(process_timeout=60), FOUNDRY_SCOPE)
        client = AsyncOpenAI(base_url=settings.foundry_endpoint.rstrip("/") + "/openai/v1/", api_key=api_key)
        generator_box["gen"] = DaxGenerator(
            client.responses,
            deployment=settings.foundry_deployment,
            rules=skills.dax_rules,
            reasoning_effort=settings.foundry_reasoning_effort,
        )
        return generator_box["gen"]

    async def fetch_schema(model_id: str, fabric_token: str) -> dict:
        key = (_current_user_key(), model_id.lower())
        cached = schema_cache.get(key)
        if cached is not None:
            return cached
        hosted = HostedPowerBIMcp(fabric_token, settings.hosted_mcp_url)
        try:
            schema = await hosted.get_schema(model_id)
        except Exception as exc:  # noqa: BLE001 - every failure becomes a readable tool error
            raise _as_tool_error(exc) from exc
        finally:
            await hosted.aclose()
        schema_cache.set(key, schema)
        return schema

    # ------------------------------------------------------------------ tools

    @mcp.tool(name="list_semantic_models")
    async def list_semantic_models(
        include_uncurated: bool = True,
        fabric_token: str = EntraOBOToken(fabric_scopes),
    ) -> list[dict]:
        """List the Power BI semantic models the signed-in user can open. Call this first and use
        the returned `id` in every other tool; never guess or recall a model id. Curated models come
        first with their description, data scope, default date table, key measures and recipes.
        Set include_uncurated=false to see only the curated models."""
        user = _current_user_key()
        rows = models_cache.get(user)
        if rows is None:
            client = FabricClient(fabric_token, settings.fabric_api_url)
            try:
                accessible = await client.list_accessible_models()
            except Exception as exc:  # noqa: BLE001
                raise _as_tool_error(exc) from exc
            finally:
                await client.aclose()
            rows = catalog.merge(accessible)
            models_cache.set(user, rows)
        return rows if include_uncurated else [r for r in rows if r["curated"]]

    @mcp.tool(name="get_business_context")
    def get_business_context() -> str:
        """This deployment's business glossary: which measure answers which question, sign
        conventions, date tables, model traps, and the index of available recipes. Read it before
        answering a business question or writing DAX by hand."""
        return skills.glossary.strip() + "\n\n## Recipes (use get_recipe)\n" + skills.recipe_index()

    @mcp.tool(name="get_recipe")
    def get_recipe(name: str) -> str:
        """A step-by-step recipe for a recurring analysis defined by this deployment: the queries to
        run in order and how to read them. Recipe names are listed by get_business_context."""
        recipe = skills.recipes.get(name.strip().lower())
        if recipe is None:
            raise ToolError(f"Unknown recipe '{name}'. Available:\n{skills.recipe_index()}")
        return recipe

    @mcp.tool(name="get_semantic_model_schema")
    async def get_semantic_model_schema(
        model_id: str,
        compact: bool = True,
        fabric_token: str = EntraOBOToken(fabric_scopes),
    ) -> Any:
        """Tables, columns, measures (with descriptions) and relationships of one semantic model,
        plus the curated notes for it. Large: fetch once per model per conversation. compact=true
        returns a readable text block; compact=false returns the raw JSON."""
        schema = await fetch_schema(model_id, fabric_token)
        notes = catalog.notes_for(model_id)
        if compact:
            return (f"## Notes\n{notes}\n\n" if notes else "") + "## Schema\n" + compact_schema(schema)
        return {"notes": notes, "schema": schema}

    @mcp.tool(name="execute_dax")
    async def execute_dax(
        model_id: str,
        dax_queries: list[str],
        max_rows: int | None = None,
        fabric_token: str = EntraOBOToken(fabric_scopes),
    ) -> dict:
        """Run one to four DAX queries (each a single EVALUATE) against a semantic model as the
        signed-in user and return the rows. Default cap 250 rows per query. Use the queries from a
        recipe or from generate_dax; aggregate before you list rows."""
        if not dax_queries or len(dax_queries) > 4:
            raise ToolError("Pass between 1 and 4 DAX queries.")
        hosted = HostedPowerBIMcp(fabric_token, settings.hosted_mcp_url)
        try:
            return await hosted.execute_query(model_id, dax_queries, max_rows or settings.default_max_rows)
        except Exception as exc:  # noqa: BLE001
            raise _as_tool_error(exc) from exc
        finally:
            await hosted.aclose()

    @mcp.tool(name="generate_dax")
    async def generate_dax(
        model_id: str,
        question: str,
        execute: bool = True,
        max_rows: int | None = None,
        chat_history: list[dict[str, str]] | None = None,
        fabric_token: str = EntraOBOToken(fabric_scopes),
    ) -> dict:
        """Turn a business question into a DAX query for the given semantic model, grounded in the
        model schema, this deployment's glossary and its DAX rules (a Foundry model writes the
        query). With execute=true (default) the query runs as the signed-in user and the rows come
        back with the DAX and the assumptions made. Pass chat_history ([{role, content}]) for
        follow-up questions so the query builds on the previous turn."""
        schema = await fetch_schema(model_id, fabric_token)
        notes = catalog.notes_for(model_id)
        try:
            generated = await generator().generate(question, schema, notes, skills.glossary, chat_history)
        except ToolError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise ToolError(f"DAX generation failed: {type(exc).__name__}: {exc}") from exc

        result: dict[str, Any] = {
            "model_id": model_id,
            "dax": generated.dax,
            "explanation": generated.explanation,
            "assumptions": generated.assumptions,
        }
        if not execute:
            return result

        hosted = HostedPowerBIMcp(fabric_token, settings.hosted_mcp_url)
        try:
            rows = max_rows or settings.default_max_rows
            try:
                run = await hosted.execute_query(model_id, [generated.dax], rows)
            except Exception as first_exc:  # noqa: BLE001 - give the model one shot at fixing its own query
                first_error = str(_as_tool_error(first_exc))
                try:
                    repaired = await generator().repair(question, schema, notes, skills.glossary, generated.dax, first_error)
                    run = await hosted.execute_query(model_id, [repaired.dax], rows)
                except Exception as second_exc:  # noqa: BLE001 - return both errors and the DAX for the client
                    result["execution_error"] = first_error
                    result["repair_error"] = str(_as_tool_error(second_exc))
                    return result
                result.update({"dax": repaired.dax, "explanation": repaired.explanation,
                               "assumptions": repaired.assumptions, "repaired_after": first_error})
            result["result"] = run.get("executionResult", run)
        finally:
            await hosted.aclose()
        return result

    @mcp.tool(name="get_report_metadata")
    async def get_report_metadata(
        report_id: str,
        fabric_token: str = EntraOBOToken(fabric_scopes),
    ) -> Any:
        """Pages, visuals, field bindings and filters of a Power BI report the user can open.
        Useful to learn how a model is used in practice before writing DAX for a question phrased
        in report terms."""
        hosted = HostedPowerBIMcp(fabric_token, settings.hosted_mcp_url)
        try:
            return await hosted.get_report_metadata(report_id)
        except Exception as exc:  # noqa: BLE001
            raise _as_tool_error(exc) from exc
        finally:
            await hosted.aclose()

    # ---------------------------------------------------------------- prompts

    for recipe_name in skills.recipes:
        mcp.prompt(name=recipe_name, description=skills.recipe_title(recipe_name))(
            _recipe_prompt_factory(skills, recipe_name)
        )

    # -------------------------------------------------------------- resources

    @mcp.resource("skill://glossary", name="glossary", mime_type="text/markdown")
    def glossary_resource() -> str:
        return skills.glossary

    @mcp.resource("skill://dax-rules", name="dax-rules", mime_type="text/markdown")
    def rules_resource() -> str:
        return skills.dax_rules

    @mcp.resource("skill://catalog", name="model-catalog", mime_type="application/x-yaml")
    def catalog_resource() -> str:
        return (settings.skills_dir / "catalog.yaml").read_text(encoding="utf-8")

    @mcp.resource("skill://recipes/{name}", name="recipe", mime_type="text/markdown")
    def recipe_resource(name: str) -> str:
        return skills.recipes.get(name, f"Unknown recipe '{name}'. Available:\n{skills.recipe_index()}")

    # ------------------------------------------------------------ health probe

    @mcp.custom_route("/healthz", methods=["GET"], include_in_schema=False)
    async def healthz(_: Request) -> JSONResponse:
        return JSONResponse({"status": "ok", "recipes": sorted(skills.recipes), "curated_models": len(catalog.entries)})

    return mcp
