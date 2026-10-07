"""The numeric reset code (links are gone).

Every reset email carries one 6-digit code and nothing else. These tests pin
the properties that make a 6-digit space safe: a peppered hash, a guess cap
that burns the row, scoping to the account's email, and the same
non-disclosure the old flow had.
"""
import re

from app.models import PasswordResetToken
from app.services import otp
from app.services import reset_tokens


def _register(client, email):
    client.post("/v1/auth/register", json={
        "email": email, "password": "originalpass1", "learner_name": "Reset",
    })


def _reset_mail(smtp):
    resets = [m for m in smtp.sent if (m["Subject"] or "") == "Reset your Talyn password"]
    assert len(resets) == 1, f"expected exactly one reset email, got {len(resets)}"
    return resets[0]


def _text(message) -> str:
    if message.is_multipart():
        for part in message.walk():
            if part.get_content_type() == "text/plain":
                return part.get_payload(decode=True).decode("utf-8")
    return message.get_payload(decode=True).decode("utf-8")


def _code_from_mail(smtp) -> str:
    body = _text(_reset_mail(smtp))
    match = re.search(r"Your code: (\d{6})", body)
    assert match, f"no reset code in the email: {body[:300]!r}"
    return match.group(1)


def _request(client, email):
    r = client.post("/v1/auth/password-reset/request", json={"email": email})
    assert r.status_code == 200, r.text


def _confirm_code(client, email, code, new_password="brandnewpass1"):
    return client.post("/v1/auth/password-reset/confirm", json={
        "email": email, "code": code, "new_password": new_password,
    })


# ── The email carries only the code ──────────────────────────────────────────


def test_the_email_contains_a_six_digit_code_and_no_link(client, smtp):
    _register(client, "codeonly@example.com")
    _request(client, "codeonly@example.com")
    body = _text(_reset_mail(smtp))
    assert re.search(r"Your code: (\d{6})", body)
    assert "password-reset?token=" not in body
    assert "token=" not in body


def test_the_code_is_hashed_with_a_pepper_not_stored_plain(
    client, smtp, db_session
):
    """A database dump must not hand over a live code.

    Six digits brute-force in milliseconds under plain SHA-256, so the code
    hash is HMAC'd with the app secret instead. The stored value must match
    neither the plaintext nor its unpeppered hash.
    """
    import hashlib

    _register(client, "pepper@example.com")
    _request(client, "pepper@example.com")
    code = _code_from_mail(smtp)

    row = db_session.query(PasswordResetToken).one()
    assert row.code_hash != code
    assert row.code_hash != hashlib.sha256(code.encode()).hexdigest()
    assert row.code_hash == otp.hash_code(code)
    assert len(code) == 6 and code.isdigit()


# ── Confirming with a code ───────────────────────────────────────────────────


def test_a_correct_code_resets_the_password(client, smtp):
    _register(client, "coder@example.com")
    _request(client, "coder@example.com")
    code = _code_from_mail(smtp)

    r = _confirm_code(client, "coder@example.com", code)
    assert r.status_code == 200, r.text
    assert r.json() == {
        "message": "Password has been reset",
        "next_step": "login",
    }

    login = client.post("/v1/auth/login", json={
        "email": "coder@example.com", "password": "brandnewpass1",
    })
    assert login.status_code == 200


def test_a_code_with_a_leading_zero_survives_the_round_trip(client, smtp, monkeypatch):
    """Codes are strings end to end: int("042013") is 42013."""
    monkeypatch.setattr(reset_tokens, "new_code", lambda: "042013")
    _register(client, "zero@example.com")
    _request(client, "zero@example.com")
    assert _code_from_mail(smtp) == "042013"

    r = _confirm_code(client, "zero@example.com", "042013")
    assert r.status_code == 200, r.text


def test_a_used_code_cannot_be_replayed(client, smtp):
    _register(client, "replay@example.com")
    _request(client, "replay@example.com")
    code = _code_from_mail(smtp)

    assert _confirm_code(client, "replay@example.com", code).status_code == 200
    replay = _confirm_code(client, "replay@example.com", code,
                           new_password="anotherpass1")
    assert replay.status_code == 401


def test_a_new_request_kills_the_previous_code(client, smtp, monkeypatch):
    codes = iter(["111111", "222222"])
    monkeypatch.setattr(reset_tokens, "new_code", lambda: next(codes))
    _register(client, "twice@example.com")
    _request(client, "twice@example.com")

    smtp.sent.clear()
    _request(client, "twice@example.com")
    # The first code is dead: the second request invalidated its row, and
    # only live rows redeem.
    assert _confirm_code(client, "twice@example.com", "111111").status_code == 401
    assert _confirm_code(client, "twice@example.com", "222222").status_code == 200


# ── Guessing ─────────────────────────────────────────────────────────────────


def test_a_wrong_code_is_a_401_not_a_422(client, smtp):
    """Right shape, wrong value: authentication failure, not a validation one."""
    _register(client, "wrong@example.com")
    _request(client, "wrong@example.com")
    code = _code_from_mail(smtp)
    wrong = "000000" if code != "000000" else "111111"

    r = _confirm_code(client, "wrong@example.com", wrong)
    assert r.status_code == 401
    assert "code" in r.json()["detail"].lower()


def test_a_malformed_code_is_a_422_and_costs_no_guesses(client, smtp, db_session):
    """Validation fires before the service: garbage never touches the row."""
    _register(client, "malformed@example.com")
    _request(client, "malformed@example.com")

    r = _confirm_code(client, "malformed@example.com", "12")
    assert r.status_code == 422

    row = db_session.query(PasswordResetToken).one()
    assert row.attempts == 0
    assert row.used_at is None


def test_ten_wrong_guesses_burn_the_row(client, smtp, db_session):
    _register(client, "burn@example.com")
    _request(client, "burn@example.com")
    code = _code_from_mail(smtp)
    wrong = "000000" if code != "000000" else "111111"

    for _ in range(otp.CODE_MAX_ATTEMPTS):
        assert _confirm_code(client, "burn@example.com", wrong).status_code == 401

    row = db_session.query(PasswordResetToken).one()
    assert row.used_at is not None

    # The right code no longer works either: the row is spent.
    assert _confirm_code(client, "burn@example.com", code).status_code == 401


def test_guesses_against_an_unknown_address_cost_nothing(client, smtp, db_session):
    """No row exists to count against, and the response must not say so."""
    _register(client, "real@example.com")
    _request(client, "real@example.com")
    before = db_session.query(PasswordResetToken).one().attempts

    r = _confirm_code(client, "ghost@example.com", "123456")
    assert r.status_code == 401

    assert db_session.query(PasswordResetToken).one().attempts == before


def test_a_code_for_one_account_does_not_reset_another(client, smtp):
    """Codes are scoped to the email: sharing a code across rows is possible,
    so the email is part of the credential, not a hint."""
    _register(client, "alice@example.com")
    _register(client, "bob@example.com")
    _request(client, "alice@example.com")
    code = _code_from_mail(smtp)

    r = _confirm_code(client, "bob@example.com", code)
    assert r.status_code == 401

    # Bob's password is unchanged; Alice's code is still live.
    assert client.post("/v1/auth/login", json={
        "email": "bob@example.com", "password": "originalpass1",
    }).status_code == 200
    assert _confirm_code(client, "alice@example.com", code).status_code == 200


# ── Contract shape ───────────────────────────────────────────────────────────


def test_code_without_email_is_rejected(client):
    r = client.post("/v1/auth/password-reset/confirm", json={
        "code": "123456", "new_password": "brandnewpass1",
    })
    assert r.status_code == 422


def test_email_without_code_is_rejected(client):
    r = client.post("/v1/auth/password-reset/confirm", json={
        "email": "x@example.com", "new_password": "brandnewpass1",
    })
    assert r.status_code == 422


def test_dev_inline_response_includes_the_code(client, smtp_off):
    _register(client, "devcode@example.com")
    r = client.post("/v1/auth/password-reset/request",
                    json={"email": "devcode@example.com"})
    assert r.status_code == 200
    body = r.json()
    assert re.fullmatch(r"\d{6}", body["reset_code"])
    assert "reset_token" not in body
