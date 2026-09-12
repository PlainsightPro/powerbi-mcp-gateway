import pytest

from powerbi_mcp.config import load_settings
from powerbi_mcp.server import build_server

EXPECTED_TOOLS = {
    "list_semantic_models", "get_finance_context", "get_recipe", "get_semantic_model_schema",
    "execute_dax", "generate_dax", "get_report_metadata",
}


@pytest.fixture
def server(dummy_env):
    return build_server(load_settings())


async def test_server_exposes_tools_prompts_and_resources(server):
    from fastmcp import Client

    async with Client(server) as client:  # in-memory transport; HTTP auth is not in the path
        assert EXPECTED_TOOLS <= {t.name for t in await client.list_tools()}
        assert {"indirect-cost-analysis", "ebitda-bridge"} <= {p.name for p in await client.list_prompts()}
        assert {"skill://glossary", "skill://dax-rules", "skill://catalog"} <= {
            str(r.uri) for r in await client.list_resources()}
        assert "skill://recipes/{name}" in {str(t.uri_template) for t in await client.list_resource_templates()}

        recipe = await client.call_tool("get_recipe", {"name": "ebitda-bridge"})
        assert recipe.content[0].text.startswith("# Recipe: EBITDA bridge")
        context = await client.call_tool("get_finance_context", {})
        assert "[Indirect Cost]" in context.content[0].text and "indirect-cost-analysis" in context.content[0].text
        prompt = await client.get_prompt("indirect-cost-analysis", {"period": "2026"})
        assert "2026" in prompt.messages[0].content.text


async def test_server_instructions_carry_the_skill(server):
    assert "list_semantic_models" in (server.instructions or "")
    assert "GenerateQuery" in (server.instructions or "")
