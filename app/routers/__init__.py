"""API routers for the Talyn backend."""
from app.routers.auth import router as auth_router
from app.routers.users import router as users_router
from app.routers.courses import router as courses_router
from app.routers.progress import router as progress_router
from app.routers.missions import (
    catalog_router as missions_catalog_router,
    router as missions_router,
)
from app.routers.creator_missions import router as creator_missions_router
from app.routers.onboarding import router as onboarding_router
from app.routers.waitlist import router as waitlist_router
from app.routers.buddies import router as buddies_router
from app.routers.admin import router as admin_router
from app.routers.creators import router as creators_router
from app.routers.creators import public_router as creators_public_router
from app.routers.curriculum import router as curriculum_router
from app.routers.curriculum import lessons_router as curriculum_lessons_router
from app.routers.uploads import router as uploads_router
from app.routers.uploads import files_router as files_router
from app.routers.uploads import lesson_assets_router as lesson_assets_router
from app.routers.purchases import router as purchases_router
from app.routers.purchases import webhook_router as payments_webhook_router
from app.routers.social import community_router
from app.routers.social import live_detail_router as live_detail_router
from app.routers.social import live_router as live_router
from app.routers.social import messages_router as messages_router

__all__ = [
    "auth_router",
    "users_router",
    "courses_router",
    "progress_router",
    "missions_router",
    "missions_catalog_router",
    "creator_missions_router",
    "onboarding_router",
    "waitlist_router",
    "buddies_router",
    "admin_router",
    "creators_router",
    "creators_public_router",
    "curriculum_router",
    "curriculum_lessons_router",
    "uploads_router",
    "files_router",
    "lesson_assets_router",
    "purchases_router",
    "payments_webhook_router",
    "community_router",
    "messages_router",
    "live_router",
    "live_detail_router",
]