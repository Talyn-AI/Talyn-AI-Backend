"""Payment model — one row per purchase attempt.

Lifecycle:

    pending ──(Paystack confirms money arrived)──> success
       │
       └──(abandoned / failed / reversed)───────> failed

``success`` is the only status that unlocks paid content. It is written by
the signed webhook or by the learner-facing verify call, never by the
client. When Paystack is unconfigured the stub provider marks success
immediately so local dev and tests work without an account.
"""
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base

PAYMENT_PENDING = "pending"
PAYMENT_SUCCESS = "success"
PAYMENT_FAILED = "failed"
PAYMENT_STATUSES = {PAYMENT_PENDING, PAYMENT_SUCCESS, PAYMENT_FAILED}

PROVIDER_STUB = "stub"
PROVIDER_PAYSTACK = "paystack"


class Payment(Base):
    __tablename__ = "payments"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    course_id: Mapped[int] = mapped_column(ForeignKey("courses.id"), index=True)
    amount_naira: Mapped[int] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(10), default="NGN")
    status: Mapped[str] = mapped_column(String(20), default=PAYMENT_PENDING)
    provider: Mapped[str] = mapped_column(String(30), default=PROVIDER_STUB)
    reference: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
