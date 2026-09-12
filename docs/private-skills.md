# Private skills and deployments: one codebase, many deployments

The public repository is the **engine**: code, deploy script, example skills for a fictional
company, tests, docs. Everything that is specific to an organisation lives outside it, in a
**deployment folder** (a private repository per organisation, or one repository with a folder per
deployment):

```
contoso-powerbi-gateway/            private
|-- skills/
|   |-- instructions.md
|   |-- glossary.md
|   |-- dax-rules.md
|   |-- catalog.yaml
|   `-- recipes/
|       `-- *.md
`-- deploy/
    `-- profile.json                parameters of deploy_to_azure.ps1, SkillsDir relative to this file
```

Deploying is one command from a checkout of the public repository:

```powershell
git clone https://github.com/PlainsightPro/powerbi-mcp-gateway
.\powerbi-mcp-gateway\deploy\deploy_to_azure.ps1 -Profile C:\deployments\contoso\deploy\profile.json
```

The script stages a temporary build folder with **only** the runtime code and the selected skills
(`deploy/build_context.ps1`), so no `.env`, Git history or private notes ever reach the image; the
image itself is pushed to the deployment's own registry. See `deploy/profiles/example.json` for
every key.

## Tracking the engine without a fork

Two ways to combine engine and skills:

| Mode | How | Use when |
|---|---|---|
| **Engine image** (`EngineImage` in the profile) | The build is a two-line Dockerfile: `FROM ghcr.io/plainsightpro/powerbi-mcp-gateway:<tag>` plus `COPY skills/`. No Python is built; the deployment pins a released engine version | Customers and any deployment that should only move when you decide |
| **Source build** (no `EngineImage`) | The checkout's code is built together with the skills | Development, or a deployment that needs an unreleased change |

Releases: pushing a tag `vX.Y.Z` to the public repository runs the tests and publishes
`ghcr.io/plainsightpro/powerbi-mcp-gateway:X.Y.Z` (and `:latest`). The package must be public, or
the deploying registry needs pull credentials for GHCR. Upgrading a deployment is changing the tag
in its profile and running the deploy script again; rolling back is the reverse. Skills change
independently: edit, redeploy with the same tag.

This gives one place to develop (the public repository, with the example skills and the test suite)
and as many deployments as you have organisations, each with its own Entra app, registry, container
app, Foundry resource and skills, each upgraded on its own schedule.

## What stays private

- Everything in `skills/`: it names your models, measures, entities and the questions your
  business asks. The catalog contains semantic model ids, which are not secret but identify your
  tenant.
- `deploy/profile.json`: resource names and the tenant's conventions.
- `.env` and the Entra client secret: never in any repository.

Skills are deployment-wide: every signed-in user of a deployment gets the same glossary and
recipes, while the data stays per user through Power BI's permissions. If two groups must not see
each other's business knowledge, give them separate deployments.

## Developing on the engine while deployments run

- Work in the public repository with the example skills; the tests do not depend on any real
  model. `PBIMCP_SKILLS_DIR` in your local `.env` can point at a private skills folder to run the
  server locally against your own models.
- Cut a release when the change is ready; deployments pick it up when their profile moves to the
  new tag.
- Keep the example skills fictional and free of customer terms; they are the template new
  deployments start from and the fixture the tests run on.
