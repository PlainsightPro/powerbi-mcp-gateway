"""Client-independent contracts. Context is carried by the client, never by a shared session."""
from __future__ import annotations
from datetime import date
from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from .errors import ErrorInfo


class AnalysisContext(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model_id: str | None = None
    reference_date: date | None = None
    timezone: str = "UTC"
    period: str | None = None
    date_table: str | None = None
    measures: list[str] = Field(default_factory=list, max_length=30)
    filters: list[str] = Field(default_factory=list, max_length=30)
    previous_question: str | None = Field(default=None, max_length=12000)
    previous_dax: str | None = Field(default=None, max_length=30000)

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value):
        try:
            ZoneInfo(value)
        except (ValueError, ZoneInfoNotFoundError) as exc:
            raise ValueError("Use a valid IANA timezone.") from exc
        return value


class ResultEvidence(BaseModel):
    executed_at: str | None = None
    row_count: int = 0
    row_limit: int
    completeness: Literal["complete", "limited", "unknown"] = "unknown"
    freshness: Literal["unknown", "reported"] = "unknown"
    data_as_of: Any = None
    freshness_source: str | None = None
    columns: list[dict] = Field(default_factory=list)


class AnalysisResult(BaseModel):
    status: Literal["needs_clarification", "context_ready", "generated", "executed", "partial", "failed"]
    model_id: str | None = None
    model: dict | None = None
    dax: str | None = None
    explanation: str = ""
    assumptions: list[str] = Field(default_factory=list)
    context: AnalysisContext | None = None
    result: dict | None = None
    evidence: ResultEvidence | None = None
    error: ErrorInfo | None = None
    execution_error: str | None = None
    repair_error: str | None = None
    repaired_after: str | None = None
    attempts: list[dict] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    clarification_question: str | None = None
    candidates: list[dict] = Field(default_factory=list)
    model_context: dict | None = None
    report: dict | None = None
    recipe: str | None = None
    steps: list[dict] = Field(default_factory=list)
    request_id: str = ""
    elapsed_ms: int = 0


class DiscoveryResult(BaseModel):
    status: Literal["complete", "partial"] = "complete"
    models: list[dict] = Field(default_factory=list)
    warnings: list[dict] = Field(default_factory=list)
    total_matches: int = 0
    next_offset: int | None = None
    checked_at: str
    cached: bool = False


def result_evidence(payload: dict, row_limit: int, executed_at: str) -> ResultEvidence:
    execution = payload.get("executionResult", payload)
    tables = execution.get("tables", []) if isinstance(execution, dict) else []
    row_count = sum(len(t.get("rows", [])) for t in tables)
    def flag(value):
        return next((value[k] for k in ("isTruncated", "truncated") if isinstance(value.get(k), bool)), None)
    root_flags = [flag(value) for value in (payload, execution) if isinstance(value, dict)]
    table_flags = [flag(t) for t in tables]
    if True in root_flags + table_flags:
        completeness = "limited"
    elif False in root_flags or (table_flags and all(value is False for value in table_flags)):
        completeness = "complete"
    else:
        completeness = "unknown"
    return ResultEvidence(executed_at=executed_at, row_count=row_count, row_limit=row_limit,
                          completeness=completeness, columns=[c for t in tables for c in t.get("columns", [])])
