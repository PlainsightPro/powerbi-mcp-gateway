"""Verify preserved model-note authorization through the expanded MCP surface."""

import json

import httpx
import pytest
from fastmcp import Client
from fastmcp.server.auth.providers.azure import _EntraOBOToken

from powerbi_mcp.catalog import Catalog
from powerbi_mcp.client_results import tool_data
from powerbi_mcp.config import load_settings
from powerbi_mcp.gateway import Gateway
from powerbi_mcp.server import build_server
from powerbi_mcp.skills import Skills

MODEL = "00000000-0000-0000-0000-000000000010"


async def test_new_analysis_context_keeps_notes_and_checks_each_users_access(dummy_env, monkeypatch):
    identity = {"user": "alice", "token": "allowed"}

    async def token(_):
        return identity["token"]

    def hosted(request):
        if request.headers["authorization"] != "Bearer allowed":
            return httpx.Response(403)
        body = json.loads(request.content)
        return httpx.Response(
            200,
            json={"id": body["id"], "result": {"structuredContent": {"schema": {"Tables": [{"Name": "Object A"}]}}}},
        )

    monkeypatch.setattr(_EntraOBOToken, "__aenter__", token)
    monkeypatch.setattr("powerbi_mcp.server._current_user_key", lambda: identity["user"])
    settings = load_settings()
    skills = Skills("Fixture instructions", "Shared definitions", "One EVALUATE")
    gateway = Gateway(settings, skills, Catalog([]), hosted_transport=httpx.MockTransport(hosted))
    async with Client(build_server(settings, gateway=gateway)) as client:
        saved = tool_data(await client.call_tool("remember", {"model_id": MODEL, "text": "Confirmed fixture note"}))
        context = tool_data(await client.call_tool("get_model_context", {"model_id": MODEL}))
        assert "Confirmed fixture note" in context["notes"]
        outcome = tool_data(await client.call_tool("analyze", {"model_hint": MODEL, "question": "Fixture question"}))
        assert outcome["status"] == "context_ready"
        assert "Confirmed fixture note" in outcome["model_context"]["notes"]
        identity.update(user="bob", token="denied")
        for tool in ("recall", "get_model_context"):
            with pytest.raises(Exception, match=r"permission|refused"):
                await client.call_tool(tool, {"model_id": MODEL})
        denied = tool_data(await client.call_tool("analyze", {"model_hint": MODEL, "question": "Fixture question"}))
        assert denied["status"] == "failed" and "Confirmed fixture note" not in json.dumps(denied)
        identity.update(user="alice", token="allowed")
        await client.call_tool("forget", {"model_id": MODEL, "memory_id": saved["memory"]["id"]})
        context = tool_data(await client.call_tool("get_model_context", {"model_id": MODEL}))
        assert "Confirmed fixture note" not in context["notes"]
