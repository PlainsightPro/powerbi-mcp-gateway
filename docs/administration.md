# Administration

## Tenant and model access

Enable the Power BI MCP endpoint for the intended users in the Fabric tenant settings. Models
must be in a workspace supported by the hosted endpoint (Premium Per User, Premium or Fabric
capacity), with XMLA access enabled. Users need the corresponding licence and Build permission.
See Microsoft's [hosted MCP documentation](https://learn.microsoft.com/en-us/power-bi/developer/mcp/remote-mcp-server-tools)
for current prerequisites; the upstream endpoint is a preview service.

Workspace discovery and query access are separate. A model shared directly can be missing from
workspace discovery; `search_semantic_models(verify_access=true)` checks matching curated
candidates, or the user can supply its id. Every schema and query uses that user's OBO token.

## Deployment modes

Install Python 3.11+ dependencies and PowerShell 7, then validate locally:

```powershell
.\deploy\deploy_to_azure.ps1 -Profile C:\deployments\contoso\deploy\profile.json -ValidateOnly
```

This reads the profile, checks timezone and skills, and exits before resolving Azure CLI.
The deployment uses the signed-in Azure CLI subscription. Explicit parameters override profile
values; unknown profile keys fail. Use separate resource names and Entra app names per deployment.

| Mode or parameter | Behavior |
|---|---|
| Default | Provision Entra application, Foundry model, registry, Container Apps and Azure Files |
| `-FoundryEndpoint https://... -ModelName <deployment-name>` | Use an existing generation deployment; skip Foundry provisioning |
| `-FoundryResourceId <Azure-resource-id>` | With an existing endpoint, assign the app's identity the OpenAI User role on that resource |
| `-DisableGeneration` | No Foundry resource or generation calls; analysis returns context for the client to write DAX |
| `-SkipFoundry` | Keep an already-provisioned managed Foundry resource; does not disable generation |
| `-SkipBuild` | Keep the existing container image; skills changes require a build |
| `-EngineImage <pinned-tag-or-digest>` | Layer selected skills onto a published engine; empty builds this checkout |
| `-TimeZone Europe/Brussels` | Resolve relative dates in an IANA timezone |
| `-RotateSecret` | Issue a new Entra client secret and update the container app |
| `-ResetSessions` | Rotate the gateway signing key and invalidate existing sessions |
| `-EphemeralOAuthStorage` | Opt out of persistent OAuth state; clients may need to reconnect on redeploy |

Existing Foundry endpoints use managed identity; without a resource id, grant access to the app's
identity yourself. The application also supports `PBIMCP_FOUNDRY_API_KEY` as a runtime alternative,
but the deploy script does not store such a key. With managed Foundry provisioning, changes to
model name, version and capacity are reconciled rather than silently ignored.

Deployment prints a liveness result. A real browser OAuth check remains necessary:
`python scripts/check_gateway.py https://<host>/mcp --model-id <id> --generate`.
Generation checks incur a small billable request. A headless OBO check does not prove client OAuth works.

## Persistent sign-ins and migration

Default deployment creates or reuses an Azure Files share and mounts it at `/mnt/gateway-oauth`.
A stable storage account name is derived from subscription, resource group and app unless
`OAuthStorageAccountName` is specified. The share name defaults to `gateway-oauth`.
Storage adds Azure usage cost; use your subscription's cost estimates rather than a fixed estimate.

The gateway encrypts registered clients and upstream token records using key material derived
from the tenant id, client id and stable `PBIMCP_JWT_SIGNING_KEY`. It namespaces different key
generations separately. Preserve the share and signing key together across restarts and deployments.
Normal Entra client-secret rotation keeps this storage namespace; signing-key rotation intentionally
starts a new namespace and requires sign-in again. Entra can independently expire or revoke tokens.

An existing ephemeral deployment needs one fresh registration/sign-in when migrated; its temporary
records are not imported. An engine version predating this feature does not use the new storage
setting. Use a source build for unreleased changes or a release known to contain the feature.

Keep **one replica**. Persistence has been tested through store recreation locally; this does not
establish safe multi-replica concurrency. Both initial and update deployments enforce one replica.
The Azure Files mounting path still needs verification in your Azure environment, including network
access from the Container Apps environment.

For local use, configure `PBIMCP_OAUTH_STORAGE_DIR` to a private local directory and retain a signing
key of at least 32 random characters. `-WriteLocalEnv` preserves the local key unless sessions are
explicitly reset, and writes a git-ignored `.oauth-state` path.

## Access and revocation

Require assignment on the enterprise application when only selected groups may sign in.
Manage model access and RLS in Power BI. Schemas and discovery are cached briefly per user;
query execution always calls Power BI with the user's current token. Use explicit refresh after
permission changes. Redeployment with persistent state is not a session revocation mechanism.
Use the identity provider's revocation controls or `-ResetSessions` when needed.

Skills are available deployment-wide. Separate deployments for groups whose business knowledge
must remain separate, even if their data permissions differ.

## Monitoring and backup

`/healthz` returns only `{"status":"ok"}`; it is public liveness, not an access or generation check.
The welcome page also contains no private model names. Authenticated `diagnose_connection`
reports discovery, schema, query and optional generation stages without business rows.

Analysis logs include request id, model id, status, elapsed time and attempt count. Restrict
operational logs because lower-level framework error messages can include query details.
Monitor Container Apps health, Azure Files availability and Foundry usage.

Keep the private skills folder and profile in a private repository. Protect and back up OAuth
storage and its signing key separately as sensitive state. Rotate storage-account keys through
the environment storage configuration when required. Do not put tokens or signing keys in Git.
After an intentional signing-key reset, old encrypted namespaces remain on the share until removed
through your normal retention process.
