"""The unified OTP endpoint: one code flow for signup and reset.

Signup codes and reset codes live in different tables with identical rules
(15 minutes, ten guesses, spent on use), and `purpose` selects which one a
call redeems. These tests pin the selection, the shape enforcement, and the
two different landings: onboarding for signup, login for reset.
"""
import re

import pytest


def _register(client, email, password="password123"):
    r = client.post("/v1/auth/register", json={
        "email": email, "password": password, "learner_name": "Otp",
    })
    assert r.status_code == 201, r.text


def _code_from_last(smtp) -> str:
    body = ""
    for part in smtp.sent[-1].walk():
        if part.get_content_type() == "text/plain":
            body = part.get_payload(decode=True).decode("utf-8")
    match = re.search(r"Your code: (\d{6})", body)
    assert match, f"no code in: {body[:200]!r}"
    return match.group(1)


def _verify(client, email, code, purpose, **extra):
    return client.post("/v1/auth/otp/verify", json={
        "email": email, "code": code, "purpose": purpose, **extra,
    })


# ── Signup ───────────────────────────────────────────────────────────────────


def test_signup_code_verifies_and_routes_to_onboarding(client, smtp):
    _register(client, "otpup@example.com")
    code = _code_from_last(smtp)

    r = _verify(client, "otpup@example.com", code, "signup")
    assert r.status_code == 200, r.text
    assert r.json()["email_verified"] is True
    assert r.json()["next_step"] == "onboarding"

    assert len(smtp.sent) == 2  # verification + the held-back welcome
    assert smtp.sent[1]["Subject"] == "Welcome to Talyn"


def test_signup_code_with_a_password_is_rejected(client, smtp):
    """A signup call must never set a password by accident."""
    _register(client, "nopw@example.com")
    code = _code_from_last(smtp)

    r = _verify(client, "nopw@example.com", code, "signup",
                new_password="brandnewpass1")
    assert r.status_code == 422


def test_signup_codes_burn_after_ten_guesses(client, smtp, db_session):
    from app.models import EmailVerificationToken
    from app.services import otp

    _register(client, "burnup@example.com")
    code = _code_from_last(smtp)
    wrong = "000000" if code != "000000" else "111111"

    for _ in range(otp.CODE_MAX_ATTEMPTS):
        assert _verify(client, "burnup@example.com", wrong, "signup"
                       ).status_code == 401

    row = db_session.query(EmailVerificationToken).one()
    assert row.used_at is not None
    assert _verify(client, "burnup@example.com", code, "signup"
                   ).status_code == 401


def test_expired_signup_code_is_rejected(client, smtp, db_session):
    from app.models import EmailVerificationToken

    _register(client, "staleup@example.com")
    code = _code_from_last(smtp)
    row = db_session.query(EmailVerificationToken).one()
    row.expires_at = row.created_at
    db_session.commit()

    assert _verify(client, "staleup@example.com", code, "signup"
                   ).status_code == 401


# ── Reset ────────────────────────────────────────────────────────────────────


def test_reset_code_verifies_and_routes_to_login(client, smtp):
    _register(client, "otpreset@example.com")
    client.post("/v1/auth/password-reset/request",
                json={"email": "otpreset@example.com"})
    code = _code_from_last(smtp)

    r = _verify(client, "otpreset@example.com", code, "reset",
                new_password="brandnewpass1")
    assert r.status_code == 200, r.text
    assert r.json()["next_step"] == "login"

    assert client.post("/v1/auth/login", json={
        "email": "otpreset@example.com", "password": "brandnewpass1",
    }).status_code == 200


def test_reset_without_a_password_is_rejected(client, smtp):
    _register(client, "nopwreset@example.com")
    client.post("/v1/auth/password-reset/request",
                json={"email": "nopwreset@example.com"})

    r = _verify(client, "nopwreset@example.com", _code_from_last(smtp), "reset")
    assert r.status_code == 422


def test_unknown_purpose_is_rejected(client):
    r = client.post("/v1/auth/otp/verify", json={
        "email": "x@example.com", "code": "123456", "purpose": "login",
        "new_password": "brandnewpass1",
    })
    assert r.status_code == 422


# ── Cross-flow isolation ─────────────────────────────────────────────────────


def test_a_signup_code_is_not_a_reset_code(client, smtp):
    """Different tables, different flows: a code from one is wrong in the other."""
    _register(client, "cross@example.com")
    code = _code_from_last(smtp)

    r = _verify(client, "cross@example.com", code, "reset",
                new_password="brandnewpass1")
    assert r.status_code == 401


def test_a_reset_code_is_not_a_signup_code(client, smtp):
    _register(client, "cross2@example.com")
    client.post("/v1/auth/password-reset/request",
                json={"email": "cross2@example.com"})
    code = _code_from_last(smtp)

    r = _verify(client, "cross2@example.com", code, "signup")
    assert r.status_code == 401


def test_unknown_email_is_indistinguishable(client):
    r = _verify(client, "ghost@example.com", "123456", "signup")
    assert r.status_code == 401
    r = _verify(client, "ghost@example.com", "123456", "reset",
                new_password="brandnewpass1")
    assert r.status_code == 401


# ── Email-first ──────────────────────────────────────────────────────────────


def test_email_first_taken_address_is_reported_immediately(client):
    """The signup 'next' click: taken addresses are named on the spot."""
    _register(client, "taken@example.com")

    r = client.get("/v1/auth/email-available",
                   params={"email": "taken@example.com"})
    assert r.status_code == 200
    assert r.json()["available"] is False

    r = client.post("/v1/auth/register", json={
        "email": "taken@example.com", "password": "password123",
        "learner_name": "Again",
    })
    assert r.status_code == 409
    assert "already exists" in r.json()["detail"]
