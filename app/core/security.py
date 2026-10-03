"""Password hashing (bcrypt) and JWT create/verify."""
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import bcrypt
import jwt

from app.config import settings


# ── Passwords ─────────────────────────────────────────────────────────────────

def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(
            password.encode("utf-8"), password_hash.encode("utf-8")
        )
    except ValueError:
        return False


# ── JWT tokens ────────────────────────────────────────────────────────────────
# Token types: "access" (API auth), "refresh" (mint new access tokens),
# "password_reset" (single-purpose, short-lived). Pre-existing tokens have
# no "type" claim and are treated as access tokens.

TOKEN_TYPE_ACCESS = "access"
TOKEN_TYPE_REFRESH = "refresh"
TOKEN_TYPE_RESET = "password_reset"

REFRESH_TOKEN_EXPIRE_DAYS = 30
RESET_TOKEN_EXPIRE_MINUTES = 15


def create_token(user_id: int, token_type: str, expires_delta: timedelta) -> str:
    expire = datetime.now(timezone.utc) + expires_delta
    payload = {
        "sub": str(user_id),
        "type": token_type,
        "exp": expire,
        "jti": uuid4().hex,  # unique per token so refreshes differ
    }
    return jwt.encode(payload, settings.secret_key, algorithm=settings.jwt_algorithm)


def create_access_token(user_id: int) -> str:
    return create_token(
        user_id,
        TOKEN_TYPE_ACCESS,
        timedelta(minutes=settings.access_token_expire_minutes),
    )


def create_refresh_token(user_id: int) -> str:
    return create_token(
        user_id, TOKEN_TYPE_REFRESH, timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)
    )


def create_reset_token(user_id: int) -> str:
    """Legacy stateless reset JWT.

    Superseded by app/services/reset_tokens.py, which stores only a hash and
    spends the token on use so it cannot be replayed inside its validity
    window. Nothing issues these any more; the helper remains so the token
    type stays defined and the primitive remains tested.
    """
    return create_token(
        user_id, TOKEN_TYPE_RESET, timedelta(minutes=RESET_TOKEN_EXPIRE_MINUTES)
    )


def decode_token(token: str, expected_type: str = TOKEN_TYPE_ACCESS) -> dict | None:
    """Decode and validate a token, enforcing its type. None if unusable."""
    try:
        payload = jwt.decode(
            token, settings.secret_key, algorithms=[settings.jwt_algorithm]
        )
    except jwt.InvalidTokenError:
        return None
    # Tokens issued before typing carry no claim; they were access tokens.
    if payload.get("type", TOKEN_TYPE_ACCESS) != expected_type:
        return None
    return payload


def verify_google_token(id_token_str: str) -> dict | None:
    """Verify a Google ID token. Returns claims ({sub, email, ...}) or None."""
    if not settings.google_client_id:
        return None
    try:
        from google.auth.transport import requests as google_requests
        from google.oauth2 import id_token as google_id_token

        return google_id_token.verify_oauth2_token(
            id_token_str, google_requests.Request(), settings.google_client_id
        )
    except Exception:
        return None