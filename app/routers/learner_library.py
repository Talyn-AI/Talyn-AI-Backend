"""Learner library: private study materials, analysis, and paid schedules.

Presign → upload → claim, mirroring the creator flow, but the purpose is
locked to "material" and every row is scoped to its owner. There is no
sharing and no course attachment: the library is a shelf, not a publication.

The monetization loop lives here too: analyze (free preview) → purchase
(₦2,500 unlock) → schedule (generated once paid, kept forever).
"""
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import settings
from app.core.deps import get_current_user, require_onboarding
from app.database import get_db
from app.models import (
    LearnerMaterial,
    MaterialAnalysis,
    Payment,
    ScheduleDay,
    StudySchedule,
    User,
)
from app.models.study_schedule import SCHEDULE_DAYS
from app.schemas import (
    AnalysisRead,
    MaterialClaimIn,
    MaterialPresignIn,
    MaterialRead,
    PathCheckout,
    PathPaymentStatus,
    PlanIn,
    PresignOut,
    ScheduleDayRead,
    ScheduleRead,
)
from app.services import coach_client
from app.services import documents as document_service
from app.services import payments as pay
from app.services import uploads as upload_service
from app.services.analytics import track
from app.models.analytics import PATH_PURCHASED
from app.models.payment import (
    PAYMENT_PENDING,
    PAYMENT_SUCCESS,
    PROVIDER_PAYSTACK,
    PROVIDER_STUB,
)
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
    """Mint a presigned PUT URL for one library document.

    Same guarantees as the creator URL: the server chooses the key, and the
    uploader must send exactly the declared Content-Type. The size cap is
    advisory at upload time; claim() enforces it against the real object.
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


# ── Analysis (free preview) ──────────────────────────────────────────────────


def _owned_material(db: Session, user: User, material_id: int) -> LearnerMaterial:
    """The learner's own material, or 404 — same non-disclosure as delete."""
    material = db.scalar(
        select(LearnerMaterial).where(
            LearnerMaterial.id == material_id,
            LearnerMaterial.user_id == user.id,
        )
    )
    if material is None:
        raise HTTPException(status_code=404, detail="Material not found")
    return material


def _material_text(material: LearnerMaterial) -> str:
    """Extracted document text, or a 422/503 the frontend can show."""
    try:
        return document_service.extract_text(
            material.storage_key, material.filename, material.content_type
        )
    except document_service.ExtractionError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    except StorageError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e


def _coach_result(callable_, *args) -> dict:
    """Run a coach call, mapping failures to gateway statuses."""
    try:
        return callable_(*args)
    except coach_client.CoachUnavailable as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(e)
        ) from e
    except coach_client.CoachError as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail=str(e)
        ) from e


@router.post("/{material_id}/analyze", response_model=AnalysisRead)
def analyze_material(
    material_id: int,
    current_user: User = Depends(require_onboarding),
    db: Session = Depends(get_db),
) -> AnalysisRead:
    """Read the document and preview what it contains: topics, objectives,
    study time. Free — this is the "here's what we found" screen, and the
    paywall comes after it, not before it.

    Repeatable: a fresh analysis replaces the previous one.
    """
    material = _owned_material(db, current_user, material_id)
    text = _material_text(material)
    data = _coach_result(
        coach_client.analyze_material,
        current_user.id, text, material.filename,
    )

    analysis = db.scalar(
        select(MaterialAnalysis).where(
            MaterialAnalysis.material_id == material.id
        )
    )
    if analysis is None:
        analysis = MaterialAnalysis(material_id=material.id)
        db.add(analysis)
    analysis.topics = [str(t) for t in data.get("topics", [])][:12]
    analysis.objectives = [str(o) for o in data.get("objectives", [])][:12]
    analysis.estimated_minutes = int(data.get("estimated_minutes", 0) or 0)
    analysis.summary = str(data.get("summary", ""))[:2000]
    db.commit()
    db.refresh(analysis)
    return _read_analysis(analysis)


def _read_analysis(analysis: MaterialAnalysis) -> AnalysisRead:
    return AnalysisRead(
        topics=list(analysis.topics or []),
        objectives=list(analysis.objectives or []),
        estimated_minutes=analysis.estimated_minutes,
        summary=analysis.summary,
        purpose=analysis.purpose,
        timeline_days=analysis.timeline_days,
    )


@router.get("/{material_id}/analysis", response_model=AnalysisRead)
def get_analysis(
    material_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> AnalysisRead:
    """Re-read the preview (step 5 is a screen the learner returns to)."""
    material = _owned_material(db, current_user, material_id)
    analysis = db.scalar(
        select(MaterialAnalysis).where(
            MaterialAnalysis.material_id == material.id
        )
    )
    if analysis is None:
        raise HTTPException(status_code=404, detail="Not analyzed yet")
    return _read_analysis(analysis)


@router.post("/{material_id}/plan", response_model=AnalysisRead)
def set_plan(
    material_id: int,
    payload: PlanIn,
    current_user: User = Depends(require_onboarding),
    db: Session = Depends(get_db),
) -> AnalysisRead:
    """Steps 3+4 of the loop: purpose and timeline. Requires the analysis
    first — intent without a preview has nothing to attach to. Repeatable:
    changing your mind re-shapes the schedule generated later, not the
    preview itself."""
    material = _owned_material(db, current_user, material_id)
    analysis = db.scalar(
        select(MaterialAnalysis).where(
            MaterialAnalysis.material_id == material.id
        )
    )
    if analysis is None:
        raise HTTPException(
            status_code=409, detail="Analyze your document first"
        )
    analysis.purpose = payload.purpose.strip()
    analysis.timeline_days = payload.days
    db.commit()
    db.refresh(analysis)
    return _read_analysis(analysis)


# ── Purchase (the paywall) ───────────────────────────────────────────────────


def _path_price() -> int:
    return settings.material_path_price_naira


@router.post("/{material_id}/purchase", response_model=PathCheckout)
def purchase_schedule(
    material_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> PathCheckout:
    """Begin unlocking the 14-day schedule for a material.

    Analysis comes first (409 otherwise): the paywall follows the preview by
    design, and generation needs the analysis row anyway. Mirrors the course
    purchase flow otherwise — pending payment plus checkout URL on Paystack,
    inline settlement on the stub.
    """
    material = _owned_material(db, current_user, material_id)
    if pay.path_paid_for(db, current_user.id, material.id) is not None:
        raise HTTPException(status_code=409, detail="Already unlocked")
    analysis = db.scalar(
        select(MaterialAnalysis).where(
            MaterialAnalysis.material_id == material.id
        )
    )
    if analysis is None:
        raise HTTPException(
            status_code=409, detail="Analyze your document first"
        )
    if not analysis.purpose or not analysis.timeline_days:
        raise HTTPException(
            status_code=409,
            detail="Choose a purpose and timeline first",
        )

    price = _path_price()
    if pay.paystack_enabled():
        in_flight = db.scalar(
            select(Payment)
            .where(
                Payment.user_id == current_user.id,
                Payment.material_id == material.id,
                Payment.status == PAYMENT_PENDING,
            )
            .order_by(Payment.id.desc())
        )
        if in_flight is not None:
            return PathCheckout(
                reference=in_flight.reference,
                checkout_url=None,
                unlocked=False,
                amount_naira=in_flight.amount_naira,
            )

    if not pay.paystack_enabled():
        payment = Payment(
            user_id=current_user.id,
            course_id=None,
            material_id=material.id,
            amount_naira=price,
            currency="NGN",
            status=PAYMENT_SUCCESS,
            provider=PROVIDER_STUB,
            reference=f"stub-{uuid4().hex}",
        )
        db.add(payment)
        track(db, PATH_PURCHASED, current_user,
              meta={"material_id": material.id, "amount_naira": price,
                    "provider": PROVIDER_STUB, "reference": payment.reference})
        db.commit()
        db.refresh(payment)
        pay.send_path_receipt(db, payment, current_user)
        return PathCheckout(
            reference=payment.reference, checkout_url=None, unlocked=True,
            amount_naira=price,
        )

    reference = pay.new_reference()
    payment = Payment(
        user_id=current_user.id,
        course_id=None,
        material_id=material.id,
        amount_naira=price,
        currency="NGN",
        status=PAYMENT_PENDING,
        provider=PROVIDER_PAYSTACK,
        reference=reference,
    )
    db.add(payment)
    db.commit()
    db.refresh(payment)

    try:
        checkout_url = pay.initialize_transaction(
            reference=reference,
            amount_naira=price,
            email=current_user.email,
            callback_url=settings.payment_callback_url(material_id=material.id),
            user_id=current_user.id,
            material_id=material.id,
        )
    except pay.PaymentError as e:
        db.delete(payment)
        db.commit()
        raise HTTPException(
            status_code=502, detail=f"Could not start checkout: {e}"
        ) from e

    return PathCheckout(
        reference=payment.reference, checkout_url=checkout_url,
        unlocked=False, amount_naira=price,
    )


@router.get("/{material_id}/payment", response_model=PathPaymentStatus)
def path_payment_status(
    material_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> PathPaymentStatus:
    """Current state of the unlock payment. The callback page polls this."""
    material = _owned_material(db, current_user, material_id)
    settled = pay.path_paid_for(db, current_user.id, material.id)
    if settled is not None:
        return PathPaymentStatus(
            reference=settled.reference, status=settled.status,
            unlocked=True, amount_naira=settled.amount_naira,
        )
    pending = db.scalar(
        select(Payment)
        .where(
            Payment.user_id == current_user.id,
            Payment.material_id == material.id,
            Payment.status == PAYMENT_PENDING,
        )
        .order_by(Payment.id.desc())
    )
    if pending is None:
        raise HTTPException(status_code=404, detail="No payment found")
    return PathPaymentStatus(
        reference=pending.reference, status=pending.status,
        unlocked=False, amount_naira=pending.amount_naira,
    )


@router.post("/{material_id}/verify", response_model=PathPaymentStatus)
def verify_path_purchase(
    material_id: int,
    reference: str = "",
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> PathPaymentStatus:
    """Ask Paystack whether this unlock actually completed.

    The reference from the URL is a hint only — the provider is the
    authority, and the payment must belong to the signed-in learner.
    """
    material = _owned_material(db, current_user, material_id)
    payment = db.scalar(
        select(Payment).where(
            Payment.user_id == current_user.id,
            Payment.material_id == material.id,
            Payment.status != PAYMENT_SUCCESS,
        )
    )
    if reference:
        exact = db.scalar(
            select(Payment).where(
                Payment.reference == reference,
                Payment.user_id == current_user.id,
                Payment.material_id == material.id,
            )
        )
        if exact is not None:
            payment = exact

    if payment is None:
        settled = pay.path_paid_for(db, current_user.id, material.id)
        if settled is None:
            raise HTTPException(
                status_code=404, detail="No pending payment found"
            )
        return PathPaymentStatus(
            reference=settled.reference, status=settled.status,
            unlocked=True, amount_naira=settled.amount_naira,
        )

    if not pay.paystack_enabled():
        return PathPaymentStatus(
            reference=payment.reference, status=payment.status,
            unlocked=False, amount_naira=payment.amount_naira,
        )

    try:
        data = pay.verify_transaction(payment.reference)
        pay.settle_payment(db, payment, data=data, source="verify")
    except pay.PaymentError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e

    return PathPaymentStatus(
        reference=payment.reference, status=payment.status,
        unlocked=True, amount_naira=payment.amount_naira,
    )


# ── Schedule (the paid artifact) ─────────────────────────────────────────────


def _read_schedule(db: Session, schedule: StudySchedule) -> ScheduleRead:
    """Assemble the response with per-day completion."""

    days = db.scalars(
        select(ScheduleDay)
        .where(ScheduleDay.schedule_id == schedule.id)
        .order_by(ScheduleDay.day_number)
    ).all()
    done = sum(1 for d in days if d.completed_at is not None)
    total = len(days)
    return ScheduleRead(
        id=schedule.id,
        material_id=schedule.material_id,
        title=schedule.title,
        purpose=schedule.purpose,
        days=[
            ScheduleDayRead(
                day=d.day_number,
                title=d.title,
                objectives=list(d.objectives or []),
                tasks=list(d.tasks or []),
                completed=d.completed_at is not None,
                completed_at=d.completed_at,
            )
            for d in days
        ],
        days_total=total,
        days_completed=done,
        completion_percent=round(done / total * 100, 1) if total else 0.0,
        created_at=schedule.created_at,
    )


def _generate_schedule(
    db: Session, user: User, material: LearnerMaterial,
    analysis: MaterialAnalysis,
) -> StudySchedule:
    """Build and store the plan for the learner's own timeline. The AI call
    happens before any write, so a failed generation leaves no partial
    schedule behind."""
    text = _material_text(material)
    days = analysis.timeline_days or SCHEDULE_DAYS
    data = _coach_result(
        coach_client.generate_schedule,
        user.id, text,
        list(analysis.topics or []),
        list(analysis.objectives or []),
        days,
        user.difficulty_level,
        analysis.purpose,
    )
    schedule = StudySchedule(
        user_id=user.id,
        material_id=material.id,
        title=str(data.get("title", "") or f"Study schedule")[:255],
        purpose=analysis.purpose,
    )
    db.add(schedule)
    db.flush()
    raw_days = data.get("days", [])
    if not isinstance(raw_days, list) or not raw_days:
        raise HTTPException(
            status_code=502, detail="The schedule came back empty."
        )
    for n, raw in enumerate(raw_days[:days], start=1):
        if not isinstance(raw, dict):
            continue
        db.add(ScheduleDay(
            schedule_id=schedule.id,
            day_number=int(raw.get("day", n) or n),
            title=str(raw.get("title", "") or f"Day {n}")[:255],
            objectives=[str(o) for o in raw.get("objectives", [])][:8],
            tasks=[str(t) for t in raw.get("tasks", [])][:12],
        ))
    try:
        db.commit()
    except IntegrityError:
        # A concurrent read generated first (unique material_id): their
        # schedule is as good as ours would have been.
        db.rollback()
        existing = db.scalar(
            select(StudySchedule).where(
                StudySchedule.material_id == material.id
            )
        )
        if existing is None:  # pragma: no cover - defensive
            raise
        return existing
    db.refresh(schedule)
    return schedule


@router.get("/{material_id}/schedule", response_model=ScheduleRead)
def read_schedule(
    material_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ScheduleRead:
    """The unlocked plan, generating it on first read after payment.

    402 until paid: the schedule is the product, and reads stay honest
    about that. Permanent once unlocked — the 14 days shape the plan,
    never gate it, so no expiry is checked here.
    """
    material = _owned_material(db, current_user, material_id)
    if pay.path_paid_for(db, current_user.id, material.id) is None:
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail="Unlock your learning path first",
        )
    schedule = db.scalar(
        select(StudySchedule).where(
            StudySchedule.material_id == material.id
        )
    )
    if schedule is None:
        analysis = db.scalar(
            select(MaterialAnalysis).where(
                MaterialAnalysis.material_id == material.id
            )
        )
        if analysis is None:  # pragma: no cover - purchase requires analysis
            raise HTTPException(
                status_code=409, detail="Analyze your document first"
            )
        schedule = _generate_schedule(db, current_user, material, analysis)
    return _read_schedule(db, schedule)


@router.post("/{material_id}/days/{day_number}/complete")
def complete_schedule_day(
    material_id: int,
    day_number: int,
    current_user: User = Depends(require_onboarding),
    db: Session = Depends(get_db),
) -> dict:
    """Mark one day done. Idempotent: re-completing is a no-op, not an error."""
    material = _owned_material(db, current_user, material_id)
    schedule = db.scalar(
        select(StudySchedule).where(
            StudySchedule.material_id == material.id
        )
    )
    if schedule is None:
        raise HTTPException(
            status_code=404, detail="No schedule yet — open it first"
        )
    day = db.scalar(
        select(ScheduleDay).where(
            ScheduleDay.schedule_id == schedule.id,
            ScheduleDay.day_number == day_number,
        )
    )
    if day is None:
        raise HTTPException(status_code=404, detail="Day not found")
    from datetime import datetime, timezone

    if day.completed_at is None:
        day.completed_at = datetime.now(timezone.utc)
        db.commit()
    return {"completed": True, "day": day_number}
