"""Transactional email: single-use reset tokens and a send log.

Why a table rather than a bare JWT for password reset: the reset token was a
stateless JWT, valid for 15 minutes and *reusable* for the whole window.
Anyone who saw it — in a proxy log, a shared inbox, a forwarded message —
could set the password again, including after the real owner had already
used it. Hashing the token and marking it spent closes that.

The log exists so a failed send is visible. Email delivery fails silently
more often than anything else in a stack like this, and "no news is good
news" is not a monitoring strategy.
"""
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base

SENT = "sent"
FAILED = "failed"
SKIPPED = "skipped"


class PasswordResetToken(Base):
    """One row per issued reset token. Only the hash is stored."""

    __tablename__ = "password_reset_tokens"

    id: Mapped[int] = mapped_column(primary_key=True)
    # SET NULL: a deleted account must not keep its reset tokens alive.
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # sha256 of the raw token. A database leak must not hand over working
    # reset links, so the plaintext never touches disk.
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    @property
    def is_usable(self) -> bool:
        if self.used_at is not None:
            return False
        return self.expires_at.timestamp() > datetime.now(self.expires_at.tzinfo).timestamp()


class EmailVerificationToken(Base):
    """One row per issued verification token. Only the hash is stored."""

    __tablename__ = "email_verification_tokens"

    id: Mapped[int] = mapped_column(primary_key=True)
    # SET NULL: a deleted account must not keep verification tokens alive.
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # sha256 of the raw token, for the same reason as reset tokens: a database
    # leak must not hand over working verification links.
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    @property
    def is_usable(self) -> bool:
        if self.used_at is not None:
            return False
        return self.expires_at.timestamp() > datetime.now(self.expires_at.tzinfo).timestamp()


class EmailLog(Base):
    """Audit trail of transactional sends, including the ones that failed."""

    __tablename__ = "email_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Nullable with SET NULL so the trail outlives account deletion, matching
    # the admin audit log.
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    to_email: Mapped[str] = mapped_column(String(255))
    template: Mapped[str] = mapped_column(String(50))
    subject: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(20))
    # Truncated error text: enough to diagnose, not a copy of the message.
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
