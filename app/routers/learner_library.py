"""Learner library: private study materials.

Presign → upload → claim, mirroring the creator flow, but the purpose is
locked to "material" and every row is scoped to its owner. There is no
sharing and no course attachment: the library is a shelf, not a publication.
"""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import settings
from app.core.deps import get_current_user, require_onboarding
from app.database import get_db
from app.models import LearnerMaterial, User
from app.schemas import (
    MaterialClaimIn,
    MaterialPresignIn,
    MaterialRead,
    PresignOut,
)
from app.services import uploads as upload_service
from app.services.storage import StorageError

router = APIRouter(prefix="/me/materials", tags=["Library"])

MATERIAL_PURPOSE = "material"


def _filename_from_key(key: str) -> str:
    """Recover the sanitized filename from a server-generated key.

    Keys are "<purpose>/<uuid32>-<filename>"; the display name is the tail.
    Same shape as the lesson-asset helper, which lives in uploads.py.
    """
    _, _, tail = key.partition("/")
    return (tail.split("-", 1)[1] if "-" in tail else tail)[:255]


def _library_bytes(db: Session, user_id: int) -> int:
    """Total verified bytes the learner already holds."""
    return int(
        db.scalar(
            select(func.coalesce(func.sum(LearnerMaterial.size_bytes), 0)).where(
                LearnerMaterial.user_id == user_id
            )
        )
        or 0
    )


def _require_quota(db: Session, user: User, additional_bytes: int) -> None:
    """Refuse before the cap is breached, not after.

    Checked at presign (fail fast on the declared size) and again at claim
    (the declared size was only ever a claim). The claim-time check is the
    load-bearing one.
    """
    cap = settings.max_learner_library_bytes
    if _library_bytes(db, user.id) + additional_bytes > cap:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                "Your library is full. Delete something to make room "
                "before uploading more."
            ),
        )


@router.post("/presigned", response_model=PresignOut,
             status_code=status.HTTP_201_CREATED)
def presigned_material_upload(
    payload: MaterialPresignIn,
    current_user: User = Depends(require_onboarding),
    db: Session = Depends(get_db),
) -> PresignOut:
    """Mint a presigned upload form for one library document.

    Same guarantees as the creator form: the server chooses the key and the
    provider enforces the size cap, so a client holding this URL cannot exceed
    it.
    """
    limit = settings.max_upload_bytes(MATERIAL_PURPOSE)
    if payload.size_bytes and payload.size_bytes > limit:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="That file is too large for the library.",
        )
    if payload.size_bytes:
        _require_quota(db, current_user, payload.size_bytes)
    try:
        return PresignOut(**upload_service.presign(
            MATERIAL_PURPOSE, payload.filename, payload.content_type
        ))
    except upload_service.storage.UploadRejected as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    except StorageError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e


@router.post("", response_model=MaterialRead, status_code=status.HTTP_201_CREATED)
def claim_material(
    payload: MaterialClaimIn,
    current_user: User = Depends(require_onboarding),
    db: Session = Depends(get_db),
) -> LearnerMaterial:
    """Claim a finished upload into the library.

    `claim` enforces the purpose prefix, so a key minted for any other use
    cannot end up here. Claiming is what stops the orphan sweep from deleting
    the object as abandoned.
    """
    try:
        verified = upload_service.claim(payload.storage_key, MATERIAL_PURPOSE)
    except upload_service.storage.UploadRejected as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    except StorageError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e

    _require_quota(db, current_user, verified.size_bytes)

    # A quota refusal above leaves the object unclaimed in the bucket; the
    # orphan sweep collects it within a day, so this fails without leaking
    # storage permanently.
    material = LearnerMaterial(
        user_id=current_user.id,
        filename=_filename_from_key(payload.storage_key),
        storage_key=payload.storage_key,
        content_type=verified.content_type,
        size_bytes=verified.size_bytes,
        scan_status=verified.scan_status,
        scan_detail=verified.scan_detail[:255],
    )
    db.add(material)
    try:
        db.commit()
    except IntegrityError as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="That file has already been claimed.",
        ) from e
    db.refresh(material)
    return material


@router.get("", response_model=list[MaterialRead])
def list_materials(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[LearnerMaterial]:
    """The learner's own library, newest first."""
    return list(
        db.scalars(
            select(LearnerMaterial)
            .where(LearnerMaterial.user_id == current_user.id)
            .order_by(LearnerMaterial.id.desc())
        ).all()
    )


@router.delete("/{material_id}")
def delete_material(
    material_id: int,
    current_user: User = Depends(require_onboarding),
    db: Session = Depends(get_db),
) -> dict:
    """Remove a library entry. 404 for anyone else's: the existence of
    another learner's files is not something to confirm.

    Object cleanup in storage is out of scope, matching lesson assets — the
    orphan sweep collects the unreferenced key within a day.
    """
    material = db.scalar(
        select(LearnerMaterial).where(
            LearnerMaterial.id == material_id,
            LearnerMaterial.user_id == current_user.id,
        )
    )
    if material is None:
        raise HTTPException(status_code=404, detail="Material not found")
    db.delete(material)
    db.commit()
    return {"message": f"Material {material_id} deleted"}
