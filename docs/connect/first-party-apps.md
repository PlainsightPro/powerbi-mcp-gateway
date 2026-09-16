# Connect a first-party application

The other guides cover MCP clients that sign the user in through the gateway's own OAuth proxy.
An application that already signs its users in with Microsoft Entra (an in-house chat product,
an internal portal, a Copilot-style assistant your team runs) does not need a second browser
sign-in: it can ask Entra for a token **for the gateway's API** and send that token as is. The
gateway checks it and uses it, on behalf of the same user, for every Power BI call, so Power BI
permissions and row-level security apply exactly as with any other client.

This is the mode a "talk to your data" agent in a chat application uses: the browser acquires the
token with the user's existing session, the application forwards it on the chat request, and the
application's backend connects to `/mcp` with that token for the duration of the turn.

## What the administrator does

1. Add the application's Entra **application (client) id** to the deployment profile and redeploy:

   ```json
   "PreauthorizedClientIds": ["<application client id>"]
   ```

   The deploy script pre-authorises that application on the gateway's `access_as_user` scope (no
   consent prompt for users) and sets `PBIMCP_TRUSTED_CLIENT_IDS` on the container app. The list is
   the complete set: an id removed from the profile loses both the pre-authorisation and the
   gateway's trust on the next deploy. If the smoke test relies on `-PreauthorizeAzureCli`, keep
   passing that switch as well.
2. Give the application the gateway's client id (`PBIMCP_CLIENT_ID`; the deploy script prints it)
   so it can request the scope `api://<gateway client id>/access_as_user`.

Without any trusted application the gateway accepts proxy sign-ins only; a first-party token is
refused with `401`.

## What the application does

- Acquire an access token for scope `api://<gateway client id>/access_as_user` with the user's
  delegated sign-in (MSAL `acquireTokenSilent`, or the equivalent in your stack). The token must
  be a **delegated** token: an application token (client credentials, `roles` but no `scp`) is
  rejected because there is no user to run as, and a shared identity would bypass Power BI's
  per-user permissions.
- Send it on every request to `https://<host>/mcp` as `Authorization: Bearer <token>`, using a
  standard Streamable HTTP MCP client. No registration, no client secret, no callback URL.
- Treat the token as a per-user, per-turn capability: keep it in memory for the request, never in
  configuration, conversation storage or logs.

The gateway validates the token's signature against the tenant's keys, the tenant issuer, the
audience (`api://<gateway client id>` or the bare client id), the scope, and that the token was
issued to one of the trusted applications (`azp` claim). The user's object id from the token is
what the tool-call log and shared model notes use, as for proxied sign-ins.

## Checking it

With the application signed in, a call to `list_semantic_models` returns the models that user can
open. In the gateway's container log the tool-call line shows the same user id as a proxied
sign-in would. A rejected token appears in the log as `Rejected a first-party token from
application <id>: not trusted` or `Rejected an application-only token`, without the token itself.
