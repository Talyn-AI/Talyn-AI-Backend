"""Single-use password reset tokens.

Replaces the stateless reset JWT. A reset token is a random 256-bit string
of which only the SHA-256 is stored, so a database dump does not hand over
working reset links. Redemption marks the row spent inside the same
transaction that changes the password, so two concurrent redemptions cannot
both succeed.

Requesting a new reset invalidates every earlier one for that user. That is
slightly aggressive if someone clicks "forgot password" twice by accident,
but the alternative — several live tokens at once — means the token that
leaked is still valid after the user has already used a newer one.
"""
import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.models import PasswordResetToken, User

# Matches the old JWT's 15-minute window. Long enough to survive a slow
# inbox, short enough that a leaked link is not a standing key.
RESET_TOKEN_EXPIRE_MINUTES = 15

TOKEN_BYTES = 32  # 256 bits of entropy


def hash_token(raw: str) -> str:
    """Hex SHA-256. Plain tokens are never stored or logged."""
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def issue(db: Session, user: User) -> tuple[str, PasswordResetToken]:
    """Create a reset token, invalidating any earlier ones. Returns (raw, row).

    The caller emails the raw value and never persists it.
    """
    # A new request supersedes every previous one.
    db.execute(
        update(PasswordResetToken)
        .where(PasswordResetToken.user_id == user.id)
        .values(used_at=datetime.now(timezone.utc))
    )

    raw = secrets.token_urlsafe(TOKEN_BYTES)
    row = PasswordResetToken(
        user_id=user.id,
        token_hash=hash_token(raw),
        expires_at=datetime.now(timezone.utc)
        + timedelta(minutes=RESET_TOKEN_EXPIRE_MINUTES),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return raw, row


def redeem(db: Session, raw: str) -> User:
    """Return the user for a valid, unspent, unexpired token and mark it spent.

    Raises ValueError when the token is unusable. The lookup is by hash, so a
    wrong token is indistinguishable from a spent one to the caller.
    """
    row = db.scalar(
        select(PasswordResetToken).where(
            PasswordResetToken.token_hash == hash_token(raw)
        )
    )
    if row is None or not row.is_usable:
        raise ValueError("This reset link is invalid or has expired")

    user = db.get(User, row.user_id) if row.user_id else None
    if user is None:
        raise ValueError("This reset link is no longer valid")

    # Spend it in the same transaction as the password change that follows, so
    # a replay cannot slip in between.
    row.used_at = datetime.now(timezone.utc)
    db.flush()
    return user


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
