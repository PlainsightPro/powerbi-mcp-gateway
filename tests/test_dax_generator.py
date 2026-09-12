import json
from types import SimpleNamespace

import pytest

from powerbi_mcp.dax_generator import DaxGenerator, compact_schema, parse_generation, validate_dax

SCHEMA = {
    "schema": {
        "Tables": [
            {"Name": "GL Account", "Measures": [
                {"Name": "EBITDA", "Type": "Double"},
                {"Name": "Indirect Cost", "Description": "Indirect overhead cost as a positive amount.", "Type": "Double"}],
             "Columns": [{"Name": "GL Account Code", "Type": "Text", "FormatString": "0"}]},
            {"Name": "P&L View", "Description": "Disconnected selector.", "Columns": [{"Name": "P&L View", "Type": "Text"}]},
        ],
        "ActiveRelationships": [{"PK": "'GL Account'[GL Account Code]", "FK": "'Financial Transaction'[glAccountCode]"}],
        "CalculationGroups": [{"Name": "# EBITDA CG"}],
    }
}


def test_compact_schema_keeps_names_descriptions_relationships_and_drops_format_strings():
    text = compact_schema(SCHEMA)
    assert "TABLE 'GL Account'" in text
    assert "measure [Indirect Cost] : Double  -- Indirect overhead cost as a positive amount." in text
    assert "TABLE 'P&L View'  -- Disconnected selector." in text
    assert "ACTIVERELATIONSHIPS:" in text and "'GL Account'[GL Account Code] -> 'Financial Transaction'[glAccountCode]" in text
    assert "CALCULATION GROUP '# EBITDA CG'" in text
    assert "FormatString" not in text


def test_parse_generation_accepts_json_and_code_fences():
    g = parse_generation(json.dumps({"dax": " EVALUATE ROW(1) ", "explanation": "x", "assumptions": ["a"]}))
    assert g.dax == "EVALUATE ROW(1)" and g.assumptions == ["a"]
    g2 = parse_generation("Here you go:\n```dax\nEVALUATE ROW(2)\n```")
    assert g2.dax == "EVALUATE ROW(2)"


def test_validate_dax_rejects_multiple_evaluates_and_ddl():
    validate_dax("DEFINE MEASURE 'T'[m] = 1 EVALUATE ROW(1)")
    with pytest.raises(ValueError):
        validate_dax("EVALUATE ROW(1) EVALUATE ROW(2)")
    with pytest.raises(ValueError):
        validate_dax("DEFINE TABLE t = ROW(1) EVALUATE t")


class StubResponses:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(output_text=json.dumps({
            "dax": "EVALUATE SUMMARIZECOLUMNS('GL Account'[GL Account Code], \"Cost\", [Indirect Cost])",
            "explanation": "uses the positive indirect cost measure", "assumptions": ["current year"]}))


async def test_generator_builds_grounded_prompt_and_returns_dax():
    stub = StubResponses()
    gen = DaxGenerator(stub, deployment="gpt-5", rules="- one EVALUATE", reasoning_effort="low")
    out = await gen.generate("waarom stijgen de indirecte kosten", SCHEMA, "Model: Finance", "Glossary text",
                             chat_history=[{"role": "user", "content": "earlier question"}])
    assert out.dax.startswith("EVALUATE") and out.assumptions == ["current year"]
    call = stub.calls[0]
    assert call["model"] == "gpt-5" and call["reasoning"] == {"effort": "low"}
    assert "- one EVALUATE" in call["instructions"] and "Glossary text" in call["instructions"]
    assert "Model: Finance" in call["input"] and "TABLE 'GL Account'" in call["input"]
    assert "earlier question" in call["input"] and "waarom stijgen" in call["input"]
    assert call["text"]["format"]["type"] == "json_schema"


async def test_repair_feeds_back_the_failed_query_and_engine_error():
    stub = StubResponses()
    gen = DaxGenerator(stub, deployment="gpt-5", rules="- rules", reasoning_effort="low")
    out = await gen.repair("vraag", SCHEMA, "notes", "glossary",
                           failed_dax="EVALUATE ROW(1']", error="The syntax for ']' is incorrect.")
    assert out.dax.startswith("EVALUATE")
    user = stub.calls[0]["input"]
    assert "Previous attempt" in user and "EVALUATE ROW(1']" in user and "syntax for ']'" in user
