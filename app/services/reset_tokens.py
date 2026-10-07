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

Each row also carries a 6-digit numeric code for clients that collect a
typed-in code instead of a clicked link. The code redeems the same row as the
link, so whichever is used spends both. A 6-digit space is brute-forceable by
definition, so codes get what links do not need: a peppered hash (a database
dump alone must not recover a live code), a guess counter, and a hard cap
after which the row burns.
"""
import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.models import PasswordResetToken, User

# Matches the old JWT's 15-minute window. Long enough to survive a slow
# inbox, short enough that a leaked link is not a standing key. Codes share
# it: the email already promises 15 minutes, and two expiries for one row
# would be two ways to be wrong about which one applies.
RESET_TOKEN_EXPIRE_MINUTES = 15

TOKEN_BYTES = 32  # 256 bits of entropy

CODE_DIGITS = 6
# Wrong guesses before the row burns. Generous to thumbs on small screens —
# re-requesting is cheap — and negligible to an attacker: 10 guesses per
# 15-minute code is 10 in a million, and the auth rate limit (20/min per IP)
# means even reaching the cap repeatedly costs more time than it buys.
CODE_MAX_ATTEMPTS = 10


def hash_token(raw: str) -> str:
    """Hex SHA-256. Plain tokens are never stored or logged."""
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def hash_code(raw: str) -> str:
    """Hex HMAC-SHA-256 of a reset code, keyed by the app secret.

    Plain SHA-256 is enough for the 256-bit link token but not for six
    digits: a million hashes take milliseconds, so a database dump would hand
    over every live code. The pepper means the dump alone is not sufficient —
    and settings are read here rather than at import so tests can reconfigure
    them, matching the rate limiter's convention.
    """
    from app.config import settings

    return hmac.new(
        settings.secret_key.encode("utf-8"),
        raw.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def _new_code() -> str:
    """A zero-padded numeric string. A string, not an int: int("042013") is
    42013, and a code that changes when you parse it is a support ticket."""
    return f"{secrets.randbelow(10 ** CODE_DIGITS):0{CODE_DIGITS}d}"


def issue(db: Session, user: User) -> tuple[str, str, PasswordResetToken]:
    """Create a reset row: link token plus numeric code.

    Returns (raw_token, code, row). The caller emails both raw values and
    never persists either. Invalidates earlier rows first, exactly as before.
    """
    # A new request supersedes every previous one.
    db.execute(
        update(PasswordResetToken)
        .where(PasswordResetToken.user_id == user.id)
        .values(used_at=datetime.now(timezone.utc))
    )

    raw = secrets.token_urlsafe(TOKEN_BYTES)
    code = _new_code()
    row = PasswordResetToken(
        user_id=user.id,
        token_hash=hash_token(raw),
        code_hash=hash_code(code),
        expires_at=datetime.now(timezone.utc)
        + timedelta(minutes=RESET_TOKEN_EXPIRE_MINUTES),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return raw, code, row


def redeem_code(db: Session, email: str, code: str) -> User:
    """Return the user for a valid code and mark the row spent.

    Scoped to the account's email: the 6-digit space is small enough that two
    live rows can share a code, so a bare code does not identify a row. Raises
    ValueError when the code is unusable — deliberately the same shape of
    failure as a bad link, so this cannot be used to discover which addresses
    are registered.

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
