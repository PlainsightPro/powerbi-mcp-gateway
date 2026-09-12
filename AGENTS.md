# powerbi-mcp-gateway - AI coding assistant guide

A remote MCP server that gives any MCP client (Claude Code, Claude Desktop, claude.ai, VS Code,
Cursor) per-user access to Power BI semantic models, with the organisation's know-how ("skills")
kept on the server and DAX generation on a Foundry (Azure OpenAI) deployment. See README.md for the
architecture; this file is for working in the code.

## Layout

| Path | Role |
|---|---|
| `powerbi_mcp/server.py` | FastMCP app: AzureProvider OAuth proxy, tools, prompts, resources, `/healthz` |
| `powerbi_mcp/config.py` | Settings from `PBIMCP_*` env vars / `.env` |
| `powerbi_mcp/fabric.py` | Fabric REST: workspaces and semantic models the user can open |
| `powerbi_mcp/hosted_mcp.py` | Client for Microsoft's hosted Power BI MCP (schema, DAX execution, report metadata) |
| `powerbi_mcp/catalog.py` | Curated catalog (`skills/catalog.yaml`) merged with the user's accessible models |
| `powerbi_mcp/dax_generator.py` | gpt-5 DAX generation + one repair round, strict JSON output |
| `powerbi_mcp/skills.py` | Loads the skills folder once at startup |
| `skills/` | Example skills (fictional company). Real skills live in a private folder: `PBIMCP_SKILLS_DIR` / `-SkillsDir` |
| `deploy/deploy_to_azure.ps1` | Idempotent Azure deploy (Entra app, Foundry, ACR, Container Apps) |
| `scripts/smoke_test.py` | Headless end-to-end check of everything behind the OAuth proxy |
| `tests/` | pytest, no network (httpx MockTransport, stub Responses client) |

## Commands (Windows, PowerShell or Git Bash)

```
uv venv .venv --python 3.11
uv pip install --python .venv/Scripts/python.exe -r requirements-dev.txt
.venv/Scripts/python.exe -m pytest -q
.venv/Scripts/python.exe -m powerbi_mcp                 # http://localhost:8000/mcp
.venv/Scripts/python.exe scripts/smoke_test.py "question"
.\deploy\deploy_to_azure.ps1 -SkillsDir C:\path\to\private\skills
```

`.env` (git-ignored) carries the Entra client id/secret and the Foundry endpoint; copy from
`.env.example`. The smoke test needs `-PreauthorizeAzureCli` done once by the deploy script.

## Design rules

- Every Power BI call carries the signed-in user's token (`EntraOBOToken`); never add a service
  principal path. Premium Per User terms forbid shared identities for multi-user apps and RLS
  would not apply.
- The OAuth proxy issues its own tokens; MCP clients register dynamically (DCR, CIMD). Keep
  `forward_resource=False`: Entra's v2 endpoint rejects an RFC 8707 `resource` next to `scope`.
- Do not put downstream (Fabric) scopes in `additional_authorize_scopes`; admin consent on the
  app's delegated Power BI permissions is what makes the OBO exchange work.
- Text answers on structured hosted-MCP tools are errors (that is how the hosted server reports a
  DAX syntax error); `generate_dax` then gets one repair round with the engine message.
- Skills are data, not code: change `skills/*` (or the private folder), redeploy, done. Tests assert
  the example skills' structure (recipe names, an `[Indirect Cost]` mention, `GenerateQuery` in the
  instructions); keep those tokens when editing the examples.
- One replica: the proxy stores registered clients and encrypted upstream tokens on local disk.
  Add a `client_storage` backend before scaling out.

## Deploy gotchas already handled in the script (do not "simplify" them away)

- A PowerShell helper named `Az` calling `& az` recurses into itself; helpers are `Invoke-Az*` and
  call the resolved `az.cmd`.
- Azure Policy may require an `Owner` tag on resource groups (`-OwnerTag`).
- Graph refuses `preAuthorizedApplications` in the PATCH that creates the scope it references.
- Admin consent right after `az ad sp create` races replication; the script retries.
- `az acr build` log streaming crashes on Unicode on Windows (`az.cmd` runs Python with `-I`, so
  `PYTHONUTF8` cannot help); the script queues with `--no-logs` and polls the run.
- JSON bodies for `az rest --body` go through a temp file (`@file`), never inline.
- `azure.identity.aio` needs `aiohttp`; it is in requirements for that reason.
