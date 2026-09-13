# Power BI MCP Gateway

A remote MCP server that lets any MCP client (Claude, VS Code and GitHub Copilot, Cursor, ChatGPT,
your own agents) talk to Power BI semantic models **as the signed-in user**, with the
organisation's business knowledge kept centrally on the server instead of in every client's
instruction files.

It works with any Power BI semantic model in a workspace that has an XMLA endpoint, which means
**Premium Per User, Premium or Fabric capacity**. It is independent of organisation, industry and
model schema: everything domain-specific comes from a *skills folder* that each deployment supplies.

## Why

Microsoft's hosted Power BI MCP server can run DAX for a user, but it needs a model id you have to
know, it carries no business vocabulary, and its `GenerateQuery` tool runs on Copilot, which needs
a Copilot licence and an F2 or P1 capacity (on Premium Per User it fails with
`AI_Scenarios_SkuNotSupported`). The gateway sits in front of it:

| Need | Microsoft's hosted server | Gateway |
|---|---|---|
| Which models exist | none; you must know the id | `list_semantic_models`: only what the user can open, curated ones with description, scope and key measures |
| Business knowledge | none | server instructions, `get_business_context`, one prompt per recipe, `skill://` resources |
| DAX generation | `GenerateQuery` on Copilot: Copilot licence plus F2/P1 capacity, not Premium Per User | `generate_dax` on your own Foundry (Azure OpenAI) deployment, grounded in schema, glossary and rules, with one repair round; no Copilot, no Fabric capacity |
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

## Tools

| Tool | Purpose |
|---|---|
| `list_semantic_models` | Models the user can open; curated first with description, scope, date table, key measures, recipes |
| `get_business_context` | The deployment's glossary: vocabulary, measures, dates, model traps, recipe index |
| `get_recipe` | A validated query sequence for a recurring analysis |
| `get_semantic_model_schema` | Tables, columns, measures with descriptions, relationships |
| `execute_dax` | Run 1 to 4 DAX queries as the user (default 250 rows) |
| `generate_dax` | Question to DAX, optionally executed; returns DAX, explanation, assumptions, rows |
| `get_report_metadata` | Pages, visuals and filters of a report the user can open |
| `recall` / `remember` / `forget` | Notes users attach to one model (a correction, a trap, the measure for a recurring question). They follow the model: visible and writable only to people Power BI lets open it, and folded into the schema notes and `generate_dax` grounding |

Prompts: one per recipe in the skills folder. Resources: `skill://glossary`, `skill://dax-rules`,
`skill://catalog`, `skill://recipes/{name}`. The tool contract is in
[docs/connect/other-agents.md](docs/connect/other-agents.md).

## Skills: the part you own

`skills/` in this repository is a **fictional example** (Contoso Retail). A deployment brings its
own folder with the same five parts and the gateway is built with it:

| File | Purpose |
|---|---|
| `instructions.md` | How the assistant should work with the tools; sent to every client |
| `glossary.md` | Business vocabulary, which measure answers which question, sign conventions, traps |
| `dax-rules.md` | House rules for DAX generation |
| `recipes/*.md` | Validated query sequences; each becomes a prompt and a resource |
| `catalog.yaml` | Curated models: id, workspace, scope, key measures, notes |

Nothing about a real organisation belongs in this repository. See
[docs/skills-authoring.md](docs/skills-authoring.md) for writing a folder and
[docs/private-skills.md](docs/private-skills.md) for running many deployments from this one codebase.

## Deploy

Prerequisites: PowerShell 7, Azure CLI 2.78+, signed in with rights to register Entra applications
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
managed identity. It stages only runtime code and the selected skills for the build; `.env`, Git
history and private notes never reach the image. Re-run after changes; `-SkipFoundry` skips the
model part, `-SkipBuild` keeps the running image.

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

Claude Desktop, claude.ai, ChatGPT: add a custom connector with the URL, no client id. VS Code:
`{"type": "http", "url": "https://<host>/mcp"}` in `mcp.json`; Cursor: `{"url": "https://<host>/mcp"}`
under `mcpServers`.

## Local development

```powershell
uv sync                                  # .venv from uv.lock, dev tools included (ruff, pyright, pytest)
Copy-Item .env.example .env             # Entra app values, Foundry endpoint, optional PBIMCP_SKILLS_DIR
uv run pytest -q
uv run python -m powerbi_mcp             # http://localhost:8000/mcp
```

`http://localhost:8000/auth/callback` is registered on the Entra app by the deploy script, so a
local server runs the full sign-in. `az login` supplies the Foundry credential locally; the
signed-in user needs the Cognitive Services OpenAI User role on the Foundry resource, or set
`PBIMCP_FOUNDRY_API_KEY` for the session.

Three checks exist: `python -m powerbi_mcp --check-skills <folder>` validates a skills folder
offline, `scripts/smoke_test.py` runs the token exchange, catalog, schema, query and DAX generation
headlessly with the Azure CLI identity (the deploy script's `-PreauthorizeAzureCli` enables it), and
`scripts/check_gateway.py https://<host>/mcp` verifies a deployed gateway through the real browser
sign-in.

## Known limits

- The app runs one replica. Its OAuth state (client registrations, encrypted tokens) lives in an
  Azure Table, so a redeploy keeps users signed in; scaling out additionally needs
  `stateless_http=True` on the server (MCP sessions are per replica).
- Skills are deployment-wide, not filtered by the user's permissions; separate deployments for
  groups that must not share business knowledge.
- `generate_dax` returns one query per call; multi-step analyses follow recipes.

## License

MIT. Built by [Plainsight](https://plainsight.pro); contributions welcome.
