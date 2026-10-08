"""Purchases: redirect a learner to Paystack, then confirm before unlocking.

Flow (Paystack configured):

  1. POST /v1/courses/{id}/purchase      -> create a pending payment and hand
     back a hosted ``checkout_url``. Nothing is unlocked yet.
  2. The browser sends the learner to that URL. They pay on Paystack.
  3. Paystack redirects back to the frontend callback page with
     ``?reference=...``, and separately POSTs a signed webhook to
     /v1/payments/webhook.
  4. The callback page calls POST /v1/payments/{reference}/verify, which asks
     Paystack what really happened. Only a confirmed ``success`` for the
     exact amount marks the payment paid and upgrades the enrollment.

Both step 3 and step 4 can settle the payment, so settlement is idempotent.

Without PAYSTACK_SECRET_KEY the stub provider is used and the purchase
completes inline, which is what the test suite and local dev rely on.
"""
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.core.deps import get_current_user
from app.database import get_db
from app.models import Course, Enrollment, Payment, User
from app.models.analytics import COURSE_ENROLLED, COURSE_PURCHASED
from app.models.course import STATUS_PUBLISHED
from app.models.payment import (
    PAYMENT_PENDING,
    PAYMENT_SUCCESS,
    PROVIDER_PAYSTACK,
    PROVIDER_STUB,
)
from app.schemas.purchase import CheckoutSession, PaymentStatusRead
from app.services import payments as pay
from app.services.analytics import track

router = APIRouter(prefix="/courses", tags=["Purchases"])
webhook_router = APIRouter(prefix="/payments/webhooks", tags=["Payments"])


def _load_purchasable_course(db: Session, course_id: int) -> Course:
    course = db.get(Course, course_id)
    if course is None:
        raise HTTPException(status_code=404, detail="Course not found")
    if course.status != STATUS_PUBLISHED:
        raise HTTPException(
            status_code=422, detail="Only published courses can be purchased"
        )
    if course.course_type != "paid":
        raise HTTPException(
            status_code=422, detail="Free course — enroll directly instead"
        )
    return course


def _already_paid(db: Session, user_id: int, course_id: int) -> Payment | None:
    return db.scalar(
        select(Payment).where(
            Payment.user_id == user_id,
            Payment.course_id == course_id,
            Payment.status == PAYMENT_SUCCESS,
        )
    )


@router.post("/{course_id}/purchase", response_model=CheckoutSession)
def start_purchase(
    course_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CheckoutSession:
    """Begin a purchase.

    With Paystack configured this creates a pending payment and returns a
    hosted checkout URL — the caller redirects the browser there and no
    content is unlocked yet. Unconfigured (dev/tests) settles immediately.
    """
    course = _load_purchasable_course(db, course_id)

    existing = db.scalar(
        select(Enrollment).where(
            Enrollment.user_id == current_user.id,
            Enrollment.course_id == course_id,
        )
    )
    if existing is not None and existing.source == "purchase":
        raise HTTPException(status_code=409, detail="Already purchased")

    # Idempotent for a double-clicked Buy button: hand back the payment that
    # is already in flight instead of creating a second one.
    if pay.paystack_enabled():
        in_flight = db.scalar(
            select(Payment)
            .where(
                Payment.user_id == current_user.id,
                Payment.course_id == course_id,
                Payment.status == PAYMENT_PENDING,
                Payment.created_at >= pay.fresh_pending_after(),
            )
            .order_by(Payment.id.desc())
        )
        if in_flight is not None:
            return CheckoutSession(payment=in_flight, enrolled=False)

    if not pay.paystack_enabled():
        # Stub provider: settle inline so dev and tests work without a
        # provider account. Never reachable in a real environment — config
        # refuses to boot without PAYSTACK_SECRET_KEY there.
        payment = Payment(
            user_id=current_user.id,
            course_id=course.id,
            amount_naira=course.price_naira,
            currency="NGN",
            status=PAYMENT_SUCCESS,
            provider=PROVIDER_STUB,
            reference=f"stub-{uuid4().hex}",
        )
        db.add(payment)
        if existing is None:
            db.add(
                Enrollment(
                    user_id=current_user.id, course_id=course.id, source="purchase"
                )
            )
        else:
            # Preview enrollment upgrades to full access on purchase.
            existing.source = "purchase"
        track(db, COURSE_PURCHASED, current_user, course_id=course.id,
              meta={"amount_naira": course.price_naira, "provider": PROVIDER_STUB,
                    "reference": payment.reference})
        track(db, COURSE_ENROLLED, current_user, course_id=course.id,
              meta={"source": "purchase"})
        db.commit()
        db.refresh(payment)
        pay.send_receipt(db, payment, current_user, course)
        return CheckoutSession(payment=payment, enrolled=True)

    reference = pay.new_reference()
    payment = Payment(
        user_id=current_user.id,
        course_id=course.id,
        amount_naira=course.price_naira,
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
            amount_naira=course.price_naira,
            email=current_user.email,
            callback_url=settings.payment_callback_url(course.id),
            course_id=course.id,
            user_id=current_user.id,
        )
    except pay.PaymentError as e:
        # Don't leave an orphan pending row pointing at nothing.
        db.delete(payment)
        db.commit()
        raise HTTPException(
            status_code=502, detail=f"Could not start checkout: {e}"
        ) from e

    return CheckoutSession(payment=payment, checkout_url=checkout_url, enrolled=False)


# ── Confirmation ─────────────────────────────────────────────────────────────


@router.get("/{course_id}/payment", response_model=PaymentStatusRead)
def payment_status(
    course_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> PaymentStatusRead:
    """Current state of the learner's payment for this course.

    The callback page polls this while it waits, so a slow or slow-to-arrive
    webhook shows "confirming…" rather than an error.
    """
    settled = _already_paid(db, current_user.id, course_id)
    if settled is not None:
        return PaymentStatusRead(
            reference=settled.reference,
            status=settled.status,
            course_id=settled.course_id,
            amount_naira=settled.amount_naira,
            enrolled=True,
        )
    pending = db.scalar(
        select(Payment)
        .where(
            Payment.user_id == current_user.id,
            Payment.course_id == course_id,
            Payment.status == PAYMENT_PENDING,
            Payment.created_at >= pay.fresh_pending_after(),
        )
        .order_by(Payment.id.desc())
    )
    if pending is None:
        raise HTTPException(status_code=404, detail="No payment found for this course")
    return PaymentStatusRead(
        reference=pending.reference,
        status=pending.status,
        course_id=pending.course_id,
        amount_naira=pending.amount_naira,
        enrolled=False,
    )


@router.post("/{course_id}/verify", response_model=PaymentStatusRead)
def verify_purchase(
    course_id: int,
    reference: str = "",
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> PaymentStatusRead:
    """Ask Paystack whether this purchase actually completed.

    Called by the checkout callback page after Paystack redirects back. The
    reference from the URL is treated as a hint only — the provider is the
    authority, and the payment must belong to the signed-in learner.
    """
    payment = db.scalar(
        select(Payment).where(
            Payment.user_id == current_user.id,
            Payment.course_id == course_id,
            Payment.status != PAYMENT_SUCCESS,
        )
    )
    if reference:
        exact = db.scalar(
            select(Payment).where(
                Payment.reference == reference,
                Payment.user_id == current_user.id,
                Payment.course_id == course_id,
            )
        )
        if exact is not None:
            payment = exact

    if payment is None:
        # Either it already settled (idempotent repeat) or there is nothing
        # to verify. Report the settled state instead of an error.
        settled = _already_paid(db, current_user.id, course_id)
        if settled is None:
            raise HTTPException(
                status_code=404, detail="No pending payment found for this course"
            )
        return PaymentStatusRead(
            reference=settled.reference,
            status=settled.status,
            course_id=settled.course_id,
            amount_naira=settled.amount_naira,
            enrolled=True,
        )

    if not pay.paystack_enabled():
        return PaymentStatusRead(
            reference=payment.reference,
            status=payment.status,
            course_id=payment.course_id,
            amount_naira=payment.amount_naira,
            enrolled=False,
        )

    try:
        data = pay.verify_transaction(payment.reference)
        pay.settle_payment(db, payment, data=data, source="verify")
    except pay.PaymentError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e

    return PaymentStatusRead(
        reference=payment.reference,
        status=payment.status,
        course_id=payment.course_id,
        amount_naira=payment.amount_naira,
        enrolled=True,
    )


@webhook_router.post("/paystack", status_code=status.HTTP_200_OK)
async def paystack_webhook(request: Request, db: Session = Depends(get_db)) -> dict:
    """Paystack's server-to-server confirmation. HMAC-SHA512 signed.

    No auth: Paystack authenticates with the signature header, and the
    signature is checked against the raw body before anything is trusted.
    Unsigned or mis-signed requests are rejected without touching the DB.
    """
    raw = await request.body()
    presented = request.headers.get("x-paystack-signature", "")
    if not pay.signature_matches(raw, presented):
        raise HTTPException(status_code=401, detail="Invalid webhook signature")

    try:
        event = await request.json()
    except Exception as e:
        raise HTTPException(status_code=400, detail="Malformed webhook payload") from e

    data = event.get("data") or {}
    reference = str(data.get("reference") or "")
    if not reference:
        raise HTTPException(status_code=400, detail="Webhook payload has no reference")

    payment = pay.find_payment(db, reference)
    if payment is None:
        # Unknown reference: acknowledge so Paystack stops retrying. A payment
        # we never initiated is not ours to act on.
        return {"received": True, "settled": False}

    if event.get("event") not in pay.PAID_EVENTS:
        return {"received": True, "settled": False}

    try:
        settled = pay.settle_payment(db, payment, data=data, source="webhook")
    except pay.PaymentError as e:
        # Money moved but the amount does not match. Flag for a human rather
        # than granting access or dropping the payment.
        return {"received": True, "settled": False, "error": str(e)}

    return {"received": True, "settled": settled}
