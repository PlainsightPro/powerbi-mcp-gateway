# Security

## Reporting a vulnerability

Email security@plainsight.pro with the details and, if you have one, a way to reproduce. Please do
not open a public issue for a vulnerability. You will get an acknowledgement within three working
days and a fix or a mitigation plan within thirty.

## What the gateway does and does not hold

- Every Power BI call carries the signed-in user's token, exchanged on-behalf-of per call. There
  is no service identity with data access, so Power BI's permissions and row-level security apply.
- The OAuth proxy stores client registrations, upstream tokens and refresh tokens encrypted
  (Fernet, key derived from `PBIMCP_JWT_SIGNING_KEY`), on disk or in the deployment's Azure Table.
- Logs carry the tool name, the user's object id, model ids, durations and error messages; never
  questions, DAX, result rows or tokens.
- The Entra client secret lives in the container app's secrets (and, with `-WriteLocalEnv`, in a
  git-ignored `.env`). Rotate it with `-RotateSecret`.
- Business knowledge (the skills folder) is deployment-wide, not filtered per user.

## Supported versions

The latest minor release receives fixes. Deployments pin an engine image tag and upgrade by
changing it; see `CHANGELOG.md`.
