"""Validated, bounded query recipes. Parameters are DAX literals, never executable fragments."""
from __future__ import annotations
import math
import re
from datetime import date
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator
from .dax_generator import _NON_CODE, validate_dax
from .schema import schema_objects

PLACEHOLDER = re.compile(r"\{\{\s*([a-z][a-z0-9_]*)\s*\}\}")


class Parameter(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["string", "integer", "number", "date", "boolean"]
    description: str = ""
    required: bool = True
    default: str | int | float | bool | None = None
    choices: list[str | int | float | bool] | None = None

    def literal(self, value) -> str:
        if self.choices is not None and value not in self.choices:
            raise ValueError("Value is not one of the parameter choices.")
        if self.type == "string" and isinstance(value, str):
            return '"' + value.replace('"', '""') + '"'
        if self.type == "integer" and type(value) is int:
            return str(value)
        if self.type == "number" and type(value) in (int, float) and math.isfinite(value):
            return str(value)
        if self.type == "boolean" and type(value) is bool:
            return "TRUE()" if value else "FALSE()"
        if self.type == "date" and isinstance(value, str):
            d = date.fromisoformat(value)
            return f"DATE({d.year}, {d.month}, {d.day})"
        raise ValueError(f"Expected a {self.type} parameter.")


class RequiredObject(BaseModel):
    model_config = ConfigDict(extra="forbid")
    table: str
    name: str
    kind: Literal["measure", "column"]


class RecipeStep(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^[a-z][a-z0-9_-]*$")
    description: str = ""
    dax: str
    depends_on: list[str] = Field(default_factory=list)
    max_rows: int = Field(default=250, ge=1, le=5000)
    min_rows: int = Field(default=0, ge=0)
    expected_columns: list[str] = Field(default_factory=list)


class Recipe(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: Literal[1] = 1
    title: str
    description: str = ""
    models: list[str] = Field(default_factory=list)
    parameters: dict[str, Parameter] = Field(default_factory=dict)
    required_objects: list[RequiredObject] = Field(default_factory=list)
    steps: list[RecipeStep] = Field(min_length=1, max_length=12)

    @model_validator(mode="after")
    def check_steps(self):
        seen = set()
        for name, parameter in self.parameters.items():
            if not re.fullmatch(r"[a-z][a-z0-9_]*", name):
                raise ValueError(f"Invalid parameter name: {name}")
            if parameter.default is not None:
                parameter.literal(parameter.default)
            if not parameter.required and parameter.default is None:
                raise ValueError(f"Optional parameter {name} needs a default.")
        for step in self.steps:
            if step.id in seen or set(step.depends_on) - seen:
                raise ValueError(f"Step {step.id} has duplicate ids or dependencies that do not precede it.")
            unknown = set(PLACEHOLDER.findall(step.dax)) - self.parameters.keys()
            if unknown:
                raise ValueError(f"Step {step.id} references unknown parameters: {sorted(unknown)}")
            if any(PLACEHOLDER.search(match.group()) for match in _NON_CODE.finditer(step.dax)):
                raise ValueError("Recipe placeholders must be bare DAX values, outside quotes, identifiers and comments.")
            validate_dax(PLACEHOLDER.sub("1", step.dax))
            seen.add(step.id)
        return self

    def bind(self, values: dict) -> list[tuple[RecipeStep, str]]:
        if values.keys() - self.parameters.keys():
            raise ValueError("Unknown recipe parameters: " + ", ".join(sorted(values.keys() - self.parameters.keys())))
        literals = {}
        for name, parameter in self.parameters.items():
            value = values.get(name, parameter.default)
            if value is None:
                raise ValueError(f"Missing recipe parameter: {name}")
            literals[name] = parameter.literal(value)
        bound = [(step, PLACEHOLDER.sub(lambda m: literals[m[1]], step.dax)) for step in self.steps]
        for _, dax in bound:
            validate_dax(dax)
        return bound

    def check_model(self, model_id: str, schema: dict) -> None:
        if self.models and model_id.lower() not in [m.lower() for m in self.models]:
            raise ValueError("This recipe is not configured for the selected model.")
        available = {(o["table"], o["Name"], o["kind"]) for o in schema_objects(schema)}
        missing = [f"'{o.table}'[{o.name}]" for o in self.required_objects if (o.table, o.name, o.kind) not in available]
        if missing:
            raise ValueError("Recipe references missing model objects: " + ", ".join(missing))
