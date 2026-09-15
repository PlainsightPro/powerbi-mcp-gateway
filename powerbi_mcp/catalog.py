"""Curated semantic-model catalog (skills/catalog.yaml) merged with what the user can actually reach."""

from __future__ import annotations

from dataclasses import field
from pathlib import Path

import yaml
from pydantic import ConfigDict
from pydantic.dataclasses import dataclass

from .schema import terms

from .fabric import SemanticModelRef


@dataclass(config=ConfigDict(extra="forbid"))
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
    workspace_id: str = ""
    aliases: list[str] = field(default_factory=list)
    topics: list[str] = field(default_factory=list)
    context_file: str | None = None
    calendar: str = "Calendar year; use the reference date to resolve relative periods."
    freshness_query: str | None = None


class Catalog:
    def __init__(self, entries: list[CatalogEntry]) -> None:
        self._by_id = {e.id.lower(): e for e in entries}
        if len(self._by_id) != len(entries):
            raise ValueError("Catalog contains duplicate model ids.")

    @classmethod
    def load(cls, path: Path) -> "Catalog":
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if not isinstance(raw, dict) or set(raw) - {"models"} or not isinstance(raw.get("models", []), list):
            raise ValueError("Catalog must contain a models list and no unknown top-level keys.")
        entries = [CatalogEntry(**item) for item in raw.get("models", [])]
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
        parts.append("Calendar: " + entry.calendar)
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
                    "workspace_id": ref.workspace_id,
                    "curated": entry is not None,
                    "description": (entry.description if entry else ref.description).strip(),
                    "data_scope": entry.data_scope if entry else "",
                    "default_date_table": entry.default_date_table if entry else "",
                    "key_measures": list(entry.key_measures) if entry else [],
                    "recipes": list(entry.recipes) if entry else [],
                    "aliases": list(entry.aliases) if entry else [],
                    "topics": list(entry.topics) if entry else [],
                    "query_access": "unchecked",
                }
            )
        rows.sort(key=lambda r: (not r["curated"], r["workspace"].lower(), r["name"].lower()))
        return rows

    @staticmethod
    def search(rows: list[dict], query: str = "", workspace: str = "") -> list[dict]:
        words = terms(query)
        matches = []
        for row in rows:
            if workspace and workspace.casefold() not in (row["workspace"].casefold(), row.get("workspace_id", "").casefold()):
                continue
            labels = [row["id"], row["name"], *row.get("aliases", [])]
            exact = bool(query and query.casefold().strip() in [s.casefold() for s in labels])
            searchable = " ".join([row["name"], row["description"], *row.get("aliases", []),
                                   *row.get("topics", []), *row.get("key_measures", [])])
            overlap = words & terms(searchable)
            if query and not exact and not overlap:
                continue
            matches.append({**row, "match_score": 1000 if exact else len(overlap),
                            "match_reason": "Exact name, alias or id" if exact else
                            "Matched: " + ", ".join(sorted(overlap)) if overlap else "Available model"})
        return sorted(matches, key=lambda r: (-r["match_score"], not r["curated"], r["workspace"], r["name"]))
