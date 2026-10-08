"""Uploads, lesson assets, and file access URLs."""
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.core.deps import can_manage_course, can_read_content, optional_user, require_creator
from app.database import get_db
from app.models import Course, CreatorProfile, Lesson, LessonAsset, LearnerMaterial, User
from app.models.analytics import CONTENT_UPLOADED
from app.models.asset import ASSET_KINDS, KIND_LINK
from app.models.course import STATUS_PUBLISHED
from app.schemas.asset import AssetIn, AssetRead, PresignIn, PresignOut
from app.services import storage
from app.services import uploads as upload_service
from app.services.analytics import track
from app.services.storage import StorageError

router = APIRouter(prefix="/uploads", tags=["Uploads"])
files_router = APIRouter(prefix="/files", tags=["Uploads"])
lesson_assets_router = APIRouter(prefix="/lessons", tags=["Curriculum"])


def _owned_lesson(db: Session, user: User, lesson_id: int) -> Lesson:
    lesson = db.get(Lesson, lesson_id)
    if lesson is None:
        raise HTTPException(status_code=404, detail="Lesson not found")
    course = db.get(Course, lesson.course_id)
    if course is None or not can_manage_course(course, user):
        raise HTTPException(status_code=403, detail="Not your course")
    return lesson


@router.post("/presigned", response_model=PresignOut,
             status_code=status.HTTP_201_CREATED)
def presigned_upload(
    payload: PresignIn,
    creator: User = Depends(require_creator),
    db: Session = Depends(get_db),
) -> PresignOut:
    """Mint a presigned PUT URL. Server chooses the key; clients PUT there.

    The declared size is checked here too, purely to fail fast with a clear
    message. The provider cannot enforce the cap on a PUT URL, so verify()
    reads the real size back afterwards and deletes anything over it.
    """
    limit = settings.max_upload_bytes(payload.purpose)
    if payload.size_bytes and payload.size_bytes > limit:
        raise HTTPException(
            status_code=422,
            detail=(
                f"That file is too large ({_human_size(payload.size_bytes)}). "
                f"The limit for {payload.purpose} uploads is {_human_size(limit)}."
            ),
        )
    try:
        return PresignOut(**upload_service.presign(
            payload.purpose, payload.filename, payload.content_type
        ))
    except upload_service.storage.UploadRejected as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    except StorageError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e


def _human_size(size: int) -> str:
    if size >= 1024 * 1024:
        return f"{size / (1024 * 1024):.0f} MB"
    if size >= 1024:
        return f"{size / 1024:.0f} KB"
    return f"{size} bytes"


@lesson_assets_router.post("/{lesson_id}/assets", response_model=AssetRead,
                            status_code=status.HTTP_201_CREATED)
def attach_asset(
    lesson_id: int,
    payload: AssetIn,
    creator: User = Depends(require_creator),
    db: Session = Depends(get_db),
) -> LessonAsset:
    """Attach a video/resource/link to a lesson. Owner or admin.

    For files, this is where the upload is checked: the real size comes from
    the storage provider rather than the request, the malware scan verdict is
    recorded, and an object that fails either is deleted and refused. A
    client-supplied size_bytes is no longer accepted at all — it was never
    more than a claim.
    """
    lesson = _owned_lesson(db, creator, lesson_id)
    if payload.kind not in ASSET_KINDS:
        raise HTTPException(status_code=422, detail="Unknown asset kind")

    if payload.kind == KIND_LINK:
        if not payload.url:
            raise HTTPException(status_code=422, detail="Links require a url")
        asset = LessonAsset(
            lesson_id=lesson.id, kind=payload.kind, url=payload.url,
            filename=payload.filename, size_bytes=0, scan_status="unscanned",
        )
    else:
        if not payload.storage_key:
            raise HTTPException(status_code=422, detail="Files require a storage_key")
        if not payload.storage_key.startswith(payload.kind + "/"):
            raise HTTPException(
                status_code=422, detail="storage_key does not match asset kind"
            )
        try:
            verified = upload_service.verify(payload.storage_key)
        except upload_service.storage.UploadRejected as e:
            raise HTTPException(status_code=422, detail=str(e)) from e
        except StorageError as e:
            raise HTTPException(status_code=503, detail=str(e)) from e

        asset = LessonAsset(
            lesson_id=lesson.id,
            kind=payload.kind,
            storage_key=payload.storage_key,
            filename=payload.filename or _filename_from_key(payload.storage_key),
            size_bytes=verified.size_bytes,
            scan_status=verified.scan_status,
            scan_detail=verified.scan_detail[:255],
        )

    db.add(asset)
    db.commit()
    db.refresh(asset)
    track(db, CONTENT_UPLOADED, creator, course_id=lesson.course_id,
          lesson_id=lesson.id,
          meta={"kind": asset.kind, "size_bytes": asset.size_bytes,
                "scan_status": asset.scan_status})
    db.commit()
    return asset


def _filename_from_key(key: str) -> str:
    """Recover the original filename from a server-generated key."""
    _, _, tail = key.partition("/")
    # Keys are "<uuid32>-<filename>"; drop the uuid prefix.
    return tail.split("-", 1)[1] if "-" in tail else tail


@lesson_assets_router.get("/{lesson_id}/assets", response_model=list[AssetRead])
def list_assets(
    lesson_id: int,
    viewer: User | None = Depends(optional_user),
    db: Session = Depends(get_db),
) -> list[LessonAsset]:
    """List a lesson's assets. Paid content needs a purchase enrollment."""
    lesson = db.get(Lesson, lesson_id)
    if lesson is None:
        raise HTTPException(status_code=404, detail="Lesson not found")
    course = db.get(Course, lesson.course_id)
    if course is None:
        raise HTTPException(status_code=404, detail="Course not found")
    if not can_read_content(db, viewer, course):
        if viewer is None:
            raise HTTPException(status_code=401, detail="Not authenticated")
        raise HTTPException(status_code=403, detail="Purchase required for this content")
    return list(
        db.scalars(
            select(LessonAsset)
            .where(LessonAsset.lesson_id == lesson.id)
            .order_by(LessonAsset.id)
        ).all()
    )


@lesson_assets_router.delete("/{lesson_id}/assets/{asset_id}")
def delete_asset(
    lesson_id: int,
    asset_id: int,
    creator: User = Depends(require_creator),
    db: Session = Depends(get_db),
) -> dict:
    """Detach an asset from a lesson (object cleanup in S3 is out of scope)."""
    lesson = _owned_lesson(db, creator, lesson_id)
    asset = db.scalar(
        select(LessonAsset).where(
            LessonAsset.id == asset_id, LessonAsset.lesson_id == lesson.id
        )
    )
    if asset is None:
        raise HTTPException(status_code=404, detail="Asset not found")
    db.delete(asset)
    db.commit()
    return {"message": f"Asset {asset_id} deleted"}


@files_router.get("/url")
def file_url(
    key: str = Query(..., min_length=1, max_length=500),
    viewer: User | None = Depends(optional_user),
    db: Session = Depends(get_db),
) -> dict:
    """Resolve a storage key to a presigned download URL.

    Public when the key belongs to a published course (thumbnail/assets)
    or a creator profile image; otherwise owner/admin only. Learner library
    files are never public: the owner or an admin, and nobody else.
    """
    public = False
    course = db.scalar(select(Course).where(Course.thumbnail_key == key))
    if course is not None:
        public = course.status == STATUS_PUBLISHED
        owner_id = course.creator_user_id
    else:
        profile = db.scalar(
            select(CreatorProfile).where(CreatorProfile.image_key == key)
        )
        if profile is not None:
            return _presigned(key)
        material = db.scalar(
            select(LearnerMaterial).where(LearnerMaterial.storage_key == key)
        )
        if material is not None:
            if viewer is None:
                raise HTTPException(status_code=401, detail="Not authenticated")
            if not (viewer.is_admin or material.user_id == viewer.id):
                raise HTTPException(status_code=403, detail="Not your file")
            return _presigned(key)
        asset = db.scalar(
            select(LessonAsset).where(LessonAsset.storage_key == key)
        )
        if asset is None:
            raise HTTPException(status_code=404, detail="File not found")
        course = db.get(Course, asset.lesson.course_id)
        public = course is not None and course.status == STATUS_PUBLISHED
        owner_id = course.creator_user_id if course else None

    if not public:
        if viewer is None:
            raise HTTPException(status_code=401, detail="Not authenticated")
        if not (
            viewer.is_admin
            or (owner_id is not None and owner_id == viewer.id)
        ):
            raise HTTPException(status_code=403, detail="Not your file")
    return _presigned(key)


def _presigned(key: str) -> dict:
    try:
        return {"download_url": storage.presigned_download_url(key)}
    except StorageError as e:
        raise HTTPException(status_code=503, detail=str(e))
