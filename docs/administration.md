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
| Container registry | Holds the deployment's image (engine + private skills) |
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

## Monitoring

- `https://<host>/healthz` returns `{"status":"ok", "recipes":[...], "curated_models": n}`.
- Container logs in Log Analytics (`ContainerAppConsoleLogs_CL`) or `az containerapp logs show`.
- Foundry usage and cost in the Foundry resource's metrics; a DAX generation is roughly 10 to 20k
  input tokens (schema plus glossary) and a few hundred output tokens.

## Cost (indicative, one deployment)

| Item | Order of magnitude |
|---|---|
| Container app, 0.5 vCPU / 1 GiB, one replica always on | EUR 20 to 30 per month |
| Container registry (Basic) | EUR 5 per month |
| Log Analytics | usually under EUR 5 per month at this log volume |
| Foundry model | pay per token; a few euro per thousand questions with a gpt-5-mini class model |

Power BI licences are the existing per-user licences; the gateway adds none.

## Upgrading

Deployments built with `-EngineImage` upgrade by changing the tag in the deployment profile and
redeploying (see [Private skills and deployments](private-skills.md)). Deployments built from source
rebuild with `-SkipFoundry`. A redeploy restarts the app: connected clients sign in again, because
the OAuth proxy keeps client registrations on the replica's disk (single-replica design).

## Backups

The only state worth keeping is the skills folder and the deployment profile; keep them in a
private repository. Everything else is recreated by the script.
