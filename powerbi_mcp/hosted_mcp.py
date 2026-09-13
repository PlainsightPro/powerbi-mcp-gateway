"""Client for Microsoft's hosted Power BI MCP server, called with the user's own Fabric token.

The hosted server is stateless (no Mcp-Session-Id, no initialize handshake needed) and answers
every JSON-RPC call as an SSE stream with a single data line. We use it as the execution layer so
that schema reads, DAX execution and report metadata keep Microsoft's permission model and
row-level security.
"""

from __future__ import annotations

import json
from typing import Any

import httpx

DEFAULT_URL = "https://api.fabric.microsoft.com/v1/mcp/powerbi"
TIMEOUT_SECONDS = 120.0


class HostedMcpError(RuntimeError):
    def __init__(
        self, message: str, code: int | None = None, data: Any = None, retry_after: float | None = None
    ) -> None:
        super().__init__(message)
        self.code = code
        self.data = data
        self.retry_after = retry_after


def retry_after_seconds(resp: httpx.Response) -> float | None:
    """The Retry-After header as seconds, when it is a number (an HTTP date is ignored)."""
    value = resp.headers.get("retry-after", "").strip()
    try:
        return max(0.0, float(value)) if value else None
    except ValueError:
        return None


def raise_for_http_status(resp: httpx.Response, service: str = "hosted Power BI MCP") -> None:
    """Turn a non-2xx answer into a HostedMcpError before anyone tries to parse it as JSON-RPC."""
    status = resp.status_code
    if status < 400:
        return
    excerpt = resp.text[:300].strip()
    if status == 401:
        raise HostedMcpError(f"{service} rejected the user token (401)", code=401)
    if status == 403:
        raise HostedMcpError(f"{service} refused the request (403): {excerpt}", code=403)
    if status == 429:
        wait = retry_after_seconds(resp)
        hint = f"; retry after {wait:g} s" if wait else ""
        raise HostedMcpError(f"{service} is throttling this user (429){hint}", code=429, retry_after=wait)
    raise HostedMcpError(f"{service} answered HTTP {status}: {excerpt}", code=status)


def parse_jsonrpc_response(text: str, content_type: str) -> dict:
    """The hosted server replies as SSE (event: message / data: {...}) or as plain JSON."""
    if "text/event-stream" in content_type:
        payloads = [line[5:].strip() for line in text.splitlines() if line.startswith("data:")]
        if not payloads:
            raise HostedMcpError("empty SSE response from hosted MCP")
        return json.loads(payloads[-1])
    return json.loads(text)


class HostedPowerBIMcp:
    """One user's view of the hosted server.

    Pass a shared `client` to reuse connections across calls (the gateway does); the token then
    travels as a per-request header and `aclose()` leaves the shared client open. Without one, the
    instance owns a client and closes it.
    """

    def __init__(
        self,
        user_token: str,
        url: str = DEFAULT_URL,
        transport: httpx.AsyncBaseTransport | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._url = url
        self._headers = {
            "Authorization": f"Bearer {user_token}",
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
        }
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(timeout=TIMEOUT_SECONDS, transport=transport)
        self._next_id = 1

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def _rpc(self, method: str, params: dict | None = None) -> dict:
        body = {"jsonrpc": "2.0", "id": self._next_id, "method": method, "params": params or {}}
        self._next_id += 1
        resp = await self._client.post(self._url, content=json.dumps(body), headers=self._headers)
        raise_for_http_status(resp)
        payload = parse_jsonrpc_response(resp.text, resp.headers.get("content-type", ""))
        if "error" in payload:
            err = payload["error"]
            raise HostedMcpError(err.get("message", "hosted MCP error"), code=err.get("code"), data=err.get("data"))
        return payload["result"]

    async def call_tool(self, name: str, arguments: dict) -> Any:
        """Call a hosted tool and return its decoded JSON payload (the text content of the result)."""
        result = await self._rpc("tools/call", {"name": name, "arguments": arguments})
        content = result.get("content") or []
        texts = [c.get("text", "") for c in content if c.get("type") == "text"]
        if result.get("isError"):
            raise HostedMcpError("; ".join(t for t in texts if t) or str(content))
        if not texts:
            return result
        try:
            return json.loads(texts[0])
        except json.JSONDecodeError:
            return texts[0]

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
