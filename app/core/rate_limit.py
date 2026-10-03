"""In-memory sliding-window rate limiter (per process).

Buckets:
  - "auth": credential-sensitive endpoints (login, register, refresh,
    password reset) — tight limit against brute force.
  - "default": everything else.

Keyed by (bucket, client IP), 60-second windows. Returns 429 + Retry-After
when exhausted. Reads settings at request time so tests can reconfigure it.
Set REDIS_URL to share counters across workers (see limit_stores).
"""
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app import config as config_module
from app.core.limit_stores import get_store, reset_rate_limit_store

WINDOW_SECONDS = 60

# Credential-sensitive paths share the tight "auth" bucket. Substring match
# so versioned paths (/v1/auth/login) match too.
AUTH_PATH_FRAGMENTS = (
    "/auth/login",
    "/auth/register",
    "/auth/refresh",
    "/auth/password-reset",
)

__all__ = ["RateLimitMiddleware", "reset_rate_limit_store"]


def _bucket_for(path: str) -> tuple[str, int]:
    settings = config_module.settings
    if any(fragment in path for fragment in AUTH_PATH_FRAGMENTS):
        return "auth", settings.rate_limit_auth_per_minute
    return "default", settings.rate_limit_per_minute


class RateLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        settings = config_module.settings
        if not settings.rate_limit_enabled:
            return await call_next(request)

        bucket, limit = _bucket_for(request.url.path)
        ip = request.client.host if request.client else "unknown"
        allowed = get_store().hit(f"{bucket}:{ip}", WINDOW_SECONDS, limit)
        if not allowed:
            return JSONResponse(
                status_code=429,
                content={"detail": "Rate limit exceeded, try again later"},
                headers={"Retry-After": str(WINDOW_SECONDS)},
            )
        return await call_next(request)
