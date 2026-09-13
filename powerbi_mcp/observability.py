"""Who is calling, and one log line per tool call.

The line carries the tool name, the user key, the model or report id, the duration and the
outcome (with the error message when there is one). It never carries query text, questions,
chat history, result rows or tokens: docs/usage-guidelines.md promises users exactly that.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from fastmcp.exceptions import ToolError
from fastmcp.server.dependencies import get_access_token
from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext

logger = logging.getLogger("powerbi_mcp.tools")

# Arguments whose *value* is safe to log; everything else appears as a count or not at all.
_LOGGED_ARGUMENTS = ("model_id", "report_id", "name", "include_uncurated", "compact", "execute", "max_rows")
_ERROR_EXCERPT = 500


def current_user_key() -> str:
    """A stable, non-secret identifier of the signed-in user (the Entra object id when present)."""
    token = get_access_token()
    claims = getattr(token, "claims", None) or {}
    return claims.get("oid") or claims.get("sub") or claims.get("preferred_username") or "anonymous"


def describe_arguments(arguments: dict[str, Any] | None) -> str:
    parts = []
    for key, value in (arguments or {}).items():
        if key in _LOGGED_ARGUMENTS:
            parts.append(f"{key}={value}")
        elif isinstance(value, list | dict | str):
            parts.append(f"{key}=<{len(value)}>")
    return " ".join(parts)


class ToolCallLogger(Middleware):
    """Logs every tool call at INFO (or WARNING when it fails with a readable error)."""

    async def on_call_tool(self, context: MiddlewareContext, call_next: CallNext) -> Any:
        params = context.message
        tool = getattr(params, "name", "?")
        args = describe_arguments(getattr(params, "arguments", None))
        user = current_user_key()
        start = time.perf_counter()
        try:
            result = await call_next(context)
        except ToolError as exc:
            ms = (time.perf_counter() - start) * 1000
            message = " ".join(str(exc).split())[:_ERROR_EXCERPT]
            logger.warning("tool=%s user=%s %s ms=%.0f outcome=error message=%s", tool, user, args, ms, message)
            raise
        except Exception:
            ms = (time.perf_counter() - start) * 1000
            logger.exception("tool=%s user=%s %s ms=%.0f outcome=crash", tool, user, args, ms)
            raise
        ms = (time.perf_counter() - start) * 1000
        logger.info("tool=%s user=%s %s ms=%.0f outcome=ok", tool, user, args, ms)
        return result
