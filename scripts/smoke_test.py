"""Headless user-token diagnostics. Requires deploy -PreauthorizeAzureCli and local PBIMCP settings.

Run: python scripts/smoke_test.py [--model-id UUID] [--generate]
Uses a constant probe; does not print business rows. Real client OAuth is checked by check_gateway.py.
"""
from __future__ import annotations
import argparse
import asyncio
import json
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from powerbi_mcp.analysis import AnalysisService
from powerbi_mcp.catalog import Catalog
from powerbi_mcp.config import load_settings
from powerbi_mcp.skills import Skills


async def main(args):
    settings = load_settings()
    executable = shutil.which("az")
    if not executable:
        raise RuntimeError("Azure CLI was not found.")
    result = subprocess.run(
        [executable, "account", "get-access-token", "--resource", settings.identifier_uri,
         "--query", "accessToken", "-o", "tsv"], capture_output=True, text=True, timeout=90)
    if result.returncode or not result.stdout.strip():
        raise RuntimeError("Azure CLI sign-in failed; check az login and -PreauthorizeAzureCli.")
    from azure.identity.aio import OnBehalfOfCredential
    skills = Skills.load(settings.skills_dir)
    catalog = Catalog.load(settings.skills_dir / "catalog.yaml")
    skills.validate_catalog(catalog)
    service = AnalysisService(settings, skills, catalog)
    try:
        async with OnBehalfOfCredential(
            tenant_id=settings.tenant_id, client_id=settings.client_id,
            client_secret=settings.client_secret, user_assertion=result.stdout.strip()
        ) as credential:
            token = (await credential.get_token(settings.fabric_scope)).token
            report = await service.diagnose("local-smoke-user", token, args.model_id, args.generate)
            # Diagnostics contain statuses and counts, never query rows.
            print(json.dumps(report, indent=2))
            return 0 if report["status"] == "passed" else 1
    finally:
        await service.aclose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-id")
    parser.add_argument("--generate", action="store_true", help="Include one billable generation probe")
    try:
        raise SystemExit(asyncio.run(main(parser.parse_args())))
    except Exception:
        print("Check failed. Verify local configuration, sign-in and gateway prerequisites.")
        raise SystemExit(1)
