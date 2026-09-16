"""Memories follow the semantic model: stored per model, readable and writable only by users Power
BI lets read that model (the schema call with their own token), and folded into the notes that
ground get_semantic_model_schema and generate_dax. Model ids and user names here are invented.
"""

from __future__ import annotations

import json

import httpx
import pytest
from fastmcp.exceptions import ToolError
from key_value.aio.stores.memory import MemoryStore as InMemoryStore

from powerbi_mcp.catalog import Catalog
from powerbi_mcp.config import load_settings
from powerbi_mcp.dax_generator import DaxGenerator
from powerbi_mcp.gateway import Gateway
from powerbi_mcp.memory import COLLECTION, Memories, MemoryInputError, build_memories
from powerbi_mcp.skills import Skills

MODEL = "0a1b2c3d-1111-2222-3333-444455556666"
OTHER_MODEL = "0a1b2c3d-9999-8888-7777-666655554444"
SCHEMA = {"schema": {"Tables": [{"Name": "Shipments", "Measures": [{"Name": "Shipment Count", "Type": "Int64"}]}]}}
DAX = 'EVALUATE ROW("n", [Shipment Count])'


# ------------------------------------------------------------------ the store


@pytest.fixture
def memories() -> Memories:
    return Memories(InMemoryStore(), max_per_model=3, max_chars=40)


async def test_add_list_forget_round_trip(memories):
    added = await memories.add(MODEL, "  Use [Shipment Count], not COUNTROWS  ", author="alice")
    assert added.text == "Use [Shipment Count], not COUNTROWS", "text is stripped"
    assert len(added.id) == 12 and added.created_at.endswith("+00:00")

    [listed] = await memories.list(MODEL.upper())
    assert listed == added, "model ids are case-insensitive"
    assert listed.as_row("alice") == {"id": added.id, "text": added.text, "created_at": added.created_at, "mine": True}
    assert listed.as_row("bob")["mine"] is False and "author" not in listed.as_row("bob")
    assert await memories.list(OTHER_MODEL) == [], "memories never leak across models"

    removed = await memories.remove(MODEL, added.id.upper(), author="alice")
    assert removed == added
    assert await memories.list(MODEL) == []


async def test_only_the_author_forgets_and_unknown_ids_are_readable_errors(memories):
    added = await memories.add(MODEL, "note", author="alice")
    with pytest.raises(MemoryInputError, match="person who wrote"):
        await memories.remove(MODEL, added.id, author="bob")
    with pytest.raises(MemoryInputError, match="No memory 'nope'"):
        await memories.remove(MODEL, "nope", author="alice")
    assert len(await memories.list(MODEL)) == 1


async def test_text_and_count_caps(memories):
    with pytest.raises(MemoryInputError, match="needs some text"):
        await memories.add(MODEL, "   ", author="alice")
    with pytest.raises(MemoryInputError, match="under 40 characters"):
        await memories.add(MODEL, "x" * 41, author="alice")
    for i in range(3):
        await memories.add(MODEL, f"note {i}", author="alice")
    with pytest.raises(MemoryInputError, match="already holds 3 memories"):
        await memories.add(MODEL, "one too many", author="alice")


async def test_a_model_id_that_is_not_a_guid_never_reaches_storage(memories):
    for bad in ("../../etc/passwd", "m", "", "0a1b2c3d-1111-2222-3333-44445555666g"):
        with pytest.raises(MemoryInputError, match="not a semantic model id"):
            await memories.list(bad)
        with pytest.raises(MemoryInputError, match="not a semantic model id"):
            await memories.add(bad, "note", author="alice")


async def test_file_store_keeps_one_json_file_per_model_and_round_trips_unicode(dummy_env, tmp_path, monkeypatch):
    monkeypatch.setenv("PBIMCP_MEMORY_DIR", str(tmp_path / "mem"))
    memories = build_memories(load_settings())
    added = await memories.add(MODEL, "Bedragen in €, niet in k€ — let op afronding", author="alice")
    files = list((tmp_path / "mem" / COLLECTION).glob("*.json"))
    assert [f.stem for f in files] == [MODEL]
    assert added.text in json.loads(files[0].read_text(encoding="utf-8"))["value"]["memories"][0]["text"]

    reopened = build_memories(load_settings())  # a restart reads the same files
    assert [m.text for m in await reopened.list(MODEL)] == [added.text]
    await reopened.remove(MODEL, added.id, author="alice")
    assert not list((tmp_path / "mem" / COLLECTION).glob("*.json")), "an empty record is deleted, not kept"


@pytest.mark.filterwarnings("ignore:A configured store is unstable:UserWarning")
def test_a_storage_account_puts_memories_in_their_own_table_in_the_clear(dummy_env, monkeypatch):
    from key_value.aio.stores.azure_tables import AzureTablesStore

    monkeypatch.setenv("PBIMCP_STATE_STORAGE_ACCOUNT", "stpbimcp")
    monkeypatch.setenv("PBIMCP_MEMORY_TABLE_NAME", "notes")
    memories = build_memories(load_settings(), credential=object())  # no network: the client is lazy
    kv = memories._kv  # pyright: ignore[reportPrivateUsage]
    assert isinstance(kv, AzureTablesStore) and kv._table_name == "notes"  # pyright: ignore[reportPrivateUsage]


# -------------------------------------------------------- through the gateway


class AccessStub:
    """The hosted Power BI MCP as far as memories care: the schema comes back for (token, model)
    pairs in `allowed` and every other request is refused with 403, like Power BI does for a user
    without Build permission. Queries succeed for anyone who got past the schema."""

    def __init__(self, allowed: set[tuple[str, str]]) -> None:
        self.allowed = allowed
        self.schema_calls = 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        token = request.headers["Authorization"].removeprefix("Bearer ")
        body = json.loads(request.content)
        name, args = body["params"]["name"], body["params"]["arguments"]
        if name == "GetSemanticModelSchema":
            self.schema_calls += 1
            if (token, args["artifactId"].lower()) not in self.allowed:
                return httpx.Response(403, text="Forbidden")
            text = json.dumps(SCHEMA)
        else:
            text = json.dumps({"executionResult": {"tables": [{"columns": [{"name": "n"}], "rows": [[7]]}]}})
        payload = {"jsonrpc": "2.0", "id": body["id"], "result": {"content": [{"type": "text", "text": text}]}}
        return httpx.Response(
            200,
            content="event: message\ndata: " + json.dumps(payload) + "\n\n",
            headers={"content-type": "text/event-stream"},
        )


class OneAnswer:
    """A Responses client whose model always writes the same DAX; keeps the prompts it saw."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        return type("R", (), {"output_text": json.dumps({"dax": DAX, "explanation": "e", "assumptions": []})})()


@pytest.fixture
def hosted() -> AccessStub:
    return AccessStub(allowed={("tok-alice", MODEL), ("tok-alice", OTHER_MODEL), ("tok-bob", OTHER_MODEL)})


@pytest.fixture
def gateway(dummy_env, skills_dir, hosted) -> tuple[Gateway, OneAnswer]:
    responses = OneAnswer()
    gw = Gateway(
        load_settings(),
        Skills.load(skills_dir),
        Catalog.load(skills_dir / "catalog.yaml"),
        hosted_transport=httpx.MockTransport(hosted),
        generator_factory=lambda: DaxGenerator(responses, "gpt-5", "- rules", "low"),
    )
    return gw, responses


async def test_memories_are_visible_exactly_to_users_who_can_open_the_model(gateway):
    gw, _ = gateway
    written = await gw.remember("alice", "tok-alice", MODEL, "Shipments before 2020 are partial")
    assert written["memory"]["mine"] is True

    # Bob cannot open MODEL: no memories, no writing, and the error is the usual access message.
    with pytest.raises(ToolError, match="Build permission"):
        await gw.recall("bob", "tok-bob", MODEL)
    with pytest.raises(ToolError, match="Build permission"):
        await gw.remember("bob", "tok-bob", MODEL, "should not land")
    with pytest.raises(ToolError, match="Build permission"):
        await gw.forget("bob", "tok-bob", MODEL, written["memory"]["id"])

    # Bob can open OTHER_MODEL, which has no memories of its own: nothing cascades from MODEL.
    assert await gw.recall("bob", "tok-bob", OTHER_MODEL) == {"model_id": OTHER_MODEL, "memories": []}
    # Alice, who can open MODEL, sees what was written; nothing was written by Bob.
    recalled = await gw.recall("alice", "tok-alice", MODEL)
    assert [m["text"] for m in recalled["memories"]] == ["Shipments before 2020 are partial"]


async def test_a_colleague_with_access_sees_but_cannot_forget_someone_elses_memory(gateway, hosted):
    gw, _ = gateway
    hosted.allowed.add(("tok-bob", MODEL))
    written = await gw.remember("alice", "tok-alice", MODEL, "Use [Shipment Count]")
    seen = await gw.recall("bob", "tok-bob", MODEL)
    assert seen["memories"][0]["text"] == "Use [Shipment Count]" and seen["memories"][0]["mine"] is False
    with pytest.raises(ToolError, match="person who wrote"):
        await gw.forget("bob", "tok-bob", MODEL, written["memory"]["id"])
    forgotten = await gw.forget("alice", "tok-alice", MODEL, written["memory"]["id"])
    assert forgotten["forgotten"]["id"] == written["memory"]["id"]
    assert (await gw.recall("bob", "tok-bob", MODEL))["memories"] == []


async def test_access_is_rechecked_when_the_cached_schema_expires(gateway, hosted, monkeypatch):
    gw, _ = gateway
    await gw.remember("alice", "tok-alice", MODEL, "note")
    assert hosted.schema_calls == 1
    await gw.recall("alice", "tok-alice", MODEL)
    assert hosted.schema_calls == 1, "a model the conversation already opened costs no extra call"

    hosted.allowed.discard(("tok-alice", MODEL))  # Build permission revoked in Power BI
    await gw.recall("alice", "tok-alice", MODEL)  # still cached: revocation shows up within the TTL
    gw._schema_cache._items.clear()  # pyright: ignore[reportPrivateUsage]
    with pytest.raises(ToolError, match="Build permission"):
        await gw.recall("alice", "tok-alice", MODEL)


async def test_memories_cascade_into_the_schema_notes_and_the_dax_prompt(gateway):
    gw, responses = gateway
    before = await gw.schema("alice", "tok-alice", MODEL)
    assert "Remembered by users" not in before
    await gw.remember("alice", "tok-alice", MODEL, "Shipments before 2020 are partial")

    text = await gw.schema("alice", "tok-alice", MODEL)
    assert text.startswith("## Notes\n") and "- Shipments before 2020 are partial" in text
    raw = await gw.schema("alice", "tok-alice", MODEL, compact=False)
    assert "Remembered by users of this model" in raw["notes"]

    result = await gw.generate("alice", "tok-alice", MODEL, "how many shipments?")
    assert result["result"]["tables"][0]["rows"] == [[7]]
    prompt = json.dumps(responses.calls[-1])
    assert "Shipments before 2020 are partial" in prompt, "generate_dax is grounded in the memories too"

    with pytest.raises(ToolError, match="Build permission"):
        await gw.schema("bob", "tok-bob", MODEL)


async def test_model_ids_are_validated_before_any_upstream_call(gateway, hosted):
    gw, _ = gateway
    with pytest.raises(ToolError, match="not a semantic model id"):
        await gw.recall("alice", "tok-alice", "../etc")
    assert hosted.schema_calls == 0
