"""Single-use password reset codes.

Only the HMAC of the code is stored, so a database leak cannot be turned
into working codes, and the code is spent in the same transaction that
changes the password. A wrong guess burns one of ten attempts; the tenth
burns the row.

Requesting a new code invalidates earlier ones for that user.
"""
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.models import PasswordResetToken, User
from app.services.otp import (
    CODE_EXPIRE_MINUTES,
    CODE_MAX_ATTEMPTS,
    hash_code,
    new_code,
)


def issue(db: Session, user: User) -> tuple[str, PasswordResetToken]:
    """Create a reset code, invalidating earlier ones. Returns (code, row).

    The caller emails the code and never persists it.
    """
    # A new request supersedes every previous one.
    db.execute(
        update(PasswordResetToken)
        .where(PasswordResetToken.user_id == user.id)
        .values(used_at=datetime.now(timezone.utc))
    )

    code = new_code()
    row = PasswordResetToken(
        user_id=user.id,
        token_hash=None,
        code_hash=hash_code(code),
        expires_at=datetime.now(timezone.utc)
        + timedelta(minutes=CODE_EXPIRE_MINUTES),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return code, row


def redeem_code(db: Session, email: str, code: str) -> User:
    """Return the user for a valid code and mark the row spent.

    Scoped to the account's email: the 6-digit space is small enough that two
    live rows can share a code, so a bare code does not identify a row. Raises
    ValueError when the code is unusable — deliberately bland, so this cannot
    be used to discover which addresses are registered.

    A wrong guess against a live row burns one of its guesses; the guess that
    reaches the cap burns the row. Guesses against nothing (unknown address,
    or no live row for a known one) cost nothing, so stray traffic cannot lock
    a real user out of their own code.
    """
    user = db.scalar(select(User).where(User.email == email.strip().lower()))
    row = None
    if user is not None:
        row = db.scalar(
            select(PasswordResetToken)
            .where(
                PasswordResetToken.user_id == user.id,
                PasswordResetToken.code_hash.is_not(None),
                PasswordResetToken.used_at.is_(None),
            )
            .order_by(PasswordResetToken.id.desc())
            .limit(1)
        )

    if row is None:
        raise ValueError("This reset code is invalid or has expired")

    if row.attempts >= CODE_MAX_ATTEMPTS:
        # Defensive: the increment below burns at the cap, so a row should
        # never arrive here already over it — unless it was written by hand.
        # Committed, not flushed: the caller raises on failure and never
        # commits, so a flush here would roll back and the cap would never
        # actually engage.
        row.used_at = datetime.now(timezone.utc)
        db.commit()
        raise ValueError("This reset code is invalid or has expired")

    if row.code_hash == hash_code(code) and row.is_usable:
        owner = db.get(User, row.user_id) if row.user_id else None
        if owner is None:
            raise ValueError("This reset code is no longer valid")
        # Spend it in the same transaction as the password change that
        # follows, so a replay cannot slip in between. Flushed, not
        # committed: the caller's commit covers both writes atomically.
        row.used_at = datetime.now(timezone.utc)
        db.flush()
        return owner

    if row.is_usable:
        # Wrong guess against a live row: count it, burning at the cap.
        # Committed for the same reason as above — failure never commits.
        row.attempts += 1
        if row.attempts >= CODE_MAX_ATTEMPTS:
            row.used_at = datetime.now(timezone.utc)
        db.commit()
    raise ValueError("This reset code is invalid or has expired")


def invalidate_all(db: Session, user_id: int) -> None:
    """Kill every live reset token for a user (used after a password change)."""
    db.execute(
        update(PasswordResetToken)
        .where(
            PasswordResetToken.user_id == user_id,
            PasswordResetToken.used_at.is_(None),
        )
        .values(used_at=datetime.now(timezone.utc))
    )
