"""Private deployment scaffolding and offline/live skills validation."""

from __future__ import annotations

import argparse
import asyncio
import json
import re
from pathlib import Path

import yaml

from .catalog import Catalog, CatalogEntry
from .client_results import tool_data
from .schema import schema_body, schema_objects
from .skills import Skills


def validate_folder(folder: Path, schemas: dict[str, dict] | None = None) -> dict:
    skills = Skills.load(folder)
    catalog = Catalog.load(folder / "catalog.yaml")
    skills.validate_catalog(catalog)
    issues = []
    for entry in catalog.entries:
        if schemas is None:
            continue
        schema = schemas.get(entry.id)
        if schema is None:
            issues.append(f"{entry.name}: no schema snapshot supplied")
            continue
        objects = schema_objects(schema)
        measures = {o["Name"] for o in objects if o["kind"] == "measure"}
        tables = {t["Name"] for t in schema_body(schema)["Tables"]}
        for missing in set(entry.key_measures) - measures:
            issues.append(f"{entry.name}: missing measure [{missing}]")
        if entry.default_date_table and entry.default_date_table not in tables:
            issues.append(f"{entry.name}: missing date table '{entry.default_date_table}'")
        for name in entry.recipes:
            if name in skills.workflows:
                try:
                    skills.workflows[name].check_model(entry.id, schema)
                except ValueError as exc:
                    issues.append(f"{entry.name}/{name}: {exc}")
    return {
        "status": "failed" if issues else "passed",
        "models": len(catalog.entries),
        "recipes": len(skills.recipes),
        "executable_recipes": len(skills.workflows),
        "issues": issues,
        "schema_checked": schemas is not None,
    }


def initialize(folder: Path, engine_image: str = "", models: list[dict] | None = None):
    folder = folder.resolve()
    if folder.exists() and any(folder.iterdir()):
        raise ValueError("The destination must be new or empty; existing deployment files are never overwritten.")
    if engine_image and (
        not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9._/:@-]+", engine_image)
        or not re.search(r"(:[a-zA-Z0-9._-]+|@sha256:[a-f0-9]{64})$", engine_image)
        or engine_image.endswith(":latest")
    ):
        raise ValueError("Pin an engine release tag or digest, or omit --engine-image for a source build.")
    # Validate imported metadata before creating any files.
    if models is not None and not isinstance(models, list):
        raise ValueError("Imported catalog must be a JSON model list.")
    Catalog([CatalogEntry(id=m["id"], name=m["name"], workspace=m.get("workspace", "")) for m in models or []])
    skills = folder / "skills"
    for sub in ("recipes", "models"):
        (skills / sub).mkdir(parents=True, exist_ok=True)
    (folder / "deploy").mkdir(exist_ok=True)
    (skills / "instructions.md").write_text(
        "Use analyze for business questions. Use search_semantic_models to discover models; never guess ids.\n"
        "Preserve the returned context for follow-ups. Check execution status before describing results.\n"
        "Use get_model_context for client-written DAX and run_recipe for validated executable recipes.\n"
        "State the model, period, filters, assumptions, result limits and known data freshness.\n",
        encoding="utf-8",
    )
    (skills / "glossary.md").write_text(
        "# Shared business context\n\nDocument shared definitions here. Put model-specific definitions in models/*.md.\n"
        "Model metadata can seed names and descriptions; business meaning must be reviewed by the model owner.\n",
        encoding="utf-8",
    )
    (skills / "dax-rules.md").write_text(
        "- Exactly one EVALUATE per query; quote table and column identifiers correctly.\n"
        "- Prefer existing measures. Use the reference date and the model's calendar conventions.\n"
        "- Preserve follow-up scope. Ask when missing scope materially changes the answer.\n"
        "- Keep results bounded. Do not format numeric values in DAX.\n",
        encoding="utf-8",
    )
    entries = []
    for model in models or []:
        entries.append(
            {
                "id": model["id"],
                "name": model["name"],
                "workspace": model.get("workspace", ""),
                "workspace_id": model.get("workspace_id", ""),
                "description": model.get("description", ""),
                "key_measures": model.get("key_measures", []),
                "recipes": [],
                "notes": "Review business definitions, date conventions and data scope with the model owner.",
            }
        )
    (skills / "catalog.yaml").write_text(
        yaml.safe_dump({"models": entries}, sort_keys=False, allow_unicode=True), encoding="utf-8"
    )
    (folder / "deploy" / "profile.json").write_text(
        json.dumps(
            {
                "SkillsDir": "../skills",
                "EngineImage": engine_image,
                "AcrName": "",
                "ServerName": "Power BI MCP Gateway",
                "StorageName": "",
                "TimeZone": "UTC",
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    (folder / ".gitignore").write_text(
        ".env\n.env.*\n!.env.example\nschemas/\nevaluation-results.json\n", encoding="utf-8"
    )
    (folder / "README.md").write_text(
        "# Private Power BI deployment\n\nFill the resource names in deploy/profile.json and review the skill definitions.\n"
        "Azure Table OAuth and model-note storage is enabled by default; its name is derived from the registry unless specified.\n"
        "Run python -m powerbi_mcp validate skills before deploying.\n"
        "Use validate --gateway <url> for a read-only schema check as the signed-in user.\n"
        "Pin EngineImage to a published release, or leave it empty for a source build.\n",
        encoding="utf-8",
    )
    return {"directory": str(folder), **validate_folder(skills)}


async def gateway_models(url):
    from fastmcp import Client
    from fastmcp.client.auth import OAuth

    models, offset = [], 0
    async with Client(url, auth=OAuth(url), timeout=180) as client:
        while True:
            result = tool_data(await client.call_tool("search_semantic_models", {"offset": offset, "limit": 100}))
            if result["status"] != "complete":
                raise ValueError("Discovery is incomplete; fix connection diagnostics before importing a catalog.")
            models.extend(result["models"])
            offset = result.get("next_offset")
            if offset is None:
                return models


async def live_schemas(url, catalog):
    from fastmcp import Client
    from fastmcp.client.auth import OAuth

    schemas = {}
    async with Client(url, auth=OAuth(url), timeout=180) as client:
        for entry in catalog.entries:
            result = tool_data(
                await client.call_tool(
                    "get_semantic_model_schema", {"model_id": entry.id, "compact": False, "refresh": True}
                )
            )
            schemas[entry.id] = result["schema"]
    return schemas


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init", help="Create a private deployment folder without overwriting files")
    init.add_argument("folder", type=Path)
    init.add_argument("--engine-image", default="")
    source = init.add_mutually_exclusive_group()
    source.add_argument("--catalog-from", type=Path, help="JSON model list or search_semantic_models result")
    source.add_argument("--gateway", help="Import metadata through your browser OAuth sign-in")
    validate = commands.add_parser("validate", help="Check skills locally or against user-accessible model schemas")
    validate.add_argument("folder", type=Path)
    check = validate.add_mutually_exclusive_group()
    check.add_argument("--schemas", type=Path, help="JSON mapping of model ids to schema snapshots")
    check.add_argument("--gateway", help="Read current schemas using browser OAuth")
    args = parser.parse_args(argv)
    try:
        if args.command == "init":
            models = None
            if args.catalog_from:
                models = json.loads(args.catalog_from.read_text(encoding="utf-8"))
                if isinstance(models, dict):
                    models = models["models"]
            elif args.gateway:
                models = asyncio.run(gateway_models(args.gateway))
            result = initialize(args.folder, args.engine_image, models)
        else:
            schemas = json.loads(args.schemas.read_text(encoding="utf-8")) if args.schemas else None
            if args.gateway:
                schemas = asyncio.run(live_schemas(args.gateway, Catalog.load(args.folder / "catalog.yaml")))
            result = validate_folder(args.folder, schemas)
        print(json.dumps(result, indent=2))
        return 0 if result["status"] == "passed" else 1
    except Exception as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}))
        return 1
