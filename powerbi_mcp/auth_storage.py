"""Encrypted OAuth state in a configurable directory, suitable for a persistent single-replica mount."""
import base64
import hashlib
from cryptography.fernet import Fernet
from key_value.aio.stores.filetree import FileTreeStore
from key_value.aio.wrappers.encryption import FernetEncryptionWrapper
from .config import Settings


def oauth_storage(settings: Settings):
    if settings.oauth_storage_dir is None:
        return None
    # Separate deployments and key generations. Routine client-secret rotation preserves state;
    # explicit signing-key rotation creates a new namespace and invalidates existing sessions.
    identity = f"{settings.tenant_id}:{settings.client_id}:{settings.jwt_signing_key}"
    key = hashlib.pbkdf2_hmac("sha256", identity.encode(), b"pbimcp-oauth-storage-v1", 600_000)
    directory = settings.oauth_storage_dir / hashlib.sha256(key).hexdigest()[:16]
    directory.mkdir(parents=True, exist_ok=True)
    return FernetEncryptionWrapper(key_value=FileTreeStore(data_directory=directory),
                                   fernet=Fernet(base64.urlsafe_b64encode(key)), raise_on_decryption_error=False)
