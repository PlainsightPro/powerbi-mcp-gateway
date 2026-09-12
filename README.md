# Plainsight Power BI MCP

A remote MCP server that lets any MCP client (Claude Code, Claude Desktop, claude.ai, VS Code,
Cursor) talk to Plainsight's Power BI semantic models **as the signed-in user**, with the finance
know-how kept centrally on the server instead of in every client's instruction files.

This is the standalone gateway application. The `skills/` folder contains fictional examples;
keep organisation-specific model IDs and business knowledge in a separate private folder.

## What it adds on top of Microsoft's hosted Power BI MCP

| Need | Microsoft hosted server | This server |
|---|---|---|
| Which models exist | none (you must know the id) | `list_semantic_models`: only what the user can open, curated ones with business context |
| Domain knowledge | none | server `instructions`, `get_finance_context`, `get_recipe`, MCP prompts and `skill://` resources |
| DAX generation | Copilot (fails on Premium Per User) | `generate_dax` on a Foundry **gpt-5** deployment, grounded in schema + glossary + rules |
| Client onboarding | pre-registered Entra client id per client | OAuth proxy with dynamic client registration: paste the URL, sign in |
| Execution | ExecuteQuery / schema / report metadata | the same, proxied with the user's on-behalf-of token (RLS and Build permission apply) |

## Architecture

```
Claude / VS Code / Cursor
   |  Streamable HTTP + OAuth 2.1 (DCR against this server)
   v
ca-powerbi-mcp (Azure Container Apps, Sweden Central)      FastMCP 4 + AzureProvider
   |-- /mcp        tools, prompts, resources, instructions   skills/ folder loaded at startup
   |-- /authorize  /token  /register  /auth/callback         OAuth proxy in front of Entra ID
   |
   |-- OBO token (api://<app> -> https://api.fabric.microsoft.com/.default) per call
   |      |-- Fabric REST   GET /workspaces, /workspaces/{id}/semanticModels   (what the user can open)
   |      `-- Hosted Power BI MCP  GetSemanticModelSchema / ExecuteQuery / GetReportMetadata
   `-- managed identity -> Foundry aif-powerbi-mcp, deployment gpt-5 (Responses API)
```

No service principal ever touches Power BI data: every Power BI call carries a token issued for the
signed-in user, which is what the Premium Per User terms of use require for multi-user apps.

## Tools

| Tool | Purpose |
|---|---|
| `list_semantic_models` | Models the user can open; curated first with description, scope, date table, key measures, recipes |
| `get_finance_context` | Glossary: measure per business question, sign conventions, dates, model traps, recipe index |
| `get_recipe` | Step-by-step queries for a recurring analysis (`indirect-cost-analysis`, `ebitda-bridge`) |
| `get_semantic_model_schema` | Tables, columns, measures with descriptions, relationships (compact text or raw JSON) |
| `execute_dax` | Run 1 to 4 DAX queries as the user (default 250 rows) |
| `generate_dax` | Question to DAX on gpt-5, optionally executed; returns DAX, explanation, assumptions, rows |
| `get_report_metadata` | Pages, visuals and filters of a report the user can open |

Prompts: `indirect-cost-analysis`, `ebitda-bridge`. Resources: `skill://glossary`, `skill://dax-rules`,
`skill://catalog`, `skill://recipes/{name}`.

## The skills folder (edit this, redeploy, every client benefits)

| File | Used for |
|---|---|
| `skills/instructions.md` | Sent to every client at `initialize`; how to work with the tools |
| `skills/glossary.md` | Business vocabulary and model knowledge; returned by `get_finance_context`, fed to gpt-5 |
| `skills/dax-rules.md` | House rules for DAX generation |
| `skills/recipes/*.md` | Tested query sequences; exposed as `get_recipe`, prompts and resources |
| `skills/catalog.yaml` | Curated models: id, workspace, scope, key measures, notes. Uncurated models the user can open are still listed, unlabeled |

Model descriptions in TMDL (`/// ...`) reach the schema tool too; keep documenting measures there.

## Deploy

Prerequisites: PowerShell 7 and Azure CLI 2.78+, logged in with Global Administrator (app registration + admin
consent) and Contributor on the subscription. The first run creates everything; later runs update.

```powershell
.\deploy\deploy_to_azure.ps1 -SkillsDir C:\path\to\private\skills -ServerName 'Your Power BI'
.\deploy\deploy_to_azure.ps1 -SkillsDir C:\path\to\private\skills -SkipFoundry
.\deploy\deploy_to_azure.ps1 -SkipFoundry -SkipBuild   # configuration only; preserves the running image
```

Always supply `-SkillsDir` when building for your organisation. The deployment stages only runtime
code and the selected skill files in a temporary directory, then removes it after upload. Private
skills are baked into your private Azure registry image; they are never copied into this Git repo.
Without `-SkillsDir`, a build uses the public fictional examples. `-WriteLocalEnv` preserves the
selected skills path and server name for local development.

Resources (all in `rg-powerbi-mcp`, Sweden Central): Entra app "Power BI MCP Server", Foundry
`aif-powerbi-mcp` with project `powerbi-mcp` and deployment `gpt-5`, ACR `acrplainsightpbimcp`,
Log Analytics `log-powerbi-mcp`, Container Apps environment `env-powerbi-mcp`, app `ca-powerbi-mcp`.

Indicative cost: container app 0.5 vCPU / 1 GiB, one replica, about EUR 20 to 30 per month; registry
about EUR 4.5; Foundry pay per token (a DAX generation is roughly 15k input tokens).

## Connect a client

```bash
claude mcp add --transport http powerbi https://<fqdn>/mcp     # then /mcp -> sign in
```

Claude Desktop and claude.ai: Settings, Connectors, Add custom connector, URL `https://<fqdn>/mcp`,
no client id. VS Code: `{"type": "http", "url": "https://<fqdn>/mcp"}` in `mcp.json`.

Users need a Power BI Premium Per User license and Build permission on the models they query;
Power BI enforces Build permission when schema and query tools are called. Model discovery lists
items visible through the user's workspace access; discovery alone does not prove Build permission.

## Local development

```powershell
uv venv .venv --python 3.11
uv pip install --python .venv/Scripts/python.exe -r requirements-dev.txt
Copy-Item .env.example .env   # fill in client id/secret, Foundry endpoint
.venv/Scripts/python.exe -m pytest
.venv/Scripts/python.exe -m powerbi_mcp             # http://localhost:8000/mcp
```

`http://localhost:8000/auth/callback` is registered on the Entra app, so a local server can run the
full sign-in. `az login` supplies the Foundry credential locally (`DefaultAzureCredential`); the
signed-in user needs the **Cognitive Services OpenAI User** role on `aif-powerbi-mcp` for that, or set
`PBIMCP_FOUNDRY_API_KEY` for the session instead.

Headless check of everything except the browser sign-in (needs `-PreauthorizeAzureCli` once):

```powershell
.venv/Scripts/python.exe scripts/smoke_test.py "Indirecte kost per maand dit jaar"
```

To verify the deployed OAuth proxy and container identity as well, run:

```powershell
.venv/Scripts/python.exe scripts/check_gateway.py https://<fqdn>/mcp --generate
```

Complete Microsoft sign-in in the opened browser. This checks MCP discovery, model discovery,
schema access, a constant query, and (with `--generate`) one small billable Foundry request.
It prints no tokens or business rows. Tokens stay in memory for this check only.
The headless smoke test uses the local Azure CLI identity, so it does not validate browser OAuth
or the container's managed identity. A separate test account without Build permission is needed
to verify denial; a Global Administrator's successful query cannot establish that boundary.

## Known limits (v1)

- The OAuth proxy keeps registered clients and encrypted upstream tokens on the replica's disk.
  A restart or redeploy means clients sign in again; the app runs one replica for that reason.
  Move `client_storage` to Redis or Azure Files before scaling out.
- `generate_dax` returns one query per call; multi-step analyses follow the recipes.
- Sales and Billability entries in the catalog are thin; extend `skills/glossary.md` as questions come in.
