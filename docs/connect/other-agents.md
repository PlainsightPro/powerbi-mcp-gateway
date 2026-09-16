# Connect other agents and your own code

The gateway is a standard remote MCP server: Streamable HTTP on `/mcp`, OAuth 2.1 with PKCE, and
the discovery documents clients need:

| Endpoint | Purpose |
|---|---|
| `/.well-known/oauth-protected-resource/mcp` | Protected resource metadata (RFC 9728) pointing at the authorization server below |
| `/.well-known/oauth-authorization-server` | Authorization server metadata (RFC 8414) |
| `/register` | Dynamic client registration (RFC 7591) |
| `/authorize`, `/token` | Authorization code + PKCE, refresh tokens |
| `/` | Connection instructions without private model metadata |
| `/healthz` | Minimal public liveness: `{"status":"ok"}` |

Any client that implements the MCP authorization specification connects like the ones in the other
guides. An unauthenticated call to `/mcp` returns `401` with a `WWW-Authenticate` header that names
the resource metadata; clients start from there.

## Clients that cannot register dynamically

Some platforms (Microsoft Foundry Agent Service, Copilot Studio, older SDKs) want a client id and
secret up front. Register one client on the gateway yourself and paste the result:

```bash
curl -s https://<host>/register -H "Content-Type: application/json" -d '{
  "client_name": "Foundry data agent",
  "redirect_uris": ["https://<the platform''s documented callback URL>"],
  "grant_types": ["authorization_code", "refresh_token"],
  "token_endpoint_auth_method": "none"
}'
```

The response contains `client_id`. Use the gateway's `/authorize` and `/token` endpoints as the
OAuth URLs and `access_as_user` as the scope. For Foundry Agent Service, choose the MCP tool with
**OAuth identity passthrough** and **custom OAuth**, so every user of the agent signs in
themselves; a shared identity would bypass Power BI's per-user permissions and is not supported by
the gateway. Registrations live in the gateway's client store; after a redeploy that wipes the
store, register again. Default Azure deployment now persists this store; see [administration](../administration.md).

## From your own code

Python, with the FastMCP client:

```python
from fastmcp import Client
from fastmcp.client.auth import OAuth

async with Client("https://<host>/mcp", auth=OAuth("https://<host>/mcp")) as client:
    models = (await client.call_tool("list_semantic_models", {})).data
```

The first run opens a browser for the Microsoft sign-in; tokens are cached locally afterwards.
`scripts/check_gateway.py` in the repository is a complete example, including a probe query.
If your application already holds the user's Entra sign-in, skip the proxy and pass that token
through instead: see [first-party applications](first-party-apps.md).
For structured tools, FastMCP may return a generated Pydantic object in `.data`. Use
`powerbi_mcp.client_results.tool_data(result)` to normalize both typed and dictionary responses
before indexing fields; this helper is used by the setup and evaluation commands.

## Tool contract

Start with [workflows and result statuses](../workflows.md). The schemas returned by MCP `tools/list`
are authoritative. The original tools remain available; `execute_dax` retains upstream result keys,
and `generate_dax` retains legacy error fields while adding structured status, evidence and recovery actions.

New tools: `analyze`, `search_semantic_models`, `get_model_context`, `search_schema`,
`get_dimension_values`, `run_recipe`, `diagnose_connection`. All Power BI tools are annotated read-only.

The legacy surface, with its original required arguments:

| Tool | Input | Output |
|---|---|---|
| `list_semantic_models` | `include_uncurated` (bool, default true) | list of `{id, name, workspace, curated, description, data_scope, default_date_table, key_measures, recipes}` |
| `get_business_context` | none | Markdown glossary plus recipe index |
| `get_recipe` | `name` | Markdown |
| `get_semantic_model_schema` | `model_id`, `compact` (default true) | text block, or `{notes, schema}` when `compact=false` |
| `execute_dax` | `model_id`, `dax_queries` (1 to 4), `max_rows` | `{executionResult: {tables: [{columns, rows}]}, semanticModel}` |
| `generate_dax` | `model_id`, `question`, `execute`, `max_rows`, `chat_history` | `{dax, explanation, assumptions, result?, execution_error?, repair_error?, repaired_after?}` |
| `get_report_metadata` | `report_id` | pages, visuals, bindings, filters |

Lower-level tools raise readable MCP errors. Analysis tools return `status=failed` with a typed
`error` and recovery `action`; always inspect status before interpreting rows. Nothing runs under
a shared Power BI identity.
