# From a question to a checked result

Connect using the URL on the gateway's welcome page (`https://<host>/`). Sign in with the account
that has access to the model. The following examples are MCP tool arguments, usable from any client.

## Ask a question

```json
{"question":"Show the monthly trend for the last six complete months","model_hint":"sales"}
```

Call `analyze` with this object. A model hint can be a catalog name, alias or id. Without a hint,
discovery searches the question and asks for a choice if the model is ambiguous. A report URL can
replace the hint:

```json
{"question":"Explain the trend on this report","report_url":"https://app.powerbi.com/groups/00000000-0000-0000-0000-000000000001/reports/00000000-0000-0000-0000-000000000002"}
```

Report metadata supplies authored pages, visuals and filters, plus filters embedded in the URL.
The gateway cannot see your current personal slicers. State those filters explicitly. If metadata
does not identify one model, repeat with `model_hint` after choosing the model.

## Search and confirm access

`search_semantic_models(query="", workspace="", limit=20, offset=0, refresh=false,
verify_access=false)` returns models, match reasons, warnings, a checked timestamp and `next_offset`.
Use the returned offset for the next page; workspace accepts an exact workspace name or id.
Search matches names, aliases, descriptions, topics and key measures.

Discovery lists workspace-visible models plus the curated models shared directly with the user
(probed with a constant query as the user; `shared_directly=true`). `query_access=unchecked` does
not prove Build permission on workspace-visible rows; use `verify_access=true` to run the constant
probe on the returned page. Discovery does not enumerate uncatalogued models shared directly with a
user. A known model id or report link can still be used; downstream schema and query calls enforce
access.

A partial workspace scan is reported with warnings and is not cached as a complete list. Use
`refresh=true` after fixing an access or connection issue. Discovery and schema caches are
bounded, expire, and are isolated by authenticated user.

## Preserve a follow-up's scope

Return the previous analysis's `context` unchanged alongside the next `question`. It includes
model id, reference date, timezone, period, date table, measures, filters and previous query.
For example, “Break that down by category” can preserve the period and filters. To change scope,
state the change in the question and check the returned context. Switching models resets context.

A reference date makes “last month” reproducible. Timezone defaults to `PBIMCP_TIMEZONE` and
calendar conventions come from the selected catalog entry. The context travels with the client;
there is no shared conversation state on the gateway.

## Read the result status first

| Status | Meaning | Next action |
|---|---|---|
| `needs_clarification` | Model or material query scope is ambiguous | Answer the question; use a returned candidate or recipe parameter definition |
| `context_ready` | Foundry is disabled | Have the client write DAX from `model_context`, then call `execute_dax` |
| `generated` | DAX prepared with `execute=false` | Review the query before execution |
| `executed` | Query completed | Read rows together with context, assumptions and evidence |
| `partial` | Upstream reports a limited result, or a recipe stopped after earlier success | Check step statuses and warnings; narrow or retry only what needs attention |
| `failed` | Analysis could not finish | Follow the typed error's `action` |

Evidence includes row count, per-query row limit, column metadata, execution timestamp and
completeness (`complete`, `limited`, `unknown`). A successful response is not proof that all
possible rows were returned. Completeness requires an upstream signal; reaching the limit adds a
warning for generated analyses. Execution time is not data freshness. Freshness remains unknown
unless a configured catalog `freshness_query` returns a value, which is shown with its source.

Only a DAX query error gets one repair attempt. Both attempts and their errors are retained.
Expired sign-in, permission, throttling and transport errors have distinct recovery actions.
Transient HTTP failures receive bounded retries that honor `Retry-After`; longer delays are
returned to the client instead of tying up a request.

## Use exact schema objects and filter values

`get_model_context(model_id, question)` supplies shared and model-specific definitions, rules,
relevant schema and compatible recipes. It works without Foundry.

`search_schema(model_id, query, table, offset, limit)` returns complete metadata for matching
columns and measures. Compact schema retains relationships and native metadata before ranking
members against the question, and explicitly reports omitted details.
Use `get_semantic_model_schema(compact=false)` for the full schema.

`get_dimension_values(model_id, table, column, search, limit)` resolves labels to actual permitted
values, with a maximum of 100 values. All lookups use the signed-in user's token.

## Run a recurring analysis

Read `get_recipe(name)` for its definition, parameters and purpose. Markdown recipes remain
instructions. Recipes with YAML definitions can run through:

```json
{"model_id":"11111111-1111-1111-1111-111111111111","name":"monthly-trend","parameters":{"start_month":202601,"end_month":202606},"execute":false}
```

Call `run_recipe`; change `execute` to true to run it. A YAML recipe validates model compatibility,
object references and typed parameter values before execution. Steps run in order and stop on a
failed query or result check, preserving earlier results. Dependencies express required ordering;
they do not bind values from a previous result into a later query. Define scalar parameters up front.

## Diagnose a connection

Ask the client to call `diagnose_connection`. An optional `model_id` targets a specific model,
including one shared directly. The diagnostic checks discovery, schema and a constant query,
returning statuses rather than business rows. `check_generation=true` adds a billable generation probe.

For a deployed gateway:
```powershell
python scripts/check_gateway.py https://<host>/mcp --model-id <id>
python scripts/check_gateway.py https://<host>/mcp --model-id <id> --generate
```
