import json
from types import SimpleNamespace
from typing import Any, cast

import pytest

from powerbi_mcp.dax_generator import DaxGenerator, compact_schema, parse_generation, validate_dax

SCHEMA = {
    "schema": {
        "Tables": [
            {
                "Name": "Object A",
                "Measures": [
                    {"Name": "Metric A", "Type": "Double"},
                    {"Name": "Metric B", "Description": "Invented metric description.", "Type": "Double"},
                ],
                "Columns": [{"Name": "Object Key", "Type": "Text", "FormatString": "0"}],
            },
            {
                "Name": "Selector A",
                "Description": "Disconnected selector.",
                "Columns": [{"Name": "Selector A", "Type": "Text"}],
            },
        ],
        "ActiveRelationships": [{"PK": "'Object A'[Object Key]", "FK": "'Object B'[objectKey]"}],
        "CalculationGroups": [{"Name": "Calculation A"}],
    }
}


def test_compact_schema_keeps_names_descriptions_relationships_and_preserves_format_strings():
    text = compact_schema(SCHEMA)
    assert "TABLE 'Object A'" in text
    assert "measure [Metric B] : Double  -- Invented metric description." in text
    assert "TABLE 'Selector A'  -- Disconnected selector." in text
    assert "ACTIVERELATIONSHIPS:" in text and "'Object A'[Object Key] -> 'Object B'[objectKey]" in text
    assert "CALCULATION GROUP 'Calculation A'" in text
    assert '"FormatString": "0"' in text


def test_parse_generation_accepts_json_and_code_fences():
    g = parse_generation(json.dumps({"dax": " EVALUATE ROW(1) ", "explanation": "x", "assumptions": ["a"]}))
    assert g.dax == "EVALUATE ROW(1)" and g.assumptions == ["a"]


@pytest.mark.parametrize("status,kind", [(401, "generation"), (429, "throttled"), (503, "unavailable")])
async def test_foundry_failures_have_generation_specific_recovery(status, kind):
    import httpx
    from openai import APIStatusError

    from powerbi_mcp.errors import GatewayError

    class Client:
        async def create(self, **kwargs):
            raise APIStatusError(
                "private provider body",
                response=cast(Any, httpx.Response(status, request=httpx.Request("POST", "https://fixture.test"))),
                body={"private": "value"},
            )

    generator = DaxGenerator(Client(), "fixture", "rules")
    with pytest.raises(GatewayError) as error:
        await generator.generate("Question", SCHEMA, "", "")
    assert error.value.kind == kind and "private" not in str(error.value)


async def test_invalid_generated_query_is_not_blamed_on_user_arguments():
    from powerbi_mcp.errors import GatewayError

    class Client:
        async def create(self, **kwargs):
            return SimpleNamespace(status="completed", output_text='{"dax":"invalid"}')

    with pytest.raises(GatewayError) as error:
        await DaxGenerator(Client(), "fixture", "rules").generate("Question", SCHEMA, "", "")
    assert error.value.kind == "generation"
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
        return SimpleNamespace(
            output_text=json.dumps(
                {
                    "dax": "EVALUATE SUMMARIZECOLUMNS('Object A'[Object Key], \"Value\", [Metric B])",
                    "explanation": "uses the fixture metric",
                    "assumptions": ["current year"],
                }
            )
        )


async def test_generator_builds_grounded_prompt_and_returns_dax():
    stub = StubResponses()
    gen = DaxGenerator(stub, deployment="gpt-5", rules="- one EVALUATE", reasoning_effort="low")
    out = await gen.generate(
        "why did the fixture metric change",
        SCHEMA,
        "Model: Fixture model",
        "Glossary text",
        chat_history=[{"role": "user", "content": "earlier question"}],
    )
    assert out.dax.startswith("EVALUATE") and out.assumptions == ["current year"]
    call = stub.calls[0]
    assert call["model"] == "gpt-5" and call["reasoning"] == {"effort": "low"}
    assert "- one EVALUATE" in call["instructions"] and "Glossary text" in call["instructions"]
    assert "Model: Fixture model" in call["input"] and "TABLE 'Object A'" in call["input"]
    assert "earlier question" in call["input"] and "why did" in call["input"]
    assert call["text"]["format"]["type"] == "json_schema"


async def test_repair_feeds_back_the_failed_query_and_engine_error():
    stub = StubResponses()
    gen = DaxGenerator(stub, deployment="gpt-5", rules="- rules", reasoning_effort="low")
    out = await gen.repair(
        "vraag", SCHEMA, "notes", "glossary", failed_dax="EVALUATE ROW(1']", error="The syntax for ']' is incorrect."
    )
    assert out.dax.startswith("EVALUATE")
    user = stub.calls[0]["input"]
    assert "Previous attempt" in user and "EVALUATE ROW(1']" in user and "syntax for ']'" in user
