"""Payments: Paystack (NGN) with a stub fallback for local dev and tests.

A purchase can settle along two independent paths, and both are needed:

  1. Webhook  — Paystack POSTs ``charge.success`` to /v1/payments/webhook with
     an HMAC-SHA512 signature over the raw body. This is the path that works
     when the learner's browser never comes back (closed tab, dropped
     connection, expired session).
  2. Verify   — the learner returns from Paystack's hosted checkout and the
     app re-confirms the transaction against Paystack's API before unlocking
     anything. A query string is never trusted on its own.

Both funnel through :func:`settle_payment`, which is idempotent, so a
duplicate webhook or a verify-then-webhook race grants access exactly once.

Without ``PAYSTACK_SECRET_KEY`` the app falls back to the stub provider that
auto-succeeds, so local dev and the test suite never touch the network. In a
non-dev environment that fallback is refused unless explicitly allowed (see
``config.ensure_production_secrets``) — auto-succeeding purchases in
production would hand every paid course away for free.
"""
from __future__ import annotations

import hashlib
import hmac
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import Course, Enrollment, LearnerMaterial, Payment, User
from app.models.analytics import COURSE_ENROLLED, COURSE_PURCHASED
from app.models.payment import (
    PAYMENT_FAILED,
    PAYMENT_SUCCESS,
    PROVIDER_PAYSTACK,
)
from app.services.analytics import track

KIBO_PER_NAIRA = 100
"""Paystack charges in kobo; prices are stored in whole Naira."""

DEFAULT_API_URL = "https://api.paystack.co"

REQUEST_TIMEOUT_SECONDS = 15.0

# A pending row older than this counts as an abandoned checkout. It is
# ignored when looking for an in-flight payment (a new checkout is minted
# instead) and invisible to the status endpoints — but it is never mutated:
# if money moves late against the old reference, the webhook and verify
# paths still settle it, because a confirmed charge is a confirmed charge
# regardless of how long the learner took.
PENDING_TTL_HOURS = 24


def fresh_pending_after() -> datetime:
    """Earliest created_at a pending row may have and still count as live."""
    return datetime.now(timezone.utc) - timedelta(hours=PENDING_TTL_HOURS)


class PaymentError(Exception):
    """Payment provider rejected the call, or the outcome is not settled."""


def paystack_enabled() -> bool:
    return bool(settings.paystack_secret_key)


def naira_to_kobo(amount_naira: int) -> int:
    return int(amount_naira) * KIBO_PER_NAIRA


def new_reference() -> str:
    """Unique reference we own — also sent to Paystack as the tx reference."""
    from uuid import uuid4

    return f"tln_{uuid4().hex}"


# ── Provider calls ───────────────────────────────────────────────────────────


def _api_url() -> str:
    return settings.paystack_api_url.rstrip("/") or DEFAULT_API_URL


def _secret() -> str:
    secret = settings.paystack_secret_key
    if not secret:
        raise PaymentError("Paystack is not configured (PAYSTACK_SECRET_KEY is empty)")
    return secret


def _client():
    import httpx

    return httpx.Client(
        base_url=_api_url(),
        headers={
            "Authorization": f"Bearer {_secret()}",
            "Content-Type": "application/json",
        },
        timeout=httpx.Timeout(REQUEST_TIMEOUT_SECONDS, connect=5.0),
    )


def _unwrap(response: Any) -> dict:
    """Paystack wraps everything in {"status": bool, "message", "data"}."""
    try:
        body = response.json()
    except Exception as e:  # non-JSON error page / gateway HTML
        raise PaymentError(
            f"Payment provider returned an unreadable response ({response.status_code})"
        ) from e

    if response.status_code >= 400 or not body.get("status"):
        message = body.get("message") or "Payment provider rejected the request"
        raise PaymentError(f"{message} ({response.status_code})")
    return body.get("data") or {}


def initialize_transaction(
    *,
    reference: str,
    amount_naira: int,
    email: str,
    callback_url: str,
    course_id: int | None = None,
    user_id: int,
    material_id: int | None = None,
) -> str:
    """Start a Paystack transaction and return the hosted checkout URL.

    The learner is sent to this URL to pay with a card or bank transfer. It is
    a hosted page, so no card data ever touches Talyn servers. Exactly one of
    course_id and material_id says what is being bought.
    """
    import httpx

    payload = {
        "email": email,
        # Paystack works in kobo; a zero amount would be rejected outright.
        "amount": naira_to_kobo(amount_naira),
        "currency": "NGN",
        "reference": reference,
        "callback_url": callback_url,
        "channels": ["card", "bank", "ussd", "mobile_money", "bank_transfer"],
        "metadata": {
            "course_id": course_id,
            "material_id": material_id,
            "user_id": user_id,
            "amount_naira": amount_naira,
        },
    }
    try:
        with _client() as client:
            data = _unwrap(client.post("/transaction/initialize", json=payload))
    except httpx.HTTPError as e:
        raise PaymentError(f"Could not reach the payment provider: {e}") from e

    url = data.get("authorization_url")
    if not url:
        raise PaymentError("Payment provider did not return a checkout URL")
    return url


def verify_transaction(reference: str) -> dict:
    """Ask Paystack for the authoritative state of a transaction.

    Returns the transaction ``data`` dict. Raises PaymentError when the
    provider cannot be reached — never returns a guess.
    """
    import httpx

    try:
        with _client() as client:
            data = _unwrap(client.get(f"/transaction/verify/{reference}"))
    except httpx.HTTPError as e:
        raise PaymentError(f"Could not reach the payment provider: {e}") from e

    if data.get("status") != "success":
        raise PaymentError(
            f"Payment is not complete (status: {data.get('status') or 'unknown'})"
        )
    return data


def webhook_signature(raw_body: bytes) -> str:
    """HMAC-SHA512 hex digest of the raw body, keyed with the secret."""
    secret = settings.paystack_webhook_secret or settings.paystack_secret_key
    return hmac.new(
        secret.encode("utf-8"), raw_body, hashlib.sha512
    ).hexdigest()


def signature_matches(raw_body: bytes, presented: str) -> bool:
    if not presented or not settings.paystack_secret_key:
        return False
    return hmac.compare_digest(webhook_signature(raw_body), presented)


# ── Outcome validation ───────────────────────────────────────────────────────


def amount_matches(payment: Payment, data: dict) -> bool:
    """Guard against a paid amount that does not match what we asked for.

    Also catches a tampered reference: a real transaction for a different
    amount cannot be replayed against a cheaper course.
    """
    try:
        paid_kobo = int(data.get("amount"))
    except (TypeError, ValueError):
        return False
    return paid_kobo == naira_to_kobo(payment.amount_naira)


def currency_matches(data: dict) -> bool:
    currency = str(data.get("currency") or "").upper()
    return currency in ("NGN", "N")


def _parse_paid_at(raw: Any) -> datetime | None:
    if not raw or not isinstance(raw, str):
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


# ── Settlement ───────────────────────────────────────────────────────────────


def settle_payment(
    db: Session,
    payment: Payment,
    *,
    data: dict,
    source: str,
) -> bool:
    """Mark a payment paid and grant course access, exactly once.

    Returns True when this call performed the transition, False when the
    payment was already settled. Safe to call from the webhook and the
    learner-facing verify endpoint in any order.
    """
    if payment.status == PAYMENT_SUCCESS:
        return False

    if not amount_matches(payment, data) or not currency_matches(data):
        # A confirmed charge for the wrong amount is a reconciliation
        # problem, not a purchase. Leave it pending for a human to look at
        # rather than granting access or silently discarding the money.
        raise PaymentError(
            "Payment amount does not match the course price. "
            "Contact support with your reference."
        )

    payment.status = PAYMENT_SUCCESS
    payment.channel = str(data.get("channel") or "")[:30] or None
    payment.paid_at = _parse_paid_at(data.get("paid_at")) or datetime.now(timezone.utc)

    if payment.material_id is not None:
        # A schedule unlock, not a course: no enrollment, no course receipt.
        # The schedule itself generates lazily on first read after this.
        from app.models.analytics import PATH_PURCHASED

        user = db.get(User, payment.user_id)
        track(
            db,
            PATH_PURCHASED,
            user,
            meta={
                "material_id": payment.material_id,
                "amount_naira": payment.amount_naira,
                "provider": payment.provider,
                "reference": payment.reference,
                "source": source,
            },
        )
        db.commit()
        send_path_receipt(db, payment, user)
        return True

    course = db.get(Course, payment.course_id)
    enrollment = db.scalar(
        select(Enrollment).where(
            Enrollment.user_id == payment.user_id,
            Enrollment.course_id == payment.course_id,
        )
    )
    if enrollment is None:
        db.add(
            Enrollment(
                user_id=payment.user_id,
                course_id=payment.course_id,
                source="purchase",
            )
        )
    else:
        # A preview enrollment upgrades to full access once paid for.
        enrollment.source = "purchase"

    # track() stamps user_id off the User row, so pass it rather than the id.
    user = db.get(User, payment.user_id)
    track(
        db,
        COURSE_PURCHASED,
        user,
        course_id=payment.course_id,
        meta={
            "amount_naira": payment.amount_naira,
            "provider": payment.provider,
            "reference": payment.reference,
            "source": source,
        },
    )
    track(db, COURSE_ENROLLED, user, course_id=payment.course_id,
          meta={"source": "purchase"})
    db.commit()

    send_receipt(db, payment, user, course)
    return True


def send_receipt(db: Session, payment: Payment, user: User | None,
                 course: Course | None) -> None:
    """Email the receipt after the money is confirmed and access is granted.

    Deliberately outside the transaction: the learner already paid, so a mail
    failure must never roll back their enrollment or read as a failed
    purchase. Callers only invoke this on the transition to success, so a
    replayed webhook cannot send a second receipt.
    """
    from app.services import email as email_service

    if user is None or course is None:
        return
    email_service.send(
        db,
        to_email=user.email,
        template=email_service.TEMPLATE_RECEIPT,
        message=email_service.purchase_receipt_email(
            name=user.learner_name or "there",
            course_title=course.title,
            amount_naira=payment.amount_naira,
            reference=payment.reference,
        ),
        user_id=user.id,
    )


def send_path_receipt(db: Session, payment: Payment, user: User | None) -> None:
    """Email the schedule-unlock receipt after the money is confirmed.

    Same rule as the course receipt: outside the transaction, and only on
    the transition to success, so a mail failure never reads as a failed
    purchase and a replayed webhook cannot send a second receipt.
    """
    from app.services import email as email_service

    if user is None:
        return
    material = db.get(LearnerMaterial, payment.material_id or 0)
    email_service.send(
        db,
        to_email=user.email,
        template=email_service.TEMPLATE_PATH_RECEIPT,
        message=email_service.path_receipt_email(
            name=user.learner_name or "there",
            material_title=material.filename if material else "your document",
            amount_naira=payment.amount_naira,
            reference=payment.reference,
        ),
        user_id=user.id,
    )


def path_paid_for(db: Session, user_id: int, material_id: int) -> Payment | None:
    """The successful payment behind a schedule unlock, if there is one.

    Status-only, mirroring the course purchase flow: the stub counts in dev,
    Paystack in production. Permanent once written — the 14 days shape the
    plan, never gate it.
    """
    return db.scalar(
        select(Payment).where(
            Payment.user_id == user_id,
            Payment.material_id == material_id,
            Payment.status == PAYMENT_SUCCESS,
        )
    )


def mark_failed(db: Session, payment: Payment, reason: str | None = None) -> None:
    """Record a failed/abandoned payment. Never grants access."""
    if payment.status == PAYMENT_SUCCESS:
        return
    payment.status = PAYMENT_FAILED
    db.commit()


def find_payment(db: Session, reference: str) -> Payment | None:
    return db.scalar(select(Payment).where(Payment.reference == reference))


def course_paid_for(db: Session, user_id: int, course_id: int) -> Payment | None:
    """The successful payment behind an entitlement, if there is one."""
    return db.scalar(
        select(Payment).where(
            Payment.user_id == user_id,
            Payment.course_id == course_id,
            Payment.status == PAYMENT_SUCCESS,
            Payment.provider == PROVIDER_PAYSTACK,
        )
    )


__all__ = [
    "PaymentError",
    "amount_matches",
    "course_paid_for",
    "currency_matches",
    "find_payment",
    "initialize_transaction",
    "mark_failed",
    "new_reference",
    "naira_to_kobo",
    "fresh_pending_after",
    "path_paid_for",
    "paystack_enabled",
    "send_path_receipt",
    "send_receipt",
    "settle_payment",
    "signature_matches",
    "verify_transaction",
    "webhook_signature",
    "KIBO_PER_NAIRA",
    "PROVIDER_PAYSTACK",
    "PAID_EVENTS",
]

# Events that mean money actually landed. Paystack also sends
# charge.failed / refund.created, which must never grant access.
PAID_EVENTS = frozenset({"charge.success", "payment_channel", "transfer.success"})
