"""Run private regression cases through browser OAuth; output checks, never business rows."""

from __future__ import annotations

import argparse
import asyncio
import json
import math
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .client_results import tool_data
from .contracts import AnalysisContext
from .dax_generator import validate_dax


class ScalarCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")
    column: str
    row: int = Field(default=0, ge=0)
    value: Any
    tolerance: float = Field(default=0, ge=0, allow_inf_nan=False)


class Expectations(BaseModel):
    model_config = ConfigDict(extra="forbid")
    min_rows: int = Field(default=1, ge=0)
    max_rows: int | None = Field(default=None, ge=0)
    columns: list[str] = Field(default_factory=list)
    scalars: list[ScalarCheck] = Field(default_factory=list)
    require_complete: bool = False


class Case(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    model_id: str
    question: str | None = None
    dax: str | None = None
    recipe: str | None = None
    parameters: dict = Field(default_factory=dict)
    context: AnalysisContext | None = None
    max_rows: int = Field(default=250, ge=1, le=5000)
    step: str | None = None
    expect: Expectations

    @model_validator(mode="after")
    def check_mode(self):
        if sum(bool(v) for v in (self.question, self.dax, self.recipe)) != 1:
            raise ValueError("Each case needs exactly one of question, dax or recipe.")
        if self.dax:
            validate_dax(self.dax)
        if self.question and (not self.context or not self.context.reference_date):
            raise ValueError("Question cases need context.reference_date for reproducible relative periods.")
        if self.recipe and not self.step:
            raise ValueError("Recipe cases must name the step whose result is checked.")
        return self


def compare(case: Case, outcome: dict) -> list[str]:
    issues = []
    if outcome.get("status") != "executed":
        issues.append("Analysis did not finish with status executed.")
    target = outcome
    if case.step:
        target = next((s for s in outcome.get("steps", []) if s.get("id") == case.step), {})
        if target.get("status") != "executed":
            issues.append("The expected recipe step did not execute successfully.")
    body = target.get("result", target.get("executionResult", target)) or {}
    tables = body.get("tables", [])
    if len(tables) != 1:
        return [*issues, "Expected exactly one result table."]
    table = tables[0]
    rows, columns = table.get("rows", []), [c["name"] for c in table.get("columns", [])]
    expected = case.expect
    if len(rows) < expected.min_rows or (expected.max_rows is not None and len(rows) > expected.max_rows):
        issues.append("Row count is outside the expected bounds.")
    if set(expected.columns) - set(columns):
        issues.append("Expected columns are missing.")
    if expected.require_complete and target.get("evidence", {}).get("completeness") != "complete":
        issues.append("Result completeness was not confirmed.")
    for check in expected.scalars:
        try:
            row = rows[check.row]
            actual = row[check.column] if isinstance(row, dict) else row[columns.index(check.column)]
            if type(actual) in (int, float) and type(check.value) in (int, float):
                matches = math.isclose(actual, check.value, rel_tol=0, abs_tol=check.tolerance)
            else:
                matches = type(actual) is type(check.value) and actual == check.value
            if not matches:
                issues.append("A scalar value differs from its expected value.")
        except (IndexError, KeyError, ValueError, TypeError):
            issues.append("A scalar check could not locate its row or column.")
    return issues


async def evaluate(client, cases: list[Case]) -> dict:
    results = []
    for case in cases:
        try:
            if case.dax:
                tool, args = (
                    "execute_dax",
                    {"model_id": case.model_id, "dax_queries": [case.dax], "max_rows": case.max_rows},
                )
            elif case.recipe:
                tool, args = (
                    "run_recipe",
                    {"model_id": case.model_id, "name": case.recipe, "parameters": case.parameters},
                )
            else:
                tool, args = (
                    "generate_dax",
                    {
                        "model_id": case.model_id,
                        "question": case.question,
                        "context": case.context.model_dump(mode="json") if case.context else None,
                        "max_rows": case.max_rows,
                    },
                )
            response = await client.call_tool(tool, args)
            issues = compare(case, tool_data(response))
        except Exception:
            # Exception bodies from upstream tools can contain queries or business values.
            issues = ["Tool call failed; use diagnose_connection or inspect the case privately."]
        results.append({"name": case.name, "status": "failed" if issues else "passed", "issues": issues})
    return {
        "status": "passed" if all(r["status"] == "passed" for r in results) else "failed",
        "checked_at": datetime.now(UTC).isoformat(),
        "cases": results,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gateway", required=True, help="Gateway /mcp URL; opens browser sign-in")
    parser.add_argument("--cases", type=Path, required=True, help="Private JSON list, up to 50 cases")
    parser.add_argument("--output", type=Path, help="Optional summary file without queries or business rows")
    args = parser.parse_args(argv)
    try:
        raw = json.loads(args.cases.read_text(encoding="utf-8"))
        if not isinstance(raw, list) or not 1 <= len(raw) <= 50:
            raise ValueError("Supply 1..50 evaluation cases.")
        cases = [Case.model_validate(c) for c in raw]
        if len({c.name for c in cases}) != len(cases):
            raise ValueError("Case names must be unique.")

        async def run():
            from fastmcp import Client
            from fastmcp.client.auth import OAuth

            async with Client(args.gateway, auth=OAuth(args.gateway), timeout=240) as client:
                return await evaluate(client, cases)

        report = asyncio.run(run())
        rendered = json.dumps(report, indent=2)
        if args.output:
            args.output.write_text(rendered + "\n", encoding="utf-8")
        print(rendered)
        return 0 if report["status"] == "passed" else 1
    except Exception:
        print(
            json.dumps(
                {
                    "status": "failed",
                    "error": "Could not validate or run evaluation cases. Check file shape, fixed reference dates, and gateway access.",
                }
            )
        )
        return 1
