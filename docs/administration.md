# Administration

## Prerequisites in the Fabric / Power BI tenant

| Setting (admin portal, tenant settings) | Required state |
|---|---|
| Users can use the Power BI Model Context Protocol server endpoint (preview) | Enabled for the users of the gateway |
| Semantic Model Execute Queries REST API | Enabled |
| Allow XMLA endpoints and Analyze in Excel with on-premises semantic models | Enabled (read at least) |

Workspaces must be on **Premium Per User, Premium or Fabric capacity**; that is where XMLA and the
hosted MCP operate. Pro-only shared workspaces are not supported.

Users need a licence matching the workspace (PPU for PPU workspaces) and **Build** permission on
every semantic model they should be able to query. Build is granted per model (**Manage
permissions** on the model) or through a workspace role of Contributor or higher. The gateway lists
models by workspace access and Power BI enforces Build when a schema or query is requested.

## What the deploy script creates

| Resource | Role |
|---|---|
| Entra app registration (confidential client) | Signs users in for the OAuth proxy; exchanges their token on-behalf-of for Power BI. Delegated permissions on the Power BI Service API: `Dataset.Read.All`, `Workspace.Read.All`, `MLModel.Execute.All`, `Report.Read.All`; admin consent granted once |
| Foundry resource + project + model deployment | DAX generation (`generate_dax`) |
| Container registry | Holds the deployment's image (engine + private skills); no admin user, the app pulls with its identity (AcrPull) |
| Storage account + table | The OAuth proxy's state: registered clients and encrypted tokens, so a redeploy does not sign users out; the app identity has Storage Table Data Contributor, shared keys are disabled |
| Log Analytics + Container Apps environment + container app | Runs the gateway; system-assigned identity with **Cognitive Services OpenAI User** on the Foundry resource |

The client secret lives only in the container app's secrets (and, with `-WriteLocalEnv`, in a local
git-ignored `.env`). Rotate it with `-RotateSecret`; the script issues a new 2-year secret and updates
the app. Delete superseded credentials on the app registration afterwards.

## Controlling who can use the gateway

- **Coarse:** on the enterprise application, set **Assignment required** and assign users or
  groups; everyone else cannot sign in.
- **Fine:** Power BI permissions. A user without Build on a model gets an access error from every
  tool that touches it; a user without workspace access does not see the model at all.
- Revoke by removing the assignment or the Build permission; existing gateway sessions expire with
  their refresh tokens (hours), immediately after a redeploy.

## Memories

Users can attach short notes to a semantic model through the assistant ("remember that the
[Shipment Count] measure excludes cancelled orders"): a correction they made, a trap, the measure
that answers a recurring question. A memory has no access list of its own. It belongs to one model,
and the gateway re-checks with the user's own token, before every read or write, that Power BI lets
that user read the model (the same schema call the other tools depend on). Someone without Build
permission never sees a model's memories, and revoking the permission hides them within the schema
cache window (`PBIMCP_SCHEMA_CACHE_SECONDS`, 10 minutes by default). Everyone who can open the
model sees the same notes; only the person who wrote one can delete it through the assistant.

Memories are stored in the clear, so you can curate them: with `PBIMCP_STATE_STORAGE_ACCOUNT` set
they are the rows of table `mcpmemories` (`PBIMCP_MEMORY_TABLE_NAME`) in the deployment's storage
account, one row per model with the model id as `RowKey` and the notes as JSON in `Value`; open the
table in Storage Explorer to read, edit or delete them. Without a storage account they are files
`memories/<model id>.json` under `PBIMCP_MEMORY_DIR` (`/data/memories` in the image, wiped by every
redeploy like the rest of the replica's disk). `PBIMCP_MEMORY_MAX_PER_MODEL` (50) and
`PBIMCP_MEMORY_MAX_CHARS` (500) bound what one model can hold; they also keep one model's row under
the 64 KB Azure Tables property limit, so raise them with care. The notes are folded into the model
notes that ground `generate_dax`, which is what makes a remembered correction stick.

## Monitoring

- `https://<host>/healthz` returns `{"status":"ok", "recipes":[...], "curated_models": n}`.
- One log line per tool call, on logger `powerbi_mcp.tools`:
  `tool=generate_dax user=<oid> model_id=<id> ms=1830 outcome=ok` (WARNING with `outcome=error`
  and the message when the tool failed). Never the question, the DAX or result rows.
- Caches: a user's model list for 5 minutes, a model's schema per user for 10 minutes
  (`PBIMCP_CATALOG_CACHE_SECONDS`, `PBIMCP_SCHEMA_CACHE_SECONDS`). A new measure is invisible to
  `generate_dax` until the schema cache expires or the app restarts.
- Container logs in Log Analytics (`ContainerAppConsoleLogs_CL`) or `az containerapp logs show`.
- Foundry usage and cost in the Foundry resource's metrics; a DAX generation is roughly 10 to 20k
  input tokens (schema plus glossary) and a few hundred output tokens.

## Cost (indicative, one deployment)

| Item | Order of magnitude |
|---|---|
| Container app, 0.5 vCPU / 1 GiB, one replica always on | EUR 20 to 30 per month |
| Container registry (Basic) | EUR 5 per month |
| Log Analytics | usually under EUR 5 per month at this log volume |
| Foundry model | pay per token; a DAX generation is roughly 10 to 20k input tokens, so a few euro per hundred questions on gpt-5, less on a mini-class deployment (`ModelName` in the profile) |

Power BI licences are the existing per-user licences; the gateway adds none.

## Upgrading

Deployments built with `-EngineImage` upgrade by changing the tag in the deployment profile and
redeploying (see [Private skills and deployments](private-skills.md)). Deployments built from source
rebuild with `-SkipFoundry`. A redeploy restarts the app; because the OAuth proxy's state lives in the
deployment's storage account, connected clients stay signed in. (Deployments created before that
storage account existed keep the state on the replica's disk until they are redeployed once with the
current script.)

## Backups

Keep the skills folder and the deployment profile in a private repository. The memories users
wrote are the other state worth keeping: export the `mcpmemories` table now and then (Storage
Explorer, or `az storage entity query`), or enable soft delete on the storage account. Everything
else is recreated by the script.
