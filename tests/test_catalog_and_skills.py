from powerbi_mcp.catalog import Catalog
from powerbi_mcp.fabric import SemanticModelRef
from powerbi_mcp.skills import Skills

FINANCE = "11111111-1111-1111-1111-111111111111"


def test_catalog_loads_curated_models(skills_dir):
    catalog = Catalog.load(skills_dir / "catalog.yaml")
    assert {e.name for e in catalog.entries} >= {"Finance", "Sales"}
    finance = catalog.get(FINANCE.upper())  # id matching is case-insensitive
    assert finance is not None
    assert "indirect-cost-analysis" in finance.recipes
    notes = catalog.notes_for(FINANCE)
    assert "[EBITDA]" in notes and "'Date'" in notes


def test_merge_only_returns_accessible_models_curated_first(skills_dir):
    catalog = Catalog.load(skills_dir / "catalog.yaml")
    accessible = [
        SemanticModelRef(id="ffffffff-0000-0000-0000-000000000000", name="Scratch model",
                         workspace_id="w1", workspace_name="Sandbox", description="ad hoc"),
        SemanticModelRef(id=FINANCE, name="Finance", workspace_id="w2", workspace_name="Finance"),
    ]
    rows = catalog.merge(accessible)
    assert [r["id"] for r in rows] == [FINANCE, "ffffffff-0000-0000-0000-000000000000"]
    assert rows[0]["curated"] and rows[0]["key_measures"]
    assert not rows[1]["curated"] and rows[1]["description"] == "ad hoc"
    # a curated model the user cannot reach never shows up
    assert all(r["name"] != "Sales" for r in rows)


def test_skills_load_everything(skills_dir):
    skills = Skills.load(skills_dir)
    assert "list_semantic_models" in skills.instructions
    assert "[Indirect Cost]" in skills.glossary
    assert "EVALUATE" in skills.dax_rules
    assert {"indirect-cost-analysis", "ebitda-bridge"} <= set(skills.recipes)
    assert "indirect-cost-analysis:" in skills.recipe_index()
