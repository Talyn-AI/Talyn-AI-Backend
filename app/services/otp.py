"""Shared one-time-code mechanics for signup verification and password reset.

Both flows issue short numeric codes with the same security properties, so
they share one implementation rather than two copies that can drift:

- 6 digits from a CSPRNG, handled as strings end to end (int("042013") is
  42013, and a credential that changes when parsed is a support ticket).
- HMAC-SHA256 with the app secret, not plain SHA-256: a million hashes take
  milliseconds, so a database dump would hand over every live code under a
  plain hash. The pepper means the dump alone is not sufficient.
- A guess cap per row. Ten wrong guesses burn the code: generous to thumbs
  on small screens, negligible to an attacker (10 in a million per code,
  inside a 15-minute window, behind the auth rate limit).
"""
import hashlib
import hmac
import secrets

CODE_DIGITS = 6
CODE_MAX_ATTEMPTS = 10
# Both signup and reset codes live 15 minutes: long enough to survive a slow
# inbox, short enough that a leaked code is not a standing key. (The brief
# requires at least 10; 15 matches the reset links these replace.)
CODE_EXPIRE_MINUTES = 15


def new_code() -> str:
    """A zero-padded numeric string."""
    return f"{secrets.randbelow(10 ** CODE_DIGITS):0{CODE_DIGITS}d}"


def hash_code(raw: str) -> str:
    """Hex HMAC-SHA256 of a code, keyed by the app secret.

    Settings are read here rather than at import so tests can reconfigure
    them, matching the rate limiter's convention.
    """
    from app.config import settings

    return hmac.new(
        settings.secret_key.encode("utf-8"),
        raw.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
