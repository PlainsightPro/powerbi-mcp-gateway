# Connect ChatGPT

ChatGPT connects to remote MCP servers through **custom connectors** (called **Apps** in some
plans). This needs a plan and role that allow custom connectors (workspace admins on Team,
Enterprise and Edu; developer mode on individual plans). The gateway supports the OAuth flow
ChatGPT expects (dynamic client registration with PKCE), so no client id is needed.

1. Open **Settings**, then **Connectors** (or **Apps**), then **Create**.
2. Name: `Power BI`. MCP server URL: `https://<host>/mcp`. Authentication: **OAuth**.
3. Leave the OAuth client fields on their defaults (ChatGPT registers itself). Select **Create**.
4. Sign in with your Microsoft work account in the tab that opens and accept the consent page.
5. Start a chat, enable the connector for the conversation (through the tools or `+` menu) and
   ask "Which Power BI models can I use?".

Workspace admins can publish the connector to the whole workspace; each member still signs in
once with their own Microsoft account, so everyone only sees their own models.

ChatGPT executes tools without the per-call confirmation that coding assistants show; the gateway
is read-only towards Power BI, so this is safe, but be aware that a question triggers queries
immediately.
