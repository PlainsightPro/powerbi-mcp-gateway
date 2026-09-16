"""Remote MCP surface. Power BI operations always use the signed-in user's OBO token."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import asynccontextmanager
from typing import Any

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.server.auth.providers.azure import AzureProvider, EntraOBOToken
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse

from .analysis import AnalysisService
from .config import Settings, load_settings
from .contracts import AnalysisContext, AnalysisResult, DiscoveryResult
from .discovery import Discovery
from .errors import error_info
from .gateway import Gateway
from .observability import ToolCallLogger, current_user_key
from .schema import compact_schema, search_objects
from .skills import Skills
from .state_store import build_client_storage
from .welcome import welcome_page

READ_ONLY = {"readOnlyHint": True, "destructiveHint": False, "openWorldHint": True}


def _current_user_key() -> str:
    user = current_user_key()
    if user == "anonymous":
        raise ToolError("No authenticated user identity is available. Reconnect with your Microsoft work account.")
    return user


def _as_tool_error(exc: Exception) -> ToolError:
    info = error_info(exc)
    return ToolError(f"{info.kind}: {info.message} {info.action}")


def _recipe_prompt_factory(skills: Skills, name: str) -> Callable[..., str]:
    def recipe_prompt(model_id: str = "", period: str = "") -> str:
        method = (
            "Use run_recipe with its typed parameters."
            if name in skills.workflows
            else "Follow the steps with execute_dax, adapting the requested period and filters."
        )
        return (
            f"Follow '{skills.recipe_title(name)}' on {model_id or 'a model confirmed with search_semantic_models'} "
            f"for {period or 'the period the user requested'}. {method}\n\n" + skills.recipes[name]
        )

    recipe_prompt.__name__ = name.replace("-", "_")
    recipe_prompt.__doc__ = skills.recipe_title(name)
    return recipe_prompt


def build_server(settings: Settings | None = None, gateway: Gateway | None = None, client_storage=None) -> FastMCP:
    settings = settings or load_settings()
    gateway = gateway or Gateway.from_settings(settings)
    skills, catalog = gateway.skills, gateway.catalog
    skills.validate_catalog(catalog)
    discovery = Discovery(
        settings,
        catalog,
        fabric_factory=lambda token, url: gateway._fabric(token),
        hosted_factory=lambda token, url: gateway._hosted(token),
    )
    service = AnalysisService(settings, skills, catalog, discovery, notes_provider=gateway.notes_for)
    auth = AzureProvider(
        client_id=settings.client_id,
        client_secret=settings.client_secret,
        tenant_id=settings.tenant_id,
        base_url=settings.base_url,
        required_scopes=[settings.api_scope_name],
        identifier_uri=settings.identifier_uri,
        jwt_signing_key=settings.jwt_signing_key,
        client_storage=client_storage if client_storage is not None else build_client_storage(settings),
        # Entra rejects RFC 8707 resource alongside scope. Consent delegated permissions on the app.
        forward_resource=False,
    )

    @asynccontextmanager
    async def lifespan(_):
        try:
            yield
        finally:
            await service.aclose()
            await gateway.aclose()

    mcp = FastMCP(name=settings.server_name, instructions=skills.instructions, auth=auth, lifespan=lifespan)
    mcp.add_middleware(ToolCallLogger())
    fabric_scopes = [settings.fabric_scope]

    @mcp.tool(name="list_semantic_models", annotations=READ_ONLY)
    async def list_semantic_models(
        include_uncurated: bool = True, refresh: bool = False, fabric_token: str = EntraOBOToken(fabric_scopes)
    ) -> list[dict]:
        """List workspace-discoverable semantic models (query access initially unchecked).
        Use search_semantic_models for topic search, access checks and partial discovery details.
        refresh=true bypasses the per-user cache. Use returned ids rather than guessing."""
        try:
            result = await service.discovery.discover(_current_user_key(), fabric_token, refresh)
            if result.status == "partial":
                raise ToolError(
                    "Model discovery is incomplete. Use search_semantic_models to see available models and actionable warnings."
                )
            return result.models if include_uncurated else [r for r in result.models if r["curated"]]
        except ToolError:
            raise
        except Exception as exc:
            raise _as_tool_error(exc) from exc

    @mcp.tool(name="search_semantic_models", annotations=READ_ONLY)
    async def search_semantic_models(
        query: str = "",
        workspace: str = "",
        refresh: bool = False,
        verify_access: bool = False,
        offset: int = 0,
        limit: int = 25,
        fabric_token: str = EntraOBOToken(fabric_scopes),
    ) -> DiscoveryResult:
        """Find models by name, alias, topic or measure. Lists partial discovery warnings explicitly.
        verify_access checks query access and matching directly shared curated models as this user.
        Use offset/limit for large catalogs; refresh bypasses the discovery cache."""
        try:
            return await service.discovery.search(
                _current_user_key(), fabric_token, query, workspace, refresh, verify_access, offset, limit
            )
        except Exception as exc:
            raise _as_tool_error(exc) from exc

    @mcp.tool(name="get_business_context", annotations=READ_ONLY)
    def get_business_context(model_id: str | None = None) -> str:
        """Deployment-wide glossary and recipe index, optionally augmented by a model's context.
        Business knowledge is shared within this deployment; model data remains permission-filtered."""
        return (
            skills.glossary_for(catalog.get(model_id) if model_id else None)
            + "\n\n## Recipes (use get_recipe)\n"
            + skills.recipe_index()
        )

    @mcp.tool(name="get_recipe", annotations=READ_ONLY)
    def get_recipe(name: str) -> str:
        """Instructions for a recurring analysis. Executable recipes also include typed parameter definitions."""
        name = name.strip().lower()
        recipe = skills.recipes.get(name)
        if recipe is None:
            raise ToolError(f"Unknown recipe '{name}'. Available:\n{skills.recipe_index()}")
        if name in skills.workflows:
            recipe += "\n\nExecutable definition (run_recipe):\n" + skills.workflows[name].model_dump_json(indent=2)
        return recipe

    @mcp.tool(name="get_semantic_model_schema", annotations=READ_ONLY)
    async def get_semantic_model_schema(
        model_id: str,
        compact: bool = True,
        query: str = "",
        tables: list[str] | None = None,
        refresh: bool = False,
        fabric_token: str = EntraOBOToken(fabric_scopes),
    ) -> Any:
        """Model tables, measures, columns, relationships, formats and author metadata.
        compact=false returns raw JSON. query prioritises relevant objects within the context budget;
        tables selects table details. Coverage is explicit. Use search_schema for omitted fields."""
        try:
            schema = await service.discovery.schema(_current_user_key(), fabric_token, model_id, refresh)
            notes = await service.notes_for(model_id)
            if compact:
                return (
                    (f"## Notes\n{notes}\n\n" if notes else "")
                    + "## Schema\n"
                    + compact_schema(schema, query=query, tables=tables)
                )
            return {"notes": notes, "schema": schema}
        except Exception as exc:
            raise _as_tool_error(exc) from exc

    @mcp.tool(name="search_schema", annotations=READ_ONLY)
    async def search_schema(
        model_id: str,
        query: str = "",
        table: str | None = None,
        offset: int = 0,
        limit: int = 50,
        fabric_token: str = EntraOBOToken(fabric_scopes),
    ) -> dict:
        """Search exact schema objects and descriptions with pagination; retains complete object metadata."""
        if offset < 0 or not 1 <= limit <= 100:
            raise ToolError("Use offset >= 0 and limit 1..100.")
        try:
            schema = await service.discovery.schema(_current_user_key(), fabric_token, model_id)
            return search_objects(schema, query, table, offset, limit)
        except Exception as exc:
            raise _as_tool_error(exc) from exc

    @mcp.tool(name="get_dimension_values", annotations=READ_ONLY)
    async def get_dimension_values(
        model_id: str,
        table: str,
        column: str,
        search: str = "",
        limit: int = 25,
        fabric_token: str = EntraOBOToken(fabric_scopes),
    ) -> dict:
        """Look up up to 100 permitted distinct values of an exact schema column, optionally by text.
        Runs under the user's identity. Use this to resolve business labels into actual filter values."""
        try:
            return await service.dimension_values(
                _current_user_key(), fabric_token, model_id, table, column, search, limit
            )
        except Exception as exc:
            raise _as_tool_error(exc) from exc

    @mcp.tool(name="get_model_context", annotations=READ_ONLY)
    async def get_model_context(
        model_id: str, question: str = "", refresh: bool = False, fabric_token: str = EntraOBOToken(fabric_scopes)
    ) -> dict:
        """Model-scoped glossary, rules, relevant schema and compatible recipes, including for client-written DAX."""
        try:
            return await service.model_context(_current_user_key(), fabric_token, model_id, question, refresh)
        except Exception as exc:
            raise _as_tool_error(exc) from exc

    @mcp.tool(name="execute_dax", annotations=READ_ONLY)
    async def execute_dax(
        model_id: str,
        dax_queries: list[str],
        max_rows: int | None = None,
        fabric_token: str = EntraOBOToken(fabric_scopes),
    ) -> dict:
        """Execute 1..4 read-only DAX queries, each with one EVALUATE. Returns upstream rows plus
        status, exact queries and evidence. max_rows is a positive bounded per-query limit."""
        try:
            return await service.execute(fabric_token, model_id, dax_queries, max_rows)
        except Exception as exc:
            raise _as_tool_error(exc) from exc

    @mcp.tool(name="generate_dax", annotations=READ_ONLY, output_schema=AnalysisResult.model_json_schema())
    async def generate_dax(
        model_id: str,
        question: str,
        execute: bool = True,
        max_rows: int | None = None,
        chat_history: list[dict[str, str]] | None = None,
        context: AnalysisContext | None = None,
        fabric_token: str = EntraOBOToken(fabric_scopes),
    ) -> dict:
        """Generate grounded DAX and optionally execute it. Return context on follow-ups to retain
        periods and filters. Only query errors get one repair; auth/network errors have recovery actions.
        Check status before interpreting rows. Without Foundry, returns context_ready for client generation."""
        result = await service.generate(
            _current_user_key(), fabric_token, model_id, question, execute, max_rows, chat_history, context
        )
        return result.model_dump(mode="json", exclude_none=True)

    @mcp.tool(name="analyze", annotations=READ_ONLY)
    async def analyze(
        question: str,
        model_hint: str | None = None,
        report_url: str | None = None,
        context: AnalysisContext | None = None,
        execute: bool = True,
        max_rows: int | None = None,
        recipe: str | None = None,
        parameters: dict | None = None,
        fabric_token: str = EntraOBOToken(fabric_scopes),
    ) -> AnalysisResult:
        """Start here for a business question. Resolve a model name/alias or Power BI report URL,
        ground and execute the analysis, and return scope, DAX, evidence and follow-up context.
        Ambiguous model choices return needs_clarification with candidates. Explicitly named executable
        recipes use typed parameters; otherwise generate grounded DAX. Always inspect status."""
        return await service.analyze(
            _current_user_key(),
            fabric_token,
            question,
            model_hint,
            report_url,
            context,
            execute,
            max_rows,
            recipe,
            parameters,
        )

    @mcp.tool(name="run_recipe", annotations=READ_ONLY)
    async def run_recipe(
        model_id: str,
        name: str,
        parameters: dict | None = None,
        execute: bool = True,
        fabric_token: str = EntraOBOToken(fabric_scopes),
    ) -> AnalysisResult:
        """Run a validated YAML recipe in order with typed parameters. Stops on a failed step,
        preserving earlier results. execute=false prepares all queries without executing them."""
        return await service.run_recipe(_current_user_key(), fabric_token, model_id, name, parameters or {}, execute)

    @mcp.tool(name="get_report_metadata", annotations=READ_ONLY)
    async def get_report_metadata(report_id: str, fabric_token: str = EntraOBOToken(fabric_scopes)) -> Any:
        """Authored report pages, visuals, model references and filters. This does not capture
        a viewer's current personal slicer state. analyze also accepts a full report_url."""
        client = service.discovery.hosted(fabric_token)
        try:
            return await client.get_report_metadata(report_id)
        except Exception as exc:
            raise _as_tool_error(exc) from exc
        finally:
            await client.aclose()

    @mcp.tool(name="diagnose_connection", annotations=READ_ONLY)
    async def diagnose_connection(
        model_id: str | None = None, check_generation: bool = False, fabric_token: str = EntraOBOToken(fabric_scopes)
    ) -> dict:
        """Check discovery, schema and query access using a constant probe, without business rows.
        Supply a model_id to diagnose one model. check_generation makes one billable Foundry probe."""
        return await service.diagnose(_current_user_key(), fabric_token, model_id, check_generation)

    @mcp.tool(name="recall", annotations=READ_ONLY)
    async def recall(model_id: str, fabric_token: str = EntraOBOToken(fabric_scopes)) -> dict:
        """Read shared model notes after checking this user's model access. Notes are colleagues'
        observations, not rules. They also ground analyze, generate_dax and get_model_context."""
        return await gateway.recall(_current_user_key(), fabric_token, model_id)

    @mcp.tool(name="remember")
    async def remember(model_id: str, text: str, fabric_token: str = EntraOBOToken(fabric_scopes)) -> dict:
        """Save a short shared model note only when the user asks or confirms a lesson. Everyone
        with model access can read it. Never store result rows, personal data or secrets."""
        return await gateway.remember(_current_user_key(), fabric_token, model_id, text)

    @mcp.tool(name="forget")
    async def forget(model_id: str, memory_id: str, fabric_token: str = EntraOBOToken(fabric_scopes)) -> dict:
        """Delete one of this user's own model notes. Find its id with recall."""
        return await gateway.forget(_current_user_key(), fabric_token, model_id, memory_id)

    for name in skills.recipes:
        mcp.prompt(name=name, description=skills.recipe_title(name))(_recipe_prompt_factory(skills, name))

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
        return get_recipe(name)

    @mcp.custom_route("/", methods=["GET"], include_in_schema=False)
    async def welcome(_: Request) -> HTMLResponse:
        return HTMLResponse(
            welcome_page(settings.server_name, settings.base_url),
            headers={
                "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; frame-ancestors 'none'",
                "X-Content-Type-Options": "nosniff",
            },
        )

    @mcp.custom_route("/healthz", methods=["GET"], include_in_schema=False)
    async def healthz(_: Request) -> JSONResponse:
        # Public liveness carries no business vocabulary or private catalog metadata.
        return JSONResponse({"status": "ok"})

    return mcp
