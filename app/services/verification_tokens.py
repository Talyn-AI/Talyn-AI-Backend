"""Single-use email verification codes.

Same shape as the password reset codes, for the same reasons: only the HMAC
of the code is stored, a wrong guess burns one of ten attempts, and the code
is spent in the same transaction that marks the address verified.

Requesting a new code invalidates earlier ones for that address. Resend is a
normal thing for a user to do, and a second live code would mean the first
one still works even after the newer one has been used.
"""
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.models import EmailVerificationToken, User
from app.services.otp import (
    CODE_EXPIRE_MINUTES,
    CODE_MAX_ATTEMPTS,
    hash_code,
    new_code,
)


def issue(db: Session, user: User) -> tuple[str, EmailVerificationToken]:
    """Create a verification code, invalidating earlier ones. Returns (code, row).

    The caller emails the code and never persists it.
    """
    db.execute(
        update(EmailVerificationToken)
        .where(EmailVerificationToken.user_id == user.id)
        .values(used_at=datetime.now(timezone.utc))
    )

    code = new_code()
    row = EmailVerificationToken(
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

    Scoped to the account's email, counted guesses, committed-on-failure —
    the same contract as the reset codes, because it defends against the
    same attacker. Raises ValueError when the code is unusable.
    """
    user = db.scalar(select(User).where(User.email == email.strip().lower()))
    row = None
    if user is not None:
        row = db.scalar(
            select(EmailVerificationToken)
            .where(
                EmailVerificationToken.user_id == user.id,
                EmailVerificationToken.code_hash.is_not(None),
                EmailVerificationToken.used_at.is_(None),
            )
            .order_by(EmailVerificationToken.id.desc())
            .limit(1)
        )

    if row is None:
        raise ValueError("This verification code is invalid or has expired")

    if row.attempts >= CODE_MAX_ATTEMPTS:
        row.used_at = datetime.now(timezone.utc)
        db.commit()
        raise ValueError("This verification code is invalid or has expired")

    if row.code_hash == hash_code(code) and row.is_usable:
        owner = db.get(User, row.user_id) if row.user_id else None
        if owner is None:
            raise ValueError("This verification code is no longer valid")
        row.used_at = datetime.now(timezone.utc)
        db.flush()
        return owner

    if row.is_usable:
        row.attempts += 1
        if row.attempts >= CODE_MAX_ATTEMPTS:
            row.used_at = datetime.now(timezone.utc)
        db.commit()
    raise ValueError("This verification code is invalid or has expired")


def mark_verified(db: Session, user: User) -> None:
    """Stamp the address as proven. Idempotent.

    Called both by the code path and by Google sign-in, which arrives with
    Google's own assertion that the address is verified.
    """
    if user.email_verified_at is None:
        user.email_verified_at = datetime.now(timezone.utc)
        db.commit()
