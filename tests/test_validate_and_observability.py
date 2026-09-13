"""The skills checker and the tool-call log line."""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError

from powerbi_mcp.__main__ import main as cli
from powerbi_mcp.config import load_settings
from powerbi_mcp.observability import describe_arguments
from powerbi_mcp.server import build_server
from powerbi_mcp.validate import check_skills


def test_example_skills_pass_the_check(skills_dir):
    report = check_skills(skills_dir)
    assert report.ok, report.render()


def test_check_reports_missing_files_and_dangling_recipe_references(tmp_path, skills_dir):
    assert "missing instructions.md" in check_skills(tmp_path).render()
    assert "not a folder" in check_skills(tmp_path / "nope").render()

    broken = tmp_path / "broken"
    shutil.copytree(skills_dir, broken)
    catalog = broken / "catalog.yaml"
    catalog.write_text(
        catalog.read_text(encoding="utf-8").replace(
            "recipes: [monthly-trend, top-movers]", "recipes: [no-such-recipe]"
        ),
        encoding="utf-8",
    )
    (broken / "recipes" / "Untitled Draft.md").write_text("no heading", encoding="utf-8")
    report = check_skills(broken)
    assert not report.ok
    assert any("'Untitled Draft.md' is not a valid recipe name" in e for e in report.errors), report.errors


def test_check_warns_about_key_measures_the_glossary_never_mentions(tmp_path):
    for name, text in {"instructions.md": "i", "glossary.md": "[Known] is known.", "dax-rules.md": "r"}.items():
        (tmp_path / name).write_text(text, encoding="utf-8")
    (tmp_path / "catalog.yaml").write_text(
        "models:\n  - id: 11111111-1111-1111-1111-111111111111\n    name: M\n    workspace: W\n"
        "    key_measures: [Known, Unknown]\n    recipes: [gone]\n",
        encoding="utf-8",
    )
    report = check_skills(tmp_path)
    assert report.errors == ["catalog.yaml: model 'M' lists recipe 'gone' but recipes/gone.md does not exist"]
    assert any("[Unknown]" in w for w in report.warnings) and not any("[Known]" in w for w in report.warnings)
    assert any("no recipes" in w for w in report.warnings)


def test_cli_check_skills_exit_codes(skills_dir, tmp_path, capsys):
    assert cli(["--check-skills", str(skills_dir)]) == 0
    assert "OK" in capsys.readouterr().out
    assert cli(["--check-skills", str(tmp_path)]) == 1
    assert "ERROR" in capsys.readouterr().out


def test_describe_arguments_keeps_ids_and_counts_but_never_query_text():
    line = describe_arguments(
        {"model_id": "m-1", "dax_queries": ["EVALUATE secret"], "question": "why?", "max_rows": 5, "chat_history": [{}]}
    )
    assert line == "model_id=m-1 dax_queries=<1> question=<4> max_rows=5 chat_history=<1>"
    assert "secret" not in line and "why" not in line


async def test_tool_calls_are_logged_with_outcome_but_without_content(dummy_env, caplog):
    server = build_server(load_settings())
    with caplog.at_level(logging.INFO, logger="powerbi_mcp.tools"):
        async with Client(server) as client:
            await client.call_tool("get_business_context", {})
            with pytest.raises(ToolError):
                await client.call_tool("get_recipe", {"name": "not-a-recipe"})
    records = [r for r in caplog.records if r.name == "powerbi_mcp.tools"]
    assert [r.levelno for r in records] == [logging.INFO, logging.WARNING]
    ok, failed = (r.getMessage() for r in records)
    assert ok.startswith("tool=get_business_context user=") and "outcome=ok" in ok
    assert "tool=get_recipe" in failed and "outcome=error" in failed and "Unknown recipe" in failed


def test_settings_validate_reasoning_effort(dummy_env, monkeypatch):
    monkeypatch.setenv("PBIMCP_FOUNDRY_REASONING_EFFORT", "extreme")
    with pytest.raises(Exception, match="foundry_reasoning_effort"):
        load_settings()


def test_every_setting_is_documented_in_env_example():
    example = (Path(__file__).resolve().parent.parent / ".env.example").read_text(encoding="utf-8")
    from powerbi_mcp.config import Settings

    missing = [f"PBIMCP_{name.upper()}" for name in Settings.model_fields if f"PBIMCP_{name.upper()}" not in example]
    assert not missing, f".env.example does not mention {missing}"
