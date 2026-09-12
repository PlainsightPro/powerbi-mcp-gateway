"""Curated semantic-model catalog (skills/catalog.yaml) merged with what the user can actually reach."""

from __future__ import annotations

from dataclasses import dataclass, field, fields
from pathlib import Path

import yaml

from .fabric import SemanticModelRef


@dataclass
class CatalogEntry:
    id: str
    name: str
    workspace: str
    description: str = ""
    data_scope: str = ""
    default_date_table: str = ""
    key_measures: list[str] = field(default_factory=list)
    recipes: list[str] = field(default_factory=list)
    notes: str = ""


_ENTRY_FIELDS = {f.name for f in fields(CatalogEntry)}


class Catalog:
    def __init__(self, entries: list[CatalogEntry]) -> None:
        self._by_id = {e.id.lower(): e for e in entries}

    @classmethod
    def load(cls, path: Path) -> Catalog:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        entries = [
            CatalogEntry(**{k: v for k, v in item.items() if k in _ENTRY_FIELDS}) for item in raw.get("models", [])
        ]
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
