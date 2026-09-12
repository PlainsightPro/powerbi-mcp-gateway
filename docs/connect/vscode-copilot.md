# Connect VS Code and GitHub Copilot

Requires VS Code with GitHub Copilot and agent mode. You need the gateway URL
(`https://<host>/mcp`) from your administrator.

## Add the server

Create or edit `.vscode/mcp.json` in your workspace (shared with the repository) or add it to your
user settings for all workspaces:

```json
{
  "servers": {
    "powerbi": {
      "type": "http",
      "url": "https://<host>/mcp"
    }
  }
}
```

VS Code shows a **Start** link above the entry. Select it: VS Code registers itself with the
gateway and opens the Microsoft sign-in page. Sign in with your work account and accept the consent
page the first time.

Alternatively run **MCP: Add Server** from the command palette, choose **HTTP**, and paste the URL.

## Use it

1. Open the Copilot chat, switch to **Agent** mode.
2. Check the tools picker (the tools icon in the chat input): the `powerbi` tools must be enabled.
3. Ask "Which Power BI models can I use?" and go from there. Copilot asks for confirmation
   before it runs a tool the first time; you can allow it for the session.

Prompts from the gateway (one per recipe) appear when you type `/` in the chat input, prefixed
with `mcp.powerbi`. Resources are available through **Add Context**, **MCP Resources**.

## Notes

- The gateway's own sign-in is separate from GitHub Copilot's; you sign in to both.
- If VS Code keeps reporting the server as needing authentication, run **MCP: Reset trust** or
  remove and re-add the server, then start it again.
