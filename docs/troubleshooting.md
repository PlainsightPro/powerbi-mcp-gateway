# Troubleshooting

| Symptom | Likely cause | What to do |
|---|---|---|
| Sign-in page opens but the tool never shows "connected" | The browser callback did not reach the tool (blocked pop-up, a different browser profile, corporate proxy) | Retry with the browser the tool opened; in Claude Code paste the full callback URL from the address bar when asked |
| Sign-in loops, or `AADSTS50011` (redirect URI mismatch) | The gateway was redeployed on a new host name and the Entra app still lists the old callback | Administrator: re-run the deploy script, which registers `https://<host>/auth/callback` again |
| `401 Unauthorized` after it used to work | Your gateway session expired, or the gateway restarted and lost its client registrations (single-replica deployments) | Reconnect: Claude Code `/mcp` then clear authentication and sign in; Desktop/claude.ai remove and re-add the connector; VS Code restart the server |
| "Power BI refused the request for the signed-in user" | No **Build** permission on that model, or no licence for the workspace (PPU workspaces need a PPU licence) | Ask the model owner for Build permission; check your licence in Power BI, **Settings**, **Licenses** |
| `list_semantic_models` returns an empty list | Your account has no workspace access, or the Fabric tenant blocks the REST API for your user | Open app.powerbi.com and confirm you can see workspaces; then ask the administrator to check the tenant settings in [Administration](administration.md) |
| A model you can open in Power BI is missing from the list | It is a Pro-only workspace, or the workspace listing is cached (5 minutes) | Wait five minutes and retry; Pro-only workspaces are outside the supported tiers |
| "generate_dax is not configured" | The deployment has no Foundry endpoint | Administrator: set `PBIMCP_FOUNDRY_ENDPOINT`; until then the assistant writes DAX itself and runs `execute_dax` |
| `DAX generation failed: ... 401` or `PermissionDenied` | The gateway's identity lacks the **Cognitive Services OpenAI User** role on the Foundry resource | Administrator: re-run the deploy script (it assigns the role) or assign it in the portal |
| `AI_Scenarios_SkuNotSupported` in an error | Something called Microsoft's Copilot-backed GenerateQuery instead of the gateway | Tell the assistant to use `generate_dax`; the gateway's instructions already say so |
| The generated query fails twice (`execution_error` and `repair_error`) | The question needs a field the model does not have, or the glossary lacks the vocabulary | Rephrase with the measure names from `get_business_context`; if this repeats, the deployment's glossary needs an entry |
| Two different answers to the same question | Different date tables, "original" versus "corrected" views, or an incomplete current month | Ask which model, date table and view were used; make the period explicit |
| Slow first response of the day | The container app scaled down or restarted | Nothing to do; subsequent calls are fast |
| Health check `https://<host>/healthz` fails | The container app is down | Administrator: `az containerapp logs show`, then redeploy |

## For administrators: reading the logs

```powershell
az containerapp logs show -n <app> -g <resource group> --tail 100 --format text
```

Look for `POST /mcp ... 401` (client not signed in), `Power BI MCP error` lines (the hosted
server's message, including DAX syntax errors), and `DAX generation failed` (Foundry). The gateway
never logs tokens or result rows.
