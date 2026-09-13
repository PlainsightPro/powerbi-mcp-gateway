"""Check a skills folder before it is built into an image.

    python -m powerbi_mcp --check-skills [DIR]

Errors (exit code 1) are things the server would refuse or silently ignore: a missing file, a
catalog entry with an unknown key, a recipe reference with no file behind it. Warnings point at
content that will work but probably not as intended, such as a key measure the glossary never
mentions (the glossary is what grounds every generated query).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from .catalog import Catalog
from .skills import REQUIRED_FILES, Skills

MODEL_ID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE)


@dataclass
class Report:
    skills_dir: Path
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def render(self) -> str:
        lines = [f"Skills folder: {self.skills_dir}"]
        lines += [f"ERROR    {e}" for e in self.errors]
        lines += [f"warning  {w}" for w in self.warnings]
        lines.append("OK" if self.ok else f"{len(self.errors)} error(s)")
        return "\n".join(lines)


def check_skills(skills_dir: Path) -> Report:
    report = Report(skills_dir)
    if not skills_dir.is_dir():
        report.errors.append(f"not a folder: {skills_dir}")
        return report
    for name in REQUIRED_FILES:
        if not (skills_dir / name).is_file():
            report.errors.append(f"missing {name}")
    if report.errors:
        return report

    skills = catalog = None
    try:
        skills = Skills.load(skills_dir)
    except Exception as exc:  # the loader's own message names the file
        report.errors.append(str(exc))
    try:
        catalog = Catalog.load(skills_dir / "catalog.yaml")
    except Exception as exc:
        report.errors.append(str(exc))
    if skills is None or catalog is None:
        return report

    for name, text in (
        ("instructions.md", skills.instructions),
        ("glossary.md", skills.glossary),
        ("dax-rules.md", skills.dax_rules),
    ):
        if not text.strip():
            report.errors.append(f"{name} is empty")
    if not skills.recipes:
        report.warnings.append("no recipes: recipes/*.md become one prompt and one resource each")
    for name in skills.recipes:
        if skills.recipe_title(name) == name:
            report.warnings.append(f"recipes/{name}.md has no '#' heading; the file name will be its title")
    if not catalog.entries:
        report.warnings.append("catalog.yaml curates no models: list_semantic_models will show only uncurated rows")
    for entry in catalog.entries:
        for recipe in entry.recipes:
            if recipe not in skills.recipes:
                report.errors.append(
                    f"catalog.yaml: model '{entry.name}' lists recipe '{recipe}' but recipes/{recipe}.md does not exist"
                )
        if not MODEL_ID.match(entry.id):
            report.warnings.append(f"catalog.yaml: model '{entry.name}' has id '{entry.id}', not a semantic model id")
        for measure in entry.key_measures:
            if f"[{measure}]" not in skills.glossary:
                report.warnings.append(
                    f"glossary.md never mentions [{measure}], a key measure of '{entry.name}' "
                    "(the glossary grounds every generated query)"
                )
    return report


def main(skills_dir: Path) -> int:
    report = check_skills(skills_dir)
    print(report.render())
    return 0 if report.ok else 1
