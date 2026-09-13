"""Curated semantic-model catalog (skills/catalog.yaml) merged with what the user can actually reach."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, ValidationError

from .fabric import SemanticModelRef


class CatalogEntry(BaseModel):
    """One curated model. Unknown keys are an error, so a typo cannot silently drop a field."""

    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    workspace: str
    description: str = ""
    data_scope: str = ""
    default_date_table: str = ""
    key_measures: list[str] = []
    recipes: list[str] = []
    notes: str = ""


class Catalog:
    def __init__(self, entries: list[CatalogEntry]) -> None:
        self._by_id = {e.id.lower(): e for e in entries}

    @classmethod
    def load(cls, path: Path) -> Catalog:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if not isinstance(raw, dict) or not isinstance(raw.get("models", []), list):
            raise ValueError(f"{path}: expected a mapping with a 'models' list")
        entries: list[CatalogEntry] = []
        for index, item in enumerate(raw.get("models") or [], start=1):
            label = (item.get("name") or item.get("id") or "?") if isinstance(item, dict) else "?"
            try:
                entries.append(CatalogEntry.model_validate(item))
            except ValidationError as exc:
                problems = "; ".join(
                    f"{'.'.join(str(p) for p in e['loc']) or 'entry'}: {e['msg']}" for e in exc.errors()
                )
                raise ValueError(f"{path}: model #{index} ({label}): {problems}") from None
        return cls(entries)

    @property
    def entries(self) -> list[CatalogEntry]:
        return list(self._by_id.values())

    def get(self, model_id: str) -> CatalogEntry | None:
        return self._by_id.get(model_id.lower())

    def notes_for(self, model_id: str) -> str:
        """Business context for one model, as prompt text for the DAX generator."""
        entry = self.get(model_id)
        if not entry:
            return ""
        parts = [f"Model: {entry.name} ({entry.workspace})"]
        if entry.description:
            parts.append(entry.description.strip())
        if entry.data_scope:
            parts.append(f"Data scope: {entry.data_scope}")
        if entry.default_date_table:
            parts.append(f"Default date table: '{entry.default_date_table}'")
        if entry.key_measures:
            parts.append("Key measures: " + ", ".join(f"[{m}]" for m in entry.key_measures))
        if entry.notes:
            parts.append(entry.notes.strip())
        return "\n".join(parts)

    def merge(self, accessible: list[SemanticModelRef]) -> list[dict]:
        """Only models the user can reach are returned; curated ones come first and carry context."""
        rows: list[dict] = []
        for ref in accessible:
            entry = self.get(ref.id)
            rows.append(
                {
                    "id": ref.id,
                    "name": entry.name if entry else ref.name,
                    "workspace": ref.workspace_name,
                    "curated": entry is not None,
                    "description": (entry.description if entry else ref.description).strip(),
                    "data_scope": entry.data_scope if entry else "",
                    "default_date_table": entry.default_date_table if entry else "",
                    "key_measures": list(entry.key_measures) if entry else [],
                    "recipes": list(entry.recipes) if entry else [],
                }
            )
        rows.sort(key=lambda r: (not r["curated"], r["workspace"].lower(), r["name"].lower()))
        return rows
