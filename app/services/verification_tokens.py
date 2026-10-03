"""Single-use email verification tokens.

Same shape as the password reset tokens, for the same reasons: only the
SHA-256 of the token is stored, so a database leak cannot be turned into
working verification links, and the token is spent in the same transaction that
marks the address verified.

Requesting a new token invalidates earlier ones for that address. Resend is a
normal thing for a user to do, and a second live token would mean the first
one still works even after the newer one has been used.
"""
import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.models import EmailVerificationToken, User

# Longer than a password reset: someone who has just signed up may take a
# while to open their first email, and losing the account to a 15-minute
# window would be a terrible first impression.
VERIFY_TOKEN_EXPIRE_HOURS = 24

TOKEN_BYTES = 32  # 256 bits


def hash_token(raw: str) -> str:
    """Hex SHA-256. Plain tokens are never stored or logged."""
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def issue(db: Session, user: User) -> tuple[str, EmailVerificationToken]:
    """Create a verification token, invalidating earlier ones. Returns (raw, row).

    The caller emails the raw value and never persists it.
    """
    db.execute(
        update(EmailVerificationToken)
        .where(EmailVerificationToken.user_id == user.id)
        .values(used_at=datetime.now(timezone.utc))
    )

    raw = secrets.token_urlsafe(TOKEN_BYTES)
    row = EmailVerificationToken(
        user_id=user.id,
        token_hash=hash_token(raw),
        expires_at=datetime.now(timezone.utc)
        + timedelta(hours=VERIFY_TOKEN_EXPIRE_HOURS),
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
        select(EmailVerificationToken).where(
            EmailVerificationToken.token_hash == hash_token(raw)
        )
    )
    if row is None or not row.is_usable:
        raise ValueError("This verification link is invalid or has expired")

    user = db.get(User, row.user_id) if row.user_id else None
    if user is None:
        raise ValueError("This verification link is no longer valid")

    row.used_at = datetime.now(timezone.utc)
    db.flush()
    return user


def mark_verified(db: Session, user: User) -> None:
    """Stamp the address as proven. Idempotent.

    Called both by the token path and by Google sign-in, which arrives with
    Google's own assertion that the address is verified.
    """
    if user.email_verified_at is None:
        user.email_verified_at = datetime.now(timezone.utc)
        db.commit()