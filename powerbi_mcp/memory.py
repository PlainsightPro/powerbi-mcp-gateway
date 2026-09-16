"""Memories: short notes people attach to one semantic model, kept by the gateway and shown only
to those who can open that model.

A memory carries no access list of its own. Its model id is the whole authorisation: before the
gateway returns, adds or deletes a memory it asks Power BI, with the signed-in user's own token,
whether that user can read the model (`Gateway.require_access`). Access revoked in Power BI is
access revoked to the memories, and the gateway never keeps a list of who may see what.

Storage is one record per model in the same kind of key-value store the OAuth proxy uses: a folder
of JSON files locally, an Azure Table in the deployment's storage account once
PBIMCP_STATE_STORAGE_ACCOUNT is set. Memories are business notes, not credentials, so unlike the
proxy state they are stored in the clear and an administrator can read or curate them in Storage
Explorer. The per-model caps keep a record under Azure Tables' 64 KB property limit and bound the
text that flows into DAX generation.
"""

from __future__ import annotations

import asyncio
import re
import secrets
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any

from key_value.aio.protocols import AsyncKeyValue
from key_value.aio.stores.base import BaseContextManagerStore

from .config import Settings

COLLECTION = "memories"
MODEL_ID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
MEMORY_ID_BYTES = 6


class MemoryInputError(ValueError):
    """Input the store refuses: a malformed id, empty or oversized text, a full model, a memory
    that belongs to someone else. The message is written for the assistant and its user."""


def normalize_model_id(model_id: str) -> str:
    """Semantic model ids are GUIDs; anything else cannot have memories and never touches storage."""
    value = model_id.strip().lower()
    if not MODEL_ID.match(value):
        raise MemoryInputError(f"'{model_id}' is not a semantic model id; take the id from list_semantic_models.")
    return value


@dataclass(frozen=True)
class Memory:
    id: str
    text: str
    author: str  # the writer's user key (Entra object id): never returned, decides `mine` and who may forget
    created_at: str  # ISO 8601, UTC

    def as_row(self, viewer: str) -> dict[str, Any]:
        return {"id": self.id, "text": self.text, "created_at": self.created_at, "mine": self.author == viewer}


class Memories:
    """The memories of every model, one record per model id in `kv` (collection `memories`)."""

    def __init__(self, kv: AsyncKeyValue, *, max_per_model: int = 50, max_chars: int = 500) -> None:
        self._kv = kv
        self._max_per_model = max_per_model
        self._max_chars = max_chars
        # Adding and forgetting are read-modify-write of one record; the lock keeps two tool calls
        # in this process from losing each other's change (the deployment runs one replica).
        self._write_lock = asyncio.Lock()

    async def aclose(self) -> None:
        if isinstance(self._kv, BaseContextManagerStore):
            await self._kv.close()

    async def list(self, model_id: str) -> list[Memory]:
        record = await self._kv.get(key=normalize_model_id(model_id), collection=COLLECTION)
        return [Memory(**item) for item in (record or {}).get("memories", [])]

    async def add(self, model_id: str, text: str, author: str) -> Memory:
        model_id = normalize_model_id(model_id)
        text = text.strip()
        if not text:
            raise MemoryInputError("A memory needs some text.")
        if len(text) > self._max_chars:
            raise MemoryInputError(
                f"Keep a memory under {self._max_chars} characters; this one has {len(text)}. Shorten it or split it."
            )
        async with self._write_lock:
            memories = await self.list(model_id)
            if len(memories) >= self._max_per_model:
                raise MemoryInputError(
                    f"This model already holds {self._max_per_model} memories, the maximum; forget one first."
                )
            taken = {m.id for m in memories}
            memory_id = secrets.token_hex(MEMORY_ID_BYTES)
            while memory_id in taken:
                memory_id = secrets.token_hex(MEMORY_ID_BYTES)
            memory = Memory(
                id=memory_id,
                text=text,
                author=author,
                created_at=datetime.now(UTC).isoformat(timespec="seconds"),
            )
            await self._save(model_id, [*memories, memory])
        return memory

    async def remove(self, model_id: str, memory_id: str, author: str) -> Memory:
        model_id = normalize_model_id(model_id)
        memory_id = memory_id.strip().lower()
        async with self._write_lock:
            memories = await self.list(model_id)
            match = next((m for m in memories if m.id == memory_id), None)
            if match is None:
                raise MemoryInputError(f"No memory '{memory_id}' on this model; ids come from recall.")
            if match.author != author:
                raise MemoryInputError("Only the person who wrote a memory can forget it.")
            await self._save(model_id, [m for m in memories if m.id != memory_id])
        return match

    async def _save(self, model_id: str, memories: list[Memory]) -> None:
        if memories:
            await self._kv.put(key=model_id, value={"memories": [asdict(m) for m in memories]}, collection=COLLECTION)
        else:
            await self._kv.delete(key=model_id, collection=COLLECTION)


def build_memories(settings: Settings, credential: Any | None = None) -> Memories:
    """An Azure Table next to the OAuth state when a storage account is configured, else JSON
    files under `memory_dir` (one `memories/<model id>.json` per model)."""
    kv: AsyncKeyValue
    if settings.state_storage_account:
        from key_value.aio.stores.azure_tables import AzureTablesStore

        if credential is None:
            from azure.identity.aio import DefaultAzureCredential

            credential = DefaultAzureCredential(process_timeout=60)
        kv = AzureTablesStore(
            account_name=settings.state_storage_account,
            credential=credential,
            table_name=settings.memory_table_name,
        )
    else:
        from key_value.aio.stores.filetree import FileTreeStore

        kv = FileTreeStore(data_directory=settings.memory_dir)
    return Memories(kv, max_per_model=settings.memory_max_per_model, max_chars=settings.memory_max_chars)
