"""Behavioral tests with invented schemas and user-token-aware upstream stubs."""

import copy
from datetime import date
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from powerbi_mcp.analysis import AnalysisService, report_reference
from powerbi_mcp.auth_storage import oauth_storage
from powerbi_mcp.catalog import Catalog, CatalogEntry
from powerbi_mcp.config import Settings
from powerbi_mcp.contracts import AnalysisContext, result_evidence
from powerbi_mcp.dax_generator import DaxGenerator, GeneratedDax, validate_dax
from powerbi_mcp.discovery import Discovery, TtlCache
from powerbi_mcp.errors import GatewayError
from powerbi_mcp.fabric import SemanticModelRef
from powerbi_mcp.hosted_mcp import HostedPowerBIMcp, parse_jsonrpc_response
from powerbi_mcp.recipes import Recipe
from powerbi_mcp.schema import compact_schema
from powerbi_mcp.skills import Skills

MODEL = "00000000-0000-0000-0000-000000000010"
OTHER = "00000000-0000-0000-0000-000000000020"
SCHEMA = {
    "Tables": [
        {
            "Name": "Object A",
            "Measures": [{"Name": "Metric A", "Type": "Double", "FormatString": "0.0%"}],
            "Columns": [{"Name": "Label", "Type": "Text"}],
        }
    ]
}
QUERY = 'EVALUATE ROW("Value", 1)'
ROWS: dict[str, Any] = {
    "executionResult": {"tables": [{"columns": [{"name": "Value", "type": "Double"}], "rows": [[1]]}]}
}


@pytest.fixture
def settings(tmp_path):
    return Settings(
        _env_file=None,  # type: ignore[call-arg]  # supported BaseSettings runtime argument
        tenant_id=MODEL,
        client_id=OTHER,
        client_secret="fixture-secret",
        jwt_signing_key="a" * 40,
        foundry_endpoint=None,
        skills_dir=tmp_path,
        oauth_storage_dir=None,
    )


@pytest.fixture
def service(settings):
    catalog = Catalog([CatalogEntry(MODEL, "Fixture A", "Workspace A", aliases=["alias-a"], key_measures=["Metric A"])])
    skills = Skills("instructions", "shared glossary", "one EVALUATE")
    state = SimpleNamespace(
        events=[],
        errors=[],
        warnings=[],
        refs=[SemanticModelRef(MODEL, "Fixture A", "w", "Workspace A")],
        schemas={MODEL: SCHEMA, OTHER: SCHEMA},
        denied_tokens=set(),
    )

    class Fabric:
        def __init__(self, token, url):
            self.token, self.warnings = token, copy.deepcopy(state.warnings)

        async def list_accessible_models(self, *, allow_partial=False):
            state.events.append(("list", self.token))
            return state.refs

        async def aclose(self):
            pass

    class Hosted:
        def __init__(self, token, url):
            self.token = token

        async def get_schema(self, model_id):
            state.events.append(("schema", self.token, model_id))
            if self.token in state.denied_tokens:
                raise GatewayError("Fixture permission denied", kind="permission")
            return copy.deepcopy(state.schemas[model_id])

        async def execute_query(self, model_id, queries, max_rows):
            state.events.append(("query", self.token, model_id, queries, max_rows))
            if state.errors:
                raise state.errors.pop(0)
            return copy.deepcopy(ROWS)

        async def get_report_metadata(self, report_id):
            return {"semanticModel": {"id": MODEL}, "pages": [{"name": "Page A"}]}

        async def aclose(self):
            pass

    discovery = Discovery(settings, catalog, fabric_factory=Fabric, hosted_factory=Hosted)
    svc: Any = AnalysisService(settings, skills, catalog, discovery)
    svc.state = state

    class Generator:
        def __init__(self):
            self.calls = []

        async def generate(self, *args):
            self.calls.append(("generate", args))
            return GeneratedDax(QUERY, "fixture", interpretation={"period": "2026-08", "filters": ["Label=A"]})

        async def repair(self, *args):
            self.calls.append(("repair", args))
            return GeneratedDax('EVALUATE ROW("Value", 2)', "repaired fixture")

    svc._generator = Generator()
    return svc


async def test_followup_context_and_single_query_repair_preserve_identity_and_history(service):
    service.state.errors = [GatewayError("Fixture syntax error", kind="query")]
    history = [{"role": "user", "content": "Earlier scope"}]
    context = AnalysisContext(
        model_id=MODEL,
        reference_date=date(2026, 9, 14),
        timezone="Europe/Brussels",
        filters=["Label=A"],
        period="2026-08",
    )
    result = await service.generate(
        "user-a", "token-a", MODEL, "Break that down", chat_history=history, context=context
    )
    assert result.status == "executed" and len(result.attempts) == 2
    assert result.context.model_id == MODEL and result.context.filters == ["Label=A"]
    repair = service._generator.calls[1][1]
    assert repair[-2] == history and repair[-1].reference_date == date(2026, 9, 14)
    assert all(e[1] == "token-a" for e in service.state.events)
    assert result.dax.endswith("2)") and result.evidence.freshness == "unknown"


@pytest.mark.parametrize("kind", ["authentication", "permission", "throttled", "unavailable", "protocol"])
async def test_non_query_failures_never_trigger_generation_repair(service, kind):
    service.state.errors = [GatewayError("Fixture failure", kind=kind)]
    result = await service.generate("u", "t", MODEL, "Question")
    assert result.status == "failed" and result.error.kind == kind
    assert len(service._generator.calls) == 1 and len(result.attempts) == 1


async def test_diagnostics_without_any_model_need_attention(service):
    service.state.refs = []  # no workspace role, and the direct-share probe is refused too
    service.state.errors.append(GatewayError("Fixture permission denied", kind="permission"))
    result = await service.diagnose("u", "t")
    assert result["status"] == "attention_required"
    assert result["checks"][-1]["name"] == "model_selection"


async def test_malformed_query_result_is_protocol_error(service, monkeypatch):
    async def empty(*args):
        return {}

    client = service.discovery.hosted("t")
    monkeypatch.setattr(type(client), "execute_query", empty)
    result = await service.generate("u", "t", MODEL, "Question")
    assert result.status == "failed" and result.error.kind == "protocol"
    assert len(service._generator.calls) == 1


async def test_mcp_workflow_serializes_real_contracts(service, dummy_env, monkeypatch):
    from fastmcp import Client
    from fastmcp.server.auth.providers.azure import _EntraOBOToken

    import powerbi_mcp.server as surface
    from powerbi_mcp.client_results import tool_data

    async def token(_):
        return "fixture-user-token"

    monkeypatch.setattr(_EntraOBOToken, "__aenter__", token)
    monkeypatch.setattr(surface, "_current_user_key", lambda: "fixture-user")
    monkeypatch.setattr(surface, "AnalysisService", lambda *args, **kwargs: service)
    server = surface.build_server()
    async with Client(server) as client:
        discovered = tool_data(await client.call_tool("search_semantic_models", {}))
        assert discovered["models"][0]["id"] == MODEL
        for name, args in [
            ("analyze", {"question": "Question", "model_hint": MODEL}),
            ("generate_dax", {"question": "Question", "model_id": MODEL}),
        ]:
            result = tool_data(await client.call_tool(name, args))
            assert result["status"] == "executed" and result["context"]["model_id"] == MODEL
            assert result["evidence"]["row_count"] == 1
        from powerbi_mcp.evaluation import Case, evaluate

        report = await evaluate(
            client,
            [
                Case.model_validate(
                    {
                        "name": "fixture",
                        "model_id": MODEL,
                        "question": "Question",
                        "context": {"reference_date": "2026-09-01"},
                        "expect": {"scalars": [{"column": "Value", "value": 1}]},
                    }
                )
            ],
        )
        assert report["status"] == "passed"
        before = len(service.state.events)
        with pytest.raises(Exception, match="input: max_rows"):
            await client.call_tool("execute_dax", {"model_id": MODEL, "dax_queries": [QUERY], "max_rows": -1})
        assert len(service.state.events) == before
    assert all(e[1] == "fixture-user-token" for e in service.state.events)


async def test_failed_repair_keeps_both_queries_and_errors(service):
    service.state.errors = [
        GatewayError("First syntax error", kind="query"),
        GatewayError("Second syntax error", kind="query"),
    ]
    result = await service.generate("u", "t", MODEL, "Question")
    assert result.status == "failed" and result.execution_error and result.repair_error
    assert [a["dax"] for a in result.attempts] == [QUERY, 'EVALUATE ROW("Value", 2)']
    assert result.dax == result.attempts[-1]["dax"]


async def test_generate_only_and_disabled_foundry_do_not_execute(service):
    result = await service.generate("u", "t", MODEL, "Question", execute=False)
    assert result.status == "generated" and not any(e[0] == "query" for e in service.state.events)
    service._generator = None
    result = await service.generate("u", "t", MODEL, "Question")
    assert result.status == "context_ready" and "one EVALUATE" in result.model_context["dax_rules"]


async def test_model_ambiguity_returns_candidates_without_querying(service):
    service.state.refs.append(SemanticModelRef(OTHER, "Fixture B", "w", "Workspace A"))
    result = await service.analyze("u", "t", "Question")
    assert result.status == "needs_clarification" and len(result.candidates) == 2
    assert not any(e[0] in ("schema", "query") for e in service.state.events)


async def test_alias_report_and_followup_routes(service):
    for kwargs in (
        {"model_hint": "alias-a"},
        {"report_url": f"https://app.powerbi.com/groups/{OTHER}/reports/{OTHER}/PageA"},
        {"context": AnalysisContext(model_id=MODEL)},
    ):
        result = await service.analyze("u", "t", "Question", **kwargs)
        assert result.status == "executed" and result.model_id == MODEL
    assert result.context.reference_date is not None


async def test_exact_catalog_alias_still_checks_user_permission(service):
    service.state.refs = []
    service.state.denied_tokens.add("denied")
    result = await service.analyze("u", "denied", "Question", model_hint="alias-a")
    assert result.status == "failed" and result.error.kind == "permission"
    assert not service._generator.calls


async def test_partial_discovery_is_not_cached_or_used_for_automatic_selection(service):
    service.state.warnings = [{"workspace_id": "missing", "error": {"kind": "unavailable"}}]
    first = await service.discovery.discover("u", "t")
    second = await service.discovery.discover("u", "t")
    assert first.status == second.status == "partial" and not second.cached
    outcome = await service.analyze("u", "t", "Question")
    assert outcome.status == "needs_clarification" and outcome.warnings


async def test_schema_cache_is_per_user_and_can_refresh(service):
    await service.discovery.schema("a", "token-a", MODEL)
    await service.discovery.schema("a", "token-a", MODEL)
    await service.discovery.schema("b", "token-b", MODEL)
    await service.discovery.schema("a", "token-a", MODEL, refresh=True)
    assert [e[1] for e in service.state.events] == ["token-a", "token-b", "token-a"]


@pytest.mark.parametrize("limit", [0, -5, 10001, True])
async def test_invalid_limits_rejected_before_upstream(service, limit):
    with pytest.raises(ValueError):
        await service.execute("t", MODEL, [QUERY], limit)
    assert service.state.events == []


async def test_curated_models_shared_directly_are_listed_after_a_data_free_probe(service):
    """No workspace role at all, one curated model shared directly: discovery lists it, says so,
    and the probe reads no data."""
    service.state.refs = []
    result = await service.discovery.discover("u", "t")
    assert [(r["id"], r["shared_directly"], r["curated"]) for r in result.models] == [(MODEL, True, True)]
    assert result.models[0]["workspace"] == "Workspace A"
    probes = [e for e in service.state.events if e[0] == "query"]
    assert probes == [("query", "t", MODEL, ['EVALUATE ROW("probe", 1)'], 1)]
    cached = await service.discovery.discover("u", "t")
    assert cached.cached and [e for e in service.state.events if e[0] == "query"] == probes
    result = await service.discovery.search("u", "t", "alias-a", verify_access=True)
    assert result.models[0]["id"] == MODEL and result.models[0]["query_access"] == "verified"
    assert result.models[0]["match_reason"].startswith("Exact")


async def test_a_refused_probe_leaves_no_trace_and_workspace_rows_are_not_probed(service):
    service.state.refs = []
    service.state.errors.append(GatewayError("Fixture permission denied", kind="permission"))
    result = await service.discovery.discover("u", "t", refresh=True)
    assert result.models == [] and result.status == "complete"
    service.state.refs = [SemanticModelRef(MODEL, "Fixture A", "w", "Workspace A")]
    before = len(service.state.events)
    result = await service.discovery.discover("u", "t", refresh=True)
    assert [r["shared_directly"] for r in result.models] == [False]
    assert [e for e in service.state.events[before:] if e[0] == "query"] == [], "listed models need no probe"


async def test_dimension_lookup_validates_identifiers_and_escapes_values(service):
    with pytest.raises(ValueError, match="Unknown column"):
        await service.dimension_values("u", "t", MODEL, "Missing", "Label")
    result = await service.dimension_values("u", "t", MODEL, "Object A", "Label", 'a"b')
    assert "CONTAINSSTRING" in result["dax_queries"][0] and '"a""b"' in result["dax_queries"][0]


def workflow():
    return Recipe.model_validate(
        {
            "title": "Invented workflow",
            "parameters": {"label": {"type": "string"}},
            "required_objects": [{"table": "Object A", "name": "Label", "kind": "column"}],
            "steps": [
                {"id": "first", "dax": 'EVALUATE ROW("Value", {{label}})'},
                {"id": "second", "depends_on": ["first"], "dax": QUERY},
            ],
        }
    )


async def test_recipe_validation_execution_and_fail_fast(service):
    service.skills.workflows["invented-flow"] = workflow()
    service.state.errors = [GatewayError("Fixture syntax", kind="query")]
    result = await service.run_recipe("u", "t", MODEL, "invented-flow", {"label": 'x") EVALUATE ROW("a", 1)'})
    assert result.status == "failed" and len(result.steps) == 1
    assert not service._generator.calls  # validated recipes do not invoke probabilistic repair
    prepared = await service.run_recipe("u", "t", MODEL, "invented-flow", {"label": "x"}, execute=False)
    assert prepared.status == "generated" and len(prepared.steps) == 2


def test_recipe_rejects_missing_parameters_wrong_types_and_quoted_placeholders():
    recipe = workflow()
    with pytest.raises(ValueError, match="Missing"):
        recipe.bind({})
    with pytest.raises(ValueError, match="string"):
        recipe.bind({"label": 42})
    raw = recipe.model_dump()
    raw["steps"][0]["dax"] = 'EVALUATE ROW("x", "{{label}}")'
    with pytest.raises(ValueError, match="bare DAX"):
        Recipe.model_validate(raw)


def test_large_schema_keeps_late_relevant_measure_relationships_and_metadata():
    schema = {
        "Tables": [
            {"Name": "Early", "Columns": [{"Name": f"Column{i}", "Description": "x" * 1000} for i in range(100)]},
            {"Name": "Late", "Measures": [{"Name": "Desired Metric"}]},
        ],
        "ActiveRelationships": [{"PK": "'Early'[Key]", "FK": "'Late'[Key]"}],
        "AIInstructions": "Author guidance",
    }
    text = compact_schema(schema, max_chars=3000, query="Desired Metric")
    assert "Desired Metric" in text and "ACTIVERELATIONSHIPS" in text and "Author guidance" in text
    assert "SCHEMA COVERAGE: partial" in text and len(text) <= 3000
    assert '"FormatString": "0.0%"' in compact_schema(SCHEMA)


def test_completeness_requires_evidence_for_the_whole_result():
    result = copy.deepcopy(ROWS)
    assert result_evidence(result, 1, "now").completeness == "unknown"
    result["executionResult"]["tables"][0]["isTruncated"] = False
    assert result_evidence(result, 1, "now").completeness == "complete"
    result["executionResult"]["tables"].append({"rows": [[2]]})
    assert result_evidence(result, 1, "now").completeness == "unknown"
    result["isTruncated"] = True
    assert result_evidence(result, 1, "now").completeness == "limited"


def test_dax_keywords_inside_strings_and_comments_do_not_count():
    validate_dax('EVALUATE ROW("EVALUATE", 1) // EVALUATE')
    with pytest.raises(ValueError):
        validate_dax("EVALUATE A EVALUATE B")


async def test_persistent_oauth_store_survives_recreation_and_secret_rotation(settings, tmp_path):
    settings.oauth_storage_dir = tmp_path / "oauth"
    first = oauth_storage(settings)
    assert first is not None
    await first.put("fixture", {"token": "sensitive-fixture-value"}, collection="tokens", ttl=60)
    settings.client_secret = "rotated-fixture-secret"
    second = oauth_storage(settings)
    assert second is not None
    assert await second.get("fixture", collection="tokens") == {"token": "sensitive-fixture-value"}
    assert all(
        "sensitive-fixture-value" not in p.read_text() for p in settings.oauth_storage_dir.rglob("*") if p.is_file()
    )
    settings.jwt_signing_key = "b" * 40
    reset = oauth_storage(settings)
    assert reset is not None
    assert await reset.get("fixture", collection="tokens") is None


def test_cache_is_bounded_and_returns_independent_values():
    cache = TtlCache(60, 1)
    cache.set("a", {"data": []})
    cached = cache.get("a")
    assert cached is not None
    cached["data"].append(1)
    assert cache.get("a") == {"data": []}
    cache.set("b", 2)
    assert cache.get("a") is None


async def test_hosted_retries_bounded_transient_status_and_accepts_structured_content(monkeypatch):
    import powerbi_mcp.errors as errors

    async def no_sleep(_):
        pass

    monkeypatch.setattr(errors.asyncio, "sleep", no_sleep)
    requests = []

    def handler(request):
        requests.append(request)
        if len(requests) < 3:
            return httpx.Response(503, text="not json")
        return httpx.Response(200, json={"id": 1, "result": {"structuredContent": ROWS}})

    client = HostedPowerBIMcp("t", "https://fixture.invalid", transport=httpx.MockTransport(handler))
    try:
        assert await client.execute_query(MODEL, [QUERY]) == ROWS
    finally:
        await client.aclose()
    assert len(requests) == 3


async def test_long_retry_after_is_not_shortened():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(429, headers={"Retry-After": "60"}, text="busy")

    client = HostedPowerBIMcp("t", "https://fixture.invalid", transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(GatewayError) as error:
            await client.get_schema(MODEL)
        assert error.value.kind == "throttled" and error.value.retry_after == 60
    finally:
        await client.aclose()
    assert len(seen) == 1


def test_sse_multiline_data_and_notifications():
    text = 'data: {"method":"progress"}\n\ndata: {"id":1,\ndata: "result":{}}\n\n'
    assert parse_jsonrpc_response(text, "text/event-stream") == {"id": 1, "result": {}}


@pytest.mark.parametrize(
    "url",
    [
        "https://evil.invalid/reports/" + MODEL,
        "http://app.powerbi.com/reports/" + MODEL,
        "https://app.powerbi.com/reports/not-an-id",
    ],
)
def test_report_link_validation(url):
    with pytest.raises(ValueError):
        report_reference(url)


async def test_generation_refusal_and_incomplete_outputs_are_not_treated_as_queries():
    class Stub:
        async def create(self, **kwargs):
            return SimpleNamespace(status="incomplete", output_text='{"dax":')

    with pytest.raises(GatewayError, match="complete"):
        await DaxGenerator(Stub(), "fixture", "rules").generate("q", SCHEMA, "", "")
