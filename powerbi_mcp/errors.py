"""Stable, actionable errors and bounded retries for read-only upstream requests."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Literal

import httpx
from pydantic import BaseModel

ErrorKind = Literal[
    "authentication", "permission", "throttled", "unavailable", "query", "protocol", "generation", "input"
]


class ErrorInfo(BaseModel):
    kind: ErrorKind
    message: str
    retryable: bool = False
    retry_after_seconds: float | None = None
    action: str = ""


class GatewayError(RuntimeError):
    def __init__(self, message: str, *, kind: ErrorKind = "protocol", retry_after: float | None = None):
        super().__init__(message)
        self.kind = kind
        self.retry_after = retry_after


def error_info(exc: Exception) -> ErrorInfo:
    kind: ErrorKind = getattr(exc, "kind", "protocol")
    if isinstance(exc, ValueError):
        kind = "input"
    if isinstance(exc, (httpx.TimeoutException, httpx.TransportError)):
        kind = "unavailable"
    actions = {
        "authentication": "Reconnect the gateway with your Microsoft work account.",
        "permission": "Ask the model owner to check Build permission, workspace licensing and tenant settings.",
        "throttled": "Wait for the retry interval, then repeat the same request.",
        "unavailable": "Retry the same request when the upstream service is available.",
        "query": "Check the referenced fields and filters; repair the DAX without changing the question.",
        "protocol": "Ask the administrator to check upstream compatibility using the connection diagnostic.",
        "generation": "Retry generation or use the model context to write DAX in your client.",
        "input": "Correct the supplied arguments and retry.",
    }
    return ErrorInfo(
        kind=kind,
        message=str(exc)[:2000],
        retryable=kind in ("throttled", "unavailable"),
        retry_after_seconds=getattr(exc, "retry_after", None),
        action=actions[kind],
    )


def retry_after(response: httpx.Response) -> float | None:
    value = response.headers.get("Retry-After")
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        try:
            return max(0.0, (parsedate_to_datetime(value) - datetime.now(UTC)).total_seconds())
        except (ValueError, TypeError, OverflowError):
            return None


async def request_with_retry(client: httpx.AsyncClient, method: str, url: str, **kwargs) -> httpx.Response:
    """At most three requests and three seconds of backoff. Never retry auth or query errors.

    All callers perform reads. A timeout retry may repeat work, but cannot mutate a model.
    Long Retry-After values are returned to the caller, never shortened.
    """
    for attempt in range(3):
        try:
            response = await client.request(method, url, **kwargs)
        except httpx.TransportError:
            if attempt == 2:
                raise
            await asyncio.sleep(0.5 * (attempt + 1))
            continue
        if response.status_code not in (429, 502, 503, 504) or attempt == 2:
            return response
        delay = retry_after(response)
        delay = delay if delay is not None else 0.5 * (attempt + 1)
        if delay > 1.5:
            return response
        await response.aclose()
        await asyncio.sleep(delay)
    raise AssertionError("unreachable")


def http_error(response: httpx.Response, service: str) -> GatewayError:
    status = response.status_code
    kinds: dict[int, ErrorKind] = {401: "authentication", 403: "permission", 429: "throttled"}
    kind = kinds.get(status, "protocol")
    if status >= 500:
        kind = "unavailable"
    # Do not echo arbitrary HTML, request URLs, or upstream bodies containing query data.
    return GatewayError(f"{service} returned HTTP {status}.", kind=kind, retry_after=retry_after(response))
