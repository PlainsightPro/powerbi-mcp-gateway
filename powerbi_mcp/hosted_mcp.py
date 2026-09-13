"""Client for Microsoft's hosted Power BI MCP server, called with the user's own Fabric token.

The hosted server is stateless (no Mcp-Session-Id) and answers every JSON-RPC call as an SSE
stream with a single data line. We use it as the execution layer so that schema reads, DAX
execution and report metadata keep Microsoft's permission model and row-level security.
"""

from __future__ import annotations

import json
from typing import Any

import httpx

PROTOCOL_VERSION = "2025-06-18"


class HostedMcpError(RuntimeError):
    def __init__(self, message: str, code: int | None = None, data: Any = None) -> None:
        super().__init__(message)
        self.code = code
        self.data = data


def parse_jsonrpc_response(text: str, content_type: str) -> dict:
    """The hosted server replies as SSE (event: message / data: {...}) or as plain JSON."""
    if "text/event-stream" in content_type:
        payloads = [line[5:].strip() for line in text.splitlines() if line.startswith("data:")]
        if not payloads:
            raise HostedMcpError("empty SSE response from hosted MCP")
        return json.loads(payloads[-1])
    return json.loads(text)


class HostedPowerBIMcp:
    def __init__(
        self,
        user_token: str,
        url: str = "https://api.fabric.microsoft.com/v1/mcp/powerbi",
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._url = url
        self._client = httpx.AsyncClient(
            headers={
                "Authorization": f"Bearer {user_token}",
                "Accept": "application/json, text/event-stream",
                "Content-Type": "application/json",
            },
            timeout=120.0,
            transport=transport,
        )
        self._next_id = 1

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _rpc(self, method: str, params: dict | None = None) -> dict:
        body = {"jsonrpc": "2.0", "id": self._next_id, "method": method, "params": params or {}}
        self._next_id += 1
        resp = await self._client.post(self._url, content=json.dumps(body))
        if resp.status_code == 401:
            raise HostedMcpError("hosted Power BI MCP rejected the user token (401)", code=401)
        payload = parse_jsonrpc_response(resp.text, resp.headers.get("content-type", ""))
        if "error" in payload:
            err = payload["error"]
            raise HostedMcpError(err.get("message", "hosted MCP error"), code=err.get("code"), data=err.get("data"))
        return payload["result"]

    async def initialize(self) -> dict:
        return await self._rpc(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "powerbi-mcp-gateway", "version": "0.1.0"},
            },
        )

    async def call_tool(self, name: str, arguments: dict) -> Any:
        """Call a hosted tool and return its decoded JSON payload (the text content of the result)."""
        result = await self._rpc("tools/call", {"name": name, "arguments": arguments})
        if result.get("isError"):
            raise HostedMcpError(str(result.get("content")))
        content = result.get("content") or []
        text = next((c.get("text") for c in content if c.get("type") == "text"), None)
        if text is None:
            return result
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return text

    async def _call_structured(self, name: str, arguments: dict) -> dict:
        """The structured tools answer JSON; a plain-text answer is the server describing a failure
        (for example a DAX syntax error) without setting isError, so surface it as an error."""
        payload = await self.call_tool(name, arguments)
        if isinstance(payload, str):
            raise HostedMcpError(payload.strip() or f"{name} returned no data")
        return payload

    async def get_schema(self, model_id: str) -> dict:
        return await self._call_structured("GetSemanticModelSchema", {"artifactId": model_id})

    async def execute_query(self, model_id: str, dax_queries: list[str], max_rows: int | None = None) -> dict:
        """Run the queries and return one payload whose executionResult.tables holds one table per
        query, in order. The hosted server accepts a list but only returns the first query's table,
        so each query is sent on its own and the results are merged."""
        results = [await self._execute_one(model_id, dax, max_rows) for dax in dax_queries]
        if len(results) == 1:
            return results[0]
        merged = dict(results[0])
        merged["executionResult"] = dict(results[0].get("executionResult") or {})
        merged["executionResult"]["tables"] = [
            table for r in results for table in (r.get("executionResult") or {}).get("tables", [])
        ]
        return merged

    async def _execute_one(self, model_id: str, dax: str, max_rows: int | None) -> dict:
        args: dict[str, Any] = {"artifactId": model_id, "daxQueries": [dax]}
        if max_rows is not None:
            args["maxRows"] = max_rows
        return await self._call_structured("ExecuteQuery", args)

    async def get_report_metadata(self, report_id: str) -> dict:
        return await self._call_structured("GetReportMetadata", {"artifactId": report_id})
