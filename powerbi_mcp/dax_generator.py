"""DAX generation on a Foundry (Azure OpenAI) gpt-5 deployment, grounded in the model schema and the house rules.

This replaces the hosted server's Copilot-backed GenerateQuery tool, which is unavailable on
Premium Per User workspaces (AI_Scenarios_SkuNotSupported).
"""

from __future__ import annotations

import json
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Protocol


class ResponsesClient(Protocol):
    """The slice of the OpenAI Responses API we use (lets tests inject a stub).

    Declared as a read-only callable property rather than a method so that the SDK's overloaded
    `AsyncResponses.create` and a plain test stub both satisfy it.
    """

    @property
    def create(self) -> Callable[..., Awaitable[Any]]: ...


@dataclass
class GeneratedDax:
    dax: str
    explanation: str
    assumptions: list[str] = field(default_factory=list)
    raw: str = ""


DAX_OUTPUT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "dax": {"type": "string", "description": "One complete DAX query with exactly one EVALUATE statement."},
        "explanation": {
            "type": "string",
            "description": "Two or three sentences: which measures, tables and filters were used and why.",
        },
        "assumptions": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Interpretations made where the question was ambiguous.",
        },
    },
    "required": ["dax", "explanation", "assumptions"],
}


def compact_schema(schema: dict, max_chars: int = 60_000) -> str:
    """Render the hosted server's schema payload as a compact text block for the prompt.

    Keeps names, types, descriptions and relationships; drops format strings and icons.
    """
    s = schema.get("schema", schema)
    lines: list[str] = []
    for table in s.get("Tables", []):
        head = f"TABLE '{table['Name']}'"
        if table.get("Description"):
            head += f"  -- {table['Description'].strip()}"
        lines.append(head)
        for m in table.get("Measures", []) or []:
            desc = f"  -- {m['Description'].strip()}" if m.get("Description") else ""
            lines.append(f"  measure [{m['Name']}] : {m.get('Type', '')}{desc}")
        for c in table.get("Columns", []) or []:
            desc = f"  -- {c['Description'].strip()}" if c.get("Description") else ""
            lines.append(f"  column [{c['Name']}] : {c.get('Type', '')}{desc}")
    for kind in ("ActiveRelationships", "InactiveRelationships"):
        rels = s.get(kind) or []
        if rels:
            lines.append(f"{kind.upper()}:")
            lines.extend(f"  {r.get('PK')} -> {r.get('FK')}" for r in rels)
    for cg in s.get("CalculationGroups") or []:
        lines.append(f"CALCULATION GROUP '{cg['Name']}'")
    text = "\n".join(lines)
    return text if len(text) <= max_chars else text[:max_chars] + "\n... (schema truncated)"


_FENCE = re.compile(r"```(?:dax)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)


def parse_generation(raw: str) -> GeneratedDax:
    """Accept the structured JSON we ask for, but survive a model that answers with a code fence."""
    try:
        obj = json.loads(raw)
        if isinstance(obj, dict) and "dax" in obj:
            return GeneratedDax(
                dax=obj["dax"].strip(),
                explanation=obj.get("explanation", "").strip(),
                assumptions=list(obj.get("assumptions") or []),
                raw=raw,
            )
    except json.JSONDecodeError:
        pass
    m = _FENCE.search(raw)
    dax = (m.group(1) if m else raw).strip()
    return GeneratedDax(dax=dax, explanation="", assumptions=[], raw=raw)


_EVALUATE = re.compile(r"\bEVALUATE\b", re.IGNORECASE)
_QUERY_DDL = re.compile(r"\bDEFINE\s+(TABLE|COLUMN)\b", re.IGNORECASE)


def validate_dax(dax: str) -> None:
    """The constraints the hosted server enforces on a query, checked before it is sent: exactly
    one EVALUATE statement (a measure or string containing the word does not count), and no
    query-scoped DEFINE TABLE / DEFINE COLUMN."""
    if len(_EVALUATE.findall(dax)) != 1:
        raise ValueError("generated DAX must contain exactly one EVALUATE statement")
    if match := _QUERY_DDL.search(dax):
        raise ValueError(f"generated DAX uses unsupported 'DEFINE {match.group(1).upper()}'")


class DaxGenerator:
    def __init__(
        self,
        client: ResponsesClient,
        deployment: str,
        rules: str,
        reasoning_effort: str = "low",
        max_output_tokens: int = 4000,
    ) -> None:
        self._client = client
        self._deployment = deployment
        self._rules = rules
        self._effort = reasoning_effort
        self._max_output_tokens = max_output_tokens

    def build_prompt(
        self, question: str, schema_text: str, model_notes: str, glossary: str, chat_history: list[dict] | None = None
    ) -> tuple[str, str]:
        instructions = (
            "You write DAX queries for Power BI semantic models. Answer only with the JSON object requested. "
            "Use existing measures whenever one answers the question; never re-aggregate a column that a measure already covers.\n"
            "The query engine applies a measure's or column's format string to typed results, so a measure can come "
            "back as text such as '1,729,015 EUR' or '81.11%'. The rows are consumed by a program, so for every "
            "numeric measure in the output columns do both: add + 0 and give the column an alias that is not the "
            'name of any measure ("Revenue EUR", [Revenue] + 0, never "Revenue", [Revenue]); counts too '
            '("Won Deals Count", [# Won Deals] + 0). Before answering, compare every output alias with the measure '
            "names in the schema and rename any that match. Leave text and date measures as they are.\n"
            "Never assume the date table ends at the current month: models can hold future-dated rows. Bound relative "
            "periods such as 'last 6 months' or 'year to date' by today's date (TODAY(), or a month-offset column when "
            "the model has one), never by the maximum of the date table.\n\n"
            "## House rules\n" + self._rules.strip() + "\n\n"
            "## Business glossary\n" + glossary.strip()
        )
        history = ""
        if chat_history:
            history = "\n\n## Earlier turns\n" + "\n".join(
                f"{t.get('role', 'user')}: {t.get('content', '')}" for t in chat_history[-6:]
            )
        user = (
            "## Model notes\n"
            + (model_notes.strip() or "(none)")
            + "\n\n## Model schema\n"
            + schema_text
            + history
            + "\n\n## Question\n"
            + question.strip()
        )
        return instructions, user

    async def _ask(self, instructions: str, user: str) -> GeneratedDax:
        response = await self._client.create(
            model=self._deployment,
            instructions=instructions,
            input=user,
            reasoning={"effort": self._effort},
            max_output_tokens=self._max_output_tokens,
            text={"format": {"type": "json_schema", "name": "dax_query", "strict": True, "schema": DAX_OUTPUT_SCHEMA}},
        )
        if getattr(response, "status", None) == "incomplete":
            details = getattr(response, "incomplete_details", None)
            reason = getattr(details, "reason", None) or "unknown reason"
            raise ValueError(
                f"the model's answer was cut off ({reason}) at max_output_tokens={self._max_output_tokens}; "
                "reasoning tokens count against it, so simplify the question or raise "
                "PBIMCP_FOUNDRY_MAX_OUTPUT_TOKENS"
            )
        raw = getattr(response, "output_text", None) or str(response)
        result = parse_generation(raw)
        validate_dax(result.dax)
        return result

    async def generate(
        self, question: str, schema: dict, model_notes: str, glossary: str, chat_history: list[dict] | None = None
    ) -> GeneratedDax:
        instructions, user = self.build_prompt(question, compact_schema(schema), model_notes, glossary, chat_history)
        return await self._ask(instructions, user)

    async def repair(
        self, question: str, schema: dict, model_notes: str, glossary: str, failed_dax: str, error: str
    ) -> GeneratedDax:
        """Second attempt after the engine rejected the query: same grounding plus the exact error."""
        instructions, user = self.build_prompt(question, compact_schema(schema), model_notes, glossary)
        user += (
            "\n\n## Previous attempt (rejected by the Power BI engine)\n"
            + failed_dax.strip()
            + "\n\n## Engine error\n"
            + error.strip()[:2000]
            + "\n\nFix the query so it runs. Keep the same intent; change only what the error requires."
        )
        return await self._ask(instructions, user)
