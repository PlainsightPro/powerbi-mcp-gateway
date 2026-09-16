# Changelog

What a deployment gets by moving its `EngineImage` tag. Versions follow [SemVer](https://semver.org);
a breaking change to the tool contract, the skills folder format or the deploy profile bumps the
major version.

## Unreleased

### Tools

- `remember`, `recall`, `forget`: notes users attach to one semantic model (a correction, a trap,
  the measure for a recurring question). A memory follows the model: the gateway re-checks with
  the user's own token that Power BI lets them read the model before every read or write, so
  people without Build permission never see it. Memories are folded into the schema notes and
  into `generate_dax` grounding. Stored in table `mcpmemories` of the deployment's storage
  account (in the clear, for curation), or as JSON files under `PBIMCP_MEMORY_DIR` without one.

### Operations

- The OAuth proxy's state (registered clients, encrypted tokens) can live in an Azure Table
  (`PBIMCP_STATE_STORAGE_ACCOUNT`); the deploy script creates the storage account and grants the
  app identity. A redeploy no longer signs every user out.
- The container app pulls its image with its managed identity; the registry has no admin user.
- One revision per deploy: `PBIMCP_BASE_URL` is known before the app is created.
- One log line per tool call (tool, user id, model id, duration, outcome, error message); never
  the question, the query text or result rows.
- `python -m powerbi_mcp --check-skills [DIR]` validates a skills folder; the deploy script and CI
  run it.
- The image installs from `uv.lock` and runs as a non-root user; releases check that the tag
  matches the package version and create a GitHub Release.

### Behaviour

- Upstream HTTP failures are readable: 403 maps to the access message, 429 carries the wait,
  anything else names the status. A throttled Fabric listing raises instead of caching a partial
  model list.
- `max_rows` is capped by `PBIMCP_MAX_ROWS_LIMIT` (10,000). `PBIMCP_FOUNDRY_MAX_OUTPUT_TOKENS`
  sets the generation budget; a cut-off answer is reported as such instead of as a DAX error.
- Recipe names are lower-cased file stems; `skill://recipes/{name}` matches like `get_recipe`
  and raises on an unknown name. Catalog entries with unknown keys are rejected at startup.
- Multi-query `execute_dax` calls return one table per query (the hosted server only returned
  the first). The generator adds `+ 0` to numeric output columns and bounds relative periods by
  today, because the hosted server formats bare measures as text and models may hold future rows.
- `generate_dax` results include `model_id`.

### Deploy profile

- New keys: `StorageName`, `StateTableName`, `ReasoningEffort`. `ImageTag` and `OwnerTag` were
  always accepted and are now in the example.

## 0.1.0

First release: OAuth proxy with dynamic client registration, per-user on-behalf-of access to
Microsoft's hosted Power BI MCP, curated catalog, skills folder (instructions, glossary, DAX rules,
recipes), Foundry-backed DAX generation with one repair round, Azure Container Apps deployment
script with deployment profiles and engine images.
