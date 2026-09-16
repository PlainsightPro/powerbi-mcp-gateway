import json
from types import SimpleNamespace

import httpx
import pytest
import yaml

from powerbi_mcp.authoring import initialize, validate_folder
from powerbi_mcp.catalog import Catalog
from powerbi_mcp.evaluation import Case, compare, evaluate
from powerbi_mcp.server import build_server
from powerbi_mcp.skills import Skills

MODEL = "00000000-0000-0000-0000-000000000010"


def test_initialize_and_reject_overwriting(tmp_path):
    target = tmp_path / "private"
    assert initialize(target)["status"] == "passed"
    profile = json.loads((target / "deploy/profile.json").read_text())
    assert profile["SkillsDir"] == "../skills" and not profile["EngineImage"]
    with pytest.raises(ValueError, match="never overwritten"):
        initialize(target)


@pytest.mark.parametrize("image", ["registry/image:latest", "registry/image", "registry/image:v1\nRUN bad"])
def test_initializer_rejects_unpinned_or_invalid_images_before_writes(tmp_path, image):
    target = tmp_path / "new"
    with pytest.raises(ValueError):
        initialize(target, engine_image=image)
    assert not target.exists()


def test_import_schema_validation_and_scoped_context(tmp_path):
    initialize(tmp_path, models=[{"id": MODEL, "name": "Fixture", "workspace": "Workspace"}])
    folder = tmp_path / "skills"
    catalog_path = folder / "catalog.yaml"
    catalog = yaml.safe_load(catalog_path.read_text())
    catalog["models"][0].update(key_measures=["Metric A"], default_date_table="Date A", context_file="models/a.md")
    catalog_path.write_text(yaml.safe_dump(catalog))
    with pytest.raises(ValueError, match="Missing model context"):
        validate_folder(folder)
    (folder / "models/a.md").write_text("Selected definition")
    assert validate_folder(folder)["schema_checked"] is False
    bad = validate_folder(folder, {MODEL: {"Tables": [{"Name": "Object A"}]}})
    assert len(bad["issues"]) == 2 and bad["status"] == "failed"
    good = validate_folder(
        folder, {MODEL: {"Tables": [{"Name": "Date A"}, {"Name": "Object A", "Measures": [{"Name": "Metric A"}]}]}}
    )
    assert good["status"] == "passed"
    skills = Skills.load(folder)
    assert "Selected definition" not in skills.glossary_for()
    assert "Selected definition" in skills.glossary_for(Catalog.load(catalog_path).entries[0])


@pytest.mark.parametrize(
    "contents",
    [
        "models: []\nunknown: true",
        "models: [ {id: x, name: A, workspace: W, typo: bad} ]",
        "models: [ {id: x, name: A, workspace: W}, {id: X, name: B, workspace: W} ]",
    ],
)
def test_invalid_catalog_is_rejected(tmp_path, contents):
    path = tmp_path / "catalog.yaml"
    path.write_text(contents)
    with pytest.raises(ValueError):
        Catalog.load(path)


async def test_public_routes_hide_skills_and_mcp_requires_signin(dummy_env):
    app = build_server().http_app()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost:8000") as client:
        health = await client.get("/healthz")
        assert health.json() == {"status": "ok"}
        page = await client.get("/")
        assert page.status_code == 200 and "http://localhost:8000/mcp" in page.text
        assert "default-src 'none'" in page.headers["content-security-policy"]
        catalog = Catalog.load(__import__("pathlib").Path(__file__).parents[1] / "skills/catalog.yaml")
        assert all(e.id not in page.text and e.name not in page.text for e in catalog.entries)
        response = await client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        assert response.status_code == 401


def fixture_case():
    return Case.model_validate(
        {
            "name": "constant",
            "model_id": MODEL,
            "dax": 'EVALUATE ROW("Value", 1)',
            "expect": {
                "min_rows": 1,
                "max_rows": 1,
                "columns": ["Value"],
                "scalars": [{"column": "Value", "value": 1}],
            },
        }
    )


def test_evaluation_detects_wrong_values_missing_columns_and_incomplete_results():
    case = fixture_case()
    result = {"status": "executed", "executionResult": {"tables": [{"columns": [{"name": "Value"}], "rows": [[1]]}]}}
    assert compare(case, result) == []
    result["executionResult"]["tables"][0]["rows"] = [[2]]
    assert compare(case, result) == ["A scalar value differs from its expected value."]
    case.expect.require_complete = True
    assert "Result completeness was not confirmed." in compare(case, result)
    result["status"] = "failed"
    assert compare(case, result)[0].startswith("Analysis did not finish")


def test_question_evaluation_requires_fixed_reference_date():
    with pytest.raises(ValueError, match="reference_date"):
        Case.model_validate({"name": "question", "model_id": MODEL, "question": "Question", "expect": {}})


async def test_evaluation_continues_after_error_and_never_logs_values():
    calls = []

    class Client:
        async def call_tool(self, name, args):
            calls.append((name, args))
            if len(calls) == 1:
                raise RuntimeError("Private upstream query and business value")
            return SimpleNamespace(
                data={"status": "executed", "tables": [{"columns": [{"name": "Value"}], "rows": [[1]]}]}
            )

    report = await evaluate(Client(), [fixture_case(), fixture_case()])
    assert [r["status"] for r in report["cases"]] == ["failed", "passed"]
    assert "Private upstream" not in json.dumps(report) and "EVALUATE" not in json.dumps(report)
