"""Observability middleware: request IDs + JSON access logs.

- Accepts an inbound X-Request-ID or generates one (uuid4 hex).
- Echoes it back on every response.
- Logs one JSON line per request: timestamp, id, method, path, status,
  duration_ms. uvicorn's default access log is disabled in README's run
  command in favour of this (single structured stream).
"""
import json
import logging
import time
from uuid import uuid4

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

REQUEST_ID_HEADER = "X-Request-ID"

logger = logging.getLogger("talyn.access")


def configure_logging() -> None:
    """Attach a stdout JSON handler once (uvicorn --no-access-log)."""
    if logger.handlers:
        return
    handler = logging.StreamHandler()
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False


class ObservabilityMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        request_id = request.headers.get(REQUEST_ID_HEADER) or uuid4().hex[:16]
        request.state.request_id = request_id
        start = time.perf_counter()
        try:
            response = await call_next(request)
            status_code = response.status_code
        except Exception:
            logger.exception(
                json.dumps({"request_id": request_id, "path": request.url.path,
                            "status": 500, "error": "unhandled"}))
            raise
        duration_ms = round((time.perf_counter() - start) * 1000, 1)
        response.headers[REQUEST_ID_HEADER] = request_id
        logger.info(json.dumps({
            "request_id": request_id,
            "method": request.method,
            "path": request.url.path,
            "status": status_code,
            "duration_ms": duration_ms,
        }))
        return response
