"""Verify a deployed gateway through real MCP OAuth, without logging tokens or business rows.

Run: python scripts/check_gateway.py https://<fqdn>/mcp [--generate]
The default check reads tool metadata, lists accessible models, reads one schema,
and executes a constant probe. --generate also tests Foundry on the deployed identity.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from fastmcp import Client
from fastmcp.client.auth import OAuth

from powerbi_mcp.client_results import tool_data


async def check(args: argparse.Namespace) -> None:
    class BrowserOAuth(OAuth):
        async def redirect_handler(self, authorization_url: str) -> None:
            if args.auth_url_file:
                args.auth_url_file.parent.mkdir(parents=True, exist_ok=True)
                args.auth_url_file.write_text(authorization_url, encoding="utf-8")
                print("Open the authorization URL in the specified file to sign in.", flush=True)
            else:
                await super().redirect_handler(authorization_url)

    auth = BrowserOAuth(args.url, callback_timeout=300)
    async with Client(args.url, auth=auth, timeout=180, init_timeout=360) as client:
        tools = {tool.name for tool in await client.list_tools()}
        expected = {"list_semantic_models", "get_semantic_model_schema", "execute_dax", "generate_dax"}
        if not expected <= tools:
            raise RuntimeError(f"Missing gateway tools: {sorted(expected - tools)}")
        print(f"PASS OAuth and MCP initialization: {len(tools)} tools", flush=True)
        if "diagnose_connection" in tools:
            report = tool_data(
                await client.call_tool(
                    "diagnose_connection",
                    {
                        "model_id": args.model_id,
                        "check_generation": args.generate,
                    },
                )
            )
            for stage in report["checks"]:
                print(f"{stage['status'].upper()} {stage['name']}", flush=True)
                if stage.get("error"):
                    print(stage["error"].get("action", "Check gateway configuration."), flush=True)
                if stage.get("action"):
                    print(stage["action"], flush=True)
            if report["status"] != "passed":
                raise RuntimeError("Connection needs attention; review the failed stages above.")
            return
        models = tool_data(await client.call_tool("list_semantic_models", {}))
        if not models:
            raise RuntimeError("This user has no discoverable semantic models.")
        model = {"id": args.model_id} if args.model_id else next((m for m in models if m.get("curated")), models[0])
        print(f"PASS model discovery: {len(models)} accessible models", flush=True)
        await client.call_tool("get_semantic_model_schema", {"model_id": model["id"]})
        print("PASS semantic model schema", flush=True)
        probe = tool_data(
            await client.call_tool(
                "execute_dax",
                {
                    "model_id": model["id"],
                    "dax_queries": ['EVALUATE ROW("probe", 1)'],
                    "max_rows": 1,
                },
            )
        )
        tables = probe.get("executionResult", probe).get("tables", [])
        if not tables or not tables[0].get("rows"):
            raise RuntimeError("The constant query returned no rows.")
        print("PASS constant DAX query", flush=True)
        if args.generate:
            result = tool_data(
                await client.call_tool(
                    "generate_dax",
                    {
                        "model_id": model["id"],
                        "question": "Return one constant probe value of 1. Do not return any business figures.",
                        "execute": True,
                        "max_rows": 1,
                    },
                )
            )
            if "result" not in result or result.get("execution_error") or result.get("repair_error"):
                raise RuntimeError("Generated DAX failed; inspect the gateway logs.")
            print("PASS Foundry generation and execution through the deployed gateway", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url", help="Deployed HTTPS /mcp endpoint")
    parser.add_argument("--generate", action="store_true", help="Also test Foundry (one small billable request)")
    parser.add_argument("--model-id", help="Diagnose a specific model, including directly shared models")
    parser.add_argument(
        "--auth-url-file", type=Path, help="Write sign-in URL here instead of opening the system browser"
    )
    asyncio.run(check(parser.parse_args()))
