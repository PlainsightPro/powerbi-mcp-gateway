"""The skills folder: instructions, glossary, DAX rules and recipes, loaded once at startup."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Skills:
    instructions: str
    glossary: str
    dax_rules: str
    recipes: dict[str, str] = field(default_factory=dict)

    @classmethod
    def load(cls, skills_dir: Path) -> "Skills":
        def read(name: str) -> str:
            return (skills_dir / name).read_text(encoding="utf-8")

        recipes_dir = skills_dir / "recipes"
        recipes = {
            p.stem: p.read_text(encoding="utf-8")
            for p in sorted(recipes_dir.glob("*.md"))
        } if recipes_dir.exists() else {}
        return cls(
            instructions=read("instructions.md"),
            glossary=read("glossary.md"),
            dax_rules=read("dax-rules.md"),
            recipes=recipes,
        )

    def recipe_title(self, name: str) -> str:
        """The recipe's first Markdown heading, or its file name when it has none."""
        text = self.recipes.get(name, "")
        return next((ln.lstrip("# ").strip() for ln in text.splitlines() if ln.startswith("#")), name)

    def recipe_index(self) -> str:
        if not self.recipes:
            return "(no recipes)"
        return "\n".join(f"- {name}: {self.recipe_title(name)}" for name in self.recipes)
