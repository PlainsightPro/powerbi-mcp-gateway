import json

import httpx
import pytest

from powerbi_mcp.fabric import FabricAccessError, FabricClient, FabricThrottledError
from powerbi_mcp.hosted_mcp import HostedMcpError, HostedPowerBIMcp, parse_jsonrpc_response


def _sse(payload: dict) -> httpx.Response:
    body = "event: message\ndata: " + json.dumps(payload) + "\n\n"
    return httpx.Response(200, content=body, headers={"content-type": "text/event-stream"})


def test_parse_sse_and_json():
    assert parse_jsonrpc_response('event: message\ndata: {"result": 1}\n', "text/event-stream") == {"result": 1}
    assert parse_jsonrpc_response('{"result": 2}', "application/json") == {"result": 2}
    with pytest.raises(HostedMcpError):
        parse_jsonrpc_response("", "text/event-stream")


async def test_hosted_call_tool_decodes_text_payload():
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        assert request.headers["authorization"] == "Bearer user-token"
        return _sse(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "result": {
                    "content": [
                        {"type": "text", "text": json.dumps({"executionResult": {"tables": [{"rows": [[1]]}]}})}
                    ]
                },
            }
        )

    client = HostedPowerBIMcp("user-token", "https://example.test/mcp", transport=httpx.MockTransport(handler))
    result = await client.execute_query("model-1", ['EVALUATE ROW("x", 1)'], max_rows=5)
    await client.aclose()
    assert result["executionResult"]["tables"][0]["rows"] == [[1]]
    assert seen[0]["method"] == "tools/call"
    assert seen[0]["params"] == {
        "name": "ExecuteQuery",
        "arguments": {"artifactId": "model-1", "daxQueries": ['EVALUATE ROW("x", 1)'], "maxRows": 5},
    }


async def test_hosted_error_surfaces_code_and_data():
    def handler(_: httpx.Request) -> httpx.Response:
        return _sse(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "error": {"code": -32600, "message": "nope", "data": {"error-code": "AI_Scenarios_SkuNotSupported"}},
            }
        )

    client = HostedPowerBIMcp("t", "https://example.test/mcp", transport=httpx.MockTransport(handler))
    with pytest.raises(HostedMcpError) as err:
        await client.get_schema("m")
    await client.aclose()
    assert err.value.code == -32600 and err.value.data["error-code"] == "AI_Scenarios_SkuNotSupported"


async def test_hosted_plain_text_answer_on_structured_tool_is_an_error():
    def handler(_: httpx.Request) -> httpx.Response:
        return _sse(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "result": {
                    "content": [{"type": "text", "text": "Query execution failed: The syntax for ']' is incorrect."}]
                },
            }
        )

    client = HostedPowerBIMcp("t", "https://example.test/mcp", transport=httpx.MockTransport(handler))
    with pytest.raises(HostedMcpError, match="syntax"):
        await client.execute_query("m", ["EVALUATE ROW(1']"])
    await client.aclose()


async def test_fabric_lists_models_per_workspace_with_paging():
    def handler(request: httpx.Request) -> httpx.Response:
        path, token = request.url.path, request.url.params.get("continuationToken")
        if path == "/v1/workspaces" and token is None:
            return httpx.Response(200, json={"value": [{"id": "w1", "displayName": "One"}], "continuationToken": "p2"})
        if path == "/v1/workspaces" and token == "p2":
            return httpx.Response(200, json={"value": [{"id": "w2", "displayName": "Two"}]})
        if path == "/v1/workspaces/w1/semanticModels":
            return httpx.Response(200, json={"value": [{"id": "m1", "displayName": "Finance", "description": "d"}]})
        if path == "/v1/workspaces/w2/semanticModels":
            return httpx.Response(403, text="no")  # visible workspace, items not listable
        return httpx.Response(404)

    client = FabricClient("t", "https://api.test/v1", transport=httpx.MockTransport(handler))
    models = await client.list_accessible_models()
    await client.aclose()
    assert [(m.id, m.workspace_name) for m in models] == [("m1", "One")]


async def test_fabric_401_is_an_access_error():
    client = FabricClient(
        "t", "https://api.test/v1", transport=httpx.MockTransport(lambda r: httpx.Response(401, text="expired"))
    )
    with pytest.raises(FabricAccessError):
        await client.list_workspaces()
    await client.aclose()


async def test_hosted_runs_each_query_separately_and_merges_tables():
    """The hosted server only returns the first table of a multi-query call, so the client sends
    one query per call and stitches the tables together in order."""
    sent: list[list[str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        queries = body["params"]["arguments"]["daxQueries"]
        sent.append(queries)
        table = {"columns": [{"name": "q", "type": "Int64"}], "rows": [[len(sent)]]}
        payload = {"executionResult": {"tables": [table]}, "semanticModel": {"Name": "m"}}
        return _sse(
            {"jsonrpc": "2.0", "id": body["id"], "result": {"content": [{"type": "text", "text": json.dumps(payload)}]}}
        )

    client = HostedPowerBIMcp("t", "https://example.test/mcp", transport=httpx.MockTransport(handler))
    result = await client.execute_query("m", ['EVALUATE ROW("a", 1)', 'EVALUATE ROW("b", 2)'], max_rows=5)
    await client.aclose()
    assert sent == [['EVALUATE ROW("a", 1)'], ['EVALUATE ROW("b", 2)']]
    assert [t["rows"] for t in result["executionResult"]["tables"]] == [[[1]], [[2]]]
    assert result["semanticModel"] == {"Name": "m"}


@pytest.mark.parametrize(
    ("status", "headers", "expect"),
    [
        (401, {}, "rejected the user token"),
        (403, {}, "refused the request"),
        (429, {"retry-after": "7"}, "throttling this user"),
        (503, {}, "HTTP 503"),
    ],
)
async def test_hosted_maps_http_failures_instead_of_parsing_them(status, headers, expect):
    """A 4xx/5xx body is not JSON-RPC; it must become a HostedMcpError with the status, not a JSONDecodeError."""
    transport = httpx.MockTransport(lambda r: httpx.Response(status, text="<html>", headers=headers))
    client = HostedPowerBIMcp("t", "https://example.test/mcp", transport=transport)
    with pytest.raises(HostedMcpError, match=expect) as err:
        await client.get_schema("m")
    await client.aclose()
    assert err.value.code == status
    if status == 429:
        assert err.value.retry_after == 7.0


async def test_hosted_shared_client_carries_the_token_per_request_and_stays_open():
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers["authorization"])
        return _sse({"jsonrpc": "2.0", "id": 1, "result": {"content": [{"type": "text", "text": "{}"}]}})

    shared = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    alice = HostedPowerBIMcp("alice-token", "https://example.test/mcp", client=shared)
    bob = HostedPowerBIMcp("bob-token", "https://example.test/mcp", client=shared)
    await alice.get_schema("m")
    await bob.get_schema("m")
    await alice.aclose()
    assert seen == ["Bearer alice-token", "Bearer bob-token"]
    assert not shared.is_closed, "aclose() on a per-user view leaves the shared client open"
    await shared.aclose()


async def test_fabric_retries_once_on_429_then_raises_throttled(monkeypatch):
    calls = 0
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr("powerbi_mcp.fabric.asyncio.sleep", fake_sleep)

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(429, headers={"retry-after": "2"})
        if calls == 2:
            return httpx.Response(200, json={"value": [{"id": "w1", "displayName": "One"}]})
        return httpx.Response(429, headers={"retry-after": "3"})

    client = FabricClient("t", "https://api.test/v1", transport=httpx.MockTransport(handler))
    assert await client.list_workspaces() == [{"id": "w1", "displayName": "One"}]
    assert sleeps == [2.0], "the first 429 is retried after Retry-After"
    with pytest.raises(FabricThrottledError) as err:
        await client.list_workspaces()
    await client.aclose()
    assert err.value.retry_after == 3.0


async def test_fabric_listing_never_returns_a_partial_list_silently():
    """A workspace the user may not list (403) is skipped; a throttled or failing one raises."""

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/v1/workspaces":
            workspaces = [{"id": "ok", "displayName": "A"}, {"id": "bad", "displayName": "B"}]
            return httpx.Response(200, json={"value": workspaces})
        if path == "/v1/workspaces/ok/semanticModels":
            return httpx.Response(200, json={"value": [{"id": "m1", "displayName": "One"}]})
        return httpx.Response(500, text="boom")

    client = FabricClient("t", "https://api.test/v1", transport=httpx.MockTransport(handler))
    with pytest.raises(httpx.HTTPStatusError):
        await client.list_accessible_models()
    await client.aclose()
