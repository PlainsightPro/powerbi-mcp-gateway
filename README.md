# Power BI MCP Gateway

A remote MCP server that lets any MCP client (Claude, VS Code and GitHub Copilot, Cursor, ChatGPT,
your own agents) talk to Power BI semantic models **as the signed-in user**, with the
organisation's business knowledge kept centrally on the server instead of in every client's
instruction files.

It works with any Power BI semantic model in a workspace that has an XMLA endpoint, which means
**Premium Per User, Premium or Fabric capacity**. It is independent of organisation, industry and
model schema: everything domain-specific comes from a *skills folder* that each deployment supplies.

## Why

Microsoft's hosted Power BI MCP server runs DAX and exposes model-authored metadata, but its tools
need a model id. Its `GenerateQuery` tool uses Copilot. The gateway adds model discovery, centrally
maintained business definitions, executable recipes and a choice of Foundry or client-side DAX
generation. It preserves Microsoft's model metadata rather than replacing it. See Microsoft's
[hosted tool documentation](https://learn.microsoft.com/en-us/power-bi/developer/mcp/remote-mcp-server-tools).

| Need | Microsoft's hosted server | Gateway |
|---|---|---|
| Which models exist | none; you must know the id | `list_semantic_models`: only what the user can open, curated ones with description, scope and key measures |
| Business knowledge | Model-authored AI metadata, descriptions and report context | Adds shared and model-specific business definitions, validated workflows and reusable context |
| DAX generation | `GenerateQuery` on Copilot, with its licensing/capacity requirements | Foundry generation with one query repair, or client-side generation; no use of Copilot GenerateQuery |
| Client onboarding | an Entra app registration per client | OAuth proxy with dynamic client registration: paste the URL, sign in |
| Execution | schema, DAX, report metadata | the same, called with the user's on-behalf-of token, so Build permission and row-level security apply |

The gateway never calls `GenerateQuery`. `generate_dax` writes the query on the deployment's own
Foundry model and runs it through the hosted server's `ExecuteQuery`, which only needs Build
permission on the model and an XMLA-capable workspace. Microsoft's open-source
[skills-for-fabric](https://github.com/microsoft/skills-for-fabric) and
[powerbi-modeling-mcp](https://github.com/microsoft/powerbi-modeling-mcp) take the same route:
the client's own LLM writes the DAX, guided by skill files. There is no Microsoft library for DAX
generation; the reusable part is their DAX guideline text, which a deployment can fold into its
`skill://dax-rules`.

Nothing runs under a service identity. Every Power BI call carries a token issued for the signed-in
user, which is also what the Premium Per User terms of use require for multi-user applications.

## Architecture

```
MCP client (Claude, VS Code, Cursor, ChatGPT, agent code)
   |  Streamable HTTP + OAuth 2.1 (dynamic client registration against the gateway)
   v
Gateway  (Azure Container Apps)                          FastMCP + Azure OAuth proxy
   |-- /mcp        tools, prompts, resources, instructions   <- skills folder, loaded at startup
   |-- /authorize  /token  /register  /auth/callback         <- Microsoft Entra ID behind it
   |
   |-- per call: on-behalf-of token for https://api.fabric.microsoft.com
   |      |-- Fabric REST      workspaces and semantic models the user can open
   |      `-- hosted Power BI MCP   GetSemanticModelSchema / ExecuteQuery / GetReportMetadata
   `-- managed identity -> Foundry model deployment (Responses API) for generate_dax
```

## Start with a question

Open `https://<host>/` for connection instructions. After signing in, ask a question with a model
name or paste a Power BI report link. The `analyze` tool resolves the model, loads its business
context and relevant schema, then generates and runs DAX. Ambiguous choices return a question
with model candidates. Return the supplied `context` with follow-ups to retain period and filters.

See [the workflow guide](docs/workflows.md) for examples, result statuses and client-side generation.

## Tools

| Tool | Purpose |
|---|---|
| `analyze` | Question or report link to grounded analysis, evidence and reusable follow-up context |
| `search_semantic_models` | Search names, aliases, topics or measures; filter workspace, paginate, refresh and verify query access |
| `list_semantic_models` | Workspace-discovered models; curated first; query access remains unchecked until verified |
| `get_business_context` | The deployment's glossary: vocabulary, measures, dates, model traps, recipe index |
| `get_recipe` / `run_recipe` | Read instructions or execute a typed YAML workflow, stopping on a failed step |
| `get_model_context` | Shared and model-specific definitions, relevant schema and compatible recipes |
| `search_schema` / `get_dimension_values` | Find exact objects and permitted filter values |
| `get_semantic_model_schema` | Tables, columns, measures with descriptions, relationships |
| `execute_dax` | Run 1 to 4 DAX queries as the user (default 250 rows) |
| `generate_dax` | Question to DAX, optionally executed; returns DAX, explanation, assumptions, rows |
| `get_report_metadata` | Authored pages, visuals and filters; current personal slicer state is unavailable |
| `diagnose_connection` | Check discovery, schema and constant query access; optional generation probe |

Prompts: one per recipe in the skills folder. Resources: `skill://glossary`, `skill://dax-rules`,
`skill://catalog`, `skill://recipes/{name}`. The tool contract is in
[docs/connect/other-agents.md](docs/connect/other-agents.md).

## Skills: the part you own

`skills/` in this repository is a **fictional example** (Contoso Retail). A deployment brings its
own folder with these parts and the gateway is built with it:

| File | Purpose |
|---|---|
| `instructions.md` | How the assistant should work with the tools; sent to every client |
| `glossary.md` | Business vocabulary, which measure answers which question, sign conventions, traps |
| `dax-rules.md` | House rules for DAX generation |
| `models/*.md` | Model-specific definitions selected through `catalog.yaml` |
| `recipes/*.md` | Human-readable analysis instructions; each becomes a prompt and resource |
| `recipes/*.yaml` | Optional executable workflows with typed parameters and result checks |
| `catalog.yaml` | Curated models: id, workspace, scope, key measures, notes |

Nothing about a real organisation belongs in this repository. See
[docs/skills-authoring.md](docs/skills-authoring.md) for writing a folder and
[docs/private-skills.md](docs/private-skills.md) for running many deployments from this one codebase.

## Deploy

Prerequisites: Python 3.11+ with dependencies installed using `uv sync`, PowerShell 7, Azure CLI 2.78+, signed in with rights to register Entra applications
and grant admin consent, and Contributor on the subscription. The tenant needs the Power BI MCP
endpoint enabled (see [docs/administration.md](docs/administration.md)).

```powershell
# try it with the example skills
.\deploy\deploy_to_azure.ps1 -AcrName <globally unique registry name>

# a real deployment: private skills + resource names from a profile, engine pinned to a release
.\deploy\deploy_to_azure.ps1 -Profile C:\deployments\<org>\deploy\profile.json
```

The script creates or updates, idempotently: the Entra app (confidential client, `access_as_user`
scope, delegated Power BI permissions, admin consent), a Foundry resource with a model deployment,
a container registry, Log Analytics, a Container Apps environment and the container app with a
managed identity, plus Azure Tables for encrypted OAuth state and shared model notes. It stages only runtime code and the selected skills for the build; `.env`, Git
history and private notes never reach the image. Re-run after changes; `-SkipFoundry` skips the
model part, `-SkipBuild` keeps the running image. `-ValidateOnly` checks skills and profile locally.
Use `-FoundryEndpoint` for an existing deployment, or `-DisableGeneration` for client-written DAX.
Set `-ModelName` to the deployment name when using an existing endpoint. See [administration](docs/administration.md).

Releases publish the engine image to `ghcr.io/plainsightpro/powerbi-mcp-gateway:<version>`; a
deployment profile with `EngineImage` layers its skills on that image instead of building the
source, which is how deployments upgrade without forking.

## Connect

Users need the gateway URL, a Microsoft work account, a licence matching the workspace and Build
permission on the models they query. Walkthroughs per tool, usage guidelines and troubleshooting
are in [docs/](docs/README.md). In short:

```bash
claude mcp add --transport http powerbi https://<host>/mcp      # then /mcp and sign in
```

Claude Desktop, claude.ai, ChatGPT: add a custom connector with the URL, no client id. VS Code and
Cursor: `{"type": "http", "url": "https://<host>/mcp"}` in the MCP settings file.

## Local development

```powershell
uv sync --python 3.11
Copy-Item .env.example .env             # Entra app values, Foundry endpoint, optional PBIMCP_SKILLS_DIR
.venv/Scripts/python.exe -m pytest -q
.venv/Scripts/python.exe -m powerbi_mcp   # http://localhost:8000/mcp
```

`http://localhost:8000/auth/callback` is registered on the Entra app by the deploy script, so a
local server runs the full sign-in. `az login` supplies the Foundry credential locally; the
signed-in user needs the Cognitive Services OpenAI User role on the Foundry resource, or set
`PBIMCP_FOUNDRY_API_KEY` for the session.

Two checks exist: `scripts/smoke_test.py` runs the token exchange, catalog, schema, query and DAX
generation (with `--generate`) headlessly with the Azure CLI identity (the deploy script's `-PreauthorizeAzureCli`
enables it), and `scripts/check_gateway.py https://<host>/mcp` verifies a deployed gateway through
the real browser sign-in.

## Known limits

- Keep one replica. Deployment preserves encrypted OAuth state in Azure Tables; retain both the
  storage account and signing key across updates. This does not establish support for multiple replicas.
  Existing ephemeral deployments require a fresh sign-in when first migrated.
- Skills are deployment-wide, not filtered by the user's permissions; separate deployments for
  groups that must not share business knowledge.
- `generate_dax` returns one query per call; executable recipes support up to 12 ordered steps.
- Workspace discovery can miss directly shared models. Verify curated candidates or supply a known
  model id; every schema and query request still uses the signed-in user's token.
- Query execution time is not data freshness. Completeness stays unknown unless Power BI reports it.

## License

MIT. Built by [Plainsight](https://plainsight.pro); contributions welcome.
