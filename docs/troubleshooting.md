# Troubleshooting

| Symptom | Likely cause | What to do |
|---|---|---|
| Sign-in page opens but the tool never shows "connected" | The browser callback did not reach the tool (blocked pop-up, a different browser profile, corporate proxy) | Retry with the browser the tool opened; in Claude Code paste the full callback URL from the address bar when asked |
| Sign-in loops, or `AADSTS50011` (redirect URI mismatch) | The gateway was redeployed on a new host name and the Entra app still lists the old callback | Administrator: re-run the deploy script, which registers `https://<host>/auth/callback` again |
| `401 Unauthorized` after it used to work | Your gateway session expired, or the deployment still keeps OAuth state on the replica's disk and was restarted | Reconnect: Claude Code `/mcp` then clear authentication and sign in; Desktop/claude.ai remove and re-add the connector; VS Code restart the server |
| "Power BI refused the request for the signed-in user" | No **Build** permission on that model, or no licence for the workspace (PPU workspaces need a PPU licence) | Ask the model owner for Build permission; check your licence in Power BI, **Settings**, **Licenses** |
| `list_semantic_models` returns an empty list | Your account has no workspace access, or the Fabric tenant blocks the REST API for your user | Open app.powerbi.com and confirm you can see workspaces; then ask the administrator to check the tenant settings in [Administration](administration.md) |
| A model you can open in Power BI is missing from the list | It is a Pro-only workspace, or the workspace listing is cached (5 minutes) | Wait five minutes and retry; Pro-only workspaces are outside the supported tiers |
| A new measure or column is not used by `generate_dax` | The schema is cached per user for 10 minutes | Wait, or ask the administrator to restart the app |
| "Power BI is rate-limiting the signed-in user" | Too many Fabric or query calls in a short time (the message names the wait) | Wait the stated seconds; ask fewer, more aggregated questions |
| `DAX generation failed: ... cut off (max_output_tokens)` | The model's reasoning plus answer exceeded the output budget | Simplify the question; administrator: raise `PBIMCP_FOUNDRY_MAX_OUTPUT_TOKENS` |
| "generate_dax is not configured" | The deployment has no Foundry endpoint | Administrator: set `PBIMCP_FOUNDRY_ENDPOINT`; until then the assistant writes DAX itself and runs `execute_dax` |
| `DAX generation failed: ... 401` or `PermissionDenied` | The gateway's identity lacks the **Cognitive Services OpenAI User** role on the Foundry resource | Administrator: re-run the deploy script (it assigns the role) or assign it in the portal |
| `AI_Scenarios_SkuNotSupported` in an error | Something called Microsoft's Copilot-backed GenerateQuery instead of the gateway | Tell the assistant to use `generate_dax`; the gateway's instructions already say so |
| The generated query fails twice (`execution_error` and `repair_error`) | The question needs a field the model does not have, or the glossary lacks the vocabulary | Rephrase with the measure names from `get_business_context`; if this repeats, the deployment's glossary needs an entry |
| A number comes back as text such as `€1.729.015` or `81.11%` | Microsoft's hosted server applies the measure's format string when the column is a bare measure reference, or when the column alias equals a measure name | Add `+ 0` and use an alias that is not a measure name (`"Won EUR", [Won Amount] + 0`); `generate_dax` does this by itself |
| "Last 6 months" returns empty or future months | The model holds future-dated rows (prepaid invoices, accruals), so the maximum of the date table is not today | Bound the period by today's date or the model's month-offset column; the DAX generator is told to do so |
| Two different answers to the same question | Different date tables, "original" versus "corrected" views, or an incomplete current month | Ask which model, date table and view were used; make the period explicit |
| Slow first response of the day | The container app scaled down or restarted | Nothing to do; subsequent calls are fast |
| Health check `https://<host>/healthz` fails | The container app is down | Administrator: `az containerapp logs show`, then redeploy |

## For administrators: reading the logs

```powershell
az containerapp logs show -n <app> -g <resource group> --tail 100 --format text
```

Every tool call is one line on logger `powerbi_mcp.tools`, for example
`tool=execute_dax user=<oid> model_id=<id> dax_queries=<1> ms=412 outcome=ok`. A failed call is a
WARNING with `outcome=error message=...`: the message is what the assistant saw, including
`Power BI MCP error` (the hosted server's text, DAX syntax errors included), `Power BI refused the
request` (no Build permission or licence) and `DAX generation failed` (Foundry). `POST /mcp ... 401`
in the access log is a client that is not signed in. The gateway never logs tokens, questions, query
text or result rows.
