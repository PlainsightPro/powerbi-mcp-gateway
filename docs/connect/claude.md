# Connect Claude

You need the gateway URL from your administrator. It looks like
`https://<host>/mcp`. Sign-in uses your normal Microsoft work account; there is no client id,
secret or API key to paste. Everything you ask runs under your own Power BI permissions.

## Claude Desktop and claude.ai

1. Open **Settings**, then **Connectors**.
2. Choose **Add custom connector**.
3. Name it (for example `Power BI`) and paste the gateway URL as the **Remote MCP server URL**.
   Leave the OAuth client id and secret empty.
4. Select **Add**, then **Connect**. A browser tab opens on the Microsoft sign-in page; sign in
   with your work account. The first time you may see a consent page listing the gateway's
   permissions (read semantic models, read workspaces, read reports); accept it.
5. Back in Claude, the connector shows as connected. In a new chat, open the tools menu (the
   `+` or sliders icon) and make sure the connector is enabled for that conversation.

Ask: "Which Power BI models can I use?" Claude calls `list_semantic_models` and lists what your
account can open.

**Organisation-wide (Claude Team and Enterprise):** an organisation owner adds the same connector
under **Admin settings**, **Connectors**, so every member sees it without configuring anything.
Each member still signs in once with their own Microsoft account.

**Cowork:** connectors added in Claude Desktop are available in Cowork sessions. If Cowork asks for
a model id, tell it to call `list_semantic_models` first.

## Claude Code

```bash
claude mcp add --transport http powerbi https://<host>/mcp
```

Add `--scope project` to store it in the repository's `.mcp.json` so colleagues get it too, or
`--scope user` for all your projects. Then, inside Claude Code, run `/mcp`, select `powerbi` and
follow the browser sign-in. Claude Code registers itself with the gateway automatically (dynamic
client registration); the callback lands on `http://localhost:<random port>/callback`, which the
gateway accepts for native clients.

To share the configuration by file instead of the command:

```json
{
  "mcpServers": {
    "powerbi": { "type": "http", "url": "https://<host>/mcp" }
  }
}
```

Claude Code shows the gateway's instructions to the model at the start of every session, so it
already knows to list models first and to read the business context before answering.

## What Claude sees

- **Tools:** `list_semantic_models`, `get_business_context`, `get_recipe`,
  `get_semantic_model_schema`, `execute_dax`, `generate_dax`, `get_report_metadata`.
- **Prompts:** one per recipe of your deployment (Claude Code: `/mcp__powerbi__<recipe>`; Desktop:
  the prompts entry in the `+` menu).
- **Resources:** `skill://glossary`, `skill://dax-rules`, `skill://catalog`, `skill://recipes/<name>`
  (Claude Code: `@powerbi:skill://glossary`).

## Signing out or switching accounts

Claude Desktop and claude.ai: remove the connector and add it again. Claude Code: `/mcp`, select
the server, **Clear authentication**, then sign in again.
