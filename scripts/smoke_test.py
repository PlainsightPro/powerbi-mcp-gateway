"""Headless end-to-end check of everything behind the OAuth proxy.

Exercises the exact code paths the MCP tools use, with a real user token but without a browser:
  1. Azure CLI token for our own API (api://<client_id>)  -> needs deploy -PreauthorizeAzureCli
  2. On-behalf-of exchange to a Fabric token             -> same code path as EntraOBOToken
  3. Fabric REST: which semantic models the user can open
  4. Hosted Power BI MCP: schema + a probe query on the first curated model
  5. Foundry gpt-5: generate DAX for a finance question and execute it

Run from the app folder with a .env holding PBIMCP_* (client secret included):
    .venv/Scripts/python.exe scripts/smoke_test.py ["your question"]
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from powerbi_mcp.catalog import Catalog
from powerbi_mcp.config import load_settings
from powerbi_mcp.dax_generator import DaxGenerator
from powerbi_mcp.fabric import FabricClient
from powerbi_mcp.gateway import FOUNDRY_SCOPE
from powerbi_mcp.hosted_mcp import HostedMcpError, HostedPowerBIMcp
from powerbi_mcp.skills import Skills


def ok(step: str, detail: str = "") -> None:
    print(f"PASS  {step}" + (f": {detail}" if detail else ""))


def az_token(resource: str) -> str:
    out = subprocess.run(
        ["az", "account", "get-access-token", "--resource", resource, "--query", "accessToken", "-o", "tsv"],
        capture_output=True,
        text=True,
        shell=True,
    )
    if out.returncode != 0 or not out.stdout.strip():
        raise SystemExit(f"az token for {resource} failed: {out.stderr.strip()[:400]}")
    return out.stdout.strip()


async def main(question: str) -> None:
    settings = load_settings()
    skills = Skills.load(settings.skills_dir)
    catalog = Catalog.load(settings.skills_dir / "catalog.yaml")

    user_token = az_token(settings.identifier_uri)
    ok("1 user token for our API", settings.identifier_uri)

    from azure.identity.aio import OnBehalfOfCredential

    obo = OnBehalfOfCredential(
        tenant_id=settings.tenant_id,
        client_id=settings.client_id,
        client_secret=settings.client_secret,
        user_assertion=user_token,
    )
    fabric_token = (await obo.get_token(settings.fabric_scope)).token
    ok("2 on-behalf-of exchange", settings.fabric_scope)

    fabric = FabricClient(fabric_token, settings.fabric_api_url)
    try:
        accessible = await fabric.list_accessible_models()
    finally:
        await fabric.aclose()
    rows = catalog.merge(accessible)
    curated = [r for r in rows if r["curated"]]
    ok("3 accessible models", f"{len(rows)} total, {len(curated)} curated: " + ", ".join(r["name"] for r in curated))
    if not curated:
        raise SystemExit("no curated model is accessible for this user; stopping")
    model = next((r for r in curated if r["name"] == "Finance"), curated[0])

    hosted = HostedPowerBIMcp(fabric_token, settings.hosted_mcp_url)
    try:
        schema = await hosted.get_schema(model["id"])
        tables = [t["Name"] for t in schema.get("schema", {}).get("Tables", [])]
        ok("4a schema", f"{model['name']}: {len(tables)} tables")
        probe = await hosted.execute_query(model["id"], ['EVALUATE ROW("probe", 1)'], max_rows=1)
        ok("4b probe query", json.dumps(probe.get("executionResult", {}).get("tables", [{}])[0].get("rows")))

        if not settings.foundry_endpoint:
            print("SKIP  5 generate_dax: PBIMCP_FOUNDRY_ENDPOINT not set")
            return
        from azure.identity.aio import AzureCliCredential, get_bearer_token_provider
        from openai import AsyncOpenAI

        client = AsyncOpenAI(
            base_url=settings.foundry_endpoint.rstrip("/") + "/openai/v1/",
            api_key=settings.foundry_api_key
            or get_bearer_token_provider(AzureCliCredential(process_timeout=60), FOUNDRY_SCOPE),
        )
        gen = DaxGenerator(
            client.responses, settings.foundry_deployment, skills.dax_rules, settings.foundry_reasoning_effort
        )
        notes = catalog.notes_for(model["id"])
        generated = await gen.generate(question, schema, notes, skills.glossary)
        ok("5a generate_dax", generated.explanation[:160])
        print("      " + generated.dax.replace("\n", "\n      "))
        try:
            result = await hosted.execute_query(model["id"], [generated.dax], max_rows=10)
        except HostedMcpError as first:
            print(f"      engine rejected it: {str(first)[:200]}")
            repaired = await gen.repair(question, schema, notes, skills.glossary, generated.dax, str(first))
            print("      repaired:\n      " + repaired.dax.replace("\n", "\n      "))
            result = await hosted.execute_query(model["id"], [repaired.dax], max_rows=10)
            ok("5b repair loop", "second attempt accepted")
        table = result.get("executionResult", {}).get("tables", [{}])[0]
        ok(
            "5c execute generated DAX",
            f"{len(table.get('rows', []))} rows, columns " + ", ".join(c["name"] for c in table.get("columns", [])),
        )
        for row in table.get("rows", [])[:5]:
            print("      ", row)
    finally:
        await hosted.aclose()
        await obo.close()


if __name__ == "__main__":
    q = sys.argv[1] if len(sys.argv) > 1 else "Indirect cost per month this year with its share of revenue"
    asyncio.run(main(q))
