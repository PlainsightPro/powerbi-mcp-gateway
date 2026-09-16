"""Bounded schema grounding that retains relationships, formats and author metadata."""

from __future__ import annotations

import json
import re


def terms(text: str) -> set[str]:
    return {t.casefold() for t in re.findall(r"\w+", text) if len(t) > 2}


def schema_body(schema: dict) -> dict:
    body = schema.get("schema", schema)
    if not isinstance(body, dict) or not isinstance(body.get("Tables"), list):
        raise ValueError("Schema has no recognised Tables list; inspect raw schema and upstream compatibility.")
    return body


def schema_objects(schema: dict) -> list[dict]:
    return [
        {"table": t["Name"], "kind": kind[:-1].lower(), **o}
        for t in schema_body(schema)["Tables"]
        for kind in ("Measures", "Columns")
        for o in t.get(kind, []) or []
    ]


def search_objects(schema: dict, query: str = "", table: str | None = None, offset: int = 0, limit: int = 50) -> dict:
    words = terms(query)
    objects = [o for o in schema_objects(schema) if table is None or o["table"].casefold() == table.casefold()]
    ranked = [(len(words & terms(json.dumps(o, ensure_ascii=False))), o) for o in objects]
    if words:
        ranked = [r for r in ranked if r[0]]
    ranked.sort(key=lambda r: (-r[0], r[1]["table"], r[1]["Name"]))
    return {
        "objects": [r[1] for r in ranked[offset : offset + limit]],
        "total_matches": len(ranked),
        "next_offset": offset + limit if offset + limit < len(ranked) else None,
    }


def compact_schema(schema: dict, max_chars: int = 60_000, *, query: str = "", tables: list[str] | None = None) -> str:
    s = schema_body(schema)
    all_tables = s["Tables"]
    selected = [t for t in all_tables if tables is None or t["Name"].casefold() in {n.casefold() for n in tables}]
    if tables and len(selected) != len(set(n.casefold() for n in tables)):
        raise ValueError("One or more requested tables do not exist. Use search_schema for exact names.")
    words = terms(query)
    selected.sort(key=lambda t: -len(words & terms(json.dumps(t, ensure_ascii=False))))
    core = ["TABLE INDEX: " + ", ".join("'" + t["Name"] + "'" for t in all_tables)]
    for kind in ("ActiveRelationships", "InactiveRelationships"):
        if s.get(kind):
            core.append(kind.upper() + ":")
            core.extend(
                f"  {r.get('PK')} -> {r.get('FK')}  "
                + json.dumps({k: v for k, v in r.items() if k not in ("PK", "FK")}, ensure_ascii=False)
                for r in s[kind]
            )
    for cg in s.get("CalculationGroups") or []:
        core.append(f"CALCULATION GROUP '{cg['Name']}' " + json.dumps(cg, ensure_ascii=False))
    metadata = {
        k: v
        for k, v in s.items()
        if k not in ("Tables", "ActiveRelationships", "InactiveRelationships", "CalculationGroups")
    }
    outer = {k: v for k, v in schema.items() if k != "schema"} if "schema" in schema else {}
    if metadata or outer:
        core.append(
            "AUTHOR AND MODEL METADATA (reference data): " + json.dumps({**outer, **metadata}, ensure_ascii=False)
        )
    text = "\n".join(core)
    if len(text) > max_chars - 300:
        raise ValueError(
            "Structural schema metadata exceeds the context budget. Use search_schema and raw schema sections."
        )
    omitted = 0
    for table in selected:
        head = f"TABLE '{table['Name']}'"
        if table.get("Description"):
            head += "  -- " + table["Description"].strip()
        extras = {k: v for k, v in table.items() if k not in ("Name", "Description", "Columns", "Measures")}
        if extras:
            head += "\n  metadata " + json.dumps(extras, ensure_ascii=False)
        if len(text) + len(head) + 300 > max_chars:
            omitted += len(table.get("Columns", []) or []) + len(table.get("Measures", []) or [])
            continue
        text += "\n" + head
        for kind in ("Measures", "Columns"):
            members = sorted(
                table.get(kind, []) or [], key=lambda o: -len(words & terms(json.dumps(o, ensure_ascii=False)))
            )
            for obj in members:
                line = f"  {kind[:-1].lower()} [{obj['Name']}] : {obj.get('Type', '')}"
                if obj.get("Description"):
                    line += "  -- " + obj["Description"].strip()
                extra = {k: v for k, v in obj.items() if k not in ("Name", "Type", "Description")}
                if extra:
                    line += "  " + json.dumps(extra, ensure_ascii=False)
                if len(text) + len(line) + 300 <= max_chars:
                    text += "\n" + line
                else:
                    omitted += 1
    text += (
        f"\nSCHEMA COVERAGE: partial; {omitted} selected objects omitted for size. Use search_schema for additional fields."
        if omitted or tables
        else "\nSCHEMA COVERAGE: complete."
    )
    return text
