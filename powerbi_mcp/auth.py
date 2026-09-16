"""Who may call the gateway.

Two kinds of caller hold a bearer token for `/mcp`:

- MCP clients (Claude, VS Code, Cursor, ...) that registered dynamically and signed the user in
  through the gateway's OAuth proxy. FastMCP's `AzureProvider` issues them its own token and swaps
  it for the user's Entra token on every call.
- First-party applications that sign their users in with Entra themselves and already hold an
  access token for this API (audience `api://<client id>`, scope `access_as_user`). A second
  browser sign-in through the proxy makes no sense for them, so they send that token as is.

`GatewayAuth` accepts both. A first-party token is validated against the tenant's keys with the
same issuer, audience and scope rules as a proxied one, must come from an application listed in
`PBIMCP_TRUSTED_CLIENT_IDS`, and must be delegated (carry `scp`, not only application `roles`).
It then serves as the on-behalf-of assertion for Power BI exactly like a proxied token, so every
query still runs as the signed-in user.
"""

from __future__ import annotations

import logging

from fastmcp.server.auth.providers.azure import AzureProvider
from mcp.server.auth.provider import AccessToken

logger = logging.getLogger(__name__)


class GatewayAuth(AzureProvider):
    """AzureProvider that also accepts Entra tokens issued for this API to trusted applications."""

    def __init__(self, *, trusted_client_ids: frozenset[str] = frozenset(), **kwargs) -> None:
        super().__init__(**kwargs)
        self.trusted_client_ids = frozenset(client.lower() for client in trusted_client_ids)

    async def load_access_token(self, token: str) -> AccessToken | None:
        proxied = await super().load_access_token(token)  # None for anything the proxy did not issue
        if proxied is not None or not self.trusted_client_ids:
            return proxied
        return await self.load_first_party_token(token)

    async def load_first_party_token(self, token: str) -> AccessToken | None:
        """A token Entra issued for this API to a trusted application on behalf of a user, or None."""
        # The proxy's own verifier: tenant JWKS, v2 issuer, audience [client id, api://client id], scope.
        access = await self._token_validator.verify_token(token)
        if access is None:
            return None
        claims = access.claims or {}
        application = str(claims.get("azp") or "").lower()
        if application not in self.trusted_client_ids:
            logger.info("Rejected a first-party token from application %s: not trusted", application or "<none>")
            return None
        if not claims.get("scp"):
            # Application tokens carry roles instead of scp; without a user there is nothing to run as.
            logger.info("Rejected an application-only token from %s", application)
            return None
        return access
