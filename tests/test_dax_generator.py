import json
from types import SimpleNamespace

import pytest

from powerbi_mcp.dax_generator import DaxGenerator, compact_schema, parse_generation, validate_dax

SCHEMA = {
    "schema": {
        "Tables": [
            {
                "Name": "Product",
                "Measures": [
                    {"Name": "Units", "Type": "Int64"},
                    {
                        "Name": "Return Rate",
                        "Description": "Returned units over units sold, a fraction.",
                        "Type": "Double",
                    },
                ],
                "Columns": [{"Name": "Product Code", "Type": "Text", "FormatString": "0"}],
            },
            {
                "Name": "Season Selector",
                "Description": "Disconnected selector.",
                "Columns": [{"Name": "Season", "Type": "Text"}],
            },
        ],
        "ActiveRelationships": [{"PK": "'Product'[Product Code]", "FK": "'Sales'[productCode]"}],
        "CalculationGroups": [{"Name": "Time Intelligence"}],
    }
}


def test_compact_schema_keeps_names_descriptions_relationships_and_drops_format_strings():
    text = compact_schema(SCHEMA)
    assert "TABLE 'Product'" in text
    assert "measure [Return Rate] : Double  -- Returned units over units sold, a fraction." in text
    assert "TABLE 'Season Selector'  -- Disconnected selector." in text
    assert "ACTIVERELATIONSHIPS:" in text and "'Product'[Product Code] -> 'Sales'[productCode]" in text
    assert "CALCULATION GROUP 'Time Intelligence'" in text
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


def test_validate_dax_matches_whole_words_only():
    validate_dax('EVALUATE ROW("x", [Reevaluated Score])')  # a name merely containing the letters is fine
    validate_dax('EVALUATE ROW("s", "re-evaluated")')
    with pytest.raises(ValueError, match="DEFINE TABLE"):
        validate_dax("DEFINE   TABLE t = ROW(1) EVALUATE t")  # any whitespace between the keywords
    with pytest.raises(ValueError, match="DEFINE COLUMN"):
        validate_dax("define column 'T'[c] = 1 evaluate 'T'")  # case-insensitive


class StubResponses:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(
            output_text=json.dumps(
                {
                    "dax": "EVALUATE SUMMARIZECOLUMNS('Product'[Product Code], \"Rate\", [Return Rate])",
                    "explanation": "uses the model's return-rate measure",
                    "assumptions": ["current year"],
                }
            )
        )


async def test_generator_builds_grounded_prompt_and_returns_dax():
    stub = StubResponses()
    gen = DaxGenerator(stub, deployment="gpt-5", rules="- one EVALUATE", reasoning_effort="low")
    out = await gen.generate(
        "which products are returned most often",
        SCHEMA,
        "Model: Contoso Sales",
        "Glossary text",
        chat_history=[{"role": "user", "content": "earlier question"}],
    )
    assert out.dax.startswith("EVALUATE") and out.assumptions == ["current year"]
    call = stub.calls[0]
    assert call["model"] == "gpt-5" and call["reasoning"] == {"effort": "low"}
    assert "- one EVALUATE" in call["instructions"] and "Glossary text" in call["instructions"]
    assert "Model: Contoso Sales" in call["input"] and "TABLE 'Product'" in call["input"]
    assert "earlier question" in call["input"] and "returned most often" in call["input"]
    assert call["text"]["format"]["type"] == "json_schema"


async def test_repair_feeds_back_the_failed_query_and_engine_error():
    stub = StubResponses()
    gen = DaxGenerator(stub, deployment="gpt-5", rules="- rules", reasoning_effort="low")
    out = await gen.repair(
        "return rate by product",
        SCHEMA,
        "notes",
        "glossary",
        failed_dax="EVALUATE ROW(1']",
        error="The syntax for ']' is incorrect.",
    )
    assert out.dax.startswith("EVALUATE")
    user = stub.calls[0]["input"]
    assert "Previous attempt" in user and "EVALUATE ROW(1']" in user and "syntax for ']'" in user


def test_prompt_carries_engine_rules_for_formatted_values_and_future_dates():
    """Two hosted-server behaviours every deployment meets: format strings turn measures into text,
    and date tables can extend past today. The engine states both; skills need not."""
    gen = DaxGenerator(StubResponses(), deployment="gpt-5", rules="- rules", reasoning_effort="low")
    instructions, _ = gen.build_prompt("q", "schema", "", "glossary")
    assert "+ 0" in instructions and "format string" in instructions and "alias" in instructions
    assert "future-dated" in instructions and "TODAY()" in instructions


async def test_truncated_model_answer_is_reported_as_such():
    class Truncating:
        async def create(self, **kwargs):
            details = SimpleNamespace(reason="max_output_tokens")
            return SimpleNamespace(status="incomplete", incomplete_details=details, output_text='{"dax": "EVAL')

    gen = DaxGenerator(Truncating(), deployment="gpt-5", rules="- rules", reasoning_effort="low", max_output_tokens=123)
    with pytest.raises(ValueError, match=r"cut off \(max_output_tokens\) at max_output_tokens=123"):
        await gen.generate("q", SCHEMA, "", "glossary")


async def test_max_output_tokens_is_passed_to_the_model():
    stub = StubResponses()
    gen = DaxGenerator(stub, deployment="gpt-5", rules="- rules", reasoning_effort="low", max_output_tokens=777)
    await gen.generate("q", SCHEMA, "", "glossary")
    assert stub.calls[0]["max_output_tokens"] == 777
