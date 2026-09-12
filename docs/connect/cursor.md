# Connect Cursor

You need the gateway URL (`https://<host>/mcp`) from your administrator.

1. Open **Cursor Settings**, then **MCP** (or **Tools & MCP**).
2. Choose **Add new global MCP server**. Cursor opens `~/.cursor/mcp.json`; add:

   ```json
   {
     "mcpServers": {
       "powerbi": { "url": "https://<host>/mcp" }
     }
   }
   ```

   For one project only, put the same content in `.cursor/mcp.json` inside the project.
3. Save. The MCP list shows `powerbi` with **Needs login**; select **Login**. A browser tab opens
   on the Microsoft sign-in page; sign in with your work account and accept the consent page the
   first time.
4. The entry turns green and lists the tools.

In the chat (agent mode), ask "Which Power BI models can I use?" Cursor asks before running a tool
the first time.

If the login button does nothing, remove the server, restart Cursor and add it again; Cursor
occasionally keeps a stale registration after the gateway was redeployed.
