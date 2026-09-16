# Troubleshooting

Start with `diagnose_connection` from your signed-in assistant. Supply `model_id` to test a specific
model. The public `/healthz` endpoint only says whether the server process is running.

| Symptom or status | Likely cause | Next step |
|---|---|---|
| Sign-in never finishes | Callback blocked or opened in another browser context | Retry the client's sign-in flow and use its callback instructions |
| `AADSTS50011` | Redirect URI mismatch | Verify `https://<host>/auth/callback` on the gateway's Entra application |
| `authentication` or HTTP 401 from Power BI | Expired or invalid user session | Reconnect using your work account |
| Repeated reconnects after deployment | OAuth storage missing, key changed, or ephemeral mode | Check the Azure Table account, identity role and stable signing key; initial migration requires a new sign-in |
| `permission` | Model Build access, licence or tenant settings | Have the model owner check the diagnostic's model and permissions |
| Model missing from discovery | Direct sharing, workspace access or cached listing | Refresh; use access verification for curated models, or supply its known id/report link |
| Discovery is `partial` | A workspace or candidate could not be checked | Read warnings, fix the affected access/service issue, then refresh |
| `needs_clarification` | Several matching models or material scope is missing | Choose a candidate or provide the requested scope/recipe parameters |
| `context_ready` | Server generation disabled | Have the client write DAX from returned model context, then call `execute_dax` |
| Foundry generation error | Endpoint, deployment name, model support or identity access | Check generation configuration and the app identity's OpenAI User role |
| `throttled` | Upstream rate limit | Wait for `retry_after_seconds` before repeating |
| `unavailable` | Timeout or upstream outage | Retry after the service recovers; no DAX repair is attempted |
| `protocol` | Unexpected upstream response | Administrator: check hosted MCP compatibility and the response shape |
| Both query attempts fail | Missing objects, unsupported DAX or incomplete business definitions | Inspect both attempts, exact schema and selected model context |
| Different figures from a report | Different date relationship, scope, slicers or freshness | Compare model, period and explicit filters; report links do not capture personal slicers |
| `partial` result or unknown completeness | Result limited or recipe stopped | Check step statuses; aggregate or narrow the query before drawing conclusions |
| Skills validation fails | Invalid YAML, unknown keys, missing references or incompatible recipe | Fix the reported definition, then validate again before building |

`generate_dax` repairs a DAX query error once. It retains both attempts; the final DAX is the query
actually attempted last. A prepared or failed query is never a successful analysis.

To inspect container logs:

```powershell
az containerapp logs show -n <app> -g <resource group> --tail 100 --format text
```

Use the result's `request_id` to locate its status, duration and attempt count.
See [administration](administration.md) for persistent storage, generation modes and live checks.
