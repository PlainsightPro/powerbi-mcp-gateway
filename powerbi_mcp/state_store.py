"""Where the OAuth proxy keeps its state: registered clients, upstream tokens, refresh tokens.

FastMCP's default is an encrypted file store on the replica's own disk, which Azure Container Apps
wipes on every revision: after each deploy every user signs in again. With
PBIMCP_STATE_STORAGE_ACCOUNT set, the same state lives in one Azure Table, reached with the app's
managed identity, encrypted at rest with a key derived from PBIMCP_JWT_SIGNING_KEY exactly the way
FastMCP derives its own. Deploys, restarts and (later) a second replica then share one store.
"""

from __future__ import annotations

from typing import Any

from cryptography.fernet import Fernet
from fastmcp.server.auth.jwt_issuer import derive_jwt_key
from key_value.aio.protocols import AsyncKeyValue
from key_value.aio.wrappers.encryption import FernetEncryptionWrapper

from .config import Settings

STORAGE_KEY_SALT = "fastmcp-storage-encryption-key"  # FastMCP's own salt: same key material, same ciphertext format


def encrypted(store: AsyncKeyValue, jwt_signing_key: str) -> AsyncKeyValue:
    """Wrap any key-value store so that tokens are never stored in the clear.

    A record written under a previous signing key is treated as a cache miss (the client simply
    registers again), never as a hard failure.
    """
    fernet_key = derive_jwt_key(high_entropy_material=jwt_signing_key, salt=STORAGE_KEY_SALT)
    return FernetEncryptionWrapper(key_value=store, fernet=Fernet(key=fernet_key), raise_on_decryption_error=False)


def build_client_storage(settings: Settings, credential: Any | None = None) -> AsyncKeyValue | None:
    """The proxy's `client_storage`, or None to keep FastMCP's on-disk default."""
    if not settings.state_storage_account:
        return None
    if not settings.jwt_signing_key:
        raise ValueError("PBIMCP_STATE_STORAGE_ACCOUNT needs PBIMCP_JWT_SIGNING_KEY: it derives the encryption key")
    from key_value.aio.stores.azure_tables import AzureTablesSanitizationStrategy, AzureTablesStore

    if credential is None:
        from azure.identity.aio import DefaultAzureCredential

        credential = DefaultAzureCredential(process_timeout=60)
    store = AzureTablesStore(
        account_name=settings.state_storage_account,
        credential=credential,
        table_name=settings.state_table_name,
        # Keys are opaque token strings and client ids; PartitionKey/RowKey forbid some of their characters.
        collection_sanitization_strategy=AzureTablesSanitizationStrategy(),
        key_sanitization_strategy=AzureTablesSanitizationStrategy(),
    )
    return encrypted(store, settings.jwt_signing_key)
