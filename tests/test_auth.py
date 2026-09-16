"""First-party Entra tokens: accepted only from listed applications, only when delegated to a user."""

import pytest
from fastmcp.server.auth.providers.jwt import JWTVerifier, RSAKeyPair

from powerbi_mcp.auth import GatewayAuth
from powerbi_mcp.config import load_settings
from powerbi_mcp.server import build_server

TENANT = "00000000-0000-0000-0000-000000000001"
API = "00000000-0000-0000-0000-000000000002"
CHAT_APP = "00000000-0000-0000-0000-000000000003"
OTHER_APP = "00000000-0000-0000-0000-000000000004"
USER = "00000000-0000-0000-0000-00000000aaaa"
ISSUER = f"https://login.microsoftonline.com/{TENANT}/v2.0"


@pytest.fixture(scope="module")
def keys() -> RSAKeyPair:
    return RSAKeyPair.generate()


def provider(keys: RSAKeyPair, trusted: frozenset[str]) -> GatewayAuth:
    auth = GatewayAuth(
        trusted_client_ids=trusted,
        client_id=API,
        client_secret="not-a-real-secret",
        tenant_id=TENANT,
        base_url="http://localhost:8000",
        required_scopes=["access_as_user"],
        jwt_signing_key="0123456789abcdef0123456789abcdef",
        forward_resource=False,
    )
    # Verify against a local key instead of Entra's JWKS; issuer, audience and scope stay as configured.
    auth._token_validator = JWTVerifier(
        public_key=keys.public_key, issuer=ISSUER, audience=[API, f"api://{API}"], required_scopes=["access_as_user"]
    )
    return auth


def entra_token(keys: RSAKeyPair, **overrides) -> str:
    """A v2 access token as Entra issues it for this API; override a claim, or drop it with None."""
    claims = {"aud": f"api://{API}", "azp": CHAT_APP, "scp": "access_as_user", "oid": USER, "tid": TENANT}
    claims.update(overrides)
    issuer = claims.pop("iss", ISSUER)
    return keys.create_token(subject=USER, issuer=issuer, additional_claims={k: v for k, v in claims.items() if v})


async def test_delegated_token_from_a_trusted_application_is_accepted(keys):
    token = entra_token(keys)
    access = await provider(keys, frozenset({CHAT_APP.upper()})).load_access_token(token)
    assert access is not None
    assert access.token == token  # the raw Entra token becomes the on-behalf-of assertion
    assert access.claims and access.claims["oid"] == USER  # what the tool log and memories key on
    assert access.client_id == CHAT_APP and "access_as_user" in access.scopes


@pytest.mark.parametrize(
    "overrides",
    [
        {"azp": OTHER_APP},  # an application the deployment never listed
        {"scp": None, "roles": ["Data.Read"]},  # application token: no user to run as
        {"aud": f"api://{OTHER_APP}"},  # issued for another API
        {"iss": f"https://login.microsoftonline.com/{OTHER_APP}/v2.0"},  # another tenant
        {"scp": "profile"},  # missing the gateway scope
    ],
)
async def test_other_tokens_are_rejected(keys, overrides):
    assert await provider(keys, frozenset({CHAT_APP})).load_access_token(entra_token(keys, **overrides)) is None


async def test_first_party_tokens_are_off_until_an_application_is_trusted(keys):
    assert await provider(keys, frozenset()).load_access_token(entra_token(keys)) is None


async def test_garbage_is_rejected_without_raising(keys):
    assert await provider(keys, frozenset({CHAT_APP})).load_access_token("not.a.jwt") is None


def test_settings_parse_and_validate_trusted_client_ids(dummy_env, monkeypatch):
    monkeypatch.setenv("PBIMCP_TRUSTED_CLIENT_IDS", f" {CHAT_APP.upper()}, {OTHER_APP} ,")
    assert load_settings().trusted_client_id_set == frozenset({CHAT_APP, OTHER_APP})
    monkeypatch.setenv("PBIMCP_TRUSTED_CLIENT_IDS", "genai-chat")
    with pytest.raises(ValueError, match="application"):
        load_settings()


def test_server_uses_the_gateway_provider_with_the_configured_applications(dummy_env, monkeypatch):
    monkeypatch.setenv("PBIMCP_TRUSTED_CLIENT_IDS", CHAT_APP)
    auth = build_server(load_settings()).auth
    assert isinstance(auth, GatewayAuth) and auth.trusted_client_ids == frozenset({CHAT_APP})
