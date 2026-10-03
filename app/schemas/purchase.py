"""Purchase schemas."""
from datetime import datetime

from pydantic import BaseModel


class PaymentRead(BaseModel):
    model_config = {"from_attributes": True}

    id: int
    course_id: int
    amount_naira: int
    currency: str
    status: str
    provider: str
    reference: str
    created_at: datetime


class CheckoutSession(BaseModel):
    """What the frontend needs to send the learner to Paystack's hosted page.

    ``checkout_url`` is absent when the purchase settled immediately (stub
    provider, or a repeat of an already-paid course).
    """

    payment: PaymentRead
    checkout_url: str | None = None
    enrolled: bool


# Historic names, still used by existing imports and response docs.
PurchaseRead = PaymentRead
PurchaseResult = CheckoutSession


class PaymentStatusRead(BaseModel):
    """Learner-facing view of one payment attempt.

    Polled by the checkout callback page so the learner sees "confirming…"
    while we verify with the provider rather than staring at a blank screen.
    """

    reference: str
    status: str
    course_id: int
    amount_naira: int
    enrolled: bool
