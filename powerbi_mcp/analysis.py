"""Shared analysis workflow used by every MCP client."""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime
from urllib.parse import parse_qs, urlparse
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from .catalog import Catalog
from .config import Settings
from .contracts import AnalysisContext, AnalysisResult, result_evidence
from .dax_generator import DaxGenerator, validate_dax
from .discovery import Discovery, timestamp
from .errors import GatewayError, error_info
from .schema import compact_schema, schema_objects
from .skills import Skills

FOUNDRY_SCOPE = "https://cognitiveservices.azure.com/.default"
logger = logging.getLogger(__name__)


def report_reference(url: str) -> dict:
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname != "app.powerbi.com" or parsed.username or parsed.password:
        raise ValueError("Use a report URL from https://app.powerbi.com.")
    parts = parsed.path.strip("/").split("/")
    if "reports" not in parts:
        raise ValueError("The URL must contain /reports/<report-id>.")
    index = parts.index("reports") + 1
    if index >= len(parts):
        raise ValueError("The report URL has no report id.")
    report_id = str(UUID(parts[index]))
    return {"id": report_id, "url": url, "page": parts[index + 1] if index + 1 < len(parts) else None,
            "url_filters": parse_qs(parsed.query).get("filter", []),
            "filter_scope": "Authored metadata and URL filters only; current personal slicer state is unavailable."}


def report_model_id(metadata: dict) -> str | None:
    found = set()

    def visit(value):
        if isinstance(value, dict):
            for key, child in value.items():
                if key.casefold() in ("semanticmodelid", "datasetid") and isinstance(child, str):
                    found.add(str(UUID(child)))
                elif key.casefold() in ("semanticmodel", "dataset") and isinstance(child, dict):
                    for identifier in ("id", "Id", "artifactId"):
                        if isinstance(child.get(identifier), str):
                            found.add(str(UUID(child[identifier])))
                if isinstance(child, (dict, list)):
                    visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)
    visit(metadata)
    return next(iter(found)) if len(found) == 1 else None


class AnalysisService:
    def __init__(self, settings: Settings, skills: Skills, catalog: Catalog, discovery: Discovery | None = None):
        self.settings, self.skills, self.catalog = settings, skills, catalog
        self.discovery = discovery or Discovery(settings, catalog)
        self._generator = None
        self._openai = None
        self._credential = None

    async def aclose(self):
        if self._openai is not None:
            await self._openai.close()
        if self._credential is not None:
            await self._credential.close()

    def generator(self):
        if self._generator is None:
            if not self.settings.foundry_endpoint:
                raise GatewayError("Foundry generation is not configured.", kind="generation")
            from openai import AsyncOpenAI
            key = self.settings.foundry_api_key
            if not key:
                from azure.identity.aio import DefaultAzureCredential, get_bearer_token_provider
                self._credential = DefaultAzureCredential(process_timeout=60)
                key = get_bearer_token_provider(self._credential, FOUNDRY_SCOPE)
            endpoint = self.settings.foundry_endpoint.rstrip("/")
            base_url = endpoint + "/" if endpoint.endswith("/openai/v1") else endpoint + "/openai/v1/"
            self._openai = AsyncOpenAI(base_url=base_url,
                                       api_key=key, timeout=120, max_retries=1)
            self._generator = DaxGenerator(self._openai.responses, self.settings.foundry_deployment,
                                           self.skills.dax_rules, self.settings.foundry_reasoning_effort)
        return self._generator

    def row_limit(self, value: int | None) -> int:
        value = self.settings.default_max_rows if value is None else value
        if type(value) is not int or not 1 <= value <= self.settings.max_rows_limit:
            raise ValueError(f"max_rows must be an integer from 1 to {self.settings.max_rows_limit}.")
        return value

    def make_context(self, model_id: str, context: AnalysisContext | None) -> AnalysisContext:
        context = context.model_copy(deep=True) if context else AnalysisContext(timezone=self.settings.timezone)
        if context.model_id and context.model_id.lower() != model_id.lower():
            raise ValueError("Context belongs to another model; start with a fresh context when switching models.")
        zone = ZoneInfo(context.timezone)
        context.model_id = model_id
        context.reference_date = context.reference_date or datetime.now(zone).date()
        entry = self.catalog.get(model_id)
        context.date_table = context.date_table or (entry.default_date_table if entry else None)
        return context

    async def model_context(self, user, token, model_id, question="", refresh=False):
        schema = await self.discovery.schema(user, token, model_id, refresh)
        entry = self.catalog.get(model_id)
        names = entry.recipes if entry else []
        return {"model_id": model_id, "notes": self.catalog.notes_for(model_id),
                "glossary": self.skills.glossary_for(entry), "dax_rules": self.skills.dax_rules,
                "schema": compact_schema(schema, query=question),
                "recipes": [{"name": name, "title": self.skills.recipe_title(name),
                             "executable": name in self.skills.workflows,
                             "parameters": self.skills.workflows[name].model_dump()["parameters"]
                             if name in self.skills.workflows else {}} for name in names]}

    async def execute(self, token, model_id, queries, max_rows=None) -> dict:
        limit = self.row_limit(max_rows)
        if not 1 <= len(queries) <= 4:
            raise ValueError("Pass between 1 and 4 DAX queries.")
        for query in queries:
            validate_dax(query)
        client = self.discovery.hosted(token)
        try:
            result = await client.execute_query(model_id, queries, limit)
        finally:
            await client.aclose()
        execution = result.get("executionResult", result)
        tables = execution.get("tables") if isinstance(execution, dict) else None
        if not isinstance(tables, list) or not tables or any(
            not isinstance(t, dict) or not isinstance(t.get("rows"), list) for t in tables
        ):
            raise GatewayError("The query response contained no readable result tables.", kind="protocol")
        evidence = result_evidence(result, limit, timestamp())
        return {**result, "status": "partial" if evidence.completeness == "limited" else "executed",
                "model_id": model_id, "dax_queries": queries, "evidence": evidence.model_dump()}

    def finish(self, result: AnalysisResult, start: float) -> AnalysisResult:
        result.request_id = result.request_id or uuid4().hex
        result.elapsed_ms = round((time.monotonic() - start) * 1000)
        logger.info("analysis request_id=%s model_id=%s status=%s elapsed_ms=%s attempts=%s",
                    result.request_id, result.model_id, result.status, result.elapsed_ms, len(result.attempts))
        return result

    async def generate(self, user, token, model_id, question, execute=True, max_rows=None,
                       chat_history=None, context=None, report=None) -> AnalysisResult:
        start = time.monotonic()
        result = AnalysisResult(status="failed", model_id=model_id, report=report)
        try:
            limit = self.row_limit(max_rows)
            if not question.strip() or len(question) > 12000:
                raise ValueError("Question must contain 1..12000 characters.")
            if chat_history and (len(chat_history) > 20 or sum(len(str(t)) for t in chat_history) > 30000):
                raise ValueError("Conversation history is too large; use the returned structured context.")
            ctx = self.make_context(model_id, context)
            result.context = ctx
            schema = await self.discovery.schema(user, token, model_id)
            entry = self.catalog.get(model_id)
            result.model = {"id": model_id, "name": entry.name if entry else None,
                            "workspace": entry.workspace if entry else None}
            notes, glossary = self.catalog.notes_for(model_id), self.skills.glossary_for(entry)
            if report:
                report_text = json.dumps(report, ensure_ascii=False)
                if len(report_text) > 25000:
                    raise ValueError("Report context is too large; use get_report_metadata to select relevant context.")
                notes += "\nReport reference data:\n" + report_text
                result.warnings.append(report["filter_scope"])
            if not self.settings.foundry_endpoint and self._generator is None:
                result.status = "context_ready"
                result.model_context = await self.model_context(user, token, model_id, question)
                result.explanation = "Write DAX using this context, then call execute_dax. Foundry is disabled."
                return self.finish(result, start)
            generated = await self.generator().generate(question, schema, notes, glossary, chat_history, ctx)
            result.dax, result.explanation, result.assumptions = generated.dax, generated.explanation, generated.assumptions
            result.context = self.updated_context(ctx, generated, question)
            if generated.clarification_question:
                result.status, result.clarification_question = "needs_clarification", generated.clarification_question
                return self.finish(result, start)
            if not execute:
                result.status = "generated"
                return self.finish(result, start)
            for attempt in range(2):
                try:
                    run = await self.execute(token, model_id, [result.dax], limit)
                    result.attempts.append({"dax": result.dax, "status": "executed"})
                    result.result = run.get("executionResult", run)
                    result.evidence = result_evidence(run, limit, timestamp())
                    result.status = run["status"]
                    break
                except Exception as exc:
                    info = error_info(exc)
                    result.attempts.append({"dax": result.dax, "status": "failed", "error": info.model_dump()})
                    if attempt or info.kind != "query":
                        result.error = info
                        if attempt:
                            result.execution_error = result.repaired_after
                            result.repair_error = info.message
                        else:
                            result.execution_error = info.message
                        return self.finish(result, start)
                    result.repaired_after = info.message
                    try:
                        repaired = await self.generator().repair(question, schema, notes, glossary,
                                                                  result.dax, info.message, chat_history, ctx)
                    except Exception as repair_exc:
                        result.execution_error, result.repair_error = info.message, str(repair_exc)
                        result.error = error_info(repair_exc)
                        return self.finish(result, start)
                    if repaired.clarification_question:
                        result.status, result.clarification_question = "needs_clarification", repaired.clarification_question
                        return self.finish(result, start)
                    result.dax, result.explanation, result.assumptions = repaired.dax, repaired.explanation, repaired.assumptions
                    result.context = self.updated_context(ctx, repaired, question)
            if entry and entry.freshness_query and result.evidence:
                try:
                    freshness = await self.execute(token, model_id, [entry.freshness_query], 1)
                    tables = freshness.get("executionResult", freshness).get("tables", [])
                    rows = tables[0].get("rows", []) if tables else []
                    if rows:
                        result.evidence.freshness = "reported"
                        result.evidence.data_as_of = rows[0]
                        result.evidence.freshness_source = entry.freshness_query
                except Exception:
                    result.warnings.append("The configured data freshness query could not be read; freshness is unknown.")
            if result.evidence and result.evidence.row_count >= limit and result.evidence.completeness == "unknown":
                result.warnings.append("The result reached the row limit; completeness is unknown. Aggregate or narrow the query.")
        except Exception as exc:
            result.error = error_info(GatewayError(str(exc), kind="input") if isinstance(exc, ValueError) else exc)
        return self.finish(result, start)

    @staticmethod
    def updated_context(context, generated, question):
        values = context.model_dump()
        values.update({k: v for k, v in generated.interpretation.items()
                       if k in ("period", "date_table", "measures", "filters")})
        values.update(previous_question=question, previous_dax=generated.dax)
        return AnalysisContext.model_validate(values)

    async def analyze(self, user, token, question, model_hint=None, report_url=None, context=None,
                      execute=True, max_rows=None, recipe=None, parameters=None) -> AnalysisResult:
        start = time.monotonic()
        report = None
        try:
            model_id = None
            if report_url:
                report = report_reference(report_url)
                client = self.discovery.hosted(token)
                try:
                    metadata = await client.get_report_metadata(report["id"])
                finally:
                    await client.aclose()
                model_id = report_model_id(metadata)
                report["metadata"] = metadata
                if not model_id and not model_hint:
                    return self.finish(AnalysisResult(status="needs_clarification", report=report,
                        clarification_question="Which semantic model should this report analysis use? The report metadata did not identify one model."), start)
            if not model_id and model_hint:
                try:
                    model_id = str(UUID(model_hint))
                except ValueError:
                    # Exact catalog aliases also work for directly shared curated models, but schema
                    # retrieval below must authorize the selected id before any model data is returned.
                    exact = [e.id for e in self.catalog.entries if model_hint.casefold() in
                             [s.casefold() for s in [e.name, *e.aliases]]]
                    if len(exact) == 1:
                        model_id = exact[0]
            if not model_id and not model_hint and context and context.model_id:
                model_id = context.model_id
            if not model_id:
                discovery = await self.discovery.search(user, token, model_hint or question, limit=20)
                matches = discovery.models
                if not matches and not model_hint:
                    discovery = await self.discovery.search(user, token, limit=20)
                    matches = discovery.models
                if len(matches) == 1 and discovery.total_matches == 1 and discovery.status == "complete":
                    model_id = matches[0]["id"]
                else:
                    return self.finish(AnalysisResult(status="needs_clarification", candidates=matches,
                        warnings=["Model discovery is incomplete; refresh or specify a model."] if discovery.status == "partial" else [],
                        clarification_question="Which model should I use?" if matches else
                        "No matching model was discovered. Provide a model name, id or Power BI report link."), start)
            if context and context.model_id and context.model_id.lower() != model_id.lower():
                context = None
            if recipe:
                return await self.run_recipe(user, token, model_id, recipe, parameters or {}, execute)
            # Prefer an explicitly named compatible recipe; never invent bindings from free text.
            entry = self.catalog.get(model_id)
            mentioned = [name for name in (entry.recipes if entry else []) if name.casefold() in question.casefold()]
            if len(mentioned) == 1 and mentioned[0] in self.skills.workflows:
                workflow = self.skills.workflows[mentioned[0]]
                needed = [n for n, p in workflow.parameters.items() if p.default is None]
                if not needed:
                    return await self.run_recipe(user, token, model_id, mentioned[0], {}, execute)
                return self.finish(AnalysisResult(status="needs_clarification", model_id=model_id, recipe=mentioned[0],
                    clarification_question="Provide recipe parameters: " + ", ".join(needed),
                    model_context={"parameters": workflow.model_dump()["parameters"]}), start)
            return await self.generate(user, token, model_id, question, execute, max_rows, context=context, report=report)
        except Exception as exc:
            return self.finish(AnalysisResult(status="failed",
                error=error_info(GatewayError(str(exc), kind="input") if isinstance(exc, ValueError) else exc)), start)

    async def run_recipe(self, user, token, model_id, name, parameters, execute=True):
        start = time.monotonic()
        result = AnalysisResult(status="failed", model_id=model_id, recipe=name)
        try:
            workflow = self.skills.workflows.get(name)
            if not workflow:
                raise ValueError("This recipe has no executable YAML definition. Use get_recipe for its instructions.")
            bound = workflow.bind(parameters)
            schema = await self.discovery.schema(user, token, model_id)
            workflow.check_model(model_id, schema)
            for step, query in bound:
                record = {"id": step.id, "description": step.description, "dax": query, "status": "generated"}
                if execute:
                    try:
                        run = await self.execute(token, model_id, [query], min(step.max_rows, self.settings.max_rows_limit))
                        record.update(status=run["status"], result=run.get("executionResult", run), evidence=run["evidence"])
                        columns = {c.get("name") for c in run["evidence"]["columns"]}
                        if run["evidence"]["row_count"] < step.min_rows or set(step.expected_columns) - columns:
                            raise ValueError("Recipe result did not meet its minimum rows or expected column requirements.")
                    except Exception as exc:
                        record.update(status="failed", error=error_info(exc).model_dump())
                        result.steps.append(record)
                        result.status = "partial" if any(s["status"] in ("executed", "partial") for s in result.steps) else "failed"
                        result.error = error_info(exc)
                        return self.finish(result, start)
                result.steps.append(record)
            result.status = ("partial" if any(s["status"] == "partial" for s in result.steps) else "executed") if execute else "generated"
        except Exception as exc:
            result.error = error_info(GatewayError(str(exc), kind="input") if isinstance(exc, ValueError) else exc)
        return self.finish(result, start)

    async def dimension_values(self, user, token, model_id, table, column, search="", limit=25):
        if not 1 <= limit <= 100 or len(search) > 200:
            raise ValueError("Use limit 1..100 and search of at most 200 characters.")
        schema = await self.discovery.schema(user, token, model_id)
        if not any(o["table"] == table and o["Name"] == column and o["kind"] == "column" for o in schema_objects(schema)):
            raise ValueError("Unknown column. Use search_schema for exact identifiers.")
        identifier = "'" + table.replace("'", "''") + "'[" + column.replace("]", "]]") + "]"
        source = f"VALUES({identifier})"
        if search:
            literal = '"' + search.replace('"', '""') + '"'
            source = f"FILTER({source}, CONTAINSSTRING({identifier}, {literal}))"
        dax = f"EVALUATE TOPN({limit}, {source}, {identifier}, ASC) ORDER BY {identifier} ASC"
        return await self.execute(token, model_id, [dax], limit)

    async def diagnose(self, user, token, model_id=None, check_generation=False):
        checks = [{"name": "gateway_authentication", "status": "passed"}]
        try:
            discovery = await self.discovery.discover(user, token, refresh=True)
            checks.append({"name": "model_discovery", "status": "passed" if discovery.status == "complete" else "partial",
                           "models": discovery.total_matches, "warnings": discovery.warnings})
            if model_id is None and discovery.models:
                model_id = discovery.models[0]["id"]
        except Exception as exc:
            checks.append({"name": "model_discovery", "status": "failed", "error": error_info(exc).model_dump()})
        if model_id:
            for name, call in [
                ("model_schema", lambda: self.discovery.schema(user, token, model_id, refresh=True)),
                ("query_access", lambda: self.execute(token, model_id, ['EVALUATE ROW("probe", 1)'], 1)),
            ]:
                try:
                    await call()
                    checks.append({"name": name, "status": "passed"})
                except Exception as exc:
                    checks.append({"name": name, "status": "failed", "error": error_info(exc).model_dump()})
            if check_generation:
                outcome = await self.generate(user, token, model_id, "Return one constant probe value of 1, without business data.", max_rows=1)
                checks.append({"name": "generation", "status": "passed" if outcome.status == "executed" else "failed",
                               "error": outcome.error.model_dump() if outcome.error else None})
        else:
            checks.append({"name": "model_selection", "status": "failed",
                           "action": "No model was selected. Supply model_id to test a directly shared model."})
        return {"status": "passed" if all(c["status"] == "passed" for c in checks) else "attention_required",
                "checks": checks, "model_id": model_id, "generation_configured": bool(self.settings.foundry_endpoint),
                "oauth_storage_configured": self.settings.oauth_storage_dir is not None,
                "note": "A configured directory survives redeployments only when backed by a persistent mount."}
