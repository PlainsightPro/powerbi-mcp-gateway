# Connect other agents and your own code

The gateway is a standard remote MCP server: Streamable HTTP on `/mcp`, OAuth 2.1 with PKCE, and
the discovery documents clients need:

| Endpoint | Purpose |
|---|---|
| `/.well-known/oauth-protected-resource/mcp` | Protected resource metadata (RFC 9728) pointing at the authorization server below |
| `/.well-known/oauth-authorization-server` | Authorization server metadata (RFC 8414) |
| `/register` | Dynamic client registration (RFC 7591) |
| `/authorize`, `/token` | Authorization code + PKCE, refresh tokens |
| `/healthz` | Liveness, lists the recipes and curated model count |

Any client that implements the MCP authorization specification connects like the ones in the other
guides. An unauthenticated call to `/mcp` returns `401` with a `WWW-Authenticate` header that names
the resource metadata; clients start from there.

## Clients that cannot register dynamically

Some platforms (Microsoft Foundry Agent Service, Copilot Studio, older SDKs) want a client id and
secret up front. Register one client on the gateway yourself and paste the result:

```bash
curl -s https://<host>/register -H "Content-Type: application/json" -d '{
  "client_name": "Contoso sales agent",
  "redirect_uris": ["https://<the callback URL the platform documents>"],
  "grant_types": ["authorization_code", "refresh_token"],
  "token_endpoint_auth_method": "none"
}'
```

The response contains `client_id`. Use the gateway's `/authorize` and `/token` endpoints as the
OAuth URLs and `access_as_user` as the scope. For Foundry Agent Service, choose the MCP tool with
**OAuth identity passthrough** and **custom OAuth**, so every user of the agent signs in
themselves; a shared identity would bypass Power BI's per-user permissions and is not supported by
the gateway. Registrations live in the gateway's client store; after a redeploy that wipes the
store (single-replica deployments), register again.

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

## Tool contract

| Tool | Input | Output |
|---|---|---|
| `list_semantic_models` | `include_uncurated` (bool, default true) | list of `{id, name, workspace, curated, description, data_scope, default_date_table, key_measures, recipes}` |
| `get_business_context` | none | Markdown glossary plus recipe index |
| `get_recipe` | `name` | Markdown |
| `get_semantic_model_schema` | `model_id`, `compact` (default true) | text block, or `{notes, schema}` when `compact=false` |
| `execute_dax` | `model_id`, `dax_queries` (1 to 4), `max_rows` | `{executionResult: {tables: [{columns, rows}]}, semanticModel}` |
| `generate_dax` | `model_id`, `question`, `execute`, `max_rows`, `chat_history` (the last 6 turns are used) | `{model_id, dax, explanation, assumptions, result?, execution_error?, repair_error?, repaired_after?}` |
| `get_report_metadata` | `report_id` | pages, visuals, bindings, filters |
| `recall` | `model_id` | `{model_id, memories: [{id, text, created_at, mine}]}`; the access error when the user cannot open the model |
| `remember` | `model_id`, `text` (up to 500 characters by default) | `{model_id, memory: {id, text, created_at, mine}}` |
| `forget` | `model_id`, `memory_id` (one of the caller's own) | `{model_id, forgotten: {...}}` |

All tools raise a readable error when Power BI refuses the user (no Build permission, no licence,
expired sign-in) or rate-limits them (the message carries the wait); nothing runs under a service
identity. `max_rows` is capped by the deployment (`PBIMCP_MAX_ROWS_LIMIT`, 10,000 by default). The
compact schema is cut at 60,000 characters with a `(schema truncated)` marker; the same text grounds
`generate_dax`, so on a very large model pass `compact=false` to see everything.
