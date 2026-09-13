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

This module is the MCP surface only. The behaviour behind each tool lives in `gateway.py`, which
takes the user key and the Fabric token as plain values so it can be tested without OAuth.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastmcp import FastMCP
from fastmcp.server.auth.providers.azure import AzureProvider, EntraOBOToken
from fastmcp.server.dependencies import get_access_token
from starlette.requests import Request
from starlette.responses import JSONResponse

from .config import Settings, load_settings
from .gateway import Gateway
from .skills import Skills


def _current_user_key() -> str:
    token = get_access_token()
    claims = getattr(token, "claims", None) or {}
    return claims.get("oid") or claims.get("sub") or claims.get("preferred_username") or "anonymous"


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


def build_server(settings: Settings | None = None, gateway: Gateway | None = None) -> FastMCP:
    settings = settings or load_settings()
    gateway = gateway or Gateway.from_settings(settings)
    skills = gateway.skills

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
        return await gateway.list_models(_current_user_key(), fabric_token, include_uncurated)

    @mcp.tool(name="get_business_context")
    def get_business_context() -> str:
        """This deployment's business glossary: which measure answers which question, sign
        conventions, date tables, model traps, and the index of available recipes. Read it before
        answering a business question or writing DAX by hand."""
        return gateway.business_context()

    @mcp.tool(name="get_recipe")
    def get_recipe(name: str) -> str:
        """A step-by-step recipe for a recurring analysis defined by this deployment: the queries to
        run in order and how to read them. Recipe names are listed by get_business_context."""
        return gateway.recipe(name)

    @mcp.tool(name="get_semantic_model_schema")
    async def get_semantic_model_schema(
        model_id: str,
        compact: bool = True,
        fabric_token: str = EntraOBOToken(fabric_scopes),
    ) -> Any:
        """Tables, columns, measures (with descriptions) and relationships of one semantic model,
        plus the curated notes for it. Large: fetch once per model per conversation. compact=true
        returns a readable text block; compact=false returns the raw JSON."""
        return await gateway.schema(_current_user_key(), fabric_token, model_id, compact)

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
        return await gateway.execute(fabric_token, model_id, dax_queries, max_rows)

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
        return await gateway.generate(
            _current_user_key(), fabric_token, model_id, question, execute, max_rows, chat_history
        )

    @mcp.tool(name="get_report_metadata")
    async def get_report_metadata(
        report_id: str,
        fabric_token: str = EntraOBOToken(fabric_scopes),
    ) -> Any:
        """Pages, visuals, field bindings and filters of a Power BI report the user can open.
        Useful to learn how a model is used in practice before writing DAX for a question phrased
        in report terms."""
        return await gateway.report_metadata(fabric_token, report_id)

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
        return JSONResponse(gateway.health())

    return mcp
