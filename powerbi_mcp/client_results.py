"""Normalize FastMCP's typed client results to portable JSON values."""
from pydantic import TypeAdapter
from typing import Any

_JSON_VALUE = TypeAdapter(Any)


def tool_data(result):
    # With an output schema, FastMCP may construct a generated Pydantic model.
    # Consumers must not assume result.data is already a dict or list.
    return _JSON_VALUE.dump_python(result.data, mode="json")
