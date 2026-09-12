import json

import httpx
import pytest

from powerbi_mcp.fabric import FabricAccessError, FabricClient
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
