"""Catalog and skills loading, asserted on structure so the example content can change freely."""

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
