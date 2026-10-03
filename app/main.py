"""Talyn Backend — FastAPI application entry point."""
from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.core.observability import ObservabilityMiddleware, configure_logging
from app.core.rate_limit import RateLimitMiddleware
from app.routers import auth_router, users_router, courses_router, progress_router, missions_router, missions_catalog_router, creator_missions_router, onboarding_router, buddies_router, admin_router, creators_router, creators_public_router, curriculum_router, curriculum_lessons_router, uploads_router, files_router, lesson_assets_router, purchases_router, payments_webhook_router, community_router, messages_router, live_router, live_detail_router

settings.ensure_production_secrets()
configure_logging()

app = FastAPI(
    title=settings.app_name,
    description="Data + auth service that feeds the Talyn AI coach.",
    version="0.2.0",
)

app.add_middleware(ObservabilityMiddleware)
app.add_middleware(RateLimitMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Versioned API. /health stays unversioned (load-balancer convention).
app.include_router(auth_router, prefix="/v1")
app.include_router(users_router, prefix="/v1")
app.include_router(courses_router, prefix="/v1")
app.include_router(progress_router, prefix="/v1")
app.include_router(missions_catalog_router, prefix="/v1")
app.include_router(missions_router, prefix="/v1")
app.include_router(creator_missions_router, prefix="/v1")
app.include_router(onboarding_router, prefix="/v1")
app.include_router(buddies_router, prefix="/v1")
app.include_router(admin_router, prefix="/v1")
app.include_router(creators_router, prefix="/v1")
app.include_router(creators_public_router, prefix="/v1")
app.include_router(curriculum_router, prefix="/v1")
app.include_router(curriculum_lessons_router, prefix="/v1")
app.include_router(uploads_router, prefix="/v1")
app.include_router(files_router, prefix="/v1")
app.include_router(lesson_assets_router, prefix="/v1")
app.include_router(purchases_router, prefix="/v1")
app.include_router(payments_webhook_router, prefix="/v1")
app.include_router(community_router, prefix="/v1")
app.include_router(messages_router, prefix="/v1")
app.include_router(live_router, prefix="/v1")
app.include_router(live_detail_router, prefix="/v1")


@app.get("/health", tags=["Health"])
def health() -> dict:
    """Liveness. Answers as long as the process is up and serving.

    Deliberately does not touch the database: a liveness probe that fails on a
    database blip restarts a process that was perfectly healthy.
    """
    return {
        "status": "ok",
        "service": "talyn-backend",
        "version": app.version,
        "environment": settings.environment,
    }


@app.get("/health/ready", tags=["Health"])
def readiness(db: Session = Depends(get_db)) -> dict:
    """Readiness. Answers only when the app can actually serve a request.

    This is what a load balancer or uptime check should poll: a process can be
    alive while its database is unreachable, and routing traffic to it just
    produces errors. The dependency checks are independent so a failure names
    itself rather than arriving as one opaque 503.
    """
    checks: dict[str, bool] = {}

    try:
        db.execute(text("SELECT 1"))
        checks["database"] = True
    except SQLAlchemyError:
        checks["database"] = False

    # Storage is not required to serve pages, so it is reported but does not
    # fail readiness. Blocking uploads entirely because S3 is unreachable
    # would take down sign-in too.
    checks["storage_configured"] = bool(settings.s3_bucket)

    redis_ok = False
    if settings.redis_url:
        from app.core.limit_stores import redis_available

        redis_ok = redis_available()
    checks["redis"] = redis_ok if settings.redis_url else None

    # Only the database is load-bearing for serving.
    ready = checks["database"]
    return JSONResponse(
        status_code=200 if ready else 503,
        content={
            "status": "ready" if ready else "degraded",
            "service": "talyn-backend",
            "checks": checks,
        },
    )