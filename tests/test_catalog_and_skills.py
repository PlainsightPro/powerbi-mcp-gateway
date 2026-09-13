"""Catalog and skills loading, asserted on structure so the example content can change freely."""

import pytest

from powerbi_mcp.catalog import Catalog
from powerbi_mcp.fabric import SemanticModelRef
from powerbi_mcp.skills import Skills


def test_catalog_entries_and_notes(skills_dir):
    catalog = Catalog.load(skills_dir / "catalog.yaml")
    assert catalog.entries, "the example catalog must curate at least one model"
    entry = catalog.entries[0]
    assert catalog.get(entry.id.upper()) is entry  # id matching is case-insensitive
    notes = catalog.notes_for(entry.id)
    assert entry.name in notes
    assert all(f"[{measure}]" in notes for measure in entry.key_measures)
    assert catalog.notes_for("not-a-known-id") == ""


def test_merge_only_returns_accessible_models_curated_first(skills_dir):
    catalog = Catalog.load(skills_dir / "catalog.yaml")
    curated = catalog.entries[0]
    hidden = catalog.entries[1] if len(catalog.entries) > 1 else None
    accessible = [
        SemanticModelRef(
            id="ffffffff-0000-0000-0000-000000000000",
            name="Scratch model",
            workspace_id="w1",
            workspace_name="Sandbox",
            description="ad hoc",
        ),
        SemanticModelRef(id=curated.id, name=curated.name, workspace_id="w2", workspace_name=curated.workspace),
    ]
    rows = catalog.merge(accessible)
    assert [r["id"] for r in rows] == [curated.id, "ffffffff-0000-0000-0000-000000000000"]
    assert rows[0]["curated"] and rows[0]["key_measures"] == curated.key_measures
    assert not rows[1]["curated"] and rows[1]["description"] == "ad hoc"
    if hidden:  # a curated model the user cannot reach never shows up
        assert all(r["id"] != hidden.id for r in rows)


def test_skills_load_everything(skills_dir):
    skills = Skills.load(skills_dir)
    on_disk = {p.stem for p in (skills_dir / "recipes").glob("*.md")}
    assert set(skills.recipes) == on_disk and on_disk
    assert "list_semantic_models" in skills.instructions
    assert skills.glossary.strip() and "EVALUATE" in skills.dax_rules
    index = skills.recipe_index()
    assert all(f"- {name}: {skills.recipe_title(name)}" in index for name in on_disk)


def test_skills_folder_without_recipes(tmp_path):
    for name, text in {
        "instructions.md": "i",
        "glossary.md": "g",
        "dax-rules.md": "r",
        "catalog.yaml": "models: []\n",
    }.items():
        (tmp_path / name).write_text(text, encoding="utf-8")
    skills = Skills.load(tmp_path)
    assert skills.recipes == {} and skills.recipe_index() == "(no recipes)"
    assert skills.recipe_title("missing") == "missing"


def test_catalog_rejects_unknown_keys_and_names_the_entry(tmp_path):
    path = tmp_path / "catalog.yaml"
    path.write_text(
        "models:\n  - id: 11111111-1111-1111-1111-111111111111\n    name: Widgets\n    workspace: W\n"
        "    key_measure: [X]\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match=r"model #1 \(Widgets\).*key_measure.*Extra inputs"):
        Catalog.load(path)
    path.write_text("models:\n  - id: abc\n", encoding="utf-8")
    with pytest.raises(ValueError, match=r"model #1 \(abc\).*name.*required"):
        Catalog.load(path)
    path.write_text("- not a mapping\n", encoding="utf-8")
    with pytest.raises(ValueError, match="expected a mapping"):
        Catalog.load(path)
    path.write_text("", encoding="utf-8")
    assert Catalog.load(path).entries == []


def test_recipe_names_are_lower_cased_and_validated(tmp_path):
    for name, text in {"instructions.md": "i", "glossary.md": "g", "dax-rules.md": "r", "catalog.yaml": ""}.items():
        (tmp_path / name).write_text(text, encoding="utf-8")
    recipes = tmp_path / "recipes"
    recipes.mkdir()
    (recipes / "Monthly-Trend.md").write_text("# Trend", encoding="utf-8")
    assert list(Skills.load(tmp_path).recipes) == ["monthly-trend"]
    (recipes / "bad name.md").write_text("# Bad", encoding="utf-8")
    with pytest.raises(ValueError, match=r"'bad name\.md' is not a valid recipe name"):
        Skills.load(tmp_path)
