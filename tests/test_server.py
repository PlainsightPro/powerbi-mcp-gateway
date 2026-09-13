"""The server is content-agnostic: everything domain-specific must come from the skills folder."""

import pytest
from mcp.types import TextContent

from powerbi_mcp.config import load_settings
from powerbi_mcp.server import build_server
from powerbi_mcp.skills import Skills

EXPECTED_TOOLS = {
    "list_semantic_models",
    "get_business_context",
    "get_recipe",
    "get_semantic_model_schema",
    "execute_dax",
    "generate_dax",
    "get_report_metadata",
    "recall",
    "remember",
    "forget",
}


def text_of(block: object) -> str:
    """The text of one MCP content block (tool result or prompt message)."""
    assert isinstance(block, TextContent), block
    return block.text


@pytest.fixture
def server(dummy_env):
    return build_server(load_settings())


async def test_server_exposes_tools_prompts_and_resources(server, skills_dir):
    from fastmcp import Client

    skills = Skills.load(skills_dir)
    first_recipe = next(iter(skills.recipes))
    async with Client(server) as client:  # in-memory transport; HTTP auth is not in the path
        assert {t.name for t in await client.list_tools()} >= EXPECTED_TOOLS
        # one prompt per recipe file, nothing else
        assert {p.name for p in await client.list_prompts()} == set(skills.recipes)
        assert {"skill://glossary", "skill://dax-rules", "skill://catalog"} <= {
            str(r.uri) for r in await client.list_resources()
        }
        assert "skill://recipes/{name}" in {str(t.uri_template) for t in await client.list_resource_templates()}

        recipe = await client.call_tool("get_recipe", {"name": first_recipe})
        assert text_of(recipe.content[0]) == skills.recipes[first_recipe]
        context = text_of((await client.call_tool("get_business_context", {})).content[0])
        assert skills.glossary.strip().splitlines()[0] in context
        assert all(name in context for name in skills.recipes)
        prompt = await client.get_prompt(first_recipe, {"period": "FY2026"})
        text = text_of(prompt.messages[0].content)
        assert "FY2026" in text and skills.recipe_title(first_recipe) in text and skills.recipes[first_recipe] in text


async def test_server_instructions_come_from_the_skills_folder(server, skills_dir):
    assert (server.instructions or "") == Skills.load(skills_dir).instructions


async def test_engine_carries_no_domain_vocabulary(dummy_env, tmp_path, monkeypatch):
    """A skills folder with an invented recipe drives prompts and tools without any code change."""
    from fastmcp import Client

    (tmp_path / "recipes").mkdir()
    (tmp_path / "instructions.md").write_text("Warehouse assistant. Start with list_semantic_models.", encoding="utf-8")
    (tmp_path / "glossary.md").write_text("# Warehouse glossary\n[Stock Value] is stock at cost.", encoding="utf-8")
    (tmp_path / "dax-rules.md").write_text("- One EVALUATE per query.", encoding="utf-8")
    (tmp_path / "catalog.yaml").write_text("models: []\n", encoding="utf-8")
    (tmp_path / "recipes" / "stock-aging.md").write_text("# Stock aging by warehouse\nStep 1 ...", encoding="utf-8")
    monkeypatch.setenv("PBIMCP_SKILLS_DIR", str(tmp_path))

    server = build_server(load_settings())
    assert server.instructions and server.instructions.startswith("Warehouse assistant")
    async with Client(server) as client:
        assert {p.name for p in await client.list_prompts()} == {"stock-aging"}
        prompt = await client.get_prompt("stock-aging", {"model_id": "abc"})
        text = text_of(prompt.messages[0].content)
        assert "Stock aging by warehouse" in text and "abc" in text
        context = text_of((await client.call_tool("get_business_context", {})).content[0])
        assert "[Stock Value]" in context and "stock-aging" in context
        with pytest.raises(Exception, match="Unknown recipe"):
            await client.call_tool("get_recipe", {"name": "not-a-recipe"})
