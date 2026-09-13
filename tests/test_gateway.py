"""The behaviour behind the tools, driven without OAuth: caching, error mapping and the repair loop.

Upstreams are httpx.MockTransport stand-ins for the Fabric API and the hosted Power BI MCP; the
DAX generator is a scripted Responses stub. Names in the fixtures are invented (an "Orders" table),
not taken from the example skills.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import httpx
import pytest
from fastmcp.exceptions import ToolError

from powerbi_mcp.catalog import Catalog
from powerbi_mcp.config import load_settings
from powerbi_mcp.dax_generator import DaxGenerator
from powerbi_mcp.gateway import Gateway, TtlCache
from powerbi_mcp.skills import Skills

SCHEMA = {
    "schema": {
        "Tables": [
            {
                "Name": "Orders",
                "Measures": [{"Name": "Order Count", "Type": "Int64"}],
                "Columns": [{"Name": "Order Date", "Type": "DateTime"}],
            }
        ]
    }
}
GOOD_DAX = 'EVALUATE ROW("n", [Order Count])'
BAD_DAX = 'EVALUATE ROW("n", [Order Count]'
ENGINE_ERROR = "Query execution failed: The syntax for ')' is incorrect."


def _sse(payload: dict) -> httpx.Response:
    body = "event: message\ndata: " + json.dumps(payload) + "\n\n"
    return httpx.Response(200, content=body, headers={"content-type": "text/event-stream"})


class HostedStub:
    """Answers hosted-MCP tool calls by name; DAX in `failing` gets the plain-text engine error."""

    def __init__(self, failing: set[str] | None = None) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.failing = failing or set()

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        name, args = body["params"]["name"], body["params"]["arguments"]
        self.calls.append((name, args))
        if name == "GetSemanticModelSchema":
            text = json.dumps(SCHEMA)
        elif name == "ExecuteQuery":
            dax = args["daxQueries"][0]
            if dax in self.failing:
                text = ENGINE_ERROR
            else:
                text = json.dumps({"executionResult": {"tables": [{"columns": [{"name": "n"}], "rows": [[42]]}]}})
        elif name == "GetReportMetadata":
            text = json.dumps({"pages": [{"name": "Overview"}]})
        else:
            text = json.dumps({})
        return _sse({"jsonrpc": "2.0", "id": body["id"], "result": {"content": [{"type": "text", "text": text}]}})

    def executed(self) -> list[str]:
        return [args["daxQueries"][0] for name, args in self.calls if name == "ExecuteQuery"]


class FabricStub:
    """One workspace with one model whose id is given (a curated id from the example catalog)."""

    def __init__(self, model_id: str, status: int = 200) -> None:
        self.model_id = model_id
        self.status = status
        self.calls = 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls += 1
        if self.status != 200:
            return httpx.Response(self.status, text="denied")
        if request.url.path.endswith("/workspaces"):
            return httpx.Response(200, json={"value": [{"id": "w1", "displayName": "Sandbox"}]})
        return httpx.Response(
            200,
            json={
                "value": [
                    {"id": "ffffffff-0000-0000-0000-000000000000", "displayName": "Scratch", "description": "ad hoc"},
                    {"id": self.model_id, "displayName": "Whatever the catalog says"},
                ]
            },
        )


class ScriptedResponses:
    """A Responses client that returns the given DAX strings in order."""

    def __init__(self, *dax: str) -> None:
        self.queue = list(dax)
        self.calls: list[dict] = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(
            output_text=json.dumps({"dax": self.queue.pop(0), "explanation": "scripted", "assumptions": ["none"]})
        )


@pytest.fixture
def settings(dummy_env):
    return load_settings()


@pytest.fixture
def catalog(skills_dir):
    return Catalog.load(skills_dir / "catalog.yaml")


@pytest.fixture
def curated_id(catalog) -> str:
    return catalog.entries[0].id


def make_gateway(settings, skills_dir, hosted=None, fabric=None, *dax: str) -> Gateway:
    generator_factory = None
    if dax:
        stub = ScriptedResponses(*dax)
        generator_factory = lambda: DaxGenerator(stub, "gpt-5", "- rules", "low")  # noqa: E731
    return Gateway(
        settings,
        Skills.load(skills_dir),
        Catalog.load(skills_dir / "catalog.yaml"),
        fabric_transport=httpx.MockTransport(fabric) if fabric else None,
        hosted_transport=httpx.MockTransport(hosted) if hosted else None,
        generator_factory=generator_factory,
    )


async def test_list_models_is_cached_per_user_and_curated_first(settings, skills_dir, curated_id):
    fabric = FabricStub(curated_id)
    gw = make_gateway(settings, skills_dir, fabric=fabric)
    rows = await gw.list_models("alice", "tok")
    assert [r["curated"] for r in rows] == [True, False]
    assert rows[0]["id"] == curated_id and rows[1]["description"] == "ad hoc"
    calls_after_first = fabric.calls
    assert await gw.list_models("alice", "tok", include_uncurated=False) == rows[:1]
    assert fabric.calls == calls_after_first, "second call for the same user is served from the cache"
    await gw.list_models("bob", "tok")
    assert fabric.calls > calls_after_first, "another user gets their own listing"


async def test_list_models_maps_fabric_refusals_to_a_readable_error(settings, skills_dir, curated_id):
    gw = make_gateway(settings, skills_dir, fabric=FabricStub(curated_id, status=401))
    with pytest.raises(ToolError, match="Build permission"):
        await gw.list_models("alice", "tok")


async def test_schema_is_cached_per_user_and_model_and_carries_curated_notes(settings, skills_dir, curated_id):
    hosted = HostedStub()
    gw = make_gateway(settings, skills_dir, hosted=hosted)
    text = await gw.schema("alice", "tok", curated_id.upper())
    assert text.startswith("## Notes\n") and "TABLE 'Orders'" in text and "measure [Order Count]" in text
    raw = await gw.schema("alice", "tok", curated_id, compact=False)
    assert raw["schema"] == SCHEMA and raw["notes"]
    assert len(hosted.calls) == 1, "one schema fetch per user and model, whatever the id casing"
    plain = await gw.schema("alice", "tok", "ffffffff-0000-0000-0000-000000000000")
    assert plain.startswith("## Schema\n"), "an uncurated model has no notes block"


async def test_execute_validates_the_query_count_and_maps_engine_errors(settings, skills_dir):
    hosted = HostedStub(failing={BAD_DAX})
    gw = make_gateway(settings, skills_dir, hosted=hosted)
    with pytest.raises(ToolError, match="between 1 and 4"):
        await gw.execute("tok", "m", [])
    with pytest.raises(ToolError, match="between 1 and 4"):
        await gw.execute("tok", "m", [GOOD_DAX] * 5)
    result = await gw.execute("tok", "m", [GOOD_DAX])
    assert result["executionResult"]["tables"][0]["rows"] == [[42]]
    assert hosted.calls[-1][1]["maxRows"] == settings.default_max_rows
    with pytest.raises(ToolError, match=r"Power BI MCP error.*syntax"):
        await gw.execute("tok", "m", [BAD_DAX])


async def test_generate_without_execute_returns_the_dax_only(settings, skills_dir):
    hosted = HostedStub()
    gw = make_gateway(settings, skills_dir, hosted, None, GOOD_DAX)
    result = await gw.generate("alice", "tok", "m", "how many orders", execute=False)
    assert result == {"model_id": "m", "dax": GOOD_DAX, "explanation": "scripted", "assumptions": ["none"]}
    assert hosted.executed() == []


async def test_generate_repairs_once_after_the_engine_rejects_the_query(settings, skills_dir):
    hosted = HostedStub(failing={BAD_DAX})
    gw = make_gateway(settings, skills_dir, hosted, None, BAD_DAX, GOOD_DAX)
    result = await gw.generate("alice", "tok", "m", "how many orders", max_rows=7)
    assert result["dax"] == GOOD_DAX and "syntax" in result["repaired_after"]
    assert result["result"]["tables"][0]["rows"] == [[42]]
    assert hosted.executed() == [BAD_DAX, GOOD_DAX]
    assert all(args["maxRows"] == 7 for name, args in hosted.calls if name == "ExecuteQuery")


async def test_generate_reports_both_errors_when_the_repair_fails_too(settings, skills_dir):
    hosted = HostedStub(failing={BAD_DAX})
    gw = make_gateway(settings, skills_dir, hosted, None, BAD_DAX, BAD_DAX)
    result = await gw.generate("alice", "tok", "m", "how many orders")
    assert "result" not in result and result["dax"] == BAD_DAX
    assert "syntax" in result["execution_error"] and "syntax" in result["repair_error"]
    assert hosted.executed() == [BAD_DAX, BAD_DAX]


async def test_generate_without_a_foundry_endpoint_is_a_configuration_error(settings, skills_dir):
    gw = make_gateway(settings, skills_dir, hosted=HostedStub())
    assert not settings.foundry_endpoint
    with pytest.raises(ToolError, match="not configured"):
        await gw.generate("alice", "tok", "m", "how many orders")


async def test_report_metadata_passes_through(settings, skills_dir):
    hosted = HostedStub()
    gw = make_gateway(settings, skills_dir, hosted=hosted)
    assert await gw.report_metadata("tok", "r1") == {"pages": [{"name": "Overview"}]}
    assert hosted.calls == [("GetReportMetadata", {"artifactId": "r1"})]


def test_recipe_lookup_is_case_insensitive_and_unknown_names_list_the_index(settings, skills_dir):
    gw = make_gateway(settings, skills_dir)
    name = next(iter(gw.skills.recipes))
    assert gw.recipe(f"  {name.upper()} ") == gw.skills.recipes[name]
    with pytest.raises(ToolError, match="Unknown recipe") as err:
        gw.recipe("not-a-recipe")
    assert name in str(err.value), "the error lists the available recipes"
    assert gw.health()["recipes"] == sorted(gw.skills.recipes)


def test_ttl_cache_expires_entries(monkeypatch):
    now = 1000.0
    monkeypatch.setattr("powerbi_mcp.gateway.time.monotonic", lambda: now)
    cache = TtlCache(ttl_seconds=10)
    cache.set("k", "v")
    assert cache.get("k") == "v"
    now += 11
    assert cache.get("k") is None
