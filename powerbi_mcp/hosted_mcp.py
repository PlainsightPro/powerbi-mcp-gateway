"""Client for Microsoft's hosted Power BI MCP server, called with the user's own Fabric token.

The hosted server is stateless (no Mcp-Session-Id) and answers every JSON-RPC call as an SSE
stream with a single data line. We use it as the execution layer so that schema reads, DAX
execution and report metadata keep Microsoft's permission model and row-level security.
"""

from __future__ import annotations

import json
from typing import Any

import httpx

from .errors import GatewayError, http_error, request_with_retry

PROTOCOL_VERSION = "2025-06-18"


class HostedMcpError(GatewayError):
    def __init__(self, message: str, code: int | None = None, data: Any = None, *, kind=None) -> None:
        if kind is None:
            kind = classify_error(message, code)
        super().__init__(message, kind=kind)
        self.code = code
        self.data = data


def classify_error(message: str, code: int | None = None) -> str:
    text = message.casefold()
    if code == 401 or any(s in text for s in ("unauthorized", "token expired", "invalid token")):
        return "authentication"
    if code == 403 or any(s in text for s in ("permission", "forbidden", "access denied", "license", "licence", "skunotsupported")):
        return "permission"
    if code == 429 or any(s in text for s in ("throttl", "too many requests", "rate limit")):
        return "throttled"
    if code in (502, 503, 504) or any(s in text for s in ("timeout", "timed out", "temporarily unavailable")):
        return "unavailable"
    if any(s in text for s in ("syntax", "cannot find", "was not found", "cannot be found", "dax", "query (", "query execution failed")):
        return "query"
    return "protocol"


def parse_jsonrpc_response(text: str, content_type: str) -> dict:
    """The hosted server replies as SSE (event: message / data: {...}) or as plain JSON."""
    try:
        if "text/event-stream" in content_type:
            events = text.replace("\r\n", "\n").strip().split("\n\n")
            payloads = ["\n".join(line[5:].lstrip() for line in event.splitlines() if line.startswith("data:"))
                        for event in events]
            messages = [json.loads(payload) for payload in payloads if payload and payload != "[DONE]"]
            payload = next((m for m in reversed(messages) if isinstance(m, dict) and ("result" in m or "error" in m)), None)
        else:
            payload = json.loads(text)
        if not isinstance(payload, dict) or not ("result" in payload or "error" in payload):
            raise HostedMcpError("No JSON-RPC result in the hosted MCP response.")
        return payload
    except (json.JSONDecodeError, TypeError) as exc:
        raise HostedMcpError("The hosted MCP returned an invalid JSON-RPC response.") from exc


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
        resp = await request_with_retry(self._client, "POST", self._url, content=json.dumps(body))
        if not resp.is_success:
            raise http_error(resp, "Hosted Power BI MCP")
        payload = parse_jsonrpc_response(resp.text, resp.headers.get("content-type", ""))
        if "id" in payload and payload["id"] != body["id"]:
            raise HostedMcpError("The hosted MCP returned a mismatched request id.")
        if "error" in payload:
            err = payload["error"]
            if not isinstance(err, dict):
                raise HostedMcpError("The hosted MCP returned an invalid error response.")
            raise HostedMcpError(err.get("message", "hosted MCP error"), code=err.get("code"), data=err.get("data"))
        if not isinstance(payload["result"], dict):
            raise HostedMcpError("The hosted MCP returned an invalid result response.")
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
        if isinstance(result.get("structuredContent"), dict):
            return result["structuredContent"]
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
        if not isinstance(payload, dict):
            raise HostedMcpError(str(payload).strip() or f"{name} returned no data")
        return payload

    async def get_schema(self, model_id: str) -> dict:
        return await self._call_structured("GetSemanticModelSchema", {"artifactId": model_id})

    async def execute_query(self, model_id: str, dax_queries: list[str], max_rows: int | None = None) -> dict:
        args: dict[str, Any] = {"artifactId": model_id, "daxQueries": dax_queries}
        if max_rows is not None:
            args["maxRows"] = max_rows
        return await self._call_structured("ExecuteQuery", args)

    async def get_report_metadata(self, report_id: str) -> dict:
        return await self._call_structured("GetReportMetadata", {"artifactId": report_id})
