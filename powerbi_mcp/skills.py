"""The skills folder: instructions, glossary, DAX rules and recipes, loaded once at startup."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .recipes import Recipe

REQUIRED_FILES = ("instructions.md", "glossary.md", "dax-rules.md", "catalog.yaml")
RECIPE_NAME = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")


@dataclass
class Skills:
    instructions: str
    glossary: str
    dax_rules: str
    recipes: dict[str, str] = field(default_factory=dict)
    workflows: dict[str, Recipe] = field(default_factory=dict)
    model_contexts: dict[str, str] = field(default_factory=dict)

    @classmethod
    def load(cls, skills_dir: Path) -> Skills:
        def read(name: str) -> str:
            return (skills_dir / name).read_text(encoding="utf-8")

        recipes_dir = skills_dir / "recipes"
        seen = set()
        for p in sorted(recipes_dir.iterdir()) if recipes_dir.exists() else []:
            if p.suffix not in (".md", ".yaml"):
                continue
            key = (p.stem.lower(), p.suffix)
            if not RECIPE_NAME.fullmatch(p.stem.lower()):
                raise ValueError(f"recipe file name '{p.name}' is not a valid recipe name")
            if key in seen:
                raise ValueError(f"recipe '{p.stem.lower()}' exists twice in {recipes_dir}")
            seen.add(key)
        recipes = (
            {p.stem.lower(): p.read_text(encoding="utf-8") for p in sorted(recipes_dir.glob("*.md"))}
            if recipes_dir.exists()
            else {}
        )
        workflows = {}
        for p in sorted(recipes_dir.glob("*.yaml")):
            workflows[p.stem.lower()] = Recipe.model_validate(yaml.safe_load(p.read_text(encoding="utf-8")))
            recipes.setdefault(
                p.stem.lower(),
                f"# {workflows[p.stem.lower()].title}\n\n{workflows[p.stem.lower()].description}\n\n"
                f"Execute with run_recipe(name='{p.stem}', model_id=..., parameters=...).",
            )
        contexts = {"models/" + p.name: p.read_text(encoding="utf-8") for p in (skills_dir / "models").glob("*.md")}
        return cls(
            instructions=read("instructions.md"),
            glossary=read("glossary.md"),
            dax_rules=read("dax-rules.md"),
            recipes=recipes,
            workflows=workflows,
            model_contexts=contexts,
        )

    def validate_catalog(self, catalog) -> None:
        from .dax_generator import validate_dax

        for entry in catalog.entries:
            missing = set(entry.recipes) - self.recipes.keys()
            if missing:
                raise ValueError(f"Catalog model {entry.name} references unknown recipes: {sorted(missing)}")
            for name in entry.recipes:
                workflow = self.workflows.get(name)
                if workflow and workflow.models and entry.id.lower() not in [m.lower() for m in workflow.models]:
                    raise ValueError(f"Catalog model {entry.name} references incompatible executable recipe: {name}")
            if entry.context_file and entry.context_file not in self.model_contexts:
                raise ValueError(f"Missing model context file: {entry.context_file}; use models/<name>.md")
            if entry.freshness_query:
                validate_dax(entry.freshness_query)

    def glossary_for(self, entry=None) -> str:
        scoped = self.model_contexts.get(entry.context_file, "") if entry and entry.context_file else ""
        return self.glossary.strip() + ("\n\n## Selected model context\n" + scoped if scoped else "")

    def recipe_title(self, name: str) -> str:
        """The recipe's first Markdown heading, or its file name when it has none."""
        text = self.recipes.get(name, "")
        return next((ln.lstrip("# ").strip() for ln in text.splitlines() if ln.startswith("#")), name)

    def recipe_index(self) -> str:
        if not self.recipes:
            return "(no recipes)"
        return "\n".join(f"- {name}: {self.recipe_title(name)}" for name in self.recipes)
