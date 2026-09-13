"""The skills folder: instructions, glossary, DAX rules and recipes, loaded once at startup."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

REQUIRED_FILES = ("instructions.md", "glossary.md", "dax-rules.md", "catalog.yaml")
RECIPE_NAME = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")


@dataclass
class Skills:
    instructions: str
    glossary: str
    dax_rules: str
    recipes: dict[str, str] = field(default_factory=dict)

    @classmethod
    def load(cls, skills_dir: Path) -> Skills:
        def read(name: str) -> str:
            return (skills_dir / name).read_text(encoding="utf-8")

        return cls(
            instructions=read("instructions.md"),
            glossary=read("glossary.md"),
            dax_rules=read("dax-rules.md"),
            recipes=load_recipes(skills_dir / "recipes"),
        )

    def recipe_title(self, name: str) -> str:
        """The recipe's first Markdown heading, or its file name when it has none."""
        text = self.recipes.get(name, "")
        return next((ln.lstrip("# ").strip() for ln in text.splitlines() if ln.startswith("#")), name)

    def recipe_index(self) -> str:
        if not self.recipes:
            return "(no recipes)"
        return "\n".join(f"- {name}: {self.recipe_title(name)}" for name in self.recipes)


def load_recipes(recipes_dir: Path) -> dict[str, str]:
    """recipes/<name>.md by name. The name is the lower-cased file stem and becomes an MCP prompt
    name and a resource path, so it must be letters, digits and single hyphens."""
    if not recipes_dir.is_dir():
        return {}
    recipes: dict[str, str] = {}
    for path in sorted(recipes_dir.glob("*.md")):
        name = path.stem.lower()
        if not RECIPE_NAME.match(name):
            raise ValueError(
                f"recipe file name '{path.name}' is not a valid recipe name: use lower-case letters, digits and "
                "hyphens (for example 'monthly-trend.md')"
            )
        if name in recipes:
            raise ValueError(f"recipe '{name}' exists twice in {recipes_dir} (file names differ only in case)")
        recipes[name] = path.read_text(encoding="utf-8")
    return recipes
