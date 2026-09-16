"""The OAuth proxy's state store: encrypted, derived from the signing key, pluggable into the server."""

from __future__ import annotations

import pytest
from key_value.aio.stores.memory import MemoryStore

from powerbi_mcp.config import load_settings
from powerbi_mcp.state_store import build_client_storage, encrypted


async def test_encrypted_store_round_trips_and_hides_plaintext():
    backing = MemoryStore()
    store = encrypted(backing, "0123456789abcdef0123456789abcdef")
    await store.put(key="client-1", value={"secret": "refresh-token-xyz"}, collection="mcp-oauth-proxy-clients")
    assert await store.get(key="client-1", collection="mcp-oauth-proxy-clients") == {"secret": "refresh-token-xyz"}
    raw = await backing.get(key="client-1", collection="mcp-oauth-proxy-clients")
    assert raw is not None and "refresh-token-xyz" not in str(raw), "the backing store only ever sees ciphertext"


async def test_a_record_under_another_key_is_a_miss_not_a_crash():
    backing = MemoryStore()
    await encrypted(backing, "key-one-0123456789abcdef01234567").put(key="k", value={"v": 1}, collection="c")
    assert await encrypted(backing, "key-two-0123456789abcdef01234567").get(key="k", collection="c") is None


def test_no_storage_account_keeps_the_default(dummy_env):
    assert build_client_storage(load_settings()) is None


def test_storage_account_requires_a_signing_key(dummy_env, monkeypatch):
    monkeypatch.setenv("PBIMCP_STATE_STORAGE_ACCOUNT", "stpbimcp")
    monkeypatch.delenv("PBIMCP_JWT_SIGNING_KEY")
    with pytest.raises(ValueError, match="JWT_SIGNING_KEY"):
        build_client_storage(load_settings())


@pytest.mark.filterwarnings("ignore:A configured store is unstable:UserWarning")
def test_storage_account_builds_an_encrypted_azure_table_store(dummy_env, monkeypatch):
    from key_value.aio.stores.azure_tables import AzureTablesStore
    from key_value.aio.wrappers.encryption import FernetEncryptionWrapper

    monkeypatch.setenv("PBIMCP_STATE_STORAGE_ACCOUNT", "stpbimcp")
    monkeypatch.setenv("PBIMCP_STATE_TABLE_NAME", "oauthstate")
    store = build_client_storage(load_settings(), credential=object())  # no network: the client is lazy
    assert isinstance(store, FernetEncryptionWrapper)
    assert isinstance(store.key_value, AzureTablesStore)


async def test_server_accepts_an_injected_store(dummy_env):
    from fastmcp import Client

    from powerbi_mcp.server import build_server

    server = build_server(load_settings(), client_storage=encrypted(MemoryStore(), "k" * 32))
    async with Client(server) as client:
        assert await client.list_tools()
